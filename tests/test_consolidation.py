import json
from hashlib import sha256
from pathlib import Path

import pytest

from sng_consulter.discovery.consolidation import (
    CoveredDiscoveryResult, CoveredDiscoveryService, InvalidConsolidationInputError,
)
from sng_consulter.discovery.__main__ import main

BATCH = "a" * 32


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "runtime/discovery"
    directory = root / BATCH
    directory.mkdir(parents=True)
    region = dict(x0=0.1, y0=0.2, x1=0.4, y1=0.5)
    evidence = dict(document_id="b" * 16, occurrence=2, page_number=1, region=region)
    common = dict(reference_hint="same hint", description="Possible glazed opening", glass_relevance="POSSIBLE")
    discovery = dict(batch_id=BATCH, pages_analyzed=1, candidates=[
        dict(**common, candidate_id=f"candidate-{i:04d}",
             evidence=dict(**evidence, evidence_kind="COMBINED")) for i in range(1, 4)])
    coverage = dict(batch_id=BATCH, pages_audited=1, missing_candidates=[
        dict(**common, **evidence, reason=f"Separate omitted object {i}",
             missing_candidate_id=f"coverage-{i:04d}") for i in range(1, 3)])
    for name, data in [("discovery.json", discovery), ("coverage_audit.json", coverage)]:
        if name == "coverage_audit.json":
            coverage["audited_discovery_sha256"] = sha256((directory / "discovery.json").read_bytes()).hexdigest()
        (directory / name).write_text(json.dumps(data), encoding="utf-8")
    return root, directory, discovery, coverage


@pytest.mark.parametrize("added", [0, 2])
def test_counts_order_origin_complete_provenance_and_deterministic_bytes(inputs, added):
    root, directory, discovery, coverage = inputs
    coverage["missing_candidates"] = coverage["missing_candidates"][:added]
    (directory / "coverage_audit.json").write_text(json.dumps(coverage))
    originals = {p: p.read_bytes() for p in directory.iterdir()}
    service = CoveredDiscoveryService(root)
    result = service.consolidate(BATCH)
    assert (result.discovery_count, result.coverage_added_count, result.total_candidates) == (3, added, 3 + added)
    assert [c.covered_candidate_id for c in result.candidates] == [f"covered-{i:04d}" for i in range(1, 4 + added)]
    assert [c.origin for c in result.candidates] == ["DISCOVERY"] * 3 + ["COVERAGE_AUDIT"] * added
    for source, candidate in zip(discovery["candidates"], result.candidates[:3]):
        data = candidate.model_dump(mode="json")
        assert data.pop("source_candidate_id") == source["candidate_id"]
        data.pop("covered_candidate_id")
        data.pop("origin")
        assert data == {k: v for k, v in source.items() if k != "candidate_id"}
    for source, candidate in zip(coverage["missing_candidates"], result.candidates[3:]):
        assert candidate.source_candidate_id == source["missing_candidate_id"]
        assert candidate.evidence.model_dump(mode="json") == {k: source[k] for k in
            ("document_id", "occurrence", "page_number", "region", "reason")}
        assert candidate.reference_hint == source["reference_hint"]
        assert candidate.description == source["description"]
    assert all(c.reference_hint == "same hint" for c in result.candidates)
    output = directory / "covered_discovery.json"
    before = output.read_bytes()
    assert CoveredDiscoveryResult.model_validate_json(before) == result
    assert service.consolidate(BATCH) == result
    assert output.read_bytes() == before
    assert all(p.read_bytes() == content for p, content in originals.items())


@pytest.mark.parametrize("name", ["discovery.json", "coverage_audit.json"])
@pytest.mark.parametrize("failure", ["missing", "json", "schema", "batch", "pages"])
def test_invalid_required_inputs_fail_explicitly(inputs, name, failure):
    root, directory, _, _ = inputs
    path = directory / name
    data = json.loads(path.read_text())
    if failure == "missing": path.unlink()
    elif failure == "json": path.write_text("{")
    elif failure == "schema": path.write_text("{}")
    else:
        data["batch_id" if failure == "batch" else
             ("pages_analyzed" if name == "discovery.json" else "pages_audited")] = "c" * 32 if failure == "batch" else 99
        path.write_text(json.dumps(data))
    target = directory / "covered_discovery.json"
    target.write_bytes(b"previous")
    with pytest.raises(FileNotFoundError if failure == "missing" else InvalidConsolidationInputError):
        CoveredDiscoveryService(root).consolidate(BATCH)
    assert target.read_bytes() == b"previous"


@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("failure", ["write", "replace"])
def test_persistence_failure_cleans_staging_and_preserves_prior(inputs, monkeypatch, previous, failure):
    root, directory, _, _ = inputs
    service = CoveredDiscoveryService(root)
    target = directory / "covered_discovery.json"
    if previous: service.consolidate(BATCH)
    before = target.read_bytes() if previous else None
    originals = {p: p.read_bytes() for p in directory.iterdir()}
    original_write = Path.write_text
    original_replace = Path.replace

    def broken_write(path, *args, **kwargs):
        if path.name == "covered_discovery.json":
            original_write(path, "partial")
            raise OSError("Simulated disk error")
        return original_write(path, *args, **kwargs)

    def broken_replace(path, *args, **kwargs):
        if path.name == "covered_discovery.json": raise OSError("Simulated publish error")
        return original_replace(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text" if failure == "write" else "replace",
                        broken_write if failure == "write" else broken_replace)
    with pytest.raises(OSError): service.consolidate(BATCH)
    assert (target.read_bytes() if target.exists() else None) == before
    assert not list(directory.glob(".staging-*"))
    assert all(p.read_bytes() == content for p, content in originals.items())


def test_cli_is_offline_and_reads_only_saved_json(inputs, monkeypatch, tmp_path, capsys):
    from sng_consulter.discovery import __main__ as command
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    def forbidden(*args, **kwargs): pytest.fail("Consolidation must not analyze or read preparations")
    for name in ("build_discovery_input", "OpenAIPageAnalyzer", "OpenAICoverageAuditor"):
        monkeypatch.setattr(command, name, forbidden)
    assert main(["consolidate", BATCH]) == 0
    assert "3 Discovery + 2 Coverage = 5 candidates" in capsys.readouterr().out
    (inputs[1] / "coverage_audit.json").unlink()
    assert main(["consolidate", BATCH]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err


def test_unsafe_batch_is_rejected(inputs):
    with pytest.raises(InvalidConsolidationInputError):
        CoveredDiscoveryService(inputs[0]).consolidate("../escape")


@pytest.mark.parametrize("change", ["content", "whitespace", "legacy", "invalid_hash"])
def test_stale_or_incompatible_audit_preserves_all_files(inputs, change):
    root, directory, discovery, coverage = inputs
    service = CoveredDiscoveryService(root)
    service.consolidate(BATCH)
    if change == "content":
        discovery["candidates"][0]["description"] = "Changed observation"
        (directory / "discovery.json").write_text(json.dumps(discovery))
    elif change == "whitespace":
        path = directory / "discovery.json"
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        if change == "legacy": del coverage["audited_discovery_sha256"]
        else: coverage["audited_discovery_sha256"] = "not-a-sha256"
        (directory / "coverage_audit.json").write_text(json.dumps(coverage))
    before = {p: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(InvalidConsolidationInputError, match="outdated.*rerun" if change in
                       {"content", "whitespace"} else "contract"):
        service.consolidate(BATCH)
    assert {p: p.read_bytes() for p in directory.iterdir()} == before
