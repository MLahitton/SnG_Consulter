import json

import pytest

from test_openai_technical_extractor import sdk, technical_case, identity_review, review
from test_technical_inputs import snapshot
from test_technical_models import field, structure
from sng_consulter.preparation.models import TextSpan
from sng_consulter.technical.openai_extractor import OpenAITechnicalExtractor, TECHNICAL_INSTRUCTIONS


@pytest.mark.parametrize('concept',[
    'bind every attribute to the component that the document actually describes',
    'frame/profile, sill, threshold, hardware, wall, seal/gasket, decorative element',
    'Proximity in the same sentence or schedule row is not sufficient',
    'determine what the numeric value modifies before using it',
    'Never convert sill/frame/profile, threshold, hardware, wall or other component dimensions into glass thickness',
    "Unit normalization is valid only AFTER establishing the number's actual attribute and component",
    'Glass existence is not a glass specification',
    'Prefer UNKNOWN when attribute ownership is ambiguous',
    'Never infer glass type or thickness from window type, dimensions, opening system, size, pane count or typical construction practice',
    'what exact schedule/caption/note statement supports it',
    'Every EXPLICIT glass_specification must have a note identifying the actual supporting documentary statement and its scope',
    'Retain whole-set propagation only when the statement unambiguously specifies glass for all corresponding panes',
])
def test_attribute_ownership_prompt_rules(concept):
    assert concept in ' '.join(TECHNICAL_INSTRUCTIONS.split())


@pytest.mark.parametrize('statement,glass_statement,panes',[
    ('Tempered glass', 'Tempered glass', 1),
    ('9 mm glass', '9 mm glass', 1),
    ('black aluminium frame + 75 mm sill', None, 1),
    ('sliding glazed door + black sill 65 mm', None, 1),
    ('Tempered glass + black sill 65 mm', 'Tempered glass', 1),
    ('laminated glass 11 mm + aluminium frame 45 mm', 'laminated glass 11 mm', 1),
    ('All glass bodies: Tempered glass; frame finish black', 'Tempered glass', 3),
    ('glass/frame thickness 12 mm, component attribution unclear', None, 1),
    ('glazed assembly, sill 4 cm', None, 1),
    ('glazed assembly', None, 1),
])
def test_synthetic_attribute_ownership_responses_without_local_repair(
    technical_case, sdk, statement, glass_statement, panes,
):
    """Fake responses exercise transport/contracts; they do not evaluate model reasoning."""
    evidence = technical_case[0].model_copy(deep=True)
    identity = evidence.identities[0]
    cid = identity.candidate_ids[0]
    identity.pages[0].native_text_spans = [TextSpan(text=statement,
        bbox=dict(x0=22.125,y0=94.375,x1=48.625,y1=99.875))]
    proposal = structure(identity.identity_id, cid, panes=panes)
    for pane in proposal['modules'][0]['glass_panes']:
        pane['glass_specification'] = field() if glass_statement is None else field(
            glass_statement, 'EXPLICIT', [cid],
            f"Associated schedule explicitly states '{glass_statement}' for " +
            ('all glass bodies.' if panes > 1 else 'this glass pane.'))
    sdk.payloads[identity.identity_id] = proposal
    before = snapshot(technical_case[-1])
    result = OpenAITechnicalExtractor().extract(evidence).structures[0]
    assert result.model_dump(mode='json') == proposal
    assert len(sdk.requests) == len(evidence.identities)
    assert sdk.requests[0]['instructions'] == TECHNICAL_INSTRUCTIONS
    headers = [json.loads(i['text']) for i in sdk.requests[0]['input'][0]['content'][1:]
               if i['type'] == 'input_text']
    annotation = next(h['annotation_context'] for h in headers
                      if h['kind'] == 'associated_annotation_context')
    assert annotation['local_native_spans'] == [
        [statement,[22.125,94.375,48.625,99.875]]]
    full = next(h['native_text_spans'] for h in headers if h['kind'] == 'full_native_spans')
    assert full == annotation['local_native_spans']
    for pane in result.modules[0].glass_panes:
        spec = pane.glass_specification
        if glass_statement is None:
            assert spec.value is None and spec.resolution == 'UNKNOWN'
            assert spec.evidence_candidate_ids == []
        else:
            assert spec.value == glass_statement and spec.resolution == 'EXPLICIT'
            assert spec.evidence_candidate_ids == [cid]
            assert glass_statement in spec.note
    assert snapshot(technical_case[-1]) == before
