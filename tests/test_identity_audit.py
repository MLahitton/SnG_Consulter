import base64
import json
import logging
import traceback
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_identity_review import identity_review, review, snapshot
from test_openai_analyzer import sdk, TEST_KEY
from sng_consulter.identity import build_identity_input, IdentityResolutionResult
from sng_consulter.identity.visuals import build_contact_sheets
from sng_consulter.identity.audit import IdentityAuditService, IdentityAuditProposal, IdentityAuditResult
from sng_consulter.identity.audit.service import InvalidIdentityAuditInputError, InvalidIdentityAuditProposalError
from sng_consulter.identity.audit import openai_auditor
from sng_consulter.identity.audit.openai_auditor import (
    OpenAIIdentityAuditor, IdentityAuditConfigurationError, IdentityAuditAPIError,
    InvalidIdentityAuditResponseError, IdentityAuditContextBudgetExceeded,
)
from sng_consulter.identity.__main__ import main
from sng_consulter.review import routes


def issue(kind="MERGE_ERROR"):
    data = dict(issue_type=kind, identity_ids=["identity-0001"], unresolved_ids=[],
                candidate_ids=["covered-0001", "covered-0003"], reason="Concrete visual discrepancy")
    if kind == "SPLIT_ERROR": data.update(identity_ids=["identity-0001","identity-0002"], candidate_ids=["covered-0001","covered-0004"])
    elif kind == "SHOULD_BE_UNRESOLVED": data.update(identity_ids=["identity-0002"], candidate_ids=["covered-0004"])
    elif kind == "UNRESOLVED_MAY_BE_RESOLVABLE": data.update(identity_ids=[], unresolved_ids=["unresolved-0001"], candidate_ids=["covered-0002"])
    return data


class FakeAuditor:
    def __init__(self, issues=None):
        self.calls = []
        self.issues = issues or []

    def audit(self, evidence, partition):
        self.calls.append((evidence,partition))
        return IdentityAuditProposal(issues=self.issues)


def service(auditor):
    return IdentityAuditService(auditor, routes.IDENTITY_DIR, routes.DISCOVERY_DIR, routes.BATCH_DIR, routes.PREPARED_DIR)


@pytest.mark.parametrize("kind", [None, "MERGE_ERROR", "SPLIT_ERROR", "SHOULD_BE_UNRESOLVED", "UNRESOLVED_MAY_BE_RESOLVABLE"])
def test_clean_and_each_issue_type_exact_hash_determinism_and_no_source_writes(identity_review, kind):
    _,batch_id,_,_,path,root = identity_review
    before = snapshot(root)
    auditor = FakeAuditor([issue(kind)] if kind else [])
    result = service(auditor).audit(batch_id)
    assert result.audited_identity_sha256 == sha256(path.read_bytes()).hexdigest()
    assert (result.identities_audited,result.unresolved_groups_audited) == (2,1)
    assert len(result.issues) == (1 if kind else 0)
    if kind:
        assert result.issues[0].issue_id == "identity-audit-0001"
        assert result.issues[0].issue_type == kind
    output = path.parent / "identity_audit.json"
    original = output.read_bytes()
    assert service(auditor).audit(batch_id) == result
    assert output.read_bytes() == original
    assert IdentityAuditResult.model_validate_json(original) == result
    assert all((root / p).read_bytes() == content for p,content in before.items())
    assert set(snapshot(root)) - set(before) == {output.relative_to(root)}
    # Future consumption must compare this hash: even whitespace makes the audit stale.
    path.write_bytes(path.read_bytes() + b"\n")
    assert result.audited_identity_sha256 != sha256(path.read_bytes()).hexdigest()
    updated = service(auditor).audit(batch_id)
    assert updated.audited_identity_sha256 == sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("invalid", ["identity", "unresolved", "candidate", "no-group", "membership", "duplicate", "empty-reason", "assigned-id", "corrected"])
def test_invalid_issue_rejected_without_overwriting(identity_review, invalid):
    _,batch_id,_,_,path,_ = identity_review
    service(FakeAuditor()).audit(batch_id)
    output = path.parent / "identity_audit.json"
    original = output.read_bytes()
    data = issue()
    if invalid == "identity": data["identity_ids"] = ["identity-9999"]
    elif invalid == "unresolved": data["unresolved_ids"] = ["unresolved-9999"]
    elif invalid == "candidate": data["candidate_ids"] = ["covered-9999"]
    elif invalid == "no-group": data["identity_ids"] = []
    elif invalid == "membership": data["candidate_ids"] = ["covered-0002"]
    elif invalid == "duplicate": data["candidate_ids"] *= 2
    elif invalid == "empty-reason": data["reason"] = ""
    elif invalid == "assigned-id": data["issue_id"] = "identity-audit-9999"
    else: data["corrected_identity"] = {}
    with pytest.raises((ValidationError, InvalidIdentityAuditProposalError)):
        service(FakeAuditor([data])).audit(batch_id)
    assert output.read_bytes() == original
    assert not list(path.parent.glob(".staging-*"))


@pytest.mark.parametrize("invalid", ["lineage", "batch", "counts", "duplicate-group", "duplicate-candidate", "missing-candidate", "unknown-candidate", "json", "missing", "unsafe-batch"])
def test_invalid_identity_never_reaches_auditor(identity_review, invalid):
    _,batch_id,_,_,path,root = identity_review
    data = json.loads(path.read_text())
    if invalid == "lineage": data["source_covered_discovery_sha256"] = "f" * 64
    elif invalid == "batch": data["batch_id"] = "f" * 32
    elif invalid == "counts": data["covered_candidate_count"] = 99
    elif invalid == "duplicate-group": data["identities"][1]["identity_id"] = "identity-0001"
    elif invalid == "duplicate-candidate": data["identities"][0]["candidate_ids"].append("covered-0001")
    elif invalid == "missing-candidate": data["unresolved"] = []
    elif invalid == "unknown-candidate": data["unresolved"][0]["candidate_ids"] = ["covered-9999"]
    if invalid == "missing": path.unlink()
    elif invalid == "json": path.write_text("{")
    else: path.write_text(json.dumps(data))
    before = snapshot(root)
    auditor = FakeAuditor()
    with pytest.raises((InvalidIdentityAuditInputError, FileNotFoundError, ValidationError)):
        service(auditor).audit("../escape" if invalid == "unsafe-batch" else batch_id)
    assert auditor.calls == []
    assert snapshot(root) == before


@pytest.fixture
def audit_sdk(sdk, monkeypatch):
    monkeypatch.delenv("SNG_IDENTITY_AUDIT_MODEL", raising=False)
    def factory(**kwargs):
        sdk.constructor_calls.append(kwargs)
        return sdk.client
    monkeypatch.setattr(openai_auditor,"OpenAI",factory)
    sdk.payload = dict(issues=[])
    return sdk


def test_sdk_all_metadata_current_membership_shared_sheets_and_independent_prompt(audit_sdk, identity_review):
    _,batch_id,_,_,path,root = identity_review
    before = snapshot(root)
    audit_sdk.payload = dict(issues=[issue(k) for k in ["MERGE_ERROR","SPLIT_ERROR","SHOULD_BE_UNRESOLVED","UNRESOLVED_MAY_BE_RESOLVABLE"]])
    result = service(OpenAIIdentityAuditor()).audit(batch_id)
    assert [i.issue_id for i in result.issues] == [f"identity-audit-{i:04d}" for i in range(1,5)]
    request = audit_sdk.requests[0]
    assert request["store"] is False
    assert request["text"]["format"]["strict"] is True
    assert "issue_id" not in request["text"]["format"]["schema"]["$defs"]["IdentityAuditIssueProposal"]["properties"]
    assert "Do NOT redo Identity from scratch" in request["instructions"]
    assert "issues=[]" in request["instructions"]
    content = request["input"][0]["content"]
    metadata = json.loads(content[0]["text"])
    saved = json.loads(path.read_text())
    assert metadata["identities"] == [{k:v for k,v in g.items() if k != "reason"} for g in saved["identities"]]
    assert metadata["unresolved"] == saved["unresolved"]
    assert [c["covered_candidate_id"] for c in metadata["candidates"]] == [f"covered-{i:04d}" for i in range(1,5)]
    assert all(set(c) == {"covered_candidate_id","reference_hint","description","glass_relevance","occurrence","page_number"}
               for c in metadata["candidates"])
    assert all(token not in json.dumps(content) for token in ["filename","original_name","image_path",str(root),TEST_KEY])
    evidence = build_identity_input(batch_id, routes.DISCOVERY_DIR, routes.BATCH_DIR, routes.PREPARED_DIR)
    sheets = build_contact_sheets(evidence)
    images = [item for item in content if item["type"] == "input_image"]
    assert len(images) == len(sheets)
    for image,sheet in zip(images,sheets):
        assert image["detail"] == "high"
        assert base64.b64decode(image["image_url"].split(",",1)[1]) == sheet.png_bytes
    assert all((root/p).read_bytes() == b for p,b in before.items())
    assert set(snapshot(root))-set(before) == {(path.parent / "identity_audit.json").relative_to(root)}


def test_independent_configuration(audit_sdk, monkeypatch):
    monkeypatch.setenv("SNG_IDENTITY_MODEL","must-not-be-used")
    assert OpenAIIdentityAuditor().model == "gpt-6.1-sol"
    monkeypatch.setenv("SNG_IDENTITY_AUDIT_MODEL","audit-model")
    assert OpenAIIdentityAuditor().model == "audit-model"
    assert audit_sdk.constructor_calls[0] == dict(api_key=TEST_KEY,max_retries=0,timeout=180.0)
    monkeypatch.setenv("SNG_IDENTITY_AUDIT_MODEL"," ")
    with pytest.raises(IdentityAuditConfigurationError): OpenAIIdentityAuditor()
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(IdentityAuditConfigurationError): OpenAIIdentityAuditor()
    assert audit_sdk.requests == []


@pytest.mark.parametrize("previous", [False,True])
@pytest.mark.parametrize("failure", ["invalid", "api", "incomplete", "refusal", "malformed", "connection", "write", "publish"])
def test_sdk_and_storage_errors_preserve_previous_and_never_leak_secrets(audit_sdk, identity_review, monkeypatch, caplog, previous, failure):
    _,batch_id,_,_,path,root = identity_review
    caplog.set_level(logging.DEBUG)
    audit_service = service(OpenAIIdentityAuditor())
    if previous: audit_service.audit(batch_id)
    before = snapshot(root)
    if failure == "invalid": audit_sdk.payload = dict(issues=[dict(issue(), quantity=TEST_KEY)])
    elif failure == "api": audit_sdk.error_on = len(audit_sdk.requests)+1
    elif failure == "incomplete": audit_sdk.status = "incomplete"
    elif failure == "refusal": audit_sdk.refusal = True
    elif failure == "malformed": audit_sdk.malformed_text = True
    elif failure == "connection": audit_sdk.connection_failure = True
    elif failure == "write":
        original = Path.write_text
        def broken(path,*args,**kwargs):
            original(path,"partial")
            raise OSError("Simulated write failure")
        monkeypatch.setattr(Path,"write_text",broken)
    elif failure == "publish":
        def broken(*args,**kwargs): raise OSError("Simulated publish failure")
        monkeypatch.setattr(Path,"replace",broken)
    with pytest.raises((IdentityAuditAPIError,InvalidIdentityAuditResponseError,OSError)) as caught:
        audit_service.audit(batch_id)
    assert snapshot(root) == before
    assert not list(path.parent.glob(".staging-*"))
    assert TEST_KEY not in caplog.text
    assert TEST_KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("count,groups", [(25,23),(51,20),(80,20),(81,20)])
def test_budget_supports_complete_inputs_without_chunking(audit_sdk, identity_review, count, groups):
    _,batch_id,_,_,path,_ = identity_review
    evidence = build_identity_input(batch_id,routes.DISCOVERY_DIR,routes.BATCH_DIR,routes.PREPARED_DIR)
    evidence.candidates = [evidence.candidates[0].model_copy(update={"covered_candidate_id":f"covered-{i:04d}"}) for i in range(1,count+1)]
    data = json.loads(path.read_text())
    data["identities"] = [dict(identity_id=f"identity-{i:04d}",candidate_ids=[f"covered-{i:04d}"],reference_hint=None,
        description="Provisional design",reason="Evidence") for i in range(1,groups+1)]
    data["unresolved"] = [dict(unresolved_id="unresolved-0001",candidate_ids=[f"covered-{i:04d}" for i in range(groups+1,count+1)],reason="Ambiguous")]
    data.update(covered_candidate_count=count,resolved_candidate_count=groups,unresolved_candidate_count=count-groups)
    partition = IdentityResolutionResult.model_validate(data)
    auditor = OpenAIIdentityAuditor()
    if count > 80:
        with pytest.raises(IdentityAuditContextBudgetExceeded): auditor.audit(evidence,partition)
        assert audit_sdk.requests == []
    else:
        assert auditor.audit(evidence,partition).issues == []
        metadata = json.loads(audit_sdk.requests[0]["input"][0]["content"][0]["text"])
        assert len(metadata["candidates"]) == count
        assert len(metadata["identities"]) == groups


def test_cli_runs_only_audit_and_closes_client(audit_sdk, identity_review, monkeypatch, capsys):
    from sng_consulter.identity import __main__ as command
    _,batch_id,_,_,path,_ = identity_review
    def forbidden(*args,**kwargs): pytest.fail("Audit must not execute Identity Resolver")
    monkeypatch.setattr(command,"OpenAIIdentityResolver",forbidden)
    monkeypatch.setattr(command,"build_identity_input",forbidden)
    monkeypatch.setattr(command,"IdentityAuditService",service)
    assert main(["audit",batch_id]) == 0
    assert audit_sdk.client.is_closed()
    assert len(audit_sdk.requests) == 1
    assert "0 issues" in capsys.readouterr().out
    assert (path.parent / "identity_audit.json").is_file()


def test_metadata_budget_rejected_before_sheets_or_provider(audit_sdk, identity_review, monkeypatch):
    _,batch_id,_,_,path,_ = identity_review
    evidence = build_identity_input(batch_id,routes.DISCOVERY_DIR,routes.BATCH_DIR,routes.PREPARED_DIR)
    evidence.candidates[0].description = "x" * 120_001
    partition = IdentityResolutionResult.model_validate_json(path.read_bytes())
    def forbidden(*args): pytest.fail("Over-budget input must not build sheets")
    monkeypatch.setattr(openai_auditor,"build_contact_sheets",forbidden)
    with pytest.raises(IdentityAuditContextBudgetExceeded): OpenAIIdentityAuditor().audit(evidence,partition)
    assert audit_sdk.requests == []


def test_cli_rejects_stale_identity_without_provider_request(audit_sdk, identity_review, monkeypatch, capsys):
    from sng_consulter.identity import __main__ as command
    _,batch_id,_,source,_,_ = identity_review
    source.write_bytes(source.read_bytes()+b"\n")
    monkeypatch.setattr(command,"IdentityAuditService",service)
    assert main(["audit",batch_id]) == 1
    assert audit_sdk.requests == []
    assert audit_sdk.client.is_closed()
    assert "outdated" in capsys.readouterr().err
