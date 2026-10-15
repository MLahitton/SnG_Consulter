import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from sng_consulter.identity.audit.models import IdentityAuditIssueProposal
from sng_consulter.identity.gate import IdentityGateResult, IdentityGateService, InvalidIdentityGateInputError
from sng_consulter.identity.__main__ import main

BATCH = "a" * 32
KINDS = ["MERGE_ERROR", "SPLIT_ERROR", "SHOULD_BE_UNRESOLVED", "UNRESOLVED_MAY_BE_RESOLVABLE"]


def issue(kind, index=1):
    data = dict(issue_id=f"identity-audit-{index:04d}", issue_type=kind,
                identity_ids=["identity-0001"], unresolved_ids=[], candidate_ids=["covered-0001"], reason="Observable concern")
    if kind == "SPLIT_ERROR": data["identity_ids"].append("identity-0002")
    elif kind == "UNRESOLVED_MAY_BE_RESOLVABLE":
        data.update(identity_ids=[], unresolved_ids=["unresolved-0001"], candidate_ids=["covered-0002"])
    return data


@pytest.fixture
def saved(tmp_path):
    root = tmp_path / "runtime/identity"
    directory = root / BATCH
    directory.mkdir(parents=True)
    partition = dict(batch_id=BATCH, source_covered_discovery_sha256="b" * 64,
        identities=[dict(identity_id="identity-0001", candidate_ids=["covered-0001", "covered-0003"],
                         reference_hint="V3", description="Provisional design", reason="Observable correspondence"),
                    dict(identity_id="identity-0002", candidate_ids=["covered-0004"],
                         reference_hint=None, description="Other provisional design", reason="Distinct evidence")],
        unresolved=[dict(unresolved_id="unresolved-0001", candidate_ids=["covered-0002"], reason="Ambiguous")],
        covered_candidate_count=4, resolved_candidate_count=3, unresolved_candidate_count=1)
    identity = directory / "identity.json"
    identity.write_text(json.dumps(partition), encoding="utf-8")
    audit = dict(batch_id=BATCH, audited_identity_sha256=sha256(identity.read_bytes()).hexdigest(),
                 identities_audited=2, unresolved_groups_audited=1, issues=[])
    audit_path = directory / "identity_audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    return root, identity, audit_path


def update_audit(identity, audit_path, **changes):
    audit = json.loads(audit_path.read_text())
    audit.update(audited_identity_sha256=sha256(identity.read_bytes()).hexdigest(), **changes)
    audit_path.write_text(json.dumps(audit))


@pytest.mark.parametrize("unresolved", [False, True])
def test_clean_audit_ready_including_unresolved_and_exact_determinism(saved, unresolved):
    root, identity, audit = saved
    if not unresolved:
        data = json.loads(identity.read_text())
        data["identities"][0]["candidate_ids"].append("covered-0002")
        data.update(unresolved=[], resolved_candidate_count=4, unresolved_candidate_count=0)
        identity.write_text(json.dumps(data))
        update_audit(identity,audit,unresolved_groups_audited=0)
    before = identity.read_bytes(), audit.read_bytes()
    result = IdentityGateService(root).gate(BATCH)
    assert result.status == "READY_FOR_EXTRACTION"
    assert result.issue_count == 0 and result.blocking_issue_ids == []
    assert result.source_identity_sha256 == sha256(before[0]).hexdigest()
    assert result.source_identity_audit_sha256 == sha256(before[1]).hexdigest()
    output = identity.parent / "identity_gate.json"
    original = output.read_bytes()
    assert IdentityGateResult.model_validate_json(original) == result
    assert IdentityGateService(root).gate(BATCH) == result
    assert output.read_bytes() == original
    assert (identity.read_bytes(),audit.read_bytes()) == before


@pytest.mark.parametrize("kind", KINDS)
def test_all_issue_types_block(saved, kind):
    root, identity, audit = saved
    update_audit(identity,audit,issues=[issue(kind)])
    result = IdentityGateService(root).gate(BATCH)
    assert result.status == "BLOCKED_BY_IDENTITY_ISSUES"
    assert result.issue_count == 1
    assert result.blocking_issue_ids == ["identity-audit-0001"]


def test_all_blocking_ids_keep_persisted_order_and_source_hash_is_exact(saved):
    root, identity, audit = saved
    issues = [issue(kind,index) for index,kind in zip([8,3,11,2], KINDS)]
    update_audit(identity,audit,issues=issues)
    result = IdentityGateService(root).gate(BATCH)
    assert result.issue_count == 4
    assert result.blocking_issue_ids == [i["issue_id"] for i in issues]
    audit.write_bytes(audit.read_bytes()+b"\n")
    new = IdentityGateService(root).gate(BATCH)
    assert new.source_identity_audit_sha256 != result.source_identity_audit_sha256
    assert new.blocking_issue_ids == result.blocking_issue_ids


@pytest.mark.parametrize("invalid", ["stale", "audit-batch", "identity-json", "audit-json", "missing-identity", "missing-audit",
    "identity-batch", "duplicate-identity", "duplicate-unresolved", "double-candidate", "missing-candidate", "counts",
    "covered-hash", "audit-counts", "duplicate-issue", "unknown-group", "wrong-membership", "audit-hash-missing"])
def test_invalid_inputs_fail_explicitly_without_overwriting_gate_or_sources(saved, invalid):
    root, identity, audit = saved
    IdentityGateService(root).gate(BATCH)
    data = json.loads(identity.read_text())
    if invalid == "stale": identity.write_bytes(identity.read_bytes()+b"\n")
    elif invalid == "audit-batch": update_audit(identity,audit,batch_id="f" * 32)
    elif invalid == "identity-json": identity.write_text("{")
    elif invalid == "audit-json": audit.write_text("{")
    elif invalid == "missing-identity": identity.unlink()
    elif invalid == "missing-audit": audit.unlink()
    elif invalid == "audit-counts": update_audit(identity,audit,identities_audited=99)
    elif invalid == "duplicate-issue": update_audit(identity,audit,issues=[issue("MERGE_ERROR")]*2)
    elif invalid == "unknown-group":
        item = issue("MERGE_ERROR")
        item["identity_ids"] = ["identity-9999"]
        update_audit(identity,audit,issues=[item])
    elif invalid == "wrong-membership":
        item = issue("MERGE_ERROR")
        item["candidate_ids"] = ["covered-0002"]
        update_audit(identity,audit,issues=[item])
    elif invalid == "audit-hash-missing":
        item = json.loads(audit.read_text())
        del item["audited_identity_sha256"]
        audit.write_text(json.dumps(item))
    else:
        if invalid == "identity-batch": data["batch_id"] = "f" * 32
        elif invalid == "duplicate-identity": data["identities"][1]["identity_id"] = "identity-0001"
        elif invalid == "duplicate-unresolved": data["unresolved"].append(data["unresolved"][0])
        elif invalid == "double-candidate": data["unresolved"][0]["candidate_ids"].append("covered-0001")
        elif invalid == "missing-candidate": data["unresolved"] = []
        elif invalid == "counts": data["resolved_candidate_count"] = 99
        elif invalid == "covered-hash": data["source_covered_discovery_sha256"] = "invalid"
        identity.write_text(json.dumps(data))
        update_audit(identity,audit)
    before = {p:p.read_bytes() for p in identity.parent.iterdir() if p.is_file()}
    with pytest.raises((InvalidIdentityGateInputError,FileNotFoundError)) as caught:
        IdentityGateService(root).gate(BATCH)
    if invalid == "stale": assert "outdated" in str(caught.value)
    assert {p:p.read_bytes() for p in identity.parent.iterdir() if p.is_file()} == before
    assert not list(identity.parent.glob(".staging-*"))


@pytest.mark.parametrize("kind", KINDS)
def test_audit_issue_rejects_cross_kind_group_references(kind):
    data = issue(kind)
    del data["issue_id"]
    if kind == "UNRESOLVED_MAY_BE_RESOLVABLE": data["identity_ids"] = ["identity-0001"]
    else: data["unresolved_ids"] = ["unresolved-0001"]
    with pytest.raises(ValidationError): IdentityAuditIssueProposal.model_validate(data)


@pytest.mark.parametrize("previous", [False,True])
@pytest.mark.parametrize("failure", ["write","publish"])
def test_storage_failure_never_publishes_partial_and_preserves_prior(saved, monkeypatch, previous, failure):
    root, identity, audit = saved
    if previous: IdentityGateService(root).gate(BATCH)
    before = {p:p.read_bytes() for p in identity.parent.iterdir() if p.is_file()}
    if failure == "write":
        original = Path.write_text
        def broken(path,*args,**kwargs):
            original(path,"partial")
            raise OSError("Simulated write failure")
        monkeypatch.setattr(Path,"write_text",broken)
    else:
        def broken(*args,**kwargs): raise OSError("Simulated publication failure")
        monkeypatch.setattr(Path,"replace",broken)
    with pytest.raises(OSError): IdentityGateService(root).gate(BATCH)
    assert {p:p.read_bytes() for p in identity.parent.iterdir() if p.is_file()} == before
    assert not list(identity.parent.glob(".staging-*"))


@pytest.mark.parametrize("blocked", [False,True])
def test_cli_gate_offline_uses_only_two_sources_and_never_instantiates_providers(saved, monkeypatch, capsys, blocked):
    from sng_consulter.identity import __main__ as command
    root,identity,audit = saved
    monkeypatch.chdir(root.parent.parent)
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    def forbidden(*args,**kwargs): pytest.fail("Gate must not run providers or rebuild evidence")
    for name in ["OpenAIIdentityResolver","OpenAIIdentityAuditor","build_identity_input","IdentityAuditService"]:
        monkeypatch.setattr(command,name,forbidden)
    if blocked: update_audit(identity,audit,issues=[issue("MERGE_ERROR")])
    original = Path.read_bytes
    reads = []
    def read(path):
        reads.append(path.name)
        return original(path)
    monkeypatch.setattr(Path,"read_bytes",read)
    assert main(["gate",BATCH]) == 0
    assert reads == ["identity.json","identity_audit.json"]
    output = capsys.readouterr().out
    assert ("BLOCKED_BY_IDENTITY_ISSUES" if blocked else "READY_FOR_EXTRACTION") in output
    assert ("1 blocking issues" if blocked else "0 blocking issues") in output
    audit.unlink()
    assert main(["gate",BATCH]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err


def test_gate_result_strict_and_paths_unsafe_rejected(saved):
    root,_,_ = saved
    result = IdentityGateService(root).gate(BATCH)
    data = result.model_dump()
    for changes in [dict(issue_count="0"),dict(quantity=1)]:
        with pytest.raises(ValidationError): IdentityGateResult.model_validate({**data,**changes})
    with pytest.raises(InvalidIdentityGateInputError): IdentityGateService(root).gate("../escape")
