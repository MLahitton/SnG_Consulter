import base64
import json

import pytest
from pydantic import ValidationError

from test_discovery import batch, FakeAnalyzer, snapshot
from test_openai_analyzer import sdk
from sng_consulter.discovery import build_discovery_input, DiscoveryService
from sng_consulter.discovery.coverage import CoverageAuditService, CoverageAuditProposal
from sng_consulter.discovery.coverage import openai_analyzer as coverage_openai
from sng_consulter.discovery.coverage.openai_analyzer import OpenAICoverageAuditor, InvalidCoverageResponseError
from sng_consulter.discovery.coverage.service import InvalidCoverageInputError
from sng_consulter.discovery.openai_analyzer import OpenAIAnalyzerAPIError
from sng_consulter.discovery.__main__ import main


def omission(**changes):
    data = dict(reference_hint=None, description="Separate possible glazed opening",
                region=dict(x0=0.6, y0=0.2, x1=0.9, y1=0.7), glass_relevance="POSSIBLE",
                reason="Separate visible object outside current observations")
    data.update(changes)
    return data


@pytest.fixture
def saved(batch, tmp_path):
    batch_id, batch_root, prepared_root, _, _ = batch
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)
    root = tmp_path / "discovery"
    result = DiscoveryService(FakeAnalyzer(), root).discover(evidence)
    path = root / batch_id / "discovery.json"
    # One page has no candidates; it still needs auditing.
    result.candidates = [c for c in result.candidates if c.evidence.page_number != 2]
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return evidence, root, path, result


def test_all_pages_local_candidates_omission_provenance_and_immutable_inputs(saved, batch):
    evidence, root, path, discovery = saved
    before = path.read_bytes(), snapshot(batch[1]), snapshot(batch[2])
    calls = []

    class Auditor:
        def audit(self, page, candidates):
            calls.append((page, candidates))
            # Simulated covered pages return no repeated existing object.
            return [] if candidates else [CoverageAuditProposal(**omission())]

    result = CoverageAuditService(Auditor(), root).audit(evidence)
    assert result.pages_audited == len(evidence.pages) == 4
    assert [p for p, _ in calls] == evidence.pages
    for p, candidates in calls:
        assert candidates == [c for c in discovery.candidates if
            (c.evidence.document_id, c.evidence.occurrence, c.evidence.page_number) ==
            (p.document_id, p.occurrence, p.page_number)]
    assert len(result.missing_candidates) == 1
    missing = result.missing_candidates[0]
    assert missing.missing_candidate_id == "coverage-0001"
    assert (missing.document_id, missing.occurrence, missing.page_number) == (evidence.pages[1].document_id, 0, 2)
    assert missing.region.x0 == 0.6
    assert (path.read_bytes(), snapshot(batch[1]), snapshot(batch[2])) == before
    assert json.loads((path.parent / "coverage_audit.json").read_text())["pages_audited"] == 4


def test_sdk_full_image_text_compact_candidates_and_strict_output(sdk, monkeypatch, saved):
    evidence, root, _, discovery = saved
    monkeypatch.setattr(coverage_openai, "OpenAI", lambda **kwargs: sdk.client)
    sdk.payload = {"missing_candidates": [omission(glass_relevance="LIKELY"), omission()]}
    auditor = OpenAICoverageAuditor()
    result = CoverageAuditService(auditor, root).audit(evidence)
    assert len(result.missing_candidates) == 8
    for p, request in zip(evidence.pages, sdk.requests):
        content = request["input"][0]["content"]
        assert base64.b64decode(content[0]["image_url"].split(",")[1]) == p.image_path.read_bytes()
        payload = json.loads(content[1]["text"])
        local = [c for c in discovery.candidates if
                 (c.evidence.occurrence, c.evidence.page_number) == (p.occurrence, p.page_number)]
        assert payload["existing_candidates"] == [[c.candidate_id, c.reference_hint,
            list(c.evidence.region.model_dump().values()), c.glass_relevance.value] for c in local]
        assert len(payload["native_text_spans"]) == len(p.native_text_spans)
        assert request["text"]["format"]["strict"] is True
        assert request["store"] is False
        assert "same object counts as covered" in request["instructions"]
    auditor.close()
    assert sdk.client.is_closed()


@pytest.mark.parametrize("payload", [{}, {"missing_candidates": [omission(region=dict(x0=-1,y0=0,x1=1,y1=1))]},
                                      {"missing_candidates": [omission(quantity=2)]}])
def test_invalid_output_is_explicit_and_preserves_previous(sdk, monkeypatch, saved, payload):
    evidence, root, source, _ = saved
    monkeypatch.setattr(coverage_openai, "OpenAI", lambda **kwargs: sdk.client)
    sdk.payload = payload
    target = source.parent / "coverage_audit.json"
    target.write_bytes(b"previous-valid-audit")
    original = source.read_bytes()
    with pytest.raises(InvalidCoverageResponseError):
        CoverageAuditService(OpenAICoverageAuditor(), root).audit(evidence)
    assert target.read_bytes() == b"previous-valid-audit"
    assert source.read_bytes() == original
    assert not list(root.rglob(".staging-*"))


@pytest.mark.parametrize("previous", [False, True])
def test_api_failure_midway_never_publishes_partial(sdk, monkeypatch, saved, previous):
    evidence, root, source, _ = saved
    monkeypatch.setattr(coverage_openai, "OpenAI", lambda **kwargs: sdk.client)
    sdk.payload = {"missing_candidates": [omission()]}
    service = CoverageAuditService(OpenAICoverageAuditor(), root)
    target = source.parent / "coverage_audit.json"
    if previous:
        service.audit(evidence)
    before = target.read_bytes() if target.exists() else None
    original = source.read_bytes()
    sdk.error_on = len(sdk.requests) + 2
    with pytest.raises(OpenAIAnalyzerAPIError):
        service.audit(evidence)
    assert (target.read_bytes() if target.exists() else None) == before
    assert source.read_bytes() == original
    assert not list(root.rglob(".staging-*"))


@pytest.mark.parametrize("change", ["batch", "occurrence", "document", "page", "duplicate", "count", "malformed", "missing"])
def test_invalid_persisted_discovery_never_calls_auditor(saved, change):
    evidence, root, source, result = saved
    data = result.model_dump(mode="json")
    if change == "batch": data["batch_id"] = "b" * 32
    elif change in {"occurrence", "page"}: data["candidates"][0]["evidence"]["page_number" if change == "page" else change] = 99
    elif change == "document": data["candidates"][0]["evidence"]["document_id"] = "b" * 16
    elif change == "duplicate": data["candidates"].append(data["candidates"][0])
    elif change == "count": data["pages_analyzed"] = 0
    source.write_text("{" if change == "malformed" else json.dumps(data))
    if change == "missing": source.unlink()
    class Auditor:
        def audit(self, *args): pytest.fail("Invalid input reached auditor")
    with pytest.raises((InvalidCoverageInputError, FileNotFoundError)):
        CoverageAuditService(Auditor(), root).audit(evidence)
    assert not (source.parent / "coverage_audit.json").exists()


def test_unsafe_batch_rejected(saved):
    evidence, root, _, _ = saved
    evidence.batch_id = "../escape"
    with pytest.raises(ValidationError):
        CoverageAuditService(None, root).audit(evidence)


def test_manual_coverage_command_uses_persisted_discovery_only(sdk, monkeypatch, saved, tmp_path, capsys):
    evidence, root, source, _ = saved
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sng_consulter.discovery.__main__.build_discovery_input", lambda batch_id: evidence)
    from sng_consulter.discovery import __main__ as command
    monkeypatch.setattr(command, "CoverageAuditService", lambda auditor: CoverageAuditService(auditor, root))
    monkeypatch.setattr(command.OpenAIPageAnalyzer, "discover", lambda *args: pytest.fail("Discovery rerun"))
    monkeypatch.setattr(coverage_openai, "OpenAI", lambda **kwargs: sdk.client)
    sdk.payload = {"missing_candidates": []}
    original = source.read_bytes()
    assert main(["coverage", evidence.batch_id]) == 0
    assert len(sdk.requests) == 4
    assert source.read_bytes() == original
    assert sdk.client.is_closed()
    assert "4 pages, 0 missing candidates" in capsys.readouterr().out
