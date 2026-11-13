import math

import pytest

from roadlab.config import Settings, Thresholds
from roadlab.model import Segment
from roadlab.osm import parse_maxspeed, parse_motor_access, parse_oneway, road_class, way_to_segment
from roadlab.overture import osm_way_ref, parse_access, parse_speed
from roadlab.pipeline import drop_envelope_only
from roadlab.validate import coordinate_checks

BBOX = (8.80, 53.07, 8.81, 53.08)


@pytest.mark.parametrize(("value", "expected"), [
    ("50", 50.0), ("30 mph", 48.3), ("DE:urban", 50.0), ("DE:zone:30", 30.0), ("none", None),
    ("walk", None), (None, None), ("7.5", 7.5),
])
def test_parse_maxspeed(value, expected):
    assert parse_maxspeed(value) == expected


@pytest.mark.parametrize(("tags", "expected"), [
    ({"highway": "residential"}, "both"),
    ({"highway": "residential", "oneway": "yes"}, "forward"),
    ({"highway": "residential", "oneway": "-1"}, "backward"),
    ({"highway": "primary", "junction": "roundabout"}, "forward"),
    ({"highway": "motorway"}, "forward"),
    ({"highway": "residential", "oneway": "reversible"}, None),
])
def test_parse_oneway(tags, expected):
    assert parse_oneway(tags) == expected


def test_motor_access_prefers_most_specific_key():
    assert parse_motor_access({"access": "no", "motor_vehicle": "destination"}) == "destination"
    assert parse_motor_access({"access": "private"}) == "private"
    assert parse_motor_access({"vehicle": "agricultural"}) == "other"
    assert parse_motor_access({}) is None


def test_road_class_mapping():
    assert road_class("primary_link") == "primary"
    assert road_class("road") == "unknown"
    assert road_class("platform") is None
    assert way_to_segment(1, 1, "2024-01-01T00:00:00+00:00", {"highway": "bus_stop"}, [(0, 0)]) is None


def test_overture_speed_ignores_conditional_and_flags_partial():
    rules = [
        {"max_speed": {"value": 50, "unit": "km/h"}, "when": None, "between": None},
        {"max_speed": {"value": 30, "unit": "km/h"}, "when": {"during": "Mo-Fr 07:00-17:00"}, "between": None},
        {"max_speed": {"value": 20, "unit": "km/h"}, "when": None, "between": [0.0, 0.4]},
    ]
    assert parse_speed(rules) == (50.0, True)
    assert parse_speed([{"max_speed": {"value": 20, "unit": "mph"}, "when": None, "between": None}]) == (32.2, False)


def test_overture_access_oneway_and_destination():
    oneway = [{"access_type": "denied", "when": {"heading": "backward"}, "between": None}]
    assert parse_access(oneway) == ("forward", None, False)
    destination = [
        {"access_type": "denied", "when": None, "between": None},
        {"access_type": "allowed", "when": {"using": ["at_destination"]}, "between": None},
    ]
    assert parse_access(destination) == ("both", "destination", False)
    bike_only = [{"access_type": "allowed", "when": {"mode": ["bicycle"]}, "between": None}]
    assert parse_access(bike_only) == ("both", None, False)
    car_denied = [
        {"access_type": "allowed", "when": None, "between": None},
        {"access_type": "denied", "when": {"mode": ["motor_vehicle"]}, "between": None},
    ]
    assert parse_access(car_denied) == ("both", "denied", False)


def test_osm_way_ref():
    sources = [{"dataset": "OpenStreetMap", "record_id": "w4708816@34"}]
    assert osm_way_ref(sources) == (4708816, 34)
    assert osm_way_ref([{"dataset": "TomTom", "record_id": "x"}]) == (None, None)
    assert osm_way_ref(None) == (None, None)


def _seg(i, coords, source="osm"):
    return Segment(source=source, source_id=str(i), coords=coords, road_class="residential")


def test_coordinate_checks_detect_swapped_axes_nan_and_short():
    good = [_seg(i, [(8.801, 53.071), (8.802, 53.072)], s) for i in range(10) for s in ("osm", "overture")]
    swapped = _seg("swapped", [(53.071, 8.801), (53.072, 8.802)])
    nan = _seg("nan", [(math.nan, 53.07), (8.8, 53.07)])
    short = _seg("short", [(8.801, 53.071)])
    checks, loadable = coordinate_checks(good + [swapped, nan, short], BBOX, Thresholds())
    by_name = {c.name: c for c in checks}
    assert by_name["coordinates_finite"].observed == 1
    assert by_name["coordinates_in_region"].detail == ["osm:swapped"]
    assert by_name["vertex_count"].detail == ["osm:short"]
    assert by_name["min_segments_osm"].passed and by_name["min_segments_overture"].passed
    assert nan not in loadable and swapped in loadable


def test_coordinate_checks_pass_on_clean_input():
    good = [_seg(i, [(8.801, 53.071), (8.802, 53.072)], s) for i in range(10) for s in ("osm", "overture")]
    checks, loadable = coordinate_checks(good, BBOX, Thresholds())
    assert all(c.passed for c in checks) and len(loadable) == 20


def test_drop_envelope_only_keeps_suspicious_segments():
    settings = Settings(bbox=BBOX)
    # L-shaped line whose envelope overlaps the bbox but which runs around it.
    around = _seg("around", [(8.795, 53.065), (8.815, 53.065), (8.815, 53.085)])
    inside = _seg("inside", [(8.801, 53.071), (8.802, 53.072)])
    swapped = _seg("swapped", [(53.071, 8.801), (53.072, 8.802)])
    kept, dropped = drop_envelope_only([around, inside, swapped], settings)
    assert kept == [inside, swapped]
    assert dropped == {"osm": 1, "overture": 0}


def test_stage_profiler_reports_per_stage_peak():
    from roadlab.metrics import StageProfiler

    with StageProfiler() as prof:
        with prof.stage("allocate"):
            block = bytearray(80 * 2**20)
            block[:: 4096] = b"x" * len(block[:: 4096])  # touch every page
            del block
        with prof.stage("idle"):
            pass
    report = prof.report()
    alloc, idle = report["stages"]
    assert report["per_stage_reset"]
    assert alloc["peak_rss_mib"] - alloc["rss_start_mib"] >= 70
    assert idle["peak_rss_mib"] < alloc["peak_rss_mib"] - 50
