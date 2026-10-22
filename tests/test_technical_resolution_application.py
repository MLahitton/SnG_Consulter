import copy
import json
import os
from hashlib import sha256
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from test_technical_resolution_service import (
    resolution_case, gate_case, audit_case, technical_case, identity_review, review, proposal_for, prepare, build,
)
from test_technical_models import field, known, decoration
from test_technical_inputs import snapshot
from sng_consulter.technical.models import TechnicalStructure
from sng_consulter.technical.service import technical_bookkeeping
from sng_consulter.technical.resolution import application as module
from sng_consulter.technical.resolution.diff import (
    structural_diff, apply_diff_paths, UnsupportedTechnicalResolutionCompositionChangeError,
)
from sng_consulter.technical.resolution.inputs import InvalidTechnicalResolutionInputError
from sng_consulter.technical.resolution.models import TechnicalResolutionTarget
from sng_consulter.technical.resolution.result_models import TechnicalResolutionResult, TechnicalAppliedIssueResolution
from sng_consulter.technical.resolution.service import InvalidTechnicalResolutionResponseError
from sng_consulter.technical.resolution.validation import normalize_allowed_change_paths, path_is_within_allowed_root


def write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def application_case(resolution_case):
    service, _, work, case, _ = resolution_case
    service.resolve(work.batch_id)
    application = module.TechnicalResolutionApplicationService(service.technical_root, *service.evidence_roots)
    return application, work, case, case[1].with_name('technical_resolution_proposals.json')


def retarget(target, roots):
    data = target.model_dump(mode='json')
    for issue, paths in zip(data['issues'], roots):
        issue['field_paths'] = paths
    data['allowed_change_paths'] = normalize_allowed_change_paths(p for paths in roots for p in paths)
    return TechnicalResolutionTarget.model_validate(data)


def assert_only_output(before, root, output):
    after = snapshot(root)
    assert all(after[p] == data for p, data in before.items() if p != output.relative_to(root))
    assert set(after) - set(before) <= {output.relative_to(root)}


@pytest.mark.parametrize('stale', [False, True])
def test_ready_exact_baseline_no_key_ignores_stale_proposals(application_case, monkeypatch, stale):
    app, work, case, proposals = application_case
    prepare(case, ())
    if stale:
        proposals.write_bytes(b'not even valid JSON')
    else:
        proposals.unlink()
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    before = snapshot(case[-1])
    result = app.apply(work.batch_id)
    output = case[1].with_name('technical_resolution.json')
    extraction = json.loads(case[1].read_bytes())
    assert result.structures == [TechnicalStructure.model_validate(s) for s in extraction['structures']]
    assert result.source_technical_resolution_proposals_sha256 is None
    assert result.resolution_required is False and result.issue_resolutions == []
    assert (result.corrected_issue_count, result.disputed_issue_count, result.unresolved_issue_count,
            result.changes_applied_count) == (0, 0, 0, 0)
    assert TechnicalResolutionResult.model_validate_json(output.read_bytes()) == result
    assert_only_output(before, case[-1], output)


@pytest.mark.parametrize('outcome', ['DISPUTED', 'UNRESOLVED'])
def test_blocked_no_changes_preserves_all_sources_order_and_exact_lineage(application_case, outcome):
    app, work, case, proposals = application_case
    data = json.loads(proposals.read_bytes())
    data['proposals'] = [proposal_for(target, outcome) for target in work.targets]
    write(proposals, data)
    before = snapshot(case[-1])
    result = app.apply(work.batch_id)
    assert result.structures == case[3].structures
    assert result.unresolved_groups == case[3].unresolved_groups
    assert result.resolved_identity_count == case[3].resolved_identity_count
    assert result.unresolved_group_count == case[3].unresolved_group_count
    assert result.source_identity_sha256 == case[3].source_identity_sha256
    assert result.source_identity_gate_sha256 == case[3].source_identity_gate_sha256
    assert [i.issue_id for i in result.issue_resolutions] == [i.issue_id for t in work.targets for i in t.issues]
    assert all(i.outcome == outcome and i.changed_paths == [] for i in result.issue_resolutions)
    assert result.changes_applied_count == 0
    assert result.disputed_issue_count == (3 if outcome == 'DISPUTED' else 0)
    assert result.unresolved_issue_count == (3 if outcome == 'UNRESOLVED' else 0)
    for name in ('extraction', 'audit', 'gate', 'resolution_proposals'):
        path = case[1].with_name(f'technical_{name}.json')
        assert getattr(result, f'source_technical_{name}_sha256') == sha256(path.read_bytes()).hexdigest()
    assert_only_output(before, case[-1], case[1].with_name('technical_resolution.json'))


@pytest.mark.parametrize('corruption', [
    'missing', 'invalid-json', 'batch', 'extraction-hash', 'audit-hash', 'gate-hash', 'processed',
    'count', 'order', 'identity', 'structure', 'proposed-identity', 'proposed-structure',
    'issue-order', 'foreign-evidence', 'foreign-field-evidence', 'new-default', 'missing-fields',
    'status', 'module-count', 'pane-count', 'extra', 'stale-upstream',
])
def test_invalid_sources_or_proposals_reject_without_changes(application_case, corruption):
    app, work, case, path = application_case
    data = json.loads(path.read_bytes())
    first = data['proposals'][0]
    structure = first['proposed_structure']
    if corruption == 'batch': data['batch_id'] = 'f' * 32
    elif corruption.endswith('-hash'):
        data['source_technical_' + corruption.removesuffix('-hash') + '_sha256'] = 'f' * 64
    elif corruption == 'processed': data['targets_processed'] += 1
    elif corruption == 'count': data['proposals'].pop(); data['targets_processed'] -= 1
    elif corruption == 'order': data['proposals'].reverse()
    elif corruption == 'identity': first['identity_id'] = 'identity-9999'
    elif corruption == 'structure': first['technical_structure_id'] = 'technical-structure-9999'
    elif corruption == 'proposed-identity': structure['identity_id'] = 'identity-9999'
    elif corruption == 'proposed-structure': structure['technical_structure_id'] = 'technical-structure-9999'
    elif corruption == 'issue-order': first['issue_resolutions'].reverse()
    elif corruption == 'foreign-evidence': first['issue_resolutions'][0]['evidence_candidate_ids'] = work.targets[1].identity.candidate_ids
    elif corruption == 'foreign-field-evidence': structure['scope']['evidence_candidate_ids'] = work.targets[1].identity.candidate_ids
    elif corruption == 'new-default': structure['quantity'] = field(1, 'DEFAULTED', note='Not authorized')
    elif corruption == 'missing-fields': structure['missing_fields'] = []
    elif corruption == 'status': structure['resolution_status'] = 'RESOLVED'
    elif corruption == 'module-count': structure['module_count'] = known(999)
    elif corruption == 'pane-count': structure['modules'][0]['glass_pane_count'] = known(999)
    elif corruption == 'extra': data['confidence'] = 1
    write(path, data)
    if corruption == 'missing': path.unlink()
    elif corruption == 'invalid-json': path.write_bytes(b'{broken')
    elif corruption == 'stale-upstream': case[1].write_bytes(case[1].read_bytes() + b'\n')
    before = snapshot(case[-1])
    with pytest.raises((module.InvalidTechnicalResolutionApplicationInputError,
                        InvalidTechnicalResolutionInputError, InvalidTechnicalResolutionResponseError)):
        app.apply(work.batch_id)
    assert snapshot(case[-1]) == before


@pytest.mark.parametrize('whitespace', [False, True])
def test_authorized_corrections_overlap_counts_and_bookkeeping(application_case, whitespace):
    app, work, case, path = application_case
    data = json.loads(path.read_bytes())
    for index, target in enumerate(work.targets):
        proposal = proposal_for(target, 'CORRECTED')
        proposal['proposed_structure']['quantity'] = field(index + 2, 'INFERRED',
            [target.identity.candidate_ids[0]], 'Supported inference')
        data['proposals'][index] = proposal
    write(path, data)
    if whitespace: path.write_bytes(path.read_bytes() + b'\n  ')
    before = snapshot(case[-1])
    result = app.apply(work.batch_id)
    assert result.corrected_issue_count == 3 and result.changes_applied_count == 8
    expected = ['quantity.value', 'quantity.resolution', 'quantity.evidence_candidate_ids', 'quantity.note']
    assert all(issue.changed_paths == expected for issue in result.issue_resolutions)
    assert result.issue_resolutions[0].changed_paths == result.issue_resolutions[1].changed_paths
    assert result.source_technical_resolution_proposals_sha256 == sha256(path.read_bytes()).hexdigest()
    for structure in result.structures:
        assert 'quantity' not in structure.missing_fields
        assert structure.resolution_status == 'RESOLVED'
    assert_only_output(before, case[-1], case[1].with_name('technical_resolution.json'))


@pytest.mark.parametrize('overlap_outcome', ['CORRECTED', 'DISPUTED', 'UNRESOLVED'])
def test_overlapping_issue_attribution_and_no_mutation(resolution_case, overlap_outcome):
    target = resolution_case[2].targets[0]
    payload = proposal_for(target, 'CORRECTED')
    payload['issue_resolutions'][1]['outcome'] = overlap_outcome
    payload['proposed_structure']['quantity'] = known(3, target.identity.candidate_ids[0])
    original = copy.deepcopy(payload)
    baseline = target.model_dump()
    applied, outcomes, paths = module.apply_structure(target, payload)
    assert paths == ['quantity.value', 'quantity.resolution', 'quantity.evidence_candidate_ids']
    assert outcomes[0].changed_paths == paths
    assert outcomes[1].changed_paths == (paths if overlap_outcome == 'CORRECTED' else [])
    assert applied.quantity.value == 3 and applied.missing_fields == []
    assert target.model_dump() == baseline and payload == original


@pytest.mark.parametrize('outcome', ['CORRECTED', 'DISPUTED', 'UNRESOLVED'])
@pytest.mark.parametrize('changed', [False, True])
def test_outcomes_require_real_authorized_changes(resolution_case, outcome, changed):
    target = resolution_case[2].targets[0]
    payload = proposal_for(target, outcome)
    if changed: payload['proposed_structure']['quantity'] = known(2, target.identity.candidate_ids[0])
    if (outcome == 'CORRECTED') != changed:
        with pytest.raises(module.UnauthorizedTechnicalResolutionChangeError): module.apply_structure(target, payload)
    else:
        result, _, _ = module.apply_structure(target, payload)
        if not changed: assert result == target.current_structure


@pytest.mark.parametrize('path', ['name', 'notes', 'modules[0].function', 'modules[0].dimensions.width_mm'])
def test_unrelated_diff_is_rejected_even_with_corrected_quantity(resolution_case, path):
    target = resolution_case[2].targets[0]
    payload = proposal_for(target, 'CORRECTED')
    payload['proposed_structure']['quantity'] = known(2, target.identity.candidate_ids[0])
    if path == 'name': payload['proposed_structure']['name']['value'] = 'Unauthorized name'
    elif path == 'notes': payload['proposed_structure']['notes'].append('Unauthorized note')
    elif path.endswith('function'): payload['proposed_structure']['modules'][0]['function']['value'] = 'SLIDING'
    else: payload['proposed_structure']['modules'][0]['dimensions']['width_mm']['value'] += 1
    with pytest.raises(module.UnauthorizedTechnicalResolutionChangeError): module.apply_structure(target, payload)


@pytest.mark.parametrize('before_resolution,after_resolution', [
    ('UNKNOWN', 'INFERRED'), ('UNKNOWN', 'EXPLICIT'), ('EXPLICIT', 'UNKNOWN'),
    ('INFERRED', 'UNKNOWN'), ('EXPLICIT', 'CONFLICT'),
])
def test_nested_field_transitions_recompute_canonical_bookkeeping(resolution_case, before_resolution, after_resolution):
    target = retarget(resolution_case[2].targets[0], [['modules[0].glass_panes[0].glass_specification']] * 2)
    source = target.current_structure.model_dump(mode='json')
    cid = target.identity.candidate_ids[0]
    source['quantity'] = known(2, cid)
    pane = source['modules'][0]['glass_panes'][0]
    pane['glass_specification'] = field(None if before_resolution == 'UNKNOWN' else 'Documented material',
        before_resolution, [] if before_resolution == 'UNKNOWN' else [cid],
        'Documentary inference' if before_resolution == 'INFERRED' else None)
    target.current_structure = TechnicalStructure.model_validate(source)
    target.current_structure.missing_fields, target.current_structure.resolution_status = technical_bookkeeping(target.current_structure)
    payload = proposal_for(target, 'CORRECTED')
    payload['proposed_structure']['modules'][0]['glass_panes'][0]['glass_specification'] = field(
        None if after_resolution in {'UNKNOWN', 'CONFLICT'} else 'Supported material', after_resolution,
        [] if after_resolution == 'UNKNOWN' else [cid], 'Prudent documentary explanation')
    applied, _, _ = module.apply_structure(target, payload)
    path = 'modules[0].glass_panes[0].glass_specification'
    assert (path in applied.missing_fields) == (after_resolution == 'UNKNOWN')
    assert applied.resolution_status == ('AMBIGUOUS' if after_resolution == 'CONFLICT'
        else 'PARTIAL' if after_resolution == 'UNKNOWN' else 'RESOLVED')
    assert applied.missing_fields == technical_bookkeeping(applied)[0]


@pytest.mark.parametrize('root', ['modules', 'modules[0]', 'modules[0].glass_panes',
                                  'modules[0].glass_panes[0].glass_specification'])
def test_parent_roots_authorize_existing_nested_fields(resolution_case, root):
    target = retarget(resolution_case[2].targets[0], [[root]] * 2)
    payload = proposal_for(target, 'CORRECTED')
    payload['proposed_structure']['modules'][0]['glass_panes'][0]['glass_specification'] = field()
    applied, _, paths = module.apply_structure(target, payload)
    assert all(path_is_within_allowed_root(path, root) for path in paths)
    assert applied.modules[0].glass_panes[0].glass_specification.resolution == 'UNKNOWN'


@pytest.fixture
def entity_target(resolution_case):
    target = retarget(resolution_case[2].targets[0], [['modules']] * 2)
    data = target.current_structure.model_dump(mode='json')
    data['modules'].append(copy.deepcopy(data['modules'][0]))
    data['module_count'] = known(2, target.identity.candidate_ids[0])
    for index, item in enumerate(data['modules']):
        item['module_id'] = f'module-{index + 1:04d}'
        for number, pane in enumerate(item['glass_panes']):
            pane['glass_pane_id'] = f'glass-pane-{index * 10 + number + 1:04d}'
        item['decorative_elements'] = [dict(**decoration(target.identity.candidate_ids[0]),
            decorative_element_id=f'decorative-{index * 10 + n + 1:04d}') for n in range(2)]
    target.current_structure = TechnicalStructure.model_validate(data)
    return target


@pytest.mark.parametrize('collection', ['modules', 'glass_panes', 'decorative_elements'])
@pytest.mark.parametrize('operation', ['add', 'remove', 'reorder', 'id'])
def test_entity_identity_and_composition_are_immutable(entity_target, collection, operation):
    payload = proposal_for(entity_target, 'CORRECTED')
    structure = payload['proposed_structure']
    items = structure['modules'] if collection == 'modules' else structure['modules'][0][collection]
    identifier = {'modules': 'module_id', 'glass_panes': 'glass_pane_id',
                  'decorative_elements': 'decorative_element_id'}[collection]
    replacement_id = items[0][identifier].rsplit('-', 1)[0] + '-9999'
    if operation == 'add':
        new = copy.deepcopy(items[0]); new[identifier] = replacement_id
        items.append(new)
    elif operation == 'remove': items.pop()
    elif operation == 'reorder': items.reverse()
    elif operation == 'id': items[0][identifier] = replacement_id
    if collection == 'modules': structure['module_count']['value'] = len(items)
    elif collection == 'glass_panes': structure['modules'][0]['glass_pane_count']['value'] = len(items)
    with pytest.raises(UnsupportedTechnicalResolutionCompositionChangeError):
        module.apply_structure(entity_target, payload)


def test_diff_contract_order_primitive_lists_and_sibling_indices(entity_target):
    baseline = entity_target.current_structure
    proposed = baseline.model_copy(deep=True)
    proposed.modules[0].dimensions.width_mm.note = 'First note'
    proposed.modules[1].dimensions.width_mm.note = 'Second note'
    proposed.notes = ['One', 'Two']
    expected = ['modules[0].dimensions.width_mm.note', 'modules[1].dimensions.width_mm.note', 'notes']
    assert structural_diff(baseline, proposed) == expected
    assert structural_diff(baseline.model_dump(mode='json'), proposed.model_dump(mode='json')) == expected
    assert structural_diff(baseline, proposed) == expected
    assert structural_diff(baseline, baseline.model_copy(deep=True)) == []
    assert not path_is_within_allowed_root('modules[10].function.value', 'modules[1]')
    assert not path_is_within_allowed_root('modules[1].function.value', 'modules[0]')
    assert structural_diff({'a': 1, 'b': 2}, {'a': 2, 'b': 3}) == ['a', 'b']


@pytest.mark.parametrize('path', ['__dict__', 'modules[-1].function', 'modules[99].function',
                                 'modules[0].missing', 'modules[0].__class__', 'quantity.value[0]'])
def test_apply_rejects_unsafe_or_absent_components(resolution_case, path):
    baseline = resolution_case[2].targets[0].current_structure.model_dump(mode='json')
    original = copy.deepcopy(baseline)
    with pytest.raises(ValueError): apply_diff_paths(baseline, baseline, [path])
    assert baseline == original


@pytest.mark.parametrize('failure', ['diff', 'apply', 'validation', 'write', 'replace'])
@pytest.mark.parametrize('previous', [False, True])
def test_atomic_failure_preserves_previous_and_all_sources(application_case, monkeypatch, failure, previous):
    app, work, case, proposals = application_case
    if previous: app.apply(work.batch_id)
    if failure == 'diff':
        data = json.loads(proposals.read_bytes())
        data['proposals'][0]['proposed_structure']['name']['value'] = 'Unauthorized'
        write(proposals, data)
    elif failure in {'apply', 'validation'}:
        def fail(*args, **kwargs): raise ValueError('Synthetic deterministic failure')
        monkeypatch.setattr(module, 'apply_diff_paths' if failure == 'apply' else 'validate_technical_result', fail)
    else:
        original = getattr(Path, 'write_text' if failure == 'write' else 'replace')
        def fail(path, *args, **kwargs):
            if '.staging-' in str(path):
                if failure == 'write': path.write_bytes(b'partial')
                raise OSError('Synthetic persistence failure')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'write_text' if failure == 'write' else 'replace', fail)
    before = snapshot(case[-1])
    with pytest.raises((ValueError, OSError)): app.apply(work.batch_id)
    assert snapshot(case[-1]) == before
    assert not list(case[1].parent.glob('.staging-*'))


@pytest.mark.parametrize('name', ['technical_extraction.json', 'technical_audit.json', 'technical_gate.json'])
def test_second_source_read_requires_same_bytes(application_case, monkeypatch, name):
    app, work, case, _ = application_case
    original = module.build_technical_resolution_input
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        path = case[1].with_name(name)
        path.write_bytes(path.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(module, 'build_technical_resolution_input', race)
    with pytest.raises(module.InvalidTechnicalResolutionApplicationInputError, match='between snapshot reads'):
        app.apply(work.batch_id)
    assert not case[1].with_name('technical_resolution.json').exists()


@pytest.mark.parametrize('state', ['ready', 'blocked', 'missing', 'unauthorized'])
def test_cli_offline_no_previous_execution(application_case, monkeypatch, capsys, state):
    from sng_consulter.technical import __main__ as command
    app, work, case, path = application_case
    if state == 'ready': prepare(case, ())
    elif state == 'missing': path.unlink()
    elif state == 'unauthorized':
        data = json.loads(path.read_bytes())
        data['proposals'][0]['proposed_structure']['notes'] = ['Forbidden change']
        write(path, data)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(module, 'TechnicalResolutionApplicationService', lambda: app)
    # No previous service may be called by the apply command.
    from sng_consulter.technical.service import TechnicalExtractionService
    from sng_consulter.technical.gate import TechnicalGateService
    from sng_consulter.technical.audit.service import TechnicalAuditService
    from sng_consulter.technical.resolution.service import TechnicalResolutionProposalService
    def forbidden(*args, **kwargs): pytest.fail('Apply must not execute prior services')
    for cls, method in [(TechnicalExtractionService, 'extract'), (TechnicalGateService, 'gate'),
                        (TechnicalAuditService, 'audit'), (TechnicalResolutionProposalService, 'resolve')]:
        monkeypatch.setattr(cls, method, forbidden)
    assert command.main(['apply-resolution', work.batch_id]) == (0 if state in {'ready', 'blocked'} else 1)
    captured = capsys.readouterr()
    if state == 'ready': assert '0 issues, 0 changes' in captured.out
    elif state == 'blocked': assert 'UNRESOLVED: 3' in captured.out and 'Changes: 0' in captured.out
    else: assert captured.err


@pytest.mark.parametrize('ready', [False, True])
def test_fresh_process_cli_does_not_import_provider(application_case, ready):
    app, work, case, _ = application_case
    if ready: prepare(case, ())
    code = '''
import sys
from pathlib import Path
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'openai' or 'openai_' in fullname:
            raise AssertionError('Provider import prohibited: ' + fullname)
sys.meta_path.insert(0, Guard())
from sng_consulter.technical import __main__ as command
from sng_consulter.technical.resolution import application
original = application.TechnicalResolutionApplicationService
application.TechnicalResolutionApplicationService = lambda: original(*map(Path, sys.argv[2:]))
raise SystemExit(command.main(['apply-resolution', sys.argv[1]]))
'''
    env = dict(os.environ, PYTHONPATH=str(Path('src').resolve()))
    env.pop('OPENAI_API_KEY', None)
    result = subprocess.run([sys.executable, '-c', code, work.batch_id, str(app.technical_root),
        *map(str, app.evidence_roots)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert ('0 issues, 0 changes' if ready else 'UNRESOLVED: 3') in result.stdout


def test_proposals_read_once_and_whitespace_changes_only_proposal_hash(application_case, monkeypatch):
    app, work, _, path = application_case
    first = app.apply(work.batch_id)
    path.write_bytes(path.read_bytes() + b'\n   ')
    original = Path.read_bytes
    reads = []
    def read(candidate):
        if candidate == path: reads.append(candidate)
        return original(candidate)
    monkeypatch.setattr(Path, 'read_bytes', read)
    second = app.apply(work.batch_id)
    assert len(reads) == 1
    one, two = first.model_dump(), second.model_dump()
    assert one.pop('source_technical_resolution_proposals_sha256') != two.pop('source_technical_resolution_proposals_sha256')
    assert one == two


@pytest.mark.parametrize('identifier', ['identity_id', 'technical_structure_id'])
def test_structure_identifiers_cannot_change(resolution_case, identifier):
    target = resolution_case[2].targets[0]
    payload = proposal_for(target, 'CORRECTED')
    payload['proposed_structure'][identifier] = ('identity' if identifier == 'identity_id' else 'technical-structure') + '-9999'
    with pytest.raises(InvalidTechnicalResolutionResponseError): module.apply_structure(target, payload)


def test_corrected_target_preserves_clean_structure_order(application_case, resolution_case):
    app, work, case, path = application_case
    service, resolver, _, _, _ = resolution_case
    prepare(case, (1,))
    work = build(case)
    target = work.targets[0]
    payload = proposal_for(target, 'CORRECTED')
    payload['proposed_structure']['quantity'] = known(4, target.identity.candidate_ids[0])
    resolver.payloads[target.identity_id] = payload
    service.resolve(work.batch_id)
    baseline = json.loads(case[1].read_bytes())
    before = snapshot(case[-1])
    result = app.apply(work.batch_id)
    assert result.structures[0].model_dump(mode='json') == baseline['structures'][0]
    assert result.structures[1].quantity.value == 4
    assert [s.technical_structure_id for s in result.structures] == [s['technical_structure_id'] for s in baseline['structures']]
    assert_only_output(before, case[-1], path.with_name('technical_resolution.json'))


def test_nested_glass_removal_full_batch_revalidation(application_case, resolution_case):
    app, work, case, path = application_case
    service, resolver, _, _, _ = resolution_case
    audit = json.loads(case[2].read_bytes())
    for issue in audit['issues'][:2]:
        issue['field_paths'] = ['modules[0].glass_panes[0].glass_specification']
    write(case[2], audit)
    case[0].gate(work.batch_id)
    work = build(case)
    target = work.targets[0]
    payload = proposal_for(target, 'CORRECTED')
    payload['proposed_structure']['modules'][0]['glass_panes'][0]['glass_specification'] = field()
    resolver.payloads[target.identity_id] = payload
    service.resolve(work.batch_id)
    result = app.apply(work.batch_id)
    assert result.structures[0].missing_fields == ['quantity', 'modules[0].glass_panes[0].glass_specification']
    assert result.structures[0].resolution_status == 'PARTIAL'
    assert result.structures[1] == case[3].structures[1]


@pytest.mark.parametrize('previous', [False, True])
def test_late_target_rejection_never_publishes_partial_apply(application_case, previous):
    app, work, case, path = application_case
    if previous: app.apply(work.batch_id)
    data = json.loads(path.read_bytes())
    first = proposal_for(work.targets[0], 'CORRECTED')
    first['proposed_structure']['quantity'] = known(2, work.targets[0].identity.candidate_ids[0])
    data['proposals'][0] = first
    data['proposals'][1]['proposed_structure']['notes'] = ['Unauthorized second target']
    write(path, data)
    before = snapshot(case[-1])
    with pytest.raises(module.UnauthorizedTechnicalResolutionChangeError): app.apply(work.batch_id)
    assert snapshot(case[-1]) == before


def test_existing_default_outside_corrections_survives(application_case, resolution_case):
    app, work, case, path = application_case
    service, resolver, _, _, _ = resolution_case
    data = json.loads(case[1].read_bytes())
    data['structures'][0]['location'] = field('Existing approved value', 'DEFAULTED', note='Approved baseline rule')
    write(case[1], data)
    audit = json.loads(case[2].read_bytes())
    audit['audited_technical_extraction_sha256'] = sha256(case[1].read_bytes()).hexdigest()
    write(case[2], audit)
    case[0].gate(work.batch_id)
    work = build(case)
    payload = proposal_for(work.targets[0], 'CORRECTED')
    payload['proposed_structure']['quantity'] = known(2, work.targets[0].identity.candidate_ids[0])
    resolver.payloads[work.targets[0].identity_id] = payload
    service.resolve(work.batch_id)
    result = app.apply(work.batch_id)
    assert result.structures[0].location.model_dump(mode='json') == data['structures'][0]['location']


@pytest.mark.parametrize('corruption', ['duplicate-id', 'foreign-evidence', 'unresolved-groups', 'identity-lineage'])
def test_final_canonical_validator_is_required(application_case, monkeypatch, corruption):
    app, work, case, _ = application_case
    original = module.apply_structure
    def broken(target, proposal):
        applied, outcomes, paths = original(target, proposal)
        if corruption == 'duplicate-id':
            applied.modules[0].module_id = case[3].structures[1].modules[0].module_id
        elif corruption == 'foreign-evidence':
            applied.scope.evidence_candidate_ids = work.targets[1].identity.candidate_ids
        return applied, outcomes, paths
    if corruption in {'duplicate-id', 'foreign-evidence'}:
        monkeypatch.setattr(module, 'apply_structure', broken)
    else:
        original_evidence = module.build_technical_extraction_input
        def evidence(*args):
            value = original_evidence(*args)
            if corruption == 'unresolved-groups': value.unresolved_groups = []
            else: value.source_identity_sha256 = 'f' * 64
            return value
        monkeypatch.setattr(module, 'build_technical_extraction_input', evidence)
    before = snapshot(case[-1])
    with pytest.raises(ValueError): app.apply(work.batch_id)
    assert snapshot(case[-1]) == before


@pytest.mark.parametrize('batch_id', ['../escape', '..', 'a' * 31, 'f' * 32])
def test_unsafe_or_nonexistent_batch_fails_cleanly(application_case, batch_id):
    app, _, case, _ = application_case
    before = snapshot(case[-1])
    with pytest.raises(InvalidTechnicalResolutionInputError): app.apply(batch_id)
    assert snapshot(case[-1]) == before


@pytest.mark.parametrize('change', ['mode', 'hash', 'corrected', 'disputed', 'unresolved', 'changes', 'extra'])
def test_result_contract_consistency(application_case, change):
    app, work, _, _ = application_case
    data = app.apply(work.batch_id).model_dump(mode='json')
    if change == 'mode': data['resolution_required'] = False
    elif change == 'hash': data['source_technical_resolution_proposals_sha256'] = None
    elif change == 'extra': data['confidence'] = 0.9
    else: data[{'corrected': 'corrected_issue_count', 'disputed': 'disputed_issue_count',
                'unresolved': 'unresolved_issue_count', 'changes': 'changes_applied_count'}[change]] += 1
    with pytest.raises(ValidationError): TechnicalResolutionResult.model_validate(data)


@pytest.mark.parametrize('outcome,paths', [('CORRECTED', []), ('DISPUTED', ['quantity.value']),
                                          ('UNRESOLVED', ['quantity.value']),
                                          ('CORRECTED', ['quantity.value', 'quantity.value'])])
def test_applied_issue_contract(outcome, paths):
    with pytest.raises(ValidationError):
        TechnicalAppliedIssueResolution(issue_id='technical-audit-0001', outcome=outcome,
            evidence_candidate_ids=['covered-0001'], reason='Synthetic justification', changed_paths=paths)
