# roadmap-integration-lab

Compares two public street-map datasets for one small region, central Bremen (Germany):

- **OpenStreetMap**, Geofabrik `bremen-latest.osm.pbf` extract (ODbL-1.0)
- **Overture Maps** `transportation/segment` GeoParquet, read from the public release bucket (ODbL-1.0)

One command takes a dated snapshot of both, normalises road segments into a common model, loads them
into PostgreSQL/PostGIS and MongoDB, runs coordinate, CRS and geometry checks in Python, PostGIS and
MongoDB, compares coverage and restriction attributes (speed limit, one-way, motor-vehicle access),
and publishes the snapshot only if every check passes. Each run writes a runtime and peak-memory report.

## Architecture

```
roadlab run
 ├─ acquire        data/snapshots/<YYYY-MM-DD>/{osm/*.osm.pbf, overture/segments.parquet, manifest.json}
 │                 manifest: resolved Geofabrik URL, replication timestamp, Overture release, sha256, sizes
 ├─ integrity      sha256 of each file vs manifest
 ├─ read           osm.py (pyosmium, highway=* ways) / overture.py (pyarrow, subtype=road)
 │                 -> model.Segment {road_class, maxspeed_kmh, oneway, motor_access, partial_rules, raw}
 ├─ python checks  finite coords, lon/lat range, vertices inside region (+0.05 deg; catches swapped axes), >= 2 vertices
 ├─ PostGIS        schema `staging`: osm_ways (tags jsonb), overture_segments (typed + jsonb rules), road_segments
 │                 checks: SRID 4326, LineString type, ST_IsValid, non-degenerate, within/intersects region,
 │                 EPSG:4326 -> 25832 -> 4326 round-trip drift, row counts
 ├─ MongoDB        collection `staging_road_segments`: GeoJSON + 2dsphere index + $jsonSchema validator
 │                 checks: rejected documents, id-set md5 equal to PostGIS, $geoIntersects finds every document
 ├─ compare        PostGIS, in EPSG:25832, clipped to the bbox: buffered-overlap coverage per class,
 │                 attribute fill, linkage via Overture sources[].record_id (w<way>@<version>), restriction agreement
 ├─ publish gate   all checks pass -> PostGIS `staging` renamed to `published` (geometry typmod tightened to
 │                 LineString,4326) and Mongo collection renamed to `road_segments`; row in roadlab_meta.publications
 └─ report         reports/<date>/{report.md, validation.json, comparison.json, runtime.json, manifest.json}
```

Normalisation rules (in `osm.py` / `overture.py`):

- `maxspeed_kmh`: OSM numeric values, `mph` converted, German implicit values (`DE:urban` = 50, `DE:zone:30` = 30, ...).
  Overture: the unconditional, whole-segment `speed_limits[].max_speed`.
- `oneway`: OSM `oneway` plus the implied one-way of `junction=roundabout` and motorways. Overture: an
  `access_restrictions` rule `denied` with `when.heading` (Overture's encoding of OSM oneway).
- `motor_access`: most specific of OSM `motorcar` > `motor_vehicle` > `vehicle` > `access`. Overture: generic or
  car/motor_vehicle/vehicle mode rules; `when.using` = `at_destination`/`as_customer`/`to_deliver` map to
  destination/customers/delivery.
- Rules restricted to part of a segment (Overture `between`, OSM `maxspeed:forward/backward` without `maxspeed`)
  set `partial_rules`; those pairs are excluded from the attribute comparison and counted separately.
- Time-dependent (`when.during`) and vehicle-dimension rules are not compared.

## Quickstart

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d --wait        # PostGIS on 127.0.0.1:55433, MongoDB on 127.0.0.1:57018
uv sync
uv run roadlab run                 # today's snapshot; exit code 0 = published, 2 = validation failed
uv run roadlab run --refresh       # re-download even if today's snapshot exists
uv run roadlab run --snapshot-dir data/snapshots/2026-11-13 --no-publish
docker compose down -v
```

Connection settings: `ROADLAB_PG_DSN`, `ROADLAB_MONGO_URI`, `ROADLAB_MONGO_DB`. Region: `--bbox XMIN YMIN XMAX YMAX`
(default `8.78 53.065 8.83 53.09`, about 3.3 km x 2.8 km; the Geofabrik URL is fixed to the Bremen extract, so other
regions must lie inside Bremen or the URL in `config.py` must change).

## Tests

```bash
docker compose up -d --wait
uv run ruff check .
uv run pytest -v
```

`tests/test_normalise.py` covers tag/rule parsing, the coordinate checks and the memory profiler.
`tests/test_pipeline_db.py` runs the full pipeline against PostGIS and MongoDB on a committed fixture
(`tests/fixtures/snapshot`: 430 KB, cut from the 2026-11-13 snapshot with `scripts/make_fixture.py`, bbox
`8.805 53.074 8.812 53.078` around the Marktplatz). It asserts that a clean run publishes, that a fixture with one
Overture segment's lon/lat swapped fails `coordinates_in_region`, `pg_within_region`, `pg_intersects_region` and
`mongo_geo_intersects_region` and leaves the published data untouched, and that a modified snapshot file fails the
sha256 check and makes the CLI exit with code 2. The tests use a separate `roadlab_test` database in both stores.
CI (`.github/workflows/ci.yml`) runs the same suite with PostGIS 16-3.4 and MongoDB 7.0 service containers.

## Measured results (snapshot 2026-11-13)

All numbers below come from one run of `uv run roadlab run --data-dir /tmp/roadlab-data --refresh` on 2026-11-13; the
full output is committed in [`docs/results/2026-11-13/`](docs/results/2026-11-13/report.md). Inputs: Geofabrik
`bremen-261112.osm.pbf` (replication timestamp 2026-11-12T21:23:05Z, 21,273,423 bytes) and Overture release
`2026-10-21.0` (5,210 segment rows in the bbox, 1,556,182 bytes). Re-running on a later date downloads newer data, so
the counts will differ.

### Validation

21/21 checks passed and the snapshot was published: 6,177 OSM ways and 4,772 Overture road segments, 0 coordinate,
SRID, type or validity failures, 0 m CRS round-trip drift, 0 MongoDB rejections, identical id-set md5 in PostGIS and
MongoDB (10,949 segments).

Intake: 164 OSM `highway=*` ways were not roads (80 `platform`, 62 `corridor`, 18 `elevator`, 2 `disused:footway`,
1 `services`, 1 `construction`); 435 Overture rows were not roads (433 `rail`, 2 `water`); 1 OSM way and 3 Overture
segments had only their bounding box, not their line, inside the region and were dropped.

### Coverage

Share of each source's length (clipped to the bbox, EPSG:25832) lying within 10 m of the other source:

| source | segments | length km | covered |
|---|---|---|---|
| OSM | 6,177 | 343.658 | 99.5% |
| Overture | 4,772 | 336.502 | 99.6% |

The differences are concentrated in two places:

- `pedestrian`: 16.5 km in OSM vs 9.386 km in Overture (OSM covered 91.5%). 50 OSM pedestrian ways tagged
  `area=yes` (7.19 km of outline) have no Overture segment; Overture does not carry pedestrian areas as segments.
- `unknown`: 1.607 km in Overture, 28.0% covered. These are the 27 Overture segments with no OpenStreetMap source
  record; their `sources` dataset is TomTom.

### Restriction attributes

Share of drivable-road length (motorway ... service) carrying the attribute:

| source | drivable km | maxspeed | oneway (directional) | motor access tagged |
|---|---|---|---|---|
| OSM | 216.194 | 65.9% | 47.8% | 17.6% |
| Overture | 215.706 | 62.8% | 46.3% | 14.9% |

Overture records the source OSM way and version for 4,745 of 4,772 segments (4,425 distinct ways; 4,394 of them are among
the 6,177 OSM road ways loaded for the region, 30 exist in the Geofabrik extract but were not selected for the region on
the OSM side, and 1 is no longer in the extract). Of
the 4,714 Overture segments that join to a loaded OSM way, 4,652 reference the same way version, 62 refer to an older
version than Geofabrik's, and 278 have partial rules and are excluded from the attribute comparison:

| attribute | agree | disagree | OSM only | Overture only | neither |
|---|---|---|---|---|---|
| road_class | 4,435 | 1 | 0 | 0 | 0 |
| maxspeed_kmh | 1,328 | 0 | 1 | 0 | 3,107 |
| oneway | 4,429 | 7 | 0 | 0 | 0 |
| motor_access | 437 | 0 | 16 | 0 | 3,983 |

What the disagreements are (examples in the report):

- `oneway`: 6 of the 7 are roundabouts (`junction=roundabout`, no `oneway` tag, e.g. ways 4245283, 71010319) where OSM
  implies one-way but Overture has no heading restriction; the 7th, way 1043091779 (`oneway=yes`, Doventorstraße),
  has no Overture access rule at the same way version.
- `motor_access`: OSM-only values are `access=permissive` (Overture drops it) and non-standard values such as
  `unknown` and `restricted`.
- `road_class` and `maxspeed_kmh`: the single differences are both on pairs where Geofabrik has a newer way version
  than the one Overture was built from, i.e. edits after the Overture release.

### Source schemas

- OSM: a way has id, version, timestamp, node list and an open key/value tag map. The 6,177 highway ways in the region
  use 363 distinct tag keys; `maxspeed` is on 29.8% of them, `oneway` on 31.8%, `access` on 9.1%,
  `motor_vehicle`/`vehicle`/`motorcar` on 1.0%/0.9%/0.9%, `maxspeed:conditional` on 0.6%.
- Overture `segment`: typed columns (`id`, `version`, `subtype`, `class`, `subclass`) plus arrays of rule structs
  (`speed_limits`, `access_restrictions`, `prohibited_transitions`, `road_surface`, `road_flags`, `width_rules`,
  `level_rules`, ...) each with an optional `when` condition (heading, mode, using, recognized, during, vehicle) and an
  optional `between` linear-reference range, plus `connectors` and provenance `sources`. Non-empty shares for road
  segments: `access_restrictions` 47.8%, `speed_limits` 30.6%, `prohibited_transitions` 3.2%, `names` 38.7%.
- The PostGIS DDL for both raw tables and the unified table is in
  [`src/roadlab/sql/schema.sql`](src/roadlab/sql/schema.sql); the MongoDB `$jsonSchema` validator is in
  [`src/roadlab/mongo.py`](src/roadlab/mongo.py).

### Runtime and memory

Pipeline process on a shared 8-core Linux machine, same run:

| stage | seconds | peak RSS MiB |
|---|---|---|
| acquire_snapshot (download 21 MB PBF + Overture query) | 42.774 | 511.4 |
| read_osm_pbf | 7.839 | 475.0 |
| read_overture_parquet | 1.489 | 482.4 |
| load_postgis | 2.493 | 486.8 |
| load_mongodb | 1.329 | 520.1 |
| compare_sources | 20.883 | 513.9 |
| total (11 stages) | 82.576 | 520.1 |

Peak RSS is the kernel's `VmHWM`, reset before each stage via `/proc/self/clear_refs`, so each stage reports its own
exact peak. It covers the Python process only, not the database containers. Staging size: PostGIS 2.45 MiB
(`osm_ways`) + 6.15 MiB (`overture_segments`) + 3.70 MiB (`road_segments`); MongoDB 11.26 MiB uncompressed BSON.
Timings vary with network and machine load: an earlier `--refresh` run of the same command on the same day took
61.476 s with a 637.3 MiB peak.

## Limitations

- One region (central Bremen) and one Geofabrik extract; the Overture side is queried by bbox, the OSM side reads the
  whole state extract and filters it.
- Coverage is a 10 m buffer overlap, not a topological match; parallel features (sidewalk next to a road) count as
  covered.
- Only unconditional, whole-segment speed limits and car access are compared; time windows, vehicle dimensions,
  lane-level and turn restrictions (`prohibited_transitions`) are stored but not compared.
- Overture's transportation theme is itself largely derived from OSM, so the comparison mostly measures conversion
  fidelity and release lag rather than independent ground truth.
- The memory figures need Linux procfs; elsewhere they are reported as null.

## License

MIT, see [LICENSE](LICENSE). Data: OpenStreetMap contributors and Overture Maps Foundation, ODbL-1.0. The fixture in
`tests/fixtures/` is an extract of that data and carries the same licence.
