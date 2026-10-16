import base64
import json
from io import BytesIO

import pytest
from PIL import Image

from test_openai_technical_extractor import sdk, technical_case, identity_review, review
from test_technical_inputs import snapshot
from test_technical_models import field, known
from sng_consulter.preparation.models import TextSpan
from sng_consulter.discovery.models import NormalizedRegion
from sng_consulter.technical import visuals
from sng_consulter.technical.openai_extractor import OpenAITechnicalExtractor, TECHNICAL_INSTRUCTIONS


def test_context_union_margin_clipping_and_canonical_converter(technical_case, monkeypatch):
    identity = technical_case[0].identities[0].model_copy(deep=True)
    page = identity.pages[0]
    original = visuals.normalized_region_to_render_bbox
    calls = []
    def track(region, width, height):
        calls.append(region)
        return original(region, width, height)
    monkeypatch.setattr(visuals, 'normalized_region_to_render_bbox', track)
    # A different page and occurrence must never expand this union.
    unrelated = identity.candidates[0].model_copy(update=dict(occurrence=42,
        region=NormalizedRegion(x0=0,y0=0,x1=1,y1=1)))
    extra = identity.candidates[0].model_copy(update=dict(
        region=NormalizedRegion(x0=.6,y0=.3,x1=.85,y1=.7)))
    identity.candidates.extend([unrelated, extra])
    assert visuals.identity_page_context_bounds(identity, page) == (8,30,182,122)
    assert len(calls) == 2
    identity.candidates[0].region = NormalizedRegion(x0=0,y0=0,x1=1,y1=1)
    assert visuals.identity_page_context_bounds(identity, page) == (0,0,200,160)


def test_context_spans_precision_pixels_determinism_and_no_writes(technical_case):
    identity = technical_case[0].identities[0].model_copy(deep=True)
    page = identity.pages[0]
    span = TextSpan(text='  1.65 m\n',bbox=dict(x0=7.125,y0=39.375,x1=22.625,y1=43.875))
    outside = TextSpan(text='outside',bbox=dict(x0=150,y0=120,x1=180,y1=130))
    touching = TextSpan(text='boundary',bbox=dict(x0=0,y0=30,x1=8,y1=40))
    page.native_text_spans = [span,outside,touching]
    before = snapshot(technical_case[-1])
    images = visuals.build_technical_images(identity)
    assert images == visuals.build_technical_images(identity)
    contexts = [i for i in images if i.metadata['kind'] == 'identity_page_context']
    assert len(contexts) == 2
    assert contexts[0].metadata['local_native_spans'] == [[span.text,[7.125,39.375,22.625,43.875]]]
    assert contexts[1].metadata['local_native_spans'] == []
    with Image.open(BytesIO(contexts[0].data)) as crop:
        assert crop.format == 'PNG' and crop.size == (84,60)
        assert crop.getpixel((20,20)) == (255,0,0)
    with Image.open(BytesIO(contexts[1].data)) as crop:
        assert crop.getpixel((20,20)) == (0,128,0)
    assert snapshot(technical_case[-1]) == before


def test_request_evidence_order_and_one_context_per_page(technical_case,sdk):
    OpenAITechnicalExtractor().extract(technical_case[0])
    for identity, request in zip(technical_case[0].identities,sdk.requests):
        content = request['input'][0]['content']
        assert list(json.loads(content[0]['text'])) == ['identity','candidates']
        expected = ['candidate_crop'] * len(identity.candidates)
        expected += ['identity_page_context','associated_annotation_context','full_page','full_native_spans'] * len(identity.pages)
        headers = [json.loads(i['text']) for i in content[1:] if i['type'] == 'input_text']
        assert [h['kind'] for h in headers] == expected
        for index, item in enumerate(content):
            if item['type'] == 'input_image':
                assert item['detail'] == 'high'
                header = json.loads(content[index-1]['text'])
                if header['kind'] == 'identity_page_context':
                    assert 'local_native_spans' in header
                    with Image.open(BytesIO(base64.b64decode(item['image_url'].split(',',1)[1]))) as image:
                        x0,y0,x1,y1 = header['context_bbox']
                        assert image.size == (x1-x0,y1-y0)


@pytest.mark.parametrize('concept',[
    'implicit document unit convention', 'ENTIRE dimensional field INFERRED',
    'Do not require a unit printed beside every dimension', 'Never estimate from pixels',
    'Structure dimensions, Module dimensions and GlassPane dimensions are separate',
    'Do not use project\ntitle-block location as Structure.location',
    'Never assume metres\nfrom an isolated number', 'Resolve width and height independently',
    'never borrow a neighboring detail', 'divide total by module count',
    'frame, opening or module\nsize is not finished glass size',
])
def test_dimension_prompt_rules(concept):
    assert concept in TECHNICAL_INSTRUCTIONS


@pytest.mark.parametrize('case',['explicit','implicit','isolated','module-unknown','partial','pane-unknown','diameter'])
def test_synthetic_dimension_outputs_preserved_without_local_inference(technical_case,sdk,case):
    evidence = technical_case[0].model_copy(deep=True)
    identity = evidence.identities[0]
    cid = identity.candidate_ids[0]
    payload = sdk.payloads[identity.identity_id]
    text = {'explicit':'1.65 m', 'implicit':'.75 / 1.35 / 2.65 / 4.85',
            'isolated':'2.65', 'diameter':'diameter 1480 mm'}.get(case,'3250 mm')
    identity.pages[0].native_text_spans = [TextSpan(text=text,bbox=dict(x0=25,y0=45,x1=45,y1=50))]
    payload['dimensions'] = dict(width_mm=known(3250,cid),height_mm=field())
    if case == 'explicit':
        payload['dimensions']['width_mm'] = field(1650,'EXPLICIT',[cid],'Explicit 1.65 m normalized to 1650 mm.')
    elif case == 'implicit':
        payload['dimensions']['width_mm'] = field(2650,'INFERRED',[cid],
            'Multiple coherent decimal dimensions in this detail support metres; unit inferred, 2.65 normalized to 2650 mm.')
    elif case == 'isolated':
        payload['dimensions']['width_mm'] = field()
    elif case == 'module-unknown':
        payload['modules'][0]['dimensions'] = dict(width_mm=field(),height_mm=field())
    elif case == 'pane-unknown':
        for pane in payload['modules'][0]['glass_panes']:
            pane['dimensions'] = dict(width_mm=field(),height_mm=field())
    elif case == 'diameter':
        payload['dimensions'] = {axis:field(1480,'EXPLICIT',[cid],
            'Explicit diameter used as both bounding width and height.') for axis in ['width_mm','height_mm']}
    result = OpenAITechnicalExtractor().extract(evidence).structures[0]
    assert result.dimensions.model_dump(mode='json') == payload['dimensions']
    assert [m.dimensions.model_dump(mode='json') for m in result.modules] == [m['dimensions'] for m in payload['modules']]
    assert text in json.dumps(sdk.requests[0]['input'])
