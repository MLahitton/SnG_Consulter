import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_technical_inputs import technical_case, identity_review, review, snapshot
from test_technical_models import structure
from test_technical_service import FakeTechnicalExtractor
from sng_consulter.review import routes
from sng_consulter.technical.service import TechnicalExtractionService
from sng_consulter.technical.audit.models import TechnicalAuditIssueType, TechnicalAuditIssueProposal
from sng_consulter.technical.audit.service import (
    TechnicalAuditService, InvalidTechnicalAuditInputError, InvalidTechnicalAuditResponseError,
)
from sng_consulter.technical.validation import resolve_field_path


class Auditor:
    def __init__(self):
        self.calls = []
        self.issues = []
        self.fail_on = None
    def audit(self, identity, current):
        self.calls.append((identity, current))
        if len(self.calls) == self.fail_on:
            raise RuntimeError('Synthetic audit failure')
        return dict(identity_id=identity.identity_id, issues=self.issues)


@pytest.fixture
def audit_case(technical_case):
    evidence, batch_id, _, _, _, root = technical_case
    technical_root = root / 'technical'
    proposals = [structure(i.identity_id,i.candidate_ids[0],decorations=1) for i in evidence.identities]
    result = TechnicalExtractionService(FakeTechnicalExtractor(proposals), technical_root).extract(evidence)
    source = technical_root / batch_id / 'technical_extraction.json'
    auditor = Auditor()
    service = TechnicalAuditService(auditor, technical_root, routes.IDENTITY_DIR,
        routes.DISCOVERY_DIR, routes.BATCH_DIR, routes.PREPARED_DIR)
    return service, auditor, source, result, evidence, root


def test_clean_exact_bytes_order_and_immutable_evidence(audit_case):
    service, auditor, source, current, evidence, root = audit_case
    source.write_bytes(source.read_bytes() + b'\n  ')
    before = snapshot(root)
    result = service.audit(evidence.batch_id)
    assert result.issues == [] and result.issue_count == 0 and result.structures_audited == 2
    assert result.audited_technical_extraction_sha256 == sha256(before[source.relative_to(root)]).hexdigest()
    assert [i.identity_id for i,_ in auditor.calls] == [i.identity_id for i in evidence.identities]
    assert [s for _,s in auditor.calls] == current.structures
    after = snapshot(root)
    assert all(after[p] == data for p,data in before.items())
    assert set(after)-set(before) == {source.with_name('technical_audit.json').relative_to(root)}


@pytest.mark.parametrize('corruption',[
    'identity-hash','gate-hash','batch','omitted','extra','duplicate-identity','duplicate-structure',
    'module-id','pane-id','decorative-id','evidence','missing-fields','status','unresolved',
    'resolved-count','unresolved-count','order','module-count','pane-count','invalid-json',
])
def test_corrupt_artifact_fails_before_auditor(audit_case,corruption):
    service, auditor, source, _, evidence, root = audit_case
    data = json.loads(source.read_bytes())
    a,b = data['structures']
    if corruption == 'identity-hash': data['source_identity_sha256'] = 'f'*64
    elif corruption == 'gate-hash': data['source_identity_gate_sha256'] = 'f'*64
    elif corruption == 'batch': data['batch_id'] = 'f'*32
    elif corruption == 'omitted': data['structures'].pop()
    elif corruption == 'extra':
        extra = a.copy()
        extra.update(identity_id='identity-9999',technical_structure_id='technical-structure-9999')
        data['structures'].append(extra)
    elif corruption == 'duplicate-identity': b['identity_id'] = a['identity_id']
    elif corruption == 'duplicate-structure': b['technical_structure_id'] = a['technical_structure_id']
    elif corruption == 'module-id': b['modules'][0]['module_id'] = a['modules'][0]['module_id']
    elif corruption == 'pane-id': a['modules'][0]['glass_panes'][1]['glass_pane_id'] = a['modules'][0]['glass_panes'][0]['glass_pane_id']
    elif corruption == 'decorative-id': b['modules'][0]['decorative_elements'][0]['decorative_element_id'] = a['modules'][0]['decorative_elements'][0]['decorative_element_id']
    elif corruption == 'evidence': a['scope']['evidence_candidate_ids'] = evidence.identities[1].candidate_ids
    elif corruption == 'missing-fields': a['missing_fields'] = []
    elif corruption == 'status': a['resolution_status'] = 'RESOLVED'
    elif corruption == 'unresolved': data['unresolved_groups'][0]['reason'] = 'Changed partition'
    elif corruption == 'resolved-count': data['resolved_identity_count'] = 0
    elif corruption == 'unresolved-count': data['unresolved_group_count'] = 0
    elif corruption == 'order': data['structures'].reverse()
    elif corruption == 'module-count': a['module_count']['value'] = 2
    elif corruption == 'pane-count': a['modules'][0]['glass_pane_count']['value'] = 9
    source.write_text('{invalid' if corruption == 'invalid-json' else json.dumps(data),encoding='utf-8')
    before = snapshot(root)
    with pytest.raises(InvalidTechnicalAuditInputError): service.audit(evidence.batch_id)
    assert auditor.calls == [] and snapshot(root) == before


@pytest.mark.parametrize('path',[
    'scope','reference','quantity','dimensions.width_mm','module_count','modules',
    'modules[0].function','modules[0].dimensions.width_mm','modules[0].glass_pane_count',
    'modules[0].glass_panes','modules[0].glass_panes[0].glass_specification',
])
def test_valid_field_paths(audit_case,path):
    assert resolve_field_path(audit_case[3].structures[0],path) is not None


@pytest.mark.parametrize('path',[
    '', 'fake', 'modules[99].function','modules[-1]','modules[0].fake', '__class__',
    'modules[0].__dict__', "modules[0]['function']", 'modules[0];print(1)',
    'modules..function', 'modules[0].function()', 'scope[0]', 'modules[0].',
])
def test_invalid_field_paths(audit_case,path):
    with pytest.raises(ValueError): resolve_field_path(audit_case[3].structures[0],path)


@pytest.mark.parametrize('kind',list(TechnicalAuditIssueType))
def test_issue_ids_and_specific_types(audit_case,kind):
    service,auditor,_,_,evidence,_ = audit_case
    def audit(identity,current):
        return dict(identity_id=identity.identity_id,issues=[dict(issue_type=kind,
            field_paths=['quantity'],candidate_ids=[identity.candidate_ids[0]],reason='Concrete synthetic evidence')])
    auditor.audit = audit
    result = service.audit(evidence.batch_id)
    assert [i.issue_id for i in result.issues] == ['technical-audit-0001','technical-audit-0002']
    assert [i.identity_id for i in result.issues] == [i.identity_id for i in evidence.identities]
    assert all(i.issue_type == kind for i in result.issues)


@pytest.mark.parametrize('failure',['unknown-candidate','other-identity','identity','path','empty-candidates','blank','duplicate','empty-paths'])
def test_invalid_proposals_rejected(audit_case,failure):
    service,auditor,_,_,evidence,root = audit_case
    issue = dict(issue_type='UNSUPPORTED_VALUE',field_paths=['quantity'],
        candidate_ids=[evidence.identities[0].candidate_ids[0]],reason='Documentary evidence')
    if failure == 'unknown-candidate': issue['candidate_ids'] = ['covered-9999']
    elif failure == 'other-identity': issue['candidate_ids'] = evidence.identities[1].candidate_ids
    elif failure == 'path': issue['field_paths'] = ['modules[88].function']
    elif failure == 'empty-candidates': issue['candidate_ids'] = []
    elif failure == 'blank': issue['reason'] = '  '
    elif failure == 'duplicate': issue['candidate_ids'] *= 2
    elif failure == 'empty-paths': issue['field_paths'] = []
    auditor.issues = [issue]
    if failure == 'identity': auditor.audit = lambda i,s:dict(identity_id='identity-9999',issues=[])
    before = snapshot(root)
    with pytest.raises(InvalidTechnicalAuditResponseError): service.audit(evidence.batch_id)
    assert snapshot(root) == before


@pytest.mark.parametrize('previous',[False,True])
@pytest.mark.parametrize('failure',['request','write','replace'])
def test_atomic_failure_keeps_prior_audit_and_all_inputs(audit_case,monkeypatch,previous,failure):
    service,auditor,source,_,evidence,root = audit_case
    if previous: service.audit(evidence.batch_id)
    before = snapshot(root)
    if failure == 'request': auditor.fail_on = len(auditor.calls)+2
    else:
        original = Path.write_text if failure == 'write' else Path.replace
        def fail(path,*args,**kwargs):
            if '.staging-' in str(path): raise OSError('Synthetic persistence failure')
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'write_text' if failure == 'write' else 'replace',fail)
    with pytest.raises((RuntimeError,OSError)): service.audit(evidence.batch_id)
    assert snapshot(root) == before
    assert not list(source.parent.glob('.staging-*'))


@pytest.mark.parametrize('failure',['missing','unsafe','stale-evidence'])
def test_invalid_input_paths_and_upstream_evidence(audit_case,failure):
    service,auditor,source,_,evidence,_ = audit_case
    batch = evidence.batch_id
    if failure == 'missing': source.unlink()
    elif failure == 'unsafe': batch = '../escape'
    else: (service.evidence_roots[0]/batch/'identity.json').write_bytes(b'{}')
    with pytest.raises(InvalidTechnicalAuditInputError): service.audit(batch)
    assert auditor.calls == []


def test_contract_forbids_corrections_and_requires_reason():
    data=dict(issue_type='UNSUPPORTED_VALUE',field_paths=['scope'],candidate_ids=[],reason='Evidence')
    for extra in ['confidence','corrected_value','replacement_structure']:
        with pytest.raises(ValidationError): TechnicalAuditIssueProposal(**data,**{extra:1})


def test_within_structure_issue_order_and_valid_conflict_state(audit_case):
    service,auditor,source,_,evidence,_ = audit_case
    data=json.loads(source.read_bytes())
    data['structures'][0]['reference']=dict(value=None,resolution='CONFLICT',
        evidence_candidate_ids=[evidence.identities[0].candidate_ids[0]],note='Conflicting documentary references')
    data['structures'][0]['resolution_status']='AMBIGUOUS'
    source.write_text(json.dumps(data),encoding='utf-8')
    def audit(identity,current):
        return dict(identity_id=identity.identity_id,issues=[dict(issue_type=kind,field_paths=['reference'],
            candidate_ids=[identity.candidate_ids[0]],reason='Concrete evidence')
            for kind in ['UNSUPPORTED_VALUE','RESOLUTION_ERROR']])
    auditor.audit=audit
    result=service.audit(evidence.batch_id)
    assert [i.issue_id for i in result.issues]==[f'technical-audit-{n:04d}' for n in range(1,5)]
    assert [i.issue_type for i in result.issues]==['UNSUPPORTED_VALUE','RESOLUTION_ERROR']*2


def test_gate_snapshot_changed_without_changing_ready_status_is_stale(audit_case):
    service,auditor,_,_,evidence,_=audit_case
    gate=service.evidence_roots[0]/evidence.batch_id/'identity_gate.json'
    gate.write_bytes(gate.read_bytes()+b'\n')
    with pytest.raises(InvalidTechnicalAuditInputError): service.audit(evidence.batch_id)
    assert not auditor.calls
