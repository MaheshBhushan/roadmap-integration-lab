# Snapshot 2026-11-13 - bremen-mitte
Region bbox (EPSG:4326): `[8.78, 53.065, 8.83, 53.09]`
- OSM: Geofabrik `osm/bremen-261112.osm.pbf`, replication timestamp 2026-11-12T21:23:05Z, 21,273,423 bytes
- Overture: release `2026-10-21.0`, 5,210 segment rows, 1,556,182 bytes
**Validation passed; published.**
## Validation checks
| check | stage | result | observed | expected |
|---|---|---|---|---|
| snapshot_sha256_osm | snapshot | pass | ec253a2e6fe73f6d007d2f8c0a44b6e510c18907884d33e548828af499783ed6 | sha256 recorded in manifest (ec253a2e6fe7...) |
| snapshot_sha256_overture | snapshot | pass | 172463fb3b4ab8d6593ee3fd5e30cfaf61d145fb78ad07bdcf01a1686c67fc96 | sha256 recorded in manifest (172463fb3b4a...) |
| coordinates_finite | python | pass | 0 | 0 segments with NaN/inf |
| coordinates_lonlat_range | python | pass | 0 | 0 segments outside lon [-180,180] / lat [-90,90] |
| coordinates_in_region | python | pass | 0 | 0 segments with vertices outside bbox +/- 0.05 deg (axis order / CRS mix-up) |
| vertex_count | python | pass | 0 | 0 segments with < 2 vertices |
| min_segments_osm | python | pass | 6177 | >= 10 |
| min_segments_overture | python | pass | 4772 | >= 10 |
| pg_srid_4326 | postgis | pass | 0 | 0 geometries with SRID other than 4326 |
| pg_geometry_type | postgis | pass | 0 | 0 non-LineString geometries |
| pg_geometry_valid | postgis | pass | 0 | 0 geometries failing ST_IsValid |
| pg_non_degenerate | postgis | pass | 0 | 0 empty or zero-length geometries |
| pg_within_region | postgis | pass | 0 | 0 geometries outside bbox +/- 0.05 deg |
| pg_intersects_region | postgis | pass | 0 | 0 geometries not touching the region bbox |
| pg_crs_roundtrip | postgis | pass | 0 | <= 0.01 m drift after EPSG:4326 -> EPSG:25832 -> EPSG:4326 |
| pg_row_counts | postgis | pass | {'osm_ways': 6177, 'overture_segments': 4772, 'road_segments': 10949} | equal to rows written {'osm_ways': 6177, 'overture_segments': 4772, 'road_segments': 10949} |
| mongo_rejected_documents | mongodb | pass | 0 | 0 documents rejected by $jsonSchema / 2dsphere indexing |
| mongo_matches_postgis | mongodb | pass | {'mongodb': [10949, '87f7a49f1563ea23907a6289462a33bd'], 'postgis': [10949, '87f7a49f1563ea23907a6289462a33bd']} | same segment count and id-set md5 in MongoDB and PostGIS |
| mongo_geo_intersects_region | mongodb | pass | {'intersecting': 10949, 'total': 10949} | every document found by a 2dsphere $geoIntersects query on the region |
| coverage_osm | comparison | pass | 0.9945 | >= 0.75 of osm length within 10.0 m of the other source |
| coverage_overture | comparison | pass | 0.9964 | >= 0.75 of overture length within 10.0 m of the other source |

## Intake
| item | value |
|---|---|
| osm_highway_ways_bbox_overlap | 6342 |
| osm_excluded_non_road_highway | {'platform': 80, 'corridor': 62, 'elevator': 18, 'disused:footway': 2, 'services': 1, 'construction': 1} |
| osm_missing_node_locations | 0 |
| overture_rows | 5210 |
| overture_excluded_subtypes | {'rail': 433, 'water': 2} |
| dropped_envelope_only | {'osm': 1, 'overture': 3} |
| segments | {'osm': 6177, 'overture': 4772} |

## Coverage (length within 10.0 m of the other source, clipped to bbox)
| source | segments | length km | covered km | covered |
|---|---|---|---|---|
| osm | 6177 | 343.658 | 341.764 | 99.5% |
| overture | 4772 | 336.502 | 335.3 | 99.6% |

Per road class:

| class | OSM km | OSM covered | Overture km | Overture covered |
|---|---|---|---|---|
| cycleway | 8.094 | 100.0% | 8.049 | 100.0% |
| footway | 42.956 | 99.7% | 42.227 | 100.0% |
| living_street | 1.599 | 100.0% | 1.599 | 100.0% |
| path | 57.394 | 99.7% | 57.016 | 100.0% |
| pedestrian | 16.5 | 91.5% | 9.386 | 100.0% |
| primary | 8.141 | 100.0% | 8.141 | 100.0% |
| residential | 59.933 | 100.0% | 59.933 | 100.0% |
| secondary | 27.571 | 100.0% | 27.571 | 100.0% |
| service | 74.969 | 99.8% | 74.481 | 100.0% |
| steps | 1.764 | 99.8% | 1.754 | 100.0% |
| tertiary | 16.36 | 100.0% | 16.36 | 100.0% |
| track | 0.757 | 100.0% | 0.757 | 100.0% |
| trunk | 14.144 | 100.0% | 14.144 | 100.0% |
| unclassified | 13.478 | 100.0% | 13.478 | 100.0% |
| unknown | - | - | 1.607 | 28.0% |

## Restriction attribute fill (share of drivable-road length)
| source | drivable km | maxspeed | oneway (directional) | motor access tagged |
|---|---|---|---|---|
| osm | 216.194 | 65.9% | 47.8% | 17.6% |
| overture | 215.706 | 62.8% | 46.3% | 14.9% |

## Linkage via Overture `sources[].record_id`
| item | value |
|---|---|
| overture_segments | 4772 |
| overture_without_osm_ref | 27 |
| overture_distinct_osm_ways | 4425 |
| osm_ways | 6177 |
| osm_ways_referenced_by_overture | 4394 |
| referenced_ways_present_in_osm | 4394 |
| pairs_same_version | 4652 |
| pairs_osm_newer | 62 |
| pairs_overture_newer | 0 |

## Restriction comparison on 4,714 (Overture segment, source OSM way) pairs
278 pairs with partial (`between`) rules excluded.

| attribute | agree | disagree | OSM only | Overture only | neither | agreement | disagree w/ newer OSM version |
|---|---|---|---|---|---|---|---|
| road_class | 4435 | 1 | 0 | 0 | 0 | 100.0% | 1 |
| maxspeed_kmh | 1328 | 0 | 1 | 0 | 3107 | 100.0% | 0 |
| oneway | 4429 | 7 | 0 | 0 | 0 | 99.8% | 0 |
| motor_access | 437 | 0 | 16 | 0 | 3983 | 100.0% | 0 |

Examples for `road_class`:

| overture id | osm way | same version | OSM | Overture | raw OSM tag |
|---|---|---|---|---|---|
| 06361733-3cdc-42cb-8e20-64f17baef6cc | 509317309 | False | footway | path | - |

Examples for `maxspeed_kmh`:

| overture id | osm way | same version | OSM | Overture | raw OSM tag |
|---|---|---|---|---|---|
| 0a6e12cf-be3f-47bc-9ade-789060d2f9b4 | 262455587 | False | 30 | - | 30 |

Examples for `oneway`:

| overture id | osm way | same version | OSM | Overture | raw OSM tag |
|---|---|---|---|---|---|
| 2310dbbb-861f-43c1-8069-01679e386d50 | 1100584176 | True | forward | both | - |
| 23a4f580-4dc7-4e90-83b9-e0af60373569 | 71010319 | True | forward | both | - |
| 2f7fbc17-89a5-464f-88b5-5343e75afac7 | 1043091779 | True | forward | both | yes |
| 43829201-93aa-4ddc-a167-99440f4bcb38 | 4245283 | True | forward | both | - |
| 5c3ed980-a5b1-40b6-b7b7-46db15e24727 | 4245283 | True | forward | both | - |
| 9daeac7d-fac1-450b-8cda-2ce060882237 | 4245283 | True | forward | both | - |
| db5f0060-02af-4b42-b2bd-1d0d9793ecb9 | 4245283 | True | forward | both | - |

Examples for `motor_access`:

| overture id | osm way | same version | OSM | Overture | raw OSM tag |
|---|---|---|---|---|---|
| 04596df0-aacb-4f19-8117-7ddc57a69936 | 188490817 | True | allowed | - | permissive |
| 063ef7e2-dd77-450f-8567-b2229116d0cc | 531950148 | True | allowed | - | permissive |
| 22123ec5-fef9-4d3f-9198-25728ae93b99 | 215504369 | True | other | - | unknown |
| 46a7389e-c86a-4031-80b9-e22d652c7313 | 99756461 | True | allowed | - | permissive |
| 5083f8de-9e61-4b1a-aa8b-f4900da4b7bc | 25004261 | True | other | - | restricted |
| 5934bc8f-ad37-4285-ba71-d98402402c3a | 256460809 | True | allowed | - | permissive |
| 6f0492d2-0fe1-4987-8f39-19875b6cadb5 | 812522215 | True | allowed | - | permissive |
| 83f8e546-2734-4ff8-8440-2cc1209607a8 | 184724896 | True | allowed | - | permissive |

## Source schema: `osm_ways` (6,177 rows)
| column | type | non-empty |
|---|---|---|
| way_id | bigint | 100.0% |
| version | integer | 100.0% |
| osm_timestamp | timestamp with time zone | 100.0% |
| highway | text | 100.0% |
| tags | jsonb | 100.0% |
| geom | geometry | 100.0% |

## Source schema: `overture_segments` (4,772 rows)
| column | type | non-empty |
|---|---|---|
| id | text | 100.0% |
| version | integer | 100.0% |
| subtype | text | 100.0% |
| class | text | 100.0% |
| subclass | text | 16.2% |
| names | jsonb | 38.7% |
| connectors | jsonb | 100.0% |
| road_surface | jsonb | 89.6% |
| road_flags | jsonb | 9.9% |
| width_rules | jsonb | 9.1% |
| level_rules | jsonb | 3.4% |
| access_restrictions | jsonb | 47.8% |
| speed_limits | jsonb | 30.6% |
| prohibited_transitions | jsonb | 3.2% |
| destinations | jsonb | 1.8% |
| routes | jsonb | 1.1% |
| sources | jsonb | 100.0% |
| geom | geometry | 100.0% |

OSM tag keys on highway ways: 363 distinct. Fill of selected keys:

| tag | ways with tag |
|---|---|
| highway | 100.0% |
| surface | 88.2% |
| lit | 71.7% |
| name | 38.6% |
| oneway | 31.8% |
| maxspeed | 29.8% |
| sidewalk | 22.3% |
| lanes | 18.7% |
| access | 9.1% |
| width | 8.5% |
| oneway:bicycle | 5.7% |
| tunnel | 4.5% |
| layer | 3.4% |
| bridge | 1.8% |
| cycleway | 1.6% |
| motor_vehicle | 1.0% |
| vehicle | 0.9% |
| motorcar | 0.9% |
| maxspeed:conditional | 0.6% |
| junction | 0.5% |

## Runtime and memory (pipeline process)
Total 61.476 s; peak RSS 637.3 MiB. Peaks are the kernel high-water mark VmHWM, reset before each stage (reset succeeded).

| stage | seconds | RSS start MiB | RSS end MiB | peak RSS MiB |
|---|---|---|---|---|
| acquire_snapshot | 22.856 | 108.1 | 537 | 537 |
| snapshot_integrity | 0.135 | 537 | 537 | 537 |
| read_osm_pbf | 6.566 | 537 | 563 | 637.3 |
| read_overture_parquet | 2.256 | 563 | 494.5 | 562.3 |
| coordinate_checks | 0.296 | 494.9 | 494.9 | 494.9 |
| load_postgis | 4.58 | 498.1 | 500.5 | 500.5 |
| postgis_checks | 0.827 | 500.5 | 500.5 | 500.5 |
| load_mongodb | 2.207 | 500.5 | 525.2 | 532.4 |
| mongodb_checks | 0.248 | 525.2 | 525.3 | 525.3 |
| compare_sources | 19.143 | 525.3 | 525.3 | 525.3 |
| publish | 0.352 | 525.3 | 525.3 | 525.3 |

Staging storage: PostGIS osm_ways 2.68 MiB, overture_segments 6.14 MiB, road_segments 3.86 MiB; MongoDB data 11.26 MiB, indexes 0.01 MiB.
