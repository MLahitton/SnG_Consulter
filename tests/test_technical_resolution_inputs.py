import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_technical_gate import gate_case, add_issues, audit_case, technical_case, identity_review, review
from test_technical_inputs import snapshot
from sng_consulter.technical.resolution import build_technical_resolution_input, InvalidTechnicalResolutionInputError
from sng_consulter.technical.resolution.models import TechnicalResolutionInput, TechnicalResolutionTarget


def build(case):
    gate=case[0]
    return build_technical_resolution_input(case[4].batch_id,gate.technical_root,*gate.evidence_roots)


def prepare(case,targets=()):
    add_issues(case,targets)
    case[0].gate(case[4].batch_id)


@pytest.mark.parametrize('targets',[(),(0,),(0,0),(1,),(0,0,1)])
def test_work_grouping_order_exact_snapshots_identity_and_no_writes(gate_case,targets):
    prepare(gate_case,targets)
    _,source,audit,extraction,evidence,root=gate_case
    before=snapshot(root)
    result=build(gate_case)
    expected=list(dict.fromkeys(targets))
    assert result.resolution_required is bool(targets)
    assert [t.current_structure for t in result.targets]==[extraction.structures[n] for n in expected]
    assert [t.identity for t in result.targets]==[evidence.identities[n] for n in expected]
    assert [t.technical_structure_id for t in result.targets]==[extraction.structures[n].technical_structure_id for n in expected]
    assert [i.issue_id for t in result.targets for i in t.issues]==[f'technical-audit-{n:04d}' for n in range(1,len(targets)+1)]
    assert result.unresolved_groups==extraction.unresolved_groups==evidence.unresolved_groups
    assert result.resolved_identity_count==2
    assert all(t.allowed_change_paths==['quantity'] for t in result.targets)
    for path,attribute in [(source,'source_technical_extraction_sha256'),(audit,'source_technical_audit_sha256'),
        (source.with_name('technical_gate.json'),'source_technical_gate_sha256')]:
        assert getattr(result,attribute)==sha256(before[path.relative_to(root)]).hexdigest()
    assert snapshot(root)==before
    assert build(gate_case)==result
    assert snapshot(root)==before


def test_three_snapshots_read_once_and_gate_whitespace_changes_only_gate_hash(gate_case,monkeypatch):
    prepare(gate_case,(0,))
    first=build(gate_case)
    gate_path=gate_case[1].with_name('technical_gate.json')
    gate_path.write_bytes(gate_path.read_bytes()+b'\n  ')
    reads={gate_case[1]:0,gate_case[2]:0,gate_path:0}
    original=Path.read_bytes
    def read(path):
        if path in reads: reads[path]+=1
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    result=build(gate_case)
    assert list(reads.values())==[1,1,1]
    assert first.source_technical_gate_sha256!=result.source_technical_gate_sha256
    assert first.source_technical_audit_sha256==result.source_technical_audit_sha256
    assert first.source_technical_extraction_sha256==result.source_technical_extraction_sha256


@pytest.mark.parametrize('corruption',[
    'extraction-space','audit-space','audit-batch','gate-batch','audit-hash','gate-extraction-hash','gate-audit-hash',
    'gate-count','gate-blocking-ids','gate-blocking-order','gate-ready-with-issues','gate-blocked-clean',
    'structure','identity','structure-identity','foreign-candidate','empty-candidate','path','path-index','unsafe-path',
    'blank-reason','empty-paths','issue-type','issue-order','issue-id','duplicate-issue-id','audit-count','audit-structures',
    'missing-extraction','missing-audit','missing-gate','invalid-extraction','invalid-audit','invalid-gate',
])
def test_stale_tampered_or_missing_artifacts_are_not_no_work(gate_case,corruption):
    prepare(gate_case,() if corruption=='gate-blocked-clean' else (0,1))
    _,source,audit_path,_,_,root=gate_case
    gate_path=source.with_name('technical_gate.json')
    audit=json.loads(audit_path.read_bytes())
    gate=json.loads(gate_path.read_bytes())
    issue=audit['issues'][0] if audit['issues'] else None
    if corruption=='extraction-space': source.write_bytes(source.read_bytes()+b'\n')
    elif corruption=='audit-space': audit_path.write_bytes(audit_path.read_bytes()+b'\n')
    elif corruption=='audit-batch': audit['batch_id']='f'*32
    elif corruption=='gate-batch': gate['batch_id']='f'*32
    elif corruption=='audit-hash': audit['audited_technical_extraction_sha256']='f'*64
    elif corruption=='gate-extraction-hash': gate['source_technical_extraction_sha256']='f'*64
    elif corruption=='gate-audit-hash': gate['source_technical_audit_sha256']='f'*64
    elif corruption=='gate-count': gate['issue_count']=0
    elif corruption=='gate-blocking-ids': gate['blocking_issue_ids']=[]
    elif corruption=='gate-blocking-order': gate['blocking_issue_ids'].reverse()
    elif corruption=='gate-ready-with-issues': gate['status']='READY_FOR_VERIFICATION'
    elif corruption=='gate-blocked-clean': gate['status']='BLOCKED_BY_TECHNICAL_ISSUES'
    elif corruption=='structure': issue['technical_structure_id']='technical-structure-9999'
    elif corruption=='identity': issue['identity_id']='identity-9999'
    elif corruption=='structure-identity': issue['technical_structure_id']=audit['issues'][1]['technical_structure_id']
    elif corruption=='foreign-candidate': issue['candidate_ids']=audit['issues'][1]['candidate_ids']
    elif corruption=='empty-candidate': issue['candidate_ids']=[]
    elif corruption=='path': issue['field_paths']=['nonexistent']
    elif corruption=='path-index': issue['field_paths']=['modules[99].function']
    elif corruption=='unsafe-path': issue['field_paths']=['modules[0].__dict__']
    elif corruption=='blank-reason': issue['reason']=' '
    elif corruption=='empty-paths': issue['field_paths']=[]
    elif corruption=='issue-type': issue['issue_type']='INVENTED'
    elif corruption=='issue-order':
        audit['issues'].reverse()
        for n,i in enumerate(audit['issues'],1): i['issue_id']=f'technical-audit-{n:04d}'
    elif corruption=='issue-id': issue['issue_id']='technical-audit-9999'
    elif corruption=='duplicate-issue-id': audit['issues'][1]['issue_id']=issue['issue_id']
    elif corruption=='audit-count': audit['issue_count']=0
    elif corruption=='audit-structures': audit['structures_audited']=0
    if corruption!='audit-space':
        audit_path.write_text(json.dumps(audit),encoding='utf-8')
        # Keep the Gate hash valid for edited issues, proving actual revalidation.
        if corruption!='gate-audit-hash': gate['source_technical_audit_sha256']=sha256(audit_path.read_bytes()).hexdigest()
    gate_path.write_text(json.dumps(gate),encoding='utf-8')
    for label,path in [('extraction',source),('audit',audit_path),('gate',gate_path)]:
        if corruption==f'missing-{label}': path.unlink()
        elif corruption==f'invalid-{label}': path.write_bytes(b'{invalid')
    before=snapshot(root)
    with pytest.raises(InvalidTechnicalResolutionInputError): build(gate_case)
    assert snapshot(root)==before


@pytest.mark.parametrize('upstream',['identity','identity-gate','covered','prepared'])
def test_current_canonical_evidence_is_revalidated(gate_case,upstream):
    prepare(gate_case,(0,))
    gate,_,_,_,evidence,root=gate_case
    if upstream in {'identity','identity-gate'}:
        path=gate.evidence_roots[0]/evidence.batch_id/('identity.json' if upstream=='identity' else 'identity_gate.json')
        path.write_bytes(path.read_bytes()+b'\n')
    elif upstream=='covered':
        path=gate.evidence_roots[1]/evidence.batch_id/'covered_discovery.json'
        path.write_bytes(path.read_bytes()+b'\n')
    else: evidence.identities[0].pages[0].image_path.unlink()
    before=snapshot(root)
    with pytest.raises(InvalidTechnicalResolutionInputError): build(gate_case)
    assert snapshot(root)==before


def test_allowed_paths_come_only_from_issues_with_stable_minimal_roots(gate_case):
    prepare(gate_case,(0,0))
    data=json.loads(gate_case[2].read_bytes())
    data['issues'][0]['field_paths']=['modules[0].function','quantity','quantity','modules[0].glass_panes']
    data['issues'][1]['field_paths']=['modules','dimensions.height_mm']
    gate_case[2].write_text(json.dumps(data),encoding='utf-8')
    gate_case[0].gate(gate_case[4].batch_id)
    result=build(gate_case)
    assert result.targets[0].allowed_change_paths==['quantity','modules','dimensions.height_mm']
    assert result.targets[0].issues[0].field_paths==data['issues'][0]['field_paths']
    forged=result.targets[0].model_dump()
    forged['allowed_change_paths']=['scope']
    with pytest.raises(ValidationError): TechnicalResolutionTarget.model_validate(forged)


def test_input_model_rejects_inconsistent_work_or_extra_fields(gate_case):
    prepare(gate_case,(0,))
    valid=build(gate_case).model_dump()
    for change in [dict(resolution_required=False),dict(resolved_identity_count=0),dict(confidence=.5)]:
        with pytest.raises(ValidationError): TechnicalResolutionInput.model_validate(valid|change)


def test_no_phase_execution_or_writes_even_without_api_key(gate_case,monkeypatch):
    prepare(gate_case,(0,))
    from sng_consulter.discovery.service import DiscoveryService
    from sng_consulter.identity.service import IdentityResolutionService
    from sng_consulter.identity.audit.service import IdentityAuditService
    from sng_consulter.identity.gate import IdentityGateService
    from sng_consulter.technical.service import TechnicalExtractionService
    from sng_consulter.technical.audit.service import TechnicalAuditService
    from sng_consulter.technical.gate import TechnicalGateService
    def forbidden(*a,**kw): pytest.fail('Resolution builder must be read-only and must not execute phases')
    for cls,name in [(DiscoveryService,'discover'),(IdentityResolutionService,'resolve'),(IdentityAuditService,'audit'),
        (IdentityGateService,'gate'),(TechnicalExtractionService,'extract'),(TechnicalAuditService,'audit'),(TechnicalGateService,'gate')]:
        monkeypatch.setattr(cls,name,forbidden)
    for name in ['write_bytes','write_text','replace','mkdir','unlink']:
        monkeypatch.setattr(Path,name,forbidden)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    before=snapshot(gate_case[-1])
    assert build(gate_case).resolution_required is True
    assert snapshot(gate_case[-1])==before


def test_resolution_imports_no_provider_or_adapters():
    code="""
import sys
class NoProvider:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'openai' or fullname.startswith('openai.') or fullname in {
            'sng_consulter.technical.openai_extractor', 'sng_consulter.technical.audit.openai_auditor'}:
            raise AssertionError('Forbidden provider/adapter import: ' + fullname)
sys.meta_path.insert(0,NoProvider())
from sng_consulter.technical.resolution import build_technical_resolution_input
"""
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('batch',['../escape','..\\escape','/outside'])
def test_unsafe_batch_rejected(gate_case,batch):
    gate=gate_case[0]
    with pytest.raises(InvalidTechnicalResolutionInputError):
        build_technical_resolution_input(batch,gate.technical_root,*gate.evidence_roots)
