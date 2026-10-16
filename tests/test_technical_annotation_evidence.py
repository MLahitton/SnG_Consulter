import base64
import json
from io import BytesIO

import pytest
from PIL import Image

from test_openai_technical_extractor import sdk, technical_case, identity_review, review
from test_technical_inputs import snapshot
from test_technical_models import field, known, structure
from sng_consulter.discovery.models import NormalizedRegion
from sng_consulter.preparation.models import TextSpan
from sng_consulter.technical import visuals
from sng_consulter.technical.openai_extractor import OpenAITechnicalExtractor, TECHNICAL_INSTRUCTIONS


def annotation_images(identity):
    return [i for i in visuals.build_technical_images(identity)
            if i.metadata['kind'] == 'associated_annotation_context']


def test_annotation_union_shared_with_identity_context_and_isolated(technical_case, monkeypatch):
    identity = technical_case[0].identities[0].model_copy(deep=True)
    page = identity.pages[0]
    identity.candidates.append(identity.candidates[0].model_copy(update=dict(
        region=NormalizedRegion(x0=.6, y0=.3, x1=.85, y1=.7))))
    # Same visible page number alone cannot establish shared evidence.
    for change in [dict(occurrence=42), dict(document_id='f' * 16)]:
        identity.candidates.append(identity.candidates[0].model_copy(update=dict(
            **change, region=NormalizedRegion(x0=0,y0=0,x1=1,y1=1))))
    calls = []
    converter = visuals.normalized_region_to_render_bbox
    def track(*args):
        calls.append(args)
        return converter(*args)
    monkeypatch.setattr(visuals, 'normalized_region_to_render_bbox', track)
    images = visuals.build_technical_images(identity)
    # Each matching candidate is converted once for BOTH contexts; other pages stay separate.
    assert len(calls) == 3
    first = next(i for i in images if i.metadata['kind'] == 'identity_page_context')
    annotation = next(i for i in images if i.metadata['kind'] == 'associated_annotation_context')
    assert first.metadata['context_bbox'] == [8,30,182,122]
    assert annotation.metadata['annotation_context']['context_bbox'] == [4,30,186,132]
    assert annotation.metadata['render_size'] == [page.width_px,page.height_px]


@pytest.mark.parametrize('region,expected',[
    ((.1,.25,.4,.5),[4,30,96,100]),
    ((0,0,.1,.1),[0,0,36,36]),
    ((.9,.9,1,1),[164,134,200,160]),
    ((0,0,1,1),[0,0,200,160]),
])
def test_annotation_margins_rounding_and_clipping(technical_case,region,expected):
    identity = technical_case[0].identities[0].model_copy(deep=True)
    identity.pages = identity.pages[:1]
    identity.candidates = identity.candidates[:1]
    identity.candidates[0].region = NormalizedRegion(**dict(zip(['x0','y0','x1','y1'],region)))
    contexts = annotation_images(identity)
    assert len(contexts) == 1
    assert contexts[0].metadata['annotation_context']['context_bbox'] == expected


def test_annotation_spans_pixels_determinism_and_no_writes(technical_case):
    identity = technical_case[0].identities[0].model_copy(deep=True)
    page = identity.pages[0]
    # Below the old context, but inside the new annotation context; no semantic filtering.
    text = '  Synthetic schedule: Tempered glass\n'
    page.native_text_spans = [
        TextSpan(text=text,bbox=dict(x0=22.125,y0=94.375,x1=48.625,y1=99.875)),
        TextSpan(text='unrelated',bbox=dict(x0=130,y0=120,x1=180,y1=140)),
        TextSpan(text='boundary only',bbox=dict(x0=22,y0=100,x1=48,y1=110)),
    ]
    before = snapshot(technical_case[-1])
    images = visuals.build_technical_images(identity)
    assert images == visuals.build_technical_images(identity)
    annotations = [i for i in images if i.metadata['kind'] == 'associated_annotation_context']
    assert [i.metadata['page_number'] for i in annotations] == [1,2]
    assert annotations[0].metadata['annotation_context']['local_native_spans'] == [
        [text,[22.125,94.375,48.625,99.875]]]
    original = [i for i in images if i.metadata['kind'] == 'identity_page_context']
    assert original[0].metadata['local_native_spans'] == []
    # Retain the exact previous context pixels/PNG encoding, in addition to the new crop.
    with Image.open(page.image_path) as source, source.crop((8,30,92,90)) as crop, BytesIO() as buffer:
        crop.save(buffer,format='PNG')
        assert original[0].data == buffer.getvalue()
    for image,color in zip(annotations,[(255,0,0),(0,128,0)]):
        with Image.open(BytesIO(image.data)) as crop:
            assert crop.format == 'PNG' and crop.size == (92,70)
            assert crop.getpixel((20,20)) == color
    assert snapshot(technical_case[-1]) == before


def test_request_retains_evidence_one_annotation_per_page_high_detail(technical_case,sdk):
    evidence = technical_case[0]
    OpenAITechnicalExtractor().extract(evidence)
    assert len(sdk.requests) == len(evidence.identities)
    for identity,request in zip(evidence.identities,sdk.requests):
        content = request['input'][0]['content']
        headers = [json.loads(i['text']) for i in content[1:] if i['type'] == 'input_text']
        kinds = ['candidate_crop'] * len(identity.candidates)
        kinds += ['identity_page_context','associated_annotation_context','full_page','full_native_spans'] * len(identity.pages)
        assert [h['kind'] for h in headers] == kinds
        annotations = [h for h in headers if h['kind'] == 'associated_annotation_context']
        assert [(h['document_id'],h['occurrence'],h['page_number']) for h in annotations] == [
            (p.document_id,p.occurrence,p.page_number) for p in identity.pages]
        for index,item in enumerate(content):
            if item['type'] == 'input_image':
                assert item['detail'] == 'high'
                header = json.loads(content[index-1]['text'])
                if header['kind'] == 'associated_annotation_context':
                    assert 'annotation_context' in header
                    with Image.open(BytesIO(base64.b64decode(item['image_url'].split(',',1)[1]))) as crop:
                        assert crop.format == 'PNG'
        assert 'image_path' not in json.dumps(content)


@pytest.mark.parametrize('concept',[
    'explicitly inspect any associated schedule/table/caption',
    'quantity may come from that schedule',
    'Glass specification may come from\nthat schedule',
    'legible schedule text is EXPLICIT documentary evidence',
    'all corresponding GlassPane proposals as\nEXPLICIT',
    'Do not invent missing thickness',
    'bodies does not equal module count',
    'Do not select SnG commercial systems',
    'Never use a neighboring table\nmerely because it is inside the crop',
    'Do not propagate when the schedule\ndistinguishes specifications by pane/body',
])
def test_schedule_prompt_rules(concept):
    assert concept in TECHNICAL_INSTRUCTIONS


@pytest.mark.parametrize('case',[
    'one-pane','all-panes','no-thickness','quantity','glass-bodies','neighbor','ambiguous',
    'different-specifications','conflicting-specifications',
])
def test_synthetic_schedule_responses_preserved_without_local_interpretation(technical_case,sdk,case):
    evidence = technical_case[0].model_copy(deep=True)
    identity = evidence.identities[0]
    cid = identity.candidate_ids[0]
    count = 5 if case == 'glass-bodies' else 2 if case in {
        'all-panes','different-specifications','conflicting-specifications'} else 1
    data = structure(identity.identity_id,cid,panes=count)
    text = 'Tempered glass'
    if case == 'quantity':
        text = 'Quantity: 2'
        data['quantity'] = known(2,cid)
    elif case == 'glass-bodies':
        text = '5 glass bodies'
    elif case == 'neighbor':
        text = 'Neighbor reference: unrelated item. Laminated glass'
    elif case == 'ambiguous':
        text = 'Ambiguous schedule association'
    elif case == 'different-specifications':
        text = 'First pane: Tempered glass; second pane: Laminated glass'
    elif case == 'conflicting-specifications':
        text = 'Conflicting schedules: Tempered glass / Laminated glass'
    note = 'Associated schedule explicitly specifies Tempered glass for this complete set.'
    for index,pane in enumerate(data['modules'][0]['glass_panes']):
        pane['glass_specification'] = field('Tempered glass','EXPLICIT',[cid],note)
        if case in {'neighbor','ambiguous','quantity','glass-bodies'}:
            pane['glass_specification'] = field()
        elif case == 'different-specifications':
            pane['glass_specification'] = known('Tempered glass' if index == 0 else 'Laminated glass',cid)
        elif case == 'conflicting-specifications':
            pane['glass_specification'] = field(None,'CONFLICT',[cid],'Associated schedules specify incompatible glass.')
    sdk.payloads[identity.identity_id] = data
    identity.pages[0].native_text_spans = [TextSpan(text=text,bbox=dict(x0=22,y0=94,x1=48,y1=99))]
    before = snapshot(technical_case[-1])
    result = OpenAITechnicalExtractor().extract(evidence).structures[0]
    assert result.model_dump(mode='json') == data
    assert result.module_count.value == 1 and result.modules[0].glass_pane_count.value == count
    if case == 'no-thickness':
        assert result.modules[0].glass_panes[0].glass_specification.value == 'Tempered glass'
    headers = [json.loads(i['text']) for i in sdk.requests[0]['input'][0]['content'][1:] if i['type'] == 'input_text']
    local = next(h['annotation_context']['local_native_spans'] for h in headers if h['kind'] == 'associated_annotation_context')
    assert local == [[text,[22,94,48,99]]]
    assert snapshot(technical_case[-1]) == before
