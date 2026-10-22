from pathlib import Path

import pytest

from test_technical_verification_inputs import (
    verification_case, application_case, resolution_case, gate_case, audit_case, technical_case, identity_review, review, build,
)
from test_technical_verification_models import issue_for, proposal_for
from test_technical_inputs import snapshot
from sng_consulter.technical.verification import (
    TechnicalVerificationService, TechnicalVerificationAPIError, TechnicalVerificationConfigurationError,
    InvalidTechnicalVerificationInputError, InvalidTechnicalVerificationResponseError, TechnicalVerificationResult,
)


class Verifier:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.payloads = {}
        self.fail_on = None

    def verify(self, target):
        self.calls.append(target)
        if len(self.calls) == self.fail_on:
            raise TechnicalVerificationAPIError('Synthetic API failure')
        return self.payloads.get(target.identity_id, proposal_for(target))

    def close(self):
        self.closed += 1


@pytest.fixture
def service_case(verification_case):
    app, _, _, _, _ = verification_case
    verifier = Verifier()
    factories = []
    def factory():
        factories.append(True)
        return verifier
    service = TechnicalVerificationService(factory, app.technical_root, *app.evidence_roots)
    return service, verifier, build(verification_case), verification_case, factories


@pytest.mark.parametrize('counts', [(0, 0), (2, 0), (2, 2), (0, 1)])
def test_all_targets_order_global_ids_exact_hash_and_immutable_sources(service_case, counts):
    service, verifier, evidence, case, factories = service_case
    for target, count in zip(evidence.targets, counts):
        verifier.payloads[target.identity_id] = proposal_for(target, [issue_for(target,
            reason=f'Observation {number}') for number in range(count)])
    before = snapshot(case[2][-1])
    result = service.verify(evidence.batch_id)
    assert verifier.calls == evidence.targets and len(factories) == 1 and verifier.closed == 1
    assert result.structures_verified == len(evidence.targets) == 2
    assert result.issue_count == sum(counts)
    assert result.verified_technical_resolution_sha256 == evidence.source_technical_resolution_sha256
    assert [i.issue_id for i in result.issues] == [f'technical-verification-{n:04d}' for n in range(1, sum(counts) + 1)]
    assert [i.technical_structure_id for i in result.issues] == [t.technical_structure_id
        for t, count in zip(evidence.targets, counts) for _ in range(count)]
    assert [i.reason for i in result.issues] == [f'Observation {number}' for count in counts for number in range(count)]
    output = case[-1].with_name('technical_verification.json')
    assert TechnicalVerificationResult.model_validate_json(output.read_bytes()) == result
    after = snapshot(case[2][-1])
    assert all(after[path] == raw for path, raw in before.items())
    assert set(after) - set(before) == {output.relative_to(case[2][-1])}


@pytest.mark.parametrize('failure', [
    'identity', 'structure', 'foreign-evidence', 'path', 'index', 'unsafe-path', 'duplicate-paths',
    'duplicate-candidates', 'empty-candidates', 'blank-reason', 'empty-paths', 'type', 'malformed', 'extra',
])
def test_invalid_response_rejected_not_silently_clean(service_case, failure):
    service, verifier, evidence, case, _ = service_case
    target = evidence.targets[1]
    issue = issue_for(target)
    payload = proposal_for(target, [issue])
    if failure == 'identity': payload['identity_id'] = 'identity-9999'
    elif failure == 'structure': payload['technical_structure_id'] = 'technical-structure-9999'
    elif failure == 'foreign-evidence': issue['candidate_ids'] = evidence.targets[0].identity.candidate_ids
    elif failure == 'path': issue['field_paths'] = ['nonexistent']
    elif failure == 'index': issue['field_paths'] = ['modules[999].function']
    elif failure == 'unsafe-path': issue['field_paths'] = ['modules[0].__dict__']
    elif failure == 'duplicate-paths': issue['field_paths'] = ['quantity', 'quantity']
    elif failure == 'duplicate-candidates': issue['candidate_ids'] *= 2
    elif failure == 'empty-candidates': issue['candidate_ids'] = []
    elif failure == 'blank-reason': issue['reason'] = ' '
    elif failure == 'empty-paths': issue['field_paths'] = []
    elif failure == 'type': issue['issue_type'] = 'INVENTED'
    elif failure == 'malformed': payload = {'raw': 'private provider response'}
    elif failure == 'extra': issue['corrected_value'] = 2
    verifier.payloads[target.identity_id] = payload
    before = snapshot(case[2][-1])
    with pytest.raises(InvalidTechnicalVerificationResponseError): service.verify(evidence.batch_id)
    assert verifier.closed == 1 and len(verifier.calls) == 2
    assert snapshot(case[2][-1]) == before


@pytest.mark.parametrize('previous', [False, True])
@pytest.mark.parametrize('failure', ['api', 'invalid', 'write', 'replace'])
def test_atomicity_and_close_on_all_failures(service_case, monkeypatch, previous, failure):
    service, verifier, evidence, case, _ = service_case
    if previous: service.verify(evidence.batch_id)
    before_closed = verifier.closed
    if failure == 'api': verifier.fail_on = len(verifier.calls) + 2
    elif failure == 'invalid': verifier.payloads[evidence.targets[1].identity_id] = {'bad': 'private output'}
    else:
        original = getattr(Path, 'write_text' if failure == 'write' else 'replace')
        def fail(path, *args, **kwargs):
            if '.staging-' in str(path):
                if failure == 'write': path.write_bytes(b'partial')
                raise OSError('Synthetic write failure')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'write_text' if failure == 'write' else 'replace', fail)
    before = snapshot(case[2][-1])
    with pytest.raises((TechnicalVerificationAPIError, InvalidTechnicalVerificationResponseError, OSError)):
        service.verify(evidence.batch_id)
    assert verifier.closed == before_closed + 1
    assert snapshot(case[2][-1]) == before
    assert not list(case[-1].parent.glob('.staging-*'))


def test_input_failure_precedes_client_creation(service_case):
    service, verifier, evidence, case, factories = service_case
    case[-1].write_bytes(b'{invalid')
    before = snapshot(case[2][-1])
    with pytest.raises(InvalidTechnicalVerificationInputError): service.verify(evidence.batch_id)
    assert verifier.calls == [] and not factories and verifier.closed == 0
    assert snapshot(case[2][-1]) == before


def test_verifier_receives_only_history_free_copy(service_case):
    service, verifier, evidence, _, _ = service_case
    original = verifier.verify
    def mutate(target):
        target.current_structure.notes.append('Untrusted adapter mutation')
        target.identity.description = 'Untrusted mutation'
        return original(target)
    verifier.verify = mutate
    before = evidence.model_dump()
    service.verify(evidence.batch_id)
    assert evidence.model_dump() == before
    assert all(set(target.model_dump()) == {'identity_id', 'technical_structure_id', 'current_structure', 'identity'}
               for target in verifier.calls)


@pytest.mark.parametrize('state', ['clean', 'issues', 'invalid', 'api', 'configuration', 'system'])
def test_cli_codes_close_and_no_previous_services(service_case, monkeypatch, capsys, state):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.technical import verification
    from sng_consulter.technical.service import TechnicalExtractionService
    from sng_consulter.technical.audit.service import TechnicalAuditService
    from sng_consulter.technical.gate import TechnicalGateService
    from sng_consulter.technical.resolution.service import TechnicalResolutionProposalService
    from sng_consulter.technical.resolution.application import TechnicalResolutionApplicationService
    from sng_consulter.identity.service import IdentityResolutionService
    from sng_consulter.discovery.service import DiscoveryService
    service, verifier, evidence, case, _ = service_case
    if state == 'issues': verifier.payloads[evidence.targets[0].identity_id] = proposal_for(evidence.targets[0], [issue_for(evidence.targets[0])])
    elif state == 'invalid': case[-1].unlink()
    elif state == 'api': verifier.fail_on = 2
    elif state == 'configuration':
        def fail_config(): raise TechnicalVerificationConfigurationError('Missing synthetic configuration')
        service.verifier_factory = fail_config
    elif state == 'system':
        def fail_system(*args, **kwargs): raise OSError('PRIVATE_SYSTEM_PATH')
        monkeypatch.setattr(Path, 'replace', fail_system)
    def forbidden(*args, **kwargs): pytest.fail('Verification must not execute previous phases')
    for cls, method in ((TechnicalExtractionService, 'extract'), (TechnicalAuditService, 'audit'),
                        (TechnicalGateService, 'gate'), (TechnicalResolutionProposalService, 'resolve'),
                        (TechnicalResolutionApplicationService, 'apply'), (IdentityResolutionService, 'resolve'),
                        (DiscoveryService, 'discover')):
        monkeypatch.setattr(cls, method, forbidden)
    monkeypatch.setattr(verification, 'TechnicalVerificationService', lambda: service)
    assert command.main(['verify', evidence.batch_id]) == (0 if state in {'clean', 'issues'} else 1)
    output = capsys.readouterr()
    if state in {'clean', 'issues'}:
        assert '2 structures verified' in output.out
        assert ('1 issues' if state == 'issues' else '0 issues') in output.out
        assert verifier.closed == 1
        assert all(word not in output.out for word in ('READY', 'BLOCKED', 'PASS', 'FAIL'))
    else:
        assert output.err and 'PRIVATE_SYSTEM_PATH' not in output.err
        if state in {'api', 'system'}: assert verifier.closed == 1
