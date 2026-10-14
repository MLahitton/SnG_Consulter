"""Deterministic scaling from source coordinates to render pixels."""

from math import isfinite

from .models import BoundingBox, PreparedPage


def source_bbox_to_render_pixels(bbox: BoundingBox, page: PreparedPage) -> BoundingBox:
    """Return a new floating-point bbox without rounding or changing the source.

    Apply the recorded source-to-visible affine matrix, then scale to pixels.
    PyMuPDF already localizes text to the visible crop origin. Zero is valid at the page edges;
    dimensions must be strictly positive, coordinates finite and nonnegative.
    Transform all four corners to account for rotation. Do not clip coordinates.
    """
    dimensions = (page.coordinate_width, page.coordinate_height,
                  page.width_px, page.height_px)
    if any(not isfinite(value) or value <= 0 for value in dimensions):
        raise ValueError("Coordinate and render dimensions must be finite and positive")
    if any(not isfinite(value) or value < 0 for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1)):
        raise ValueError("BBox coordinates must be finite and nonnegative")
    if bbox.x1 < bbox.x0 or bbox.y1 < bbox.y0:
        raise ValueError("BBox endpoints must be ordered: x0 <= x1 and y0 <= y1")
    a, b, c, d, e, f = page.source_to_visible_matrix
    if not all(isfinite(value) for value in (a, b, c, d, e, f)):
        raise ValueError("Source-to-visible matrix must be finite")
    corners = [(a * x + c * y + e, b * x + d * y + f)
               for x, y in ((bbox.x0, bbox.y0), (bbox.x1, bbox.y0),
                            (bbox.x0, bbox.y1), (bbox.x1, bbox.y1))]
    xs, ys = zip(*corners)
    return BoundingBox(
        x0=min(xs) / page.coordinate_width * page.width_px,
        y0=min(ys) / page.coordinate_height * page.height_px,
        x1=max(xs) / page.coordinate_width * page.width_px,
        y1=max(ys) / page.coordinate_height * page.height_px,
    )
