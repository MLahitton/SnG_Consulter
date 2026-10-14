"""Non-destructive views of native text spans."""

from math import isfinite

from .models import TextSpan


def deduplicate_text_spans(
    spans: list[TextSpan], bbox_tolerance: float = 0.01
) -> list[TextSpan]:
    """Keep first occurrences with exact text and matching bbox endpoints.

    Tolerance is absolute and measured in source coordinate units. Each endpoint
    must differ by at most the tolerance from a retained span. Comparing only
    retained spans prevents a chain of nearby duplicates from merging distinct
    positions. The returned list references original objects without editing them.
    """
    if not isfinite(bbox_tolerance) or bbox_tolerance < 0:
        raise ValueError("bbox_tolerance must be finite and nonnegative")
    unique: list[TextSpan] = []
    by_text: dict[str, list[TextSpan]] = {}
    for span in spans:
        candidates = by_text.setdefault(span.text, [])
        if any(
            all(abs(getattr(span.bbox, axis) - getattr(previous.bbox, axis)) <= bbox_tolerance
                for axis in ("x0", "y0", "x1", "y1"))
            for previous in candidates
        ):
            continue
        candidates.append(span)
        unique.append(span)
    return unique
