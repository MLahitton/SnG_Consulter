import json
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from test_discovery_review import review, snapshot
from sng_consulter.discovery.consolidation import CoveredCandidate, CoveredDiscoveryResult
from sng_consulter.discovery.models import DiscoveryResult
from sng_consulter.identity import build_identity_input, IdentityResolutionService
from sng_consulter.review import routes


@pytest.fixture
def identity_review(review, monkeypatch):
    client, batch_id, document, discovery_path, root = review
    monkeypatch.setattr(routes, "IDENTITY_DIR", root / "identity")
    discovery = DiscoveryResult.model_validate_json(discovery_path.read_bytes())
    covered = CoveredDiscoveryResult(batch_id=batch_id, candidates=[CoveredCandidate(
        covered_candidate_id=f"covered-{i:04d}", origin="DISCOVERY", source_candidate_id=c.candidate_id,
        reference_hint=c.reference_hint, description=c.description, glass_relevance=c.glass_relevance, evidence=c.evidence,
    ) for i,c in enumerate(discovery.candidates, 1)], discovery_count=4, coverage_added_count=0, total_candidates=4)
    source = discovery_path.parent / "covered_discovery.json"
    source.write_text(covered.model_dump_json(), encoding="utf-8")
    # Distinct page colors make incorrect page selection observable.
    for page in document.pages:
        with Image.new("RGB", (200,160), "red" if page.page_number == 1 else "green") as image:
            if page.page_number == 1:
                ImageDraw.Draw(image).rectangle((100,0,199,159), fill="blue")
            image.save(document.output_dir / page.image_path, format="WEBP", lossless=True)
    evidence = build_identity_input(batch_id, routes.DISCOVERY_DIR, routes.BATCH_DIR, routes.PREPARED_DIR)

    class Resolver:
        def resolve(self, evidence):
            return dict(groups=[dict(candidate_ids=["covered-0001","covered-0003"], reference_hint="<script>",
                description="Related views", reason="Observable correspondence"),
                dict(candidate_ids=["covered-0004"], reference_hint=None, description="Distinct provisional design", reason="Separate evidence")],
                unresolved=[dict(candidate_ids=["covered-0002"], reason="Ambiguous association")])

    IdentityResolutionService(Resolver(), routes.IDENTITY_DIR).resolve(evidence)
    path = routes.IDENTITY_DIR / batch_id / "identity.json"
    return client, batch_id, document, source, path, root


def test_identity_and_unresolved_all_candidates_correct_page_crops_and_no_writes(identity_review, monkeypatch):
    client, batch_id, _, _, _, root = identity_review
    before = snapshot(root)
    batch_url = f"/review/batches/{batch_id}"
    url = batch_url + "/identity"
    assert f'href="{url}">Revisar Identity</a>' in client.get(batch_url).text
    response = client.get(url)
    assert response.status_code == 200
    for text in ["identity-0001", "identity-0002", "unresolved-0001", "Related views", "Observable correspondence",
                 "Distinct provisional design", "Separate evidence", "Ambiguous association", "Unresolved", "Sin referencia"]:
        assert text in response.text
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    from sng_consulter.identity import visuals
    converter = visuals.normalized_region_to_render_bbox
    calls = []
    def track(*args):
        calls.append(args)
        return converter(*args)
    monkeypatch.setattr(visuals, "normalized_region_to_render_bbox", track)
    for i, color in enumerate([(255,0,0),(0,0,255),(0,128,0),(255,0,0)],1):
        cid = f"covered-{i:04d}"
        assert f'<strong>{cid}</strong>' in response.text
        crop_url = url + f"/candidates/{cid}/crop"
        assert f'src="{crop_url}"' in response.text
        assert f'alt="{cid}"' in response.text
        crop = client.get(crop_url)
        assert crop.status_code == 200
        assert crop.headers["content-type"] == "image/png"
        assert crop.headers["cache-control"] == "no-store"
        with Image.open(BytesIO(crop.content)) as image:
            image.load()
            assert image.getpixel((image.width//2, image.height//2)) == color
            if i in {1,3,4}:
                assert image.size == (66,44)
                assert image.getextrema() == tuple((v,v) for v in color)
    assert len(calls) == 4
    assert snapshot(root) == before


@pytest.mark.parametrize("invalid", ["lineage", "unknown", "missing", "double", "within-group", "batch", "counts", "group-id", "json"])
def test_invalid_identity_rejected_before_view_or_crop(identity_review, invalid):
    client, batch_id, _, _, path, root = identity_review
    data = json.loads(path.read_text())
    if invalid == "lineage": data["source_covered_discovery_sha256"] = "f" * 64
    elif invalid == "unknown": data["identities"][0]["candidate_ids"][0] = "covered-9999"
    elif invalid == "missing": data["unresolved"] = []
    elif invalid == "double": data["unresolved"][0]["candidate_ids"].append("covered-0001")
    elif invalid == "within-group": data["identities"][0]["candidate_ids"].append("covered-0001")
    elif invalid == "batch": data["batch_id"] = "f" * 32
    elif invalid == "counts": data["resolved_candidate_count"] = 99
    elif invalid == "group-id": data["identities"][1]["identity_id"] = "identity-0001"
    path.write_text("{" if invalid == "json" else json.dumps(data))
    before = snapshot(root)
    for suffix in ["", "/candidates/covered-0001/crop"]:
        response = client.get(f"/review/batches/{batch_id}/identity" + suffix)
        assert response.status_code == 409
        if invalid == "lineage": assert "desactualizado" in response.json()["detail"]
    assert snapshot(root) == before


def test_current_covered_bytes_change_invalidates_identity(identity_review):
    client,batch_id,_,source,_,_ = identity_review
    source.write_bytes(source.read_bytes() + b"\n")
    response = client.get(f"/review/batches/{batch_id}/identity")
    assert response.status_code == 409
    assert "CoveredDiscovery" in response.json()["detail"]


@pytest.mark.parametrize("invalid", ["occurrence", "document", "page", "region", "image-path"])
def test_invalid_candidate_evidence_or_path_is_rejected(identity_review, invalid):
    client,batch_id,document,source,path,root = identity_review
    data = json.loads(source.read_text())
    if invalid == "image-path":
        manifest = document.output_dir / "manifest.json"
        prepared = json.loads(manifest.read_text())
        outside = root / "secret.webp"
        outside.write_bytes(b"must-not-be-read")
        prepared["pages"][0]["image_path"] = str(outside)
        manifest.write_text(json.dumps(prepared))
    else:
        evidence = data["candidates"][0]["evidence"]
        if invalid == "region": evidence["region"]["x1"] = 2
        elif invalid == "document": evidence["document_id"] = "f" * 16
        else: evidence["page_number" if invalid == "page" else "occurrence"] = 99
        source.write_text(json.dumps(data))
        identity = json.loads(path.read_text())
        identity["source_covered_discovery_sha256"] = sha256(source.read_bytes()).hexdigest()
        path.write_text(json.dumps(identity))
    before = snapshot(root)
    assert client.get(f"/review/batches/{batch_id}/identity").status_code in {404,409}
    assert client.get(f"/review/batches/{batch_id}/identity/candidates/covered-0001/crop").status_code in {404,409}
    assert snapshot(root) == before


def test_missing_identity_covered_batch_candidate_and_unsafe_paths(identity_review):
    client,batch_id,_,source,path,_ = identity_review
    base = f"/review/batches/{batch_id}/identity"
    assert client.get(base + "/candidates/covered-9999/crop").status_code == 404
    for unsafe in ["not-a-batch", "..%5Coutside", "f" * 32]:
        assert client.get(f"/review/batches/{unsafe}/identity").status_code == 404
    assert client.get(base + "/candidates/..%5Coutside/crop").status_code == 404
    source.unlink()
    assert client.get(base).status_code == 404
    path.unlink()
    assert client.get(base).status_code == 404
    summary = client.get(f"/review/batches/{batch_id}")
    assert summary.status_code == 200
    assert "Revisar Identity" not in summary.text
