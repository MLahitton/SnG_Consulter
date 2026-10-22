import json
import os
from hashlib import sha256
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from test_technical_resolution_application import (
    application_case, resolution_case, gate_case, audit_case, technical_case, identity_review, review, prepare, write,
)
from test_technical_inputs import snapshot
from sng_consulter.technical.verification import build_technical_verification_input, InvalidTechnicalVerificationInputError
from sng_consulter.technical.verification.models import TechnicalVerificationInput


@pytest.fixture
def verification_case(application_case):
    app, work, case, proposals = application_case
    result = app.apply(work.batch_id)
    source = proposals.with_name('technical_resolution.json')
    return app, result, case, proposals, source


def build(case):
    app, result, *_ = case
    return build_technical_verification_input(result.batch_id, app.technical_root, *app.evidence_roots)


def test_exact_hash_all_targets_exact_structures_identity_and_no_writes(verification_case):
    app, result, case, _, source = verification_case
    before = snapshot(case[-1])
    data = build(verification_case)
    assert data.source_technical_resolution_sha256 == sha256(source.read_bytes()).hexdigest()
    assert [t.current_structure for t in data.targets] == result.structures
    assert [t.identity for t in data.targets] == case[4].identities
    assert data.resolved_identity_count == result.resolved_identity_count
    assert data.unresolved_groups == result.unresolved_groups
    assert len(data.targets) == len(result.structures) == 2
    assert snapshot(case[-1]) == before
    assert set(data.model_dump()) == {'batch_id', 'source_technical_resolution_sha256', 'targets',
                                      'resolved_identity_count', 'unresolved_groups'}


def test_resolution_read_once_whitespace_changes_exact_hash_only(verification_case, monkeypatch):
    source = verification_case[-1]
    first = build(verification_case)
    source.write_bytes(source.read_bytes() + b'\n  ')
    reads = []
    original = Path.read_bytes
    def read(path):
        if path == source: reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    second = build(verification_case)
    assert reads == [source]
    one, two = first.model_dump(), second.model_dump()
    assert one.pop('source_technical_resolution_sha256') != two.pop('source_technical_resolution_sha256')
    assert one == two


@pytest.mark.parametrize('old_proposals', [False, True])
def test_ready_ignores_or_does_not_require_historical_proposals(verification_case, old_proposals, monkeypatch):
    app, result, case, proposals, _ = verification_case
    prepare(case, ())
    app.apply(result.batch_id)
    if old_proposals: proposals.write_bytes(b'invalid historical proposals')
    else: proposals.unlink()
    before = snapshot(case[-1])
    original = Path.read_bytes
    def read(path):
        assert path != proposals, 'READY must not read historical proposals'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    data = build(verification_case)
    assert len(data.targets) == 2
    monkeypatch.setattr(Path, 'read_bytes', original)
    assert snapshot(case[-1]) == before


@pytest.mark.parametrize('corruption', [
    'missing', 'malformed', 'extraction-hash', 'audit-hash', 'gate-hash', 'mode',
    'missing-proposals', 'stale-proposals', 'identity-source', 'identity-gate-source', 'identity-artifact',
    'target-order', 'structure-id', 'module-id', 'pane-id', 'duplicate-id', 'foreign-evidence',
    'module-count', 'pane-count', 'missing-fields', 'status', 'resolved-count', 'unresolved-count',
    'unresolved-groups', 'issue-id', 'issue-order', 'issue-missing', 'issue-extra', 'counter',
    'unauthorized-value', 'changed-counter', 'batch', 'stale-extraction', 'stale-audit', 'stale-gate',
])
def test_stale_or_tampered_inputs_fail_cleanly_without_writes(verification_case, corruption):
    _, _, case, proposals, source = verification_case
    data = json.loads(source.read_bytes())
    structure = data['structures'][0]
    if corruption.endswith('-hash'): data['source_technical_' + corruption.removesuffix('-hash') + '_sha256'] = 'f' * 64
    elif corruption == 'mode': data['resolution_required'] = False
    elif corruption == 'identity-source': data['source_identity_sha256'] = 'f' * 64
    elif corruption == 'identity-gate-source': data['source_identity_gate_sha256'] = 'f' * 64
    elif corruption == 'target-order': data['structures'].reverse()
    elif corruption == 'structure-id': structure['technical_structure_id'] = 'technical-structure-9999'
    elif corruption == 'module-id': structure['modules'][0]['module_id'] = 'module-9999'
    elif corruption == 'pane-id': structure['modules'][0]['glass_panes'][0]['glass_pane_id'] = 'glass-pane-9999'
    elif corruption == 'duplicate-id': structure['modules'][0]['glass_panes'][1]['glass_pane_id'] = structure['modules'][0]['glass_panes'][0]['glass_pane_id']
    elif corruption == 'foreign-evidence': structure['scope']['evidence_candidate_ids'] = case[4].identities[1].candidate_ids
    elif corruption == 'module-count': structure['module_count']['value'] = 999
    elif corruption == 'pane-count': structure['modules'][0]['glass_pane_count']['value'] = 999
    elif corruption == 'missing-fields': structure['missing_fields'] = []
    elif corruption == 'status': structure['resolution_status'] = 'RESOLVED'
    elif corruption == 'resolved-count': data['resolved_identity_count'] += 1
    elif corruption == 'unresolved-count': data['unresolved_group_count'] = 0
    elif corruption == 'unresolved-groups': data['unresolved_groups'] = []
    elif corruption == 'issue-id': data['issue_resolutions'][0]['issue_id'] = 'technical-audit-9999'
    elif corruption == 'issue-order': data['issue_resolutions'].reverse()
    elif corruption == 'issue-missing': data['issue_resolutions'].pop(); data['unresolved_issue_count'] -= 1
    elif corruption == 'issue-extra': data['issue_resolutions'].append(data['issue_resolutions'][0] | dict(issue_id='technical-audit-9999')); data['unresolved_issue_count'] += 1
    elif corruption == 'counter': data['unresolved_issue_count'] += 1
    elif corruption == 'changed-counter': data['changes_applied_count'] = 1
    elif corruption == 'unauthorized-value': structure['name']['value'] = 'Uncertified edit'
    elif corruption == 'batch': data['batch_id'] = 'f' * 32
    write(source, data)
    if corruption == 'missing': source.unlink()
    elif corruption == 'malformed': source.write_bytes(b'{broken')
    elif corruption == 'missing-proposals': proposals.unlink()
    elif corruption == 'stale-proposals': proposals.write_bytes(proposals.read_bytes() + b'\n')
    elif corruption.startswith('stale-'):
        path = source.with_name('technical_' + corruption.removeprefix('stale-') + '.json')
        path.write_bytes(path.read_bytes() + b'\n')
    elif corruption == 'identity-artifact':
        candidates = list(case[-1].rglob('identity.json'))
        assert candidates
        candidates[0].write_bytes(candidates[0].read_bytes() + b'\n')
    before = snapshot(case[-1])
    with pytest.raises(InvalidTechnicalVerificationInputError) as caught: build(verification_case)
    assert str(case[-1]) not in str(caught.value)
    assert snapshot(case[-1]) == before


@pytest.mark.parametrize('field,value', [
    ('source_technical_resolution_proposals_sha256', 'f' * 64),
    ('issue_resolutions', 'history'), ('changes_applied_count', 1),
])
def test_ready_rejects_proposal_lineage_or_issue_history(verification_case, field, value):
    app, result, case, _, source = verification_case
    history = json.loads(source.read_bytes())['issue_resolutions']
    prepare(case, ())
    app.apply(result.batch_id)
    data = json.loads(source.read_bytes())
    data[field] = history if value == 'history' else value
    write(source, data)
    with pytest.raises(InvalidTechnicalVerificationInputError): build(verification_case)


def test_clean_structures_are_not_omitted(verification_case, resolution_case):
    app, result, case, _, _ = verification_case
    service = resolution_case[0]
    prepare(case, (1,))
    service.resolve(result.batch_id)
    app.apply(result.batch_id)
    targets = build(verification_case).targets
    assert [t.identity_id for t in targets] == [s.identity_id for s in result.structures]


@pytest.mark.parametrize('change', ['count', 'duplicate', 'target-identity', 'target-structure', 'extra'])
def test_input_and_target_contract(verification_case, change):
    data = build(verification_case).model_dump(mode='json')
    if change == 'count': data['resolved_identity_count'] += 1
    elif change == 'duplicate': data['targets'][1] = data['targets'][0]
    elif change == 'target-identity': data['targets'][0]['identity_id'] = 'identity-9999'
    elif change == 'target-structure': data['targets'][0]['technical_structure_id'] = 'technical-structure-9999'
    elif change == 'extra': data['issue_resolutions'] = []
    with pytest.raises(ValidationError): TechnicalVerificationInput.model_validate(data)


@pytest.mark.parametrize('batch', ['../escape', '..', 'a' * 31, 'f' * 32])
def test_paths_and_missing_batches_are_rejected(verification_case, batch):
    app = verification_case[0]
    with pytest.raises(InvalidTechnicalVerificationInputError):
        build_technical_verification_input(batch, app.technical_root, *app.evidence_roots)


@pytest.mark.parametrize('transition', ['inferred', 'removed'])
def test_applied_corrections_and_recomputed_bookkeeping_are_accepted(verification_case, resolution_case, transition):
    from test_technical_models import field
    from test_technical_resolution_service import proposal_for
    from test_technical_resolution_inputs import build as build_resolution
    app, applied, case, _, _ = verification_case
    proposal_service, resolver = resolution_case[:2]
    if transition == 'removed':
        audit = json.loads(case[2].read_bytes())
        for issue in audit['issues'][:2]:
            issue['field_paths'] = ['modules[0].glass_panes[0].glass_specification']
        write(case[2], audit)
        case[0].gate(applied.batch_id)
    work = build_resolution(case)
    target = work.targets[0]
    payload = proposal_for(target, 'CORRECTED')
    if transition == 'inferred':
        payload['proposed_structure']['quantity'] = field(3, 'INFERRED',
            [target.identity.candidate_ids[0]], 'Supported synthetic inference')
    else:
        payload['proposed_structure']['modules'][0]['glass_panes'][0]['glass_specification'] = field()
    resolver.payloads[target.identity_id] = payload
    proposal_service.resolve(applied.batch_id)
    corrected = app.apply(applied.batch_id)
    before = snapshot(case[-1])
    targets = build(verification_case).targets
    assert [t.current_structure for t in targets] == corrected.structures
    assert snapshot(case[-1]) == before


def test_builder_in_fresh_process_imports_no_provider_and_needs_no_key(verification_case):
    app, applied, *_ = verification_case
    code = '''
import sys
from pathlib import Path
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'openai' or 'openai_' in fullname:
            raise AssertionError('Provider import prohibited')
sys.meta_path.insert(0, Guard())
from sng_consulter.technical.verification import build_technical_verification_input
result = build_technical_verification_input(sys.argv[1], *map(Path, sys.argv[2:]))
assert len(result.targets) == 2
'''
    env = dict(os.environ, PYTHONPATH=str(Path('src').resolve()))
    env.pop('OPENAI_API_KEY', None)
    result = subprocess.run([sys.executable, '-c', code, applied.batch_id, str(app.technical_root),
        *map(str, app.evidence_roots)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
