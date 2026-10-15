import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from test_identity_review import identity_review, review, snapshot
from sng_consulter.identity.audit.models import IdentityAuditResult
from sng_consulter.identity.gate import IdentityGateService
from sng_consulter.review import routes
from sng_consulter.technical import build_technical_extraction_input


def build(batch_id):
    return build_technical_extraction_input(batch_id,routes.IDENTITY_DIR,routes.DISCOVERY_DIR,routes.BATCH_DIR,routes.PREPARED_DIR)


@pytest.fixture
def technical_case(identity_review):
    _,batch_id,document,covered,identity,root = identity_review
    audit = identity.parent / "identity_audit.json"
    audit.write_text(IdentityAuditResult(batch_id=batch_id,audited_identity_sha256=sha256(identity.read_bytes()).hexdigest(),
        identities_audited=2,unresolved_groups_audited=1,issues=[]).model_dump_json())
    IdentityGateService(routes.IDENTITY_DIR).gate(batch_id)
    # Synthetic canonical native spans, including a duplicate, to exercise the existing view.
    raw = dict(text="Native dimensional annotation",bbox=dict(x0=20,y0=10,x1=40,y1=20))
    (document.output_dir / document.pages[0].text_path).write_text(json.dumps([raw,raw]))
    return build(batch_id),batch_id,identity,covered,document,root


def test_ready_input_complete_provenance_full_pages_native_spans_and_lineage(technical_case):
    evidence,batch_id,identity,covered,document,root = technical_case
    before = snapshot(root)
    rebuilt = build(batch_id)
    assert rebuilt == evidence
    assert evidence.source_identity_sha256 == sha256(identity.read_bytes()).hexdigest()
    assert evidence.source_identity_gate_sha256 == sha256((identity.parent / "identity_gate.json").read_bytes()).hexdigest()
    assert [g.identity_id for g in evidence.identities] == ["identity-0001","identity-0002"]
    assert evidence.identities[0].candidate_ids == ["covered-0001","covered-0003"]
    assert evidence.identities[0].pages[0].image_path.is_file()
    assert len(evidence.identities[0].pages) == 2
    assert len(evidence.identities[0].pages[0].native_text_spans) == 1
    assert evidence.identities[0].pages[0].native_text_spans[0].text == "Native dimensional annotation"
    assert evidence.identities[0].pages[1].native_text_spans == []
    assert evidence.identities[0].pages[0].native_text_spans[0].bbox.x0 == 20
    assert [g.unresolved_id for g in evidence.unresolved_groups] == ["unresolved-0001"]
    assert evidence.unresolved_groups[0].candidate_ids == ["covered-0002"]
    assert evidence.identities[1].candidates[0].occurrence == 2
    for target in evidence.identities:
        assert len(target.pages) == len({(c.document_id,c.occurrence,c.page_number) for c in target.candidates})
        for c in target.candidates:
            assert c.document_id == document.document_id
            assert c.source_candidate_id.startswith("candidate-")
            assert c.origin == "DISCOVERY"
            assert c.width_px == 200 and c.height_px == 160
            assert c.region.x0 == 0.1 and c.image_path.is_file()
    assert snapshot(root) == before


def refresh_identity_gate(identity):
    audit_path = identity.parent / "identity_audit.json"
    audit = json.loads(audit_path.read_text())
    audit["audited_identity_sha256"] = sha256(identity.read_bytes()).hexdigest()
    audit_path.write_text(json.dumps(audit))
    IdentityGateService(routes.IDENTITY_DIR).gate(identity.parent.name)


@pytest.mark.parametrize("failure",["blocked","identity-stale","audit-stale","covered-stale","missing-identity","missing-audit","missing-gate",
    "batch","missing-page","occurrence","document","page","region","image-path","text-path","unsafe-batch","gate-json","audit-json"])
def test_invalid_or_stale_lineage_and_unsafe_evidence_fail_without_writes(technical_case,failure):
    _,batch_id,identity,covered,document,root = technical_case
    gate_path = identity.parent / "identity_gate.json"
    audit_path = identity.parent / "identity_audit.json"
    if failure == "blocked":
        gate = json.loads(gate_path.read_text())
        gate.update(status="BLOCKED_BY_IDENTITY_ISSUES",issue_count=1,blocking_issue_ids=["identity-audit-0001"])
        gate_path.write_text(json.dumps(gate))
    elif failure == "identity-stale": identity.write_bytes(identity.read_bytes()+b"\n")
    elif failure == "audit-stale": audit_path.write_bytes(audit_path.read_bytes()+b"\n")
    elif failure == "covered-stale": covered.write_bytes(covered.read_bytes()+b"\n")
    elif failure.startswith("missing-") and failure != "missing-page":
        {"missing-identity":identity,"missing-audit":audit_path,"missing-gate":gate_path}[failure].unlink()
    elif failure == "batch":
        gate = json.loads(gate_path.read_text())
        gate["batch_id"] = "f" * 32
        gate_path.write_text(json.dumps(gate))
    elif failure in {"image-path","text-path","missing-page"}:
        manifest = document.output_dir / "manifest.json"
        data = json.loads(manifest.read_text())
        if failure == "missing-page": (document.output_dir / document.pages[0].image_path).unlink()
        else:
            outside = root / "secret.txt"
            outside.write_text("must not be read")
            data["pages"][0]["image_path" if failure == "image-path" else "text_path"] = str(outside)
            manifest.write_text(json.dumps(data))
    elif failure in {"occurrence","document","page","region"}:
        data = json.loads(covered.read_text())
        e = data["candidates"][0]["evidence"]
        if failure == "occurrence": e["occurrence"] = 99
        elif failure == "document": e["document_id"] = "f" * 16
        elif failure == "page": e["page_number"] = 99
        else: e["region"]["x1"] = 2
        covered.write_text(json.dumps(data))
        p = json.loads(identity.read_text())
        p["source_covered_discovery_sha256"] = sha256(covered.read_bytes()).hexdigest()
        identity.write_text(json.dumps(p))
        refresh_identity_gate(identity)
    elif failure in {"gate-json","audit-json"}: (gate_path if failure == "gate-json" else audit_path).write_text("{")
    before = snapshot(root)
    with pytest.raises((ValueError,FileNotFoundError,ValidationError)):
        build("../escape" if failure == "unsafe-batch" else batch_id)
    assert snapshot(root) == before


def test_multiple_candidates_on_same_page_are_deduplicated_as_page_evidence(technical_case):
    _,batch_id,identity,_,_,_ = technical_case
    partition = json.loads(identity.read_text())
    partition["identities"][0]["candidate_ids"].append("covered-0002")
    partition.update(unresolved=[],resolved_candidate_count=4,unresolved_candidate_count=0)
    identity.write_text(json.dumps(partition))
    audit_path = identity.parent / "identity_audit.json"
    audit = json.loads(audit_path.read_text())
    audit["unresolved_groups_audited"] = 0
    audit_path.write_text(json.dumps(audit))
    refresh_identity_gate(identity)
    result = build(batch_id)
    assert len(result.identities[0].candidates) == 3
    assert len(result.identities[0].pages) == 2
    assert result.unresolved_groups == []


@pytest.mark.parametrize("identity_count,unresolved_count",[(23,1),(20,2)])
def test_builder_preserves_current_batch_sizes_with_synthetic_artifacts(technical_case,identity_count,unresolved_count):
    _,batch_id,identity,covered,_,root = technical_case
    data = json.loads(covered.read_text())
    template = data["candidates"][0]
    total = identity_count+unresolved_count
    data["candidates"] = [{**template,"covered_candidate_id":f"covered-{i:04d}","source_candidate_id":f"candidate-{i:04d}"}
                          for i in range(1,total+1)]
    data.update(discovery_count=total,coverage_added_count=0,total_candidates=total)
    covered.write_text(json.dumps(data))
    partition = json.loads(identity.read_text())
    partition["source_covered_discovery_sha256"] = sha256(covered.read_bytes()).hexdigest()
    partition["identities"] = [dict(identity_id=f"identity-{i:04d}",candidate_ids=[f"covered-{i:04d}"],
        reference_hint=None,description="Provisional design",reason="Observable evidence") for i in range(1,identity_count+1)]
    partition["unresolved"] = [dict(unresolved_id=f"unresolved-{i:04d}",candidate_ids=[f"covered-{identity_count+i:04d}"],reason="Ambiguous")
                               for i in range(1,unresolved_count+1)]
    partition.update(covered_candidate_count=total,resolved_candidate_count=identity_count,unresolved_candidate_count=unresolved_count)
    identity.write_text(json.dumps(partition))
    audit_path = identity.parent / "identity_audit.json"
    audit = json.loads(audit_path.read_text())
    audit.update(identities_audited=identity_count,unresolved_groups_audited=unresolved_count)
    audit_path.write_text(json.dumps(audit))
    refresh_identity_gate(identity)
    before = snapshot(root)
    result = build(batch_id)
    assert len(result.identities) == identity_count
    assert len(result.unresolved_groups) == unresolved_count
    assert snapshot(root) == before
