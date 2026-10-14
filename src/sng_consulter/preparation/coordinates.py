"""Deterministic scaling from source coordinates to render pixels."""

from math import isfinite

from .models import BoundingBox, PreparedPage


def source_bbox_to_render_pixels(bbox: BoundingBox, page: PreparedPage) -> BoundingBox:
    """Return a new floating-point bbox without rounding or changing the source.

    Both spaces use a top-left origin. Zero is valid at the page edges;
    dimensions must be strictly positive, coordinates finite and nonnegative.
    This is scaling only: it does not rotate, crop, or clip coordinates.
    """
    dimensions = (page.coordinate_width, page.coordinate_height,
                  page.width_px, page.height_px)
    if any(not isfinite(value) or value <= 0 for value in dimensions):
        raise ValueError("Coordinate and render dimensions must be finite and positive")
    if any(not isfinite(value) or value < 0 for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1)):
        raise ValueError("BBox coordinates must be finite and nonnegative")
    if bbox.x1 < bbox.x0 or bbox.y1 < bbox.y0:
        raise ValueError("BBox endpoints must be ordered: x0 <= x1 and y0 <= y1")
    return BoundingBox(
        x0=bbox.x0 / page.coordinate_width * page.width_px,
        y0=bbox.y0 / page.coordinate_height * page.height_px,
        x1=bbox.x1 / page.coordinate_width * page.width_px,
        y1=bbox.y1 / page.coordinate_height * page.height_px,
    )
