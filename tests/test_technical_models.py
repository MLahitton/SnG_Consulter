import pytest
from pydantic import ValidationError

from sng_consulter.technical.models import (
    ResolvedText, ResolvedPositiveInt, ResolvedNonNegativeInt, ResolvedPositiveNumber,
    ResolvedModuleFunction, ModuleFunction, TechnicalStructureProposal,
    ResolvedTechnicalScope, TechnicalScope, TechnicalStructure, Module,
)


def field(value=None, resolution="UNKNOWN", ids=None, note=None):
    return dict(value=value,resolution=resolution,evidence_candidate_ids=ids or [],note=note)


def known(value,cid="covered-0001"):
    return field(value,"EXPLICIT",[cid])


def dimensions(cid="covered-0001"):
    return dict(width_mm=known(1000,cid),height_mm=known(1200,cid))


def pane(cid="covered-0001"):
    return dict(position_hint=known("Local position",cid),dimensions=dimensions(cid),glass_specification=known("Documented specification",cid))


def decoration(cid="covered-0001"):
    return dict(kind=known("Decorative grid",cid),description=known("Several decorative cells",cid),position_hint=known("Surface",cid))


def structure(identity_id="identity-0001",cid="covered-0001",panes=3,decorations=0):
    return dict(identity_id=identity_id,scope=known("GLASS_RELATED",cid),reference=known("Provisional reference",cid),name=known("Item",cid),
        category=known("Documented category",cid),location=known("Documented location",cid),quantity=field(),
        dimensions=dimensions(cid),module_count=known(1,cid),modules=[dict(position_hint=known("Whole item",cid),dimensions=dimensions(cid),
            function=known("FIXED",cid),opening_direction=known("No opening",cid),glass_pane_count=known(panes,cid),
            glass_panes=[pane(cid) for _ in range(panes)],decorative_elements=[decoration(cid) for _ in range(decorations)])],notes=[])


@pytest.mark.parametrize("resolution,value,ids,note",[
    ("EXPLICIT",2,["covered-0001"],None), ("INFERRED",2,["covered-0001"],"Observable derivation"),
    ("DEFAULTED",1,[],"Approved rule: default quantity=1"), ("UNKNOWN",None,[],None),
    ("CONFLICT",None,["covered-0001","covered-0002"],"Incompatible quantities"),
])
def test_valid_resolution_semantics(resolution,value,ids,note):
    result = ResolvedPositiveInt(**field(value,resolution,ids,note))
    assert result.value == value and result.resolution == resolution


@pytest.mark.parametrize("data",[
    field(1,"DEFAULTED",["covered-0001"],"Default quantity=1"),field(1,"UNKNOWN"),field(1,"EXPLICIT"),
    field(1,"INFERRED",["covered-0001"]),field(1,"CONFLICT",["covered-0001"],"Conflict"),
    field(None,"EXPLICIT",["covered-0001"]),field(None,"INFERRED",["covered-0001"],"Inference"),
    field(None,"DEFAULTED",note="Default"),field(None,"UNKNOWN",["covered-0001"]),
    field(None,"CONFLICT",note="Conflict"),field(None,"CONFLICT",["covered-0001"]),
    field(1,"DEFAULTED",note=" "),field(1,"INFERRED",["covered-0001"]," "),
    field(1,"EXPLICIT",["covered-0001","covered-0001"]),
])
def test_invalid_resolution_semantics(data):
    with pytest.raises(ValidationError): ResolvedPositiveInt(**data)


@pytest.mark.parametrize("model,value",[
    (ResolvedPositiveInt,0),(ResolvedPositiveInt,-1),(ResolvedPositiveInt,True),(ResolvedPositiveInt,1.5),
    (ResolvedPositiveInt,"2"),(ResolvedNonNegativeInt,-1),(ResolvedNonNegativeInt,False),
    (ResolvedPositiveNumber,0),(ResolvedPositiveNumber,float("nan")),(ResolvedPositiveNumber,float("inf")),
    (ResolvedPositiveNumber,"10"),(ResolvedText,2),(ResolvedText,""),(ResolvedModuleFunction,"UNKNOWN"),
])
def test_resolved_values_keep_their_types(model,value):
    with pytest.raises(ValidationError): model(**known(value))


def test_module_unknown_function_and_zero_panes_are_supported():
    assert ResolvedModuleFunction(**field()).value is None
    assert ResolvedModuleFunction(**known("FIXED")).value == ModuleFunction.FIXED
    assert ResolvedNonNegativeInt(**known(0)).value == 0


@pytest.mark.parametrize("panes,decorations",[(3,0),(1,4)])
def test_structure_module_pane_and_decoration_are_distinct(panes,decorations):
    result = TechnicalStructureProposal(**structure(panes=panes,decorations=decorations))
    assert len(result.modules) == 1
    assert result.modules[0].function.value == "FIXED"
    assert result.modules[0].glass_pane_count.value == panes
    assert len(result.modules[0].glass_panes) == panes
    assert len(result.modules[0].decorative_elements) == decorations
    assert result.quantity.value is None and result.quantity.resolution == "UNKNOWN"


@pytest.mark.parametrize("extra",["system","price","module_id","resolution_status","missing_fields","confidence"])
def test_proposals_reject_downstream_or_service_owned_fields(extra):
    with pytest.raises(ValidationError): TechnicalStructureProposal(**structure(),**{extra:1})


def test_null_is_explicitly_required_even_when_unknown():
    with pytest.raises(ValidationError): ResolvedText(resolution="UNKNOWN",evidence_candidate_ids=[])


@pytest.mark.parametrize("resolution",["EXPLICIT","INFERRED","DEFAULTED"])
@pytest.mark.parametrize("matching",[True,False])
def test_known_module_count_requires_complete_matching_array(resolution,matching):
    data = structure()
    data["module_count"] = field(2,resolution,[] if resolution == "DEFAULTED" else ["covered-0001"],
                                 "Documented derivation or approved rule" if resolution != "EXPLICIT" else None)
    if matching: data["modules"].append(data["modules"][0])
    if matching:
        assert TechnicalStructureProposal(**data).module_count.value == 2
    else:
        with pytest.raises(ValidationError): TechnicalStructureProposal(**data)


@pytest.mark.parametrize("resolution",["UNKNOWN","CONFLICT"])
@pytest.mark.parametrize("known_elements",[False,True])
def test_uncertain_total_allows_known_partial_modules_or_panes(resolution,known_elements):
    data = structure()
    uncertain = field(None,resolution,["covered-0001"] if resolution == "CONFLICT" else [],
                      "Inconsistent total" if resolution == "CONFLICT" else None)
    data["module_count"] = uncertain
    data["modules"][0]["glass_pane_count"] = uncertain
    if not known_elements: data["modules"][0]["glass_panes"] = []
    result = TechnicalStructureProposal(**data)
    assert result.module_count.value is None
    assert result.modules[0].glass_pane_count.value is None
    data["modules"] = []
    assert TechnicalStructureProposal(**data).modules == []


@pytest.mark.parametrize("resolution",["EXPLICIT","INFERRED","DEFAULTED"])
@pytest.mark.parametrize("matching",[True,False])
def test_known_pane_count_requires_complete_matching_array(resolution,matching):
    data = structure(panes=3)
    data["modules"][0]["glass_pane_count"] = field(3,resolution,[] if resolution == "DEFAULTED" else ["covered-0001"],
        "Observable derivation or approved rule" if resolution != "EXPLICIT" else None)
    if not matching: data["modules"][0]["glass_panes"].pop()
    if matching: assert TechnicalStructureProposal(**data).modules[0].glass_pane_count.value == 3
    else:
        with pytest.raises(ValidationError): TechnicalStructureProposal(**data)


@pytest.mark.parametrize("value,resolution,ids,note",[
    ("GLASS_RELATED","EXPLICIT",["covered-0001"],None),
    ("NOT_GLASS_RELATED","INFERRED",["covered-0001"],"Observable non-glass item"),
    (None,"UNKNOWN",[],None),(None,"CONFLICT",["covered-0001"],"Incompatible scope evidence"),
    ("GLASS_RELATED","DEFAULTED",[],"Explicit approved rule; not applied by service"),
])
def test_scope_uses_existing_resolution_semantics(value,resolution,ids,note):
    result = ResolvedTechnicalScope(**field(value,resolution,ids,note))
    assert result.value == value and result.resolution == resolution


@pytest.mark.parametrize("data",[
    known("UNCERTAIN"),known("UNKNOWN"),field("GLASS_RELATED","EXPLICIT"),
    field("NOT_GLASS_RELATED","INFERRED",["covered-0001"]),
    field("GLASS_RELATED","UNKNOWN"),field(None,"CONFLICT"),
    field("GLASS_RELATED","DEFAULTED",["covered-0001"],"Rule"),
])
def test_invalid_scope_is_rejected(data):
    with pytest.raises(ValidationError): ResolvedTechnicalScope(**data)


def test_new_fields_required_no_defaults_and_scope_enum_is_closed():
    assert set(TechnicalScope) == {TechnicalScope.GLASS_RELATED,TechnicalScope.NOT_GLASS_RELATED}
    for name in ["scope","module_count"]:
        data = structure()
        del data[name]
        with pytest.raises(ValidationError): TechnicalStructureProposal(**data)


def test_persisted_models_inherit_count_validation():
    data = structure(panes=0)
    module = {**data["modules"][0],"module_id":"module-0001"}
    assert Module(**module).glass_pane_count.value == 0
    module["glass_pane_count"] = known(1)
    with pytest.raises(ValidationError): Module(**module)
    data.update(modules=[],module_count=known(0),technical_structure_id="technical-structure-0001",
                resolution_status="PARTIAL",missing_fields=["quantity"])
    assert TechnicalStructure(**data).module_count.value == 0
    data["module_count"] = known(1)
    with pytest.raises(ValidationError): TechnicalStructure(**data)
