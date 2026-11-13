"""Validation results and the in-memory coordinate checks that run before anything is loaded."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Thresholds
from .model import Segment


@dataclass
class Check:
    name: str
    stage: str  # python | postgis | mongodb | comparison | snapshot
    passed: bool
    observed: Any
    expected: str
    detail: list[Any] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _examples(keys: list[str], limit: int = 5) -> list[str]:
    return sorted(keys)[:limit]


def coordinate_checks(segments: list[Segment], bbox: tuple[float, float, float, float],
                      thresholds: Thresholds) -> tuple[list[Check], list[Segment]]:
    """Return checks plus the segments that are loadable (finite coordinates).

    Non-finite coordinates cannot be represented reliably in either store, so they are dropped from the
    load but still fail the gate.
    """
    tol = thresholds.bbox_tolerance_deg
    region = (bbox[0] - tol, bbox[1] - tol, bbox[2] + tol, bbox[3] + tol)
    non_finite, out_of_range, outside_region, too_short = [], [], [], []
    loadable = []
    for seg in segments:
        if not all(math.isfinite(v) for c in seg.coords for v in c):
            non_finite.append(seg.key)
            continue
        loadable.append(seg)
        if any(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in seg.coords):
            out_of_range.append(seg.key)
        elif any(not (region[0] <= x <= region[2] and region[1] <= y <= region[3]) for x, y in seg.coords):
            outside_region.append(seg.key)
        if len(seg.coords) < 2:
            too_short.append(seg.key)
    checks = [
        Check("coordinates_finite", "python", not non_finite, len(non_finite), "0 segments with NaN/inf",
              _examples(non_finite)),
        Check("coordinates_lonlat_range", "python", not out_of_range, len(out_of_range),
              "0 segments outside lon [-180,180] / lat [-90,90]", _examples(out_of_range)),
        Check("coordinates_in_region", "python", not outside_region, len(outside_region),
              f"0 segments with vertices outside bbox +/- {tol} deg (axis order / CRS mix-up)",
              _examples(outside_region)),
        Check("vertex_count", "python", not too_short, len(too_short), "0 segments with < 2 vertices",
              _examples(too_short)),
    ]
    for source in ("osm", "overture"):
        n = sum(1 for s in segments if s.source == source)
        checks.append(Check(f"min_segments_{source}", "python", n >= thresholds.min_segments_per_source, n,
                            f">= {thresholds.min_segments_per_source}"))
    return checks, loadable
