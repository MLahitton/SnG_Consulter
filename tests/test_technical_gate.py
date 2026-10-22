import json
from hashlib import sha256
from pathlib import Path

import pytest

from test_technical_audit import audit_case, technical_case, identity_review, review
from test_technical_inputs import snapshot
from sng_consulter.technical.audit.models import TechnicalAuditIssueType
from sng_consulter.technical.gate import (
    TechnicalGateService, TechnicalGateResult, TechnicalGateStatus, InvalidTechnicalGateInputError,
)


@pytest.fixture
def gate_case(audit_case):
    audit_service, _, source, extraction, evidence, root = audit_case
    audit_service.audit(evidence.batch_id)
    gate = TechnicalGateService(audit_service.technical_root, *audit_service.evidence_roots)
    return gate, source, source.with_name('technical_audit.json'), extraction, evidence, root


def add_issues(case, targets=(0,), kind='UNSUPPORTED_VALUE'):
    _, _, audit_path, extraction, evidence, _ = case
    data = json.loads(audit_path.read_bytes())
    data['issues'] = [dict(issue_id=f'technical-audit-{n:04d}', issue_type=kind,
        technical_structure_id=extraction.structures[target].technical_structure_id,
        identity_id=evidence.identities[target].identity_id, field_paths=['quantity'],
        candidate_ids=[evidence.identities[target].candidate_ids[0]], reason='Concrete synthetic documentary concern')
        for n, target in enumerate(targets, 1)]
    data['issue_count'] = len(data['issues'])
    audit_path.write_text(json.dumps(data), encoding='utf-8')
    return data


@pytest.mark.parametrize('targets',[(),(0,),(0,0,1,1)])
def test_ready_blocked_all_ids_order_exact_hashes_and_immutable_inputs(gate_case,targets):
    gate, source, audit_path, _, evidence, root = gate_case
    audit = add_issues(gate_case, targets)
    before = snapshot(root)
    result = gate.gate(evidence.batch_id)
    assert result.status == (TechnicalGateStatus.BLOCKED_BY_TECHNICAL_ISSUES if targets
                             else TechnicalGateStatus.READY_FOR_VERIFICATION)
    assert result.issue_count == len(targets)
    assert result.blocking_issue_ids == [i['issue_id'] for i in audit['issues']]
    assert result.source_technical_extraction_sha256 == sha256(before[source.relative_to(root)]).hexdigest()
    assert result.source_technical_audit_sha256 == sha256(before[audit_path.relative_to(root)]).hexdigest()
    target = source.with_name('technical_gate.json')
    assert TechnicalGateResult.model_validate_json(target.read_bytes()) == result
    after = snapshot(root)
    assert all(after[p] == data for p, data in before.items())
    assert set(after)-set(before) == {target.relative_to(root)}
    assert gate.gate(evidence.batch_id) == result
    assert snapshot(root) == after


@pytest.mark.parametrize('kind',list(TechnicalAuditIssueType))
def test_every_issue_type_is_blocking(gate_case,kind):
    add_issues(gate_case,kind=kind)
    result = gate_case[0].gate(gate_case[4].batch_id)
    assert result.status == 'BLOCKED_BY_TECHNICAL_ISSUES'
    assert result.blocking_issue_ids == ['technical-audit-0001']


def test_reads_exact_artifact_bytes_once_and_audit_whitespace_changes_hash(gate_case,monkeypatch):
    gate, source, audit_path, _, evidence, _ = gate_case
    first = gate.gate(evidence.batch_id)
    audit_path.write_bytes(audit_path.read_bytes()+b'\n  ')
    reads = {source:0, audit_path:0}
    original = Path.read_bytes
    def read(path):
        if path in reads: reads[path] += 1
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    result = gate.gate(evidence.batch_id)
    assert reads == {source:1,audit_path:1}
    assert result.source_technical_audit_sha256 != first.source_technical_audit_sha256
    assert result.source_technical_audit_sha256 == sha256(original(audit_path)).hexdigest()
    assert result.source_technical_extraction_sha256 == first.source_technical_extraction_sha256


@pytest.mark.parametrize('corruption',[
    'extraction-whitespace','extraction-identity-hash','extraction-gate-hash','extraction-status',
    'audit-batch','audit-hash','structure-count','issue-count','duplicate-id','id-sequence',
    'identity','structure','structure-identity','unknown-candidate','other-candidate','empty-candidates',
    'duplicate-candidates','path','index','unsafe-path','empty-paths','blank-reason','issue-type','order',
    'missing-extraction','missing-audit','invalid-extraction','invalid-audit',
])
def test_invalid_stale_or_tampered_inputs_preserve_prior_gate(gate_case,corruption):
    gate, source, audit_path, _, evidence, root = gate_case
    data = add_issues(gate_case, (0,1))
    gate.gate(evidence.batch_id)
    issue = data['issues'][0]
    if corruption == 'extraction-whitespace': source.write_bytes(source.read_bytes()+b'\n')
    elif corruption.startswith('extraction-'):
        extraction = json.loads(source.read_bytes())
        if corruption == 'extraction-identity-hash': extraction['source_identity_sha256'] = 'f'*64
        elif corruption == 'extraction-gate-hash': extraction['source_identity_gate_sha256'] = 'f'*64
        else: extraction['structures'][0]['resolution_status'] = 'RESOLVED'
        source.write_text(json.dumps(extraction),encoding='utf-8')
        data['audited_technical_extraction_sha256'] = sha256(source.read_bytes()).hexdigest()
    elif corruption == 'audit-batch': data['batch_id'] = 'f'*32
    elif corruption == 'audit-hash': data['audited_technical_extraction_sha256'] = 'f'*64
    elif corruption == 'structure-count': data['structures_audited'] = 0
    elif corruption == 'issue-count': data['issue_count'] = 0
    elif corruption == 'duplicate-id': data['issues'][1]['issue_id'] = issue['issue_id']
    elif corruption == 'id-sequence': issue['issue_id'] = 'technical-audit-9999'
    elif corruption == 'identity': issue['identity_id'] = 'identity-9999'
    elif corruption == 'structure': issue['technical_structure_id'] = 'technical-structure-9999'
    elif corruption == 'structure-identity': issue['technical_structure_id'] = data['issues'][1]['technical_structure_id']
    elif corruption == 'unknown-candidate': issue['candidate_ids'] = ['covered-9999']
    elif corruption == 'other-candidate': issue['candidate_ids'] = evidence.identities[1].candidate_ids
    elif corruption == 'empty-candidates': issue['candidate_ids'] = []
    elif corruption == 'duplicate-candidates': issue['candidate_ids'] *= 2
    elif corruption == 'path': issue['field_paths'] = ['made_up']
    elif corruption == 'index': issue['field_paths'] = ['modules[999].function']
    elif corruption == 'unsafe-path': issue['field_paths'] = ['modules[0].__dict__']
    elif corruption == 'empty-paths': issue['field_paths'] = []
    elif corruption == 'blank-reason': issue['reason'] = ' '
    elif corruption == 'issue-type': issue['issue_type'] = 'UNKNOWN_ISSUE_TYPE'
    elif corruption == 'order':
        data['issues'].reverse()
        for n,item in enumerate(data['issues'],1): item['issue_id'] = f'technical-audit-{n:04d}'
    if corruption != 'invalid-audit': audit_path.write_text(json.dumps(data),encoding='utf-8')
    if corruption == 'missing-extraction': source.unlink()
    elif corruption == 'missing-audit': audit_path.unlink()
    elif corruption == 'invalid-extraction': source.write_bytes(b'{invalid')
    elif corruption == 'invalid-audit': audit_path.write_bytes(b'{invalid')
    before = snapshot(root)
    with pytest.raises(InvalidTechnicalGateInputError): gate.gate(evidence.batch_id)
    assert snapshot(root) == before


@pytest.mark.parametrize('upstream',['identity','identity-gate','covered','prepared'])
def test_stale_canonical_evidence_rejected(gate_case,upstream):
    gate, source, _, _, evidence, root = gate_case
    gate.gate(evidence.batch_id)
    if upstream in {'identity','identity-gate'}:
        path = gate.evidence_roots[0]/evidence.batch_id/('identity.json' if upstream == 'identity' else 'identity_gate.json')
        path.write_bytes(path.read_bytes()+b'\n')
    elif upstream == 'covered':
        path = gate.evidence_roots[1]/evidence.batch_id/'covered_discovery.json'
        path.write_bytes(path.read_bytes()+b'\n')
    else: evidence.identities[0].pages[0].image_path.unlink()
    before = snapshot(root)
    with pytest.raises(InvalidTechnicalGateInputError): gate.gate(evidence.batch_id)
    assert snapshot(root) == before


@pytest.mark.parametrize('previous',[False,True])
@pytest.mark.parametrize('failure',['write','replace'])
def test_atomic_publication_failure_preserves_every_previous_file(gate_case,monkeypatch,previous,failure):
    gate, source, _, _, evidence, root = gate_case
    if previous: gate.gate(evidence.batch_id)
    before = snapshot(root)
    original = Path.write_text if failure == 'write' else Path.replace
    def fail(path,*args,**kwargs):
        if '.staging-' in str(path):
            if failure == 'write': path.write_bytes(b'partial')
            raise OSError('Synthetic Gate persistence failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text' if failure == 'write' else 'replace',fail)
    with pytest.raises(OSError): gate.gate(evidence.batch_id)
    assert snapshot(root) == before
    assert not list(source.parent.glob('.staging-*'))


@pytest.mark.parametrize('batch',['../escape','..\\escape','/outside'])
def test_unsafe_batch_rejected(gate_case,batch):
    with pytest.raises(InvalidTechnicalGateInputError): gate_case[0].gate(batch)


@pytest.mark.parametrize('state',['ready','blocked','invalid'])
def test_cli_gate_offline_no_phase_execution_and_correct_exit_codes(gate_case,monkeypatch,capsys,state):
    from sng_consulter.technical import __main__ as command
    from sng_consulter.technical import gate as gate_module
    from sng_consulter.technical.audit.openai_auditor import OpenAITechnicalAuditor
    from sng_consulter.technical.audit.service import TechnicalAuditService
    from sng_consulter.technical.service import TechnicalExtractionService
    from sng_consulter.discovery.service import DiscoveryService
    from sng_consulter.discovery.coverage.service import CoverageAuditService
    from sng_consulter.identity.service import IdentityResolutionService
    from sng_consulter.identity.audit.service import IdentityAuditService
    from sng_consulter.identity.gate import IdentityGateService
    gate, source, _, _, evidence, root = gate_case
    if state == 'blocked': add_issues(gate_case, (0,0,1))
    elif state == 'invalid': source.unlink()
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    def forbidden(*args,**kwargs): pytest.fail('Gate must not create AI clients or execute phases')
    from sng_consulter.technical import openai_extractor
    monkeypatch.setattr(openai_extractor,'OpenAITechnicalExtractor',forbidden)
    monkeypatch.setattr(OpenAITechnicalAuditor,'__init__',forbidden)
    for cls,name in [(TechnicalAuditService,'audit'),(TechnicalExtractionService,'extract'),
        (DiscoveryService,'discover'),(CoverageAuditService,'audit'),(IdentityResolutionService,'resolve'),
        (IdentityAuditService,'audit'),(IdentityGateService,'gate')]:
        monkeypatch.setattr(cls,name,forbidden)
    monkeypatch.setattr(gate_module,'TechnicalGateService',lambda:gate)
    before = snapshot(root)
    assert command.main(['gate',evidence.batch_id]) == (1 if state == 'invalid' else 0)
    output = capsys.readouterr()
    after = snapshot(root)
    assert all(after[p] == data for p,data in before.items())
    if state == 'invalid':
        assert 'InvalidTechnicalGateInputError' in output.err
        assert snapshot(root) == before
    else:
        assert ('READY_FOR_VERIFICATION' if state == 'ready' else 'BLOCKED_BY_TECHNICAL_ISSUES') in output.out
        assert ('0 blocking issues' if state == 'ready' else '3 blocking issues') in output.out
