import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_discovery import batch, FakeAnalyzer, snapshot
from sng_consulter.discovery import DiscoveryService, build_discovery_input
from sng_consulter.discovery.coverage import CoverageAuditService, CoverageAuditProposal
from sng_consulter.discovery.consolidation import CoveredDiscoveryService
from sng_consulter.identity import (
    build_identity_input, IdentityInput, IdentityResolutionProposal, IdentityResolutionResult,
    IdentityResolutionService, InvalidIdentityInputError, InvalidIdentityResolutionError,
)


class FakeIdentityResolver:
    def __init__(self, proposal=None, fail=False):
        self.proposal = proposal
        self.fail = fail
        self.inputs = []

    def resolve(self, evidence):
        self.inputs.append(evidence)
        if self.fail:
            raise RuntimeError("Intentional resolver failure")
        return self.proposal if self.proposal is not None else dict(groups=[], unresolved=[
            dict(candidate_ids=[c.covered_candidate_id], reason="Insufficient identity evidence")
            for c in evidence.candidates
        ])


@pytest.fixture(params=[1, 3], ids=["one-occurrence", "multiple-occurrences"])
def artifacts(batch, tmp_path, request):
    batch_id, batch_root, prepared_root, _, _ = batch
    manifest = batch_root / batch_id / "manifest.json"
    data = json.loads(manifest.read_text())
    data["documents"] = [entry for entry in data["documents"] if entry["status"] == "prepared"][:request.param]
    manifest.write_text(json.dumps(data))
    discovery_root = tmp_path / "discovery"
    evidence = build_discovery_input(batch_id, batch_root, prepared_root)
    DiscoveryService(FakeAnalyzer(), discovery_root).discover(evidence)

    class Auditor:
        def audit(self, page, candidates):
            return [CoverageAuditProposal(reference_hint=None, description="Possible omitted object",
                region=dict(x0=0.6, y0=0.1, x1=0.8, y1=0.5), glass_relevance="POSSIBLE",
                reason="Separate uncovered observation")]

    CoverageAuditService(Auditor(), discovery_root).audit(evidence)
    covered = CoveredDiscoveryService(discovery_root).consolidate(batch_id)
    source = discovery_root / batch_id / "covered_discovery.json"
    identity_input = build_identity_input(batch_id, discovery_root, batch_root, prepared_root)
    return identity_input, source, covered, batch_root, prepared_root


def group(ids, hint=None):
    return dict(candidate_ids=ids, reference_hint=hint, description="Provisional structure", reason="Related visual evidence")


def test_complete_input_exact_byte_hash_and_locatable_evidence(artifacts):
    evidence, source, covered, batch_root, prepared_root = artifacts
    assert evidence.source_covered_discovery_sha256 == sha256(source.read_bytes()).hexdigest()
    assert [c.covered_candidate_id for c in evidence.candidates] == [c.covered_candidate_id for c in covered.candidates]
    for candidate, original in zip(evidence.candidates, covered.candidates):
        assert (candidate.document_id, candidate.occurrence, candidate.page_number, candidate.region) == (
            original.evidence.document_id, original.evidence.occurrence, original.evidence.page_number, original.evidence.region)
        assert (candidate.origin, candidate.source_candidate_id, candidate.reference_hint,
                candidate.description, candidate.glass_relevance) == (
            original.origin, original.source_candidate_id, original.reference_hint,
            original.description, original.glass_relevance)
        assert candidate.image_path.is_file()
        manifest = json.loads((prepared_root / candidate.document_id / "manifest.json").read_text())
        page = manifest["pages"][candidate.page_number - 1]
        assert (candidate.width_px, candidate.height_px) == (page["width_px"], page["height_px"])
        assert candidate.image_path == (prepared_root / candidate.document_id / page["image_path"]).resolve()
        assert not {"filename", "original_name", "quantity"} & type(candidate).model_fields.keys()
    if len({c.occurrence for c in evidence.candidates}) > 1:
        repeated = [c for c in evidence.candidates if c.occurrence in {1, 2}]
        assert len({c.document_id for c in repeated}) == 1
        assert {c.occurrence for c in repeated} == {1, 2}
    # Whitespace changes lineage even though the parsed observations are unchanged.
    source.write_bytes(source.read_bytes() + b"\n")
    new = build_identity_input(evidence.batch_id, source.parent.parent, batch_root, prepared_root)
    assert new.candidates == evidence.candidates
    assert new.source_covered_discovery_sha256 != evidence.source_covered_discovery_sha256


def test_same_hint_separate_different_hints_grouped_null_unresolved_and_determinism(artifacts, tmp_path):
    evidence, source, _, batch_root, prepared_root = artifacts
    evidence.candidates[0].reference_hint = evidence.candidates[1].reference_hint = "V3"
    evidence.candidates[2].reference_hint = "VENTANA 1"
    evidence.candidates[3].reference_hint = "ALZADO VENTANA 1"
    ids = [c.covered_candidate_id for c in evidence.candidates]
    proposal = dict(groups=[group([ids[3], ids[2]], "VENTANA 1"), group([ids[1]], "V3"), group([ids[0]], "V3")],
        unresolved=[dict(candidate_ids=[cid], reason="Ambiguous association") for cid in reversed(ids[4:])])
    before = snapshot(source.parent.parent), snapshot(batch_root), snapshot(prepared_root)
    resolver = FakeIdentityResolver(proposal)
    service = IdentityResolutionService(resolver, tmp_path / "identity")
    result = service.resolve(evidence)
    assert [g.identity_id for g in result.identities] == ["identity-0001", "identity-0002", "identity-0003"]
    assert [g.candidate_ids for g in result.identities] == [[ids[0]], [ids[1]], ids[2:4]]
    assert [g.reference_hint for g in result.identities[:2]] == ["V3", "V3"]
    assert [g.candidate_ids for g in result.unresolved] == [[cid] for cid in ids[4:]]
    assert [g.unresolved_id for g in result.unresolved] == [f"unresolved-{i:04d}" for i in range(1, len(ids) - 3)]
    assert (result.covered_candidate_count, result.resolved_candidate_count, result.unresolved_candidate_count) == (len(ids), 4, len(ids)-4)
    assert any(c.reference_hint is None for c in resolver.inputs[0].candidates)
    assert result.source_covered_discovery_sha256 == sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "identity" / evidence.batch_id / "identity.json"
    original = output.read_bytes()
    assert service.resolve(evidence) == result
    assert output.read_bytes() == original
    assert IdentityResolutionResult.model_validate_json(original) == result
    assert (snapshot(source.parent.parent), snapshot(batch_root), snapshot(prepared_root)) == before


@pytest.mark.parametrize("failure", ["omitted", "unknown", "double", "resolved-unresolved", "within-group", "within-unresolved", "empty", "technical", "assigned-id"])
def test_invalid_resolution_never_publishes_or_overwrites(artifacts, tmp_path, failure):
    evidence = artifacts[0]
    ids = [c.covered_candidate_id for c in evidence.candidates]
    groups = [group([cid]) for cid in ids]
    proposal = dict(groups=groups, unresolved=[])
    if failure == "omitted": groups.pop()
    elif failure == "unknown": groups.append(group(["covered-9999"]))
    elif failure == "double": groups.append(group([ids[0]]))
    elif failure == "resolved-unresolved": proposal["unresolved"] = [dict(candidate_ids=[ids[0]], reason="Ambiguous")]
    elif failure == "within-group": groups[0]["candidate_ids"].append(ids[0])
    elif failure == "within-unresolved":
        groups.pop(0)
        proposal["unresolved"] = [dict(candidate_ids=[ids[0]], reason="Ambiguous")] * 2
    elif failure == "empty": groups[0]["candidate_ids"] = []
    elif failure == "technical": groups[0]["quantity"] = 6
    elif failure == "assigned-id": groups[0]["identity_id"] = "identity-0001"
    root = tmp_path / "identity"
    service = IdentityResolutionService(FakeIdentityResolver(proposal), root)
    output = root / evidence.batch_id / "identity.json"
    for previous in (False, True):
        if previous:
            IdentityResolutionService(FakeIdentityResolver(), root).resolve(evidence)
        before = output.read_bytes() if output.exists() else None
        with pytest.raises((ValidationError, InvalidIdentityResolutionError)):
            service.resolve(evidence)
        assert (output.read_bytes() if output.exists() else None) == before
        assert not list(root.rglob(".staging-*"))


@pytest.mark.parametrize("failure", ["resolver", "write", "publish"])
@pytest.mark.parametrize("previous", [False, True])
def test_failures_preserve_prior_result_and_all_evidence(artifacts, tmp_path, monkeypatch, failure, previous):
    evidence, source, _, batch_root, prepared_root = artifacts
    root = tmp_path / "identity"
    service = IdentityResolutionService(FakeIdentityResolver(), root)
    if previous: service.resolve(evidence)
    before = snapshot(root), snapshot(source.parent.parent), snapshot(batch_root), snapshot(prepared_root)
    if failure == "resolver": service.resolver = FakeIdentityResolver(fail=True)
    elif failure == "write":
        original = Path.write_text
        def broken(path, *args, **kwargs):
            original(path, "partial")
            raise OSError("Simulated write failure")
        monkeypatch.setattr(Path, "write_text", broken)
    else:
        def broken(*args, **kwargs): raise OSError("Simulated publish failure")
        monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises((OSError, RuntimeError)): service.resolve(evidence)
    assert (snapshot(root), snapshot(source.parent.parent), snapshot(batch_root), snapshot(prepared_root)) == before
    assert not list(root.rglob(".staging-*"))


@pytest.mark.parametrize("invalid", ["missing", "json", "schema", "batch", "counts", "duplicate", "occurrence", "document", "page", "image-path"])
def test_invalid_or_missing_covered_and_references_fail_explicitly(artifacts, invalid):
    evidence, source, _, batch_root, prepared_root = artifacts
    data = json.loads(source.read_text())
    if invalid == "missing": source.unlink()
    elif invalid == "json": source.write_text("{")
    elif invalid == "schema": source.write_text("{}")
    elif invalid == "image-path":
        manifest = prepared_root / evidence.candidates[0].document_id / "manifest.json"
        document = json.loads(manifest.read_text())
        document["pages"][0]["image_path"] = "../../../outside.webp"
        manifest.write_text(json.dumps(document))
    else:
        if invalid == "batch": data["batch_id"] = "f" * 32
        elif invalid == "counts": data["total_candidates"] += 1
        elif invalid == "duplicate": data["candidates"][1]["covered_candidate_id"] = data["candidates"][0]["covered_candidate_id"]
        else:
            key = dict(occurrence="occurrence", document="document_id", page="page_number")[invalid]
            data["candidates"][0]["evidence"][key] = "f" * 16 if invalid == "document" else 99
        source.write_text(json.dumps(data))
    with pytest.raises((InvalidIdentityInputError, ValidationError, FileNotFoundError)):
        build_identity_input(evidence.batch_id, source.parent.parent, batch_root, prepared_root)


def test_empty_input_is_a_valid_empty_partition_and_resolver_cannot_mutate_lineage(tmp_path):
    evidence = IdentityInput(batch_id="a" * 32, source_covered_discovery_sha256="b" * 64, candidates=[])
    class MutatingResolver:
        def resolve(self, received):
            received.batch_id = "c" * 32
            received.source_covered_discovery_sha256 = "d" * 64
            return IdentityResolutionProposal(groups=[], unresolved=[])
    result = IdentityResolutionService(MutatingResolver(), tmp_path).resolve(evidence)
    assert result.batch_id == evidence.batch_id == "a" * 32
    assert result.source_covered_discovery_sha256 == evidence.source_covered_discovery_sha256 == "b" * 64
    assert result.covered_candidate_count == result.resolved_candidate_count == result.unresolved_candidate_count == 0


def test_group_can_span_documents_and_occurrences_without_collapsing_observations(artifacts, tmp_path):
    evidence = artifacts[0]
    ids = [c.covered_candidate_id for c in evidence.candidates]
    proposal = dict(groups=[group(list(reversed(ids)))], unresolved=[])
    result = IdentityResolutionService(FakeIdentityResolver(proposal), tmp_path / "identity").resolve(evidence)
    assert len(result.identities) == 1
    assert result.identities[0].candidate_ids == ids
    assert result.resolved_candidate_count == len(ids)
    assert result.unresolved_candidate_count == 0
