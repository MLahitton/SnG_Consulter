import pytest

from sng_consulter.preparation import deduplicate_text_spans
from sng_consulter.preparation.models import BoundingBox, TextSpan


def span(text="HARAS", offset=0, **indices):
    return TextSpan(text=text, bbox=BoundingBox(
        x0=10 + offset, y0=20 + offset, x1=50 + offset, y1=30 + offset,
    ), **indices)


@pytest.mark.parametrize("offset", [0, 0.005])
def test_same_text_and_matching_bbox_keep_first(offset):
    first = span(block_index=0, line_index=1, span_index=2)
    duplicate = span(offset=offset, block_index=9, line_index=8, span_index=7)
    result = deduplicate_text_spans([first, duplicate])
    assert len(result) == 1
    assert result[0] is first


def test_same_text_at_different_positions_is_preserved():
    spans = [span(), span(offset=0.02), span(offset=100)]
    assert deduplicate_text_spans(spans) == spans


def test_exact_text_required_even_at_identical_position():
    spans = [span("HARAS"), span("haras"), span("HARAS "), span("OTHER")]
    assert deduplicate_text_spans(spans) == spans


def test_order_first_occurrence_and_original_objects_are_preserved():
    spans = [span("A"), span("B"), span("A", offset=0.005), span("A", offset=100), span("B")]
    before = [item.model_dump() for item in spans]
    result = deduplicate_text_spans(spans)
    assert len(result) == 3
    assert all(actual is spans[index] for actual, index in zip(result, [0, 1, 3], strict=True))
    assert [item.model_dump() for item in spans] == before
    assert len(spans) == 5


@pytest.mark.parametrize("axis", ["x0", "y0", "x1", "y1"])
def test_every_bbox_endpoint_must_be_within_tolerance(axis):
    first, other = span(), span()
    setattr(other.bbox, axis, getattr(other.bbox, axis) + 0.02)
    assert len(deduplicate_text_spans([first, other])) == 2


def test_explicit_tolerance_boundary_and_zero():
    first, other = span(), span()
    other.bbox.x0 += 0.125
    assert len(deduplicate_text_spans([first, other], bbox_tolerance=0.125)) == 1
    assert len(deduplicate_text_spans([first, other], bbox_tolerance=0)) == 2
    assert len(deduplicate_text_spans([first, span()], bbox_tolerance=0)) == 1


def test_nearby_chain_does_not_merge_distinct_positions():
    result = deduplicate_text_spans([span(offset=0), span(offset=0.007), span(offset=0.014)])
    assert len(result) == 2


@pytest.mark.parametrize("tolerance", [-0.01, float("inf"), float("-inf"), float("nan")])
def test_invalid_tolerance_is_rejected_even_with_empty_input(tolerance):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        deduplicate_text_spans([], bbox_tolerance=tolerance)


def test_empty_input():
    assert deduplicate_text_spans([]) == []
