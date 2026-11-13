"""MongoDB staging load (GeoJSON + 2dsphere + $jsonSchema), checks and publication by collection rename."""

from __future__ import annotations

import hashlib
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import BulkWriteError, OperationFailure

from .config import Settings
from .model import ACCESS_VALUES, ONEWAY_VALUES, Segment
from .validate import Check

STAGING = "staging_road_segments"
PUBLISHED = "road_segments"
PUBLICATIONS = "publications"

VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "source", "source_id", "road_class", "geometry", "raw"],
        "properties": {
            "source": {"enum": ["osm", "overture"]},
            "source_id": {"bsonType": "string"},
            "osm_way_id": {"bsonType": ["long", "int", "null"]},
            "road_class": {"bsonType": "string"},
            "maxspeed_kmh": {"bsonType": ["double", "null"], "minimum": 0, "exclusiveMinimum": True},
            "oneway": {"enum": [*ONEWAY_VALUES, None]},
            "motor_access": {"enum": [*ACCESS_VALUES, None]},
            "partial_rules": {"bsonType": "bool"},
            "geometry": {
                "bsonType": "object",
                "required": ["type", "coordinates"],
                "properties": {
                    "type": {"enum": ["LineString"]},
                    "coordinates": {
                        "bsonType": "array",
                        "minItems": 2,
                        "items": {
                            "bsonType": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": [
                                {"bsonType": "double", "minimum": -180, "maximum": 180},
                                {"bsonType": "double", "minimum": -90, "maximum": 90},
                            ],
                        },
                    },
                },
            },
        },
    }
}


def connect(settings: Settings) -> Database:
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
    return client[settings.mongo_db]


def to_document(seg: Segment) -> dict[str, Any]:
    return {
        "_id": seg.key,
        "source": seg.source,
        "source_id": seg.source_id,
        "osm_way_id": seg.osm_way_id,
        "osm_version": seg.osm_version,
        "road_class": seg.road_class,
        "name": seg.name,
        "maxspeed_kmh": seg.maxspeed_kmh,
        "oneway": seg.oneway,
        "motor_access": seg.motor_access,
        "partial_rules": seg.partial_rules,
        "geometry": {"type": "LineString", "coordinates": [[float(x), float(y)] for x, y in seg.coords]},
        "raw": seg.raw,
    }


def load(db: Database, segments: list[Segment]) -> dict[str, Any]:
    """Create the staging collection with schema validation and a 2dsphere index, then insert.

    With the index in place MongoDB rejects documents whose geometry it cannot index, so rejected
    documents are counted rather than raised.
    """
    db.drop_collection(STAGING)
    db.create_collection(STAGING, validator=VALIDATOR, validationLevel="strict", validationAction="error")
    coll = db[STAGING]
    coll.create_index([("geometry", "2dsphere")], name="geometry_2dsphere")
    coll.create_index([("osm_way_id", 1)], name="osm_way_id")
    rejected: list[dict[str, Any]] = []
    docs = [to_document(s) for s in segments]
    try:
        coll.insert_many(docs, ordered=False)
    except BulkWriteError as exc:
        for err in exc.details["writeErrors"]:
            rejected.append({"_id": docs[err["index"]]["_id"], "code": err["code"],
                             "error": err["errmsg"][:200]})
    return {"inserted": coll.count_documents({}), "rejected": rejected}


def ids_digest(db: Database, collection: str = STAGING) -> tuple[int, str]:
    ids = sorted((d["_id"] for d in db[collection].find({}, {"_id": 1})), key=lambda s: s.encode())
    return len(ids), hashlib.md5(",".join(ids).encode()).hexdigest()


def checks(db: Database, settings: Settings, load_result: dict[str, Any],
           pg_digest: tuple[int, str]) -> list[Check]:
    coll = db[STAGING]
    rejected = load_result["rejected"]
    out = [
        Check("mongo_rejected_documents", "mongodb", not rejected, len(rejected),
              "0 documents rejected by $jsonSchema / 2dsphere indexing", rejected[:5]),
    ]
    digest = ids_digest(db)
    out.append(Check("mongo_matches_postgis", "mongodb", digest == tuple(pg_digest),
                     {"mongodb": list(digest), "postgis": list(pg_digest)},
                     "same segment count and id-set md5 in MongoDB and PostGIS"))
    tol = 0.001
    xmin, ymin, xmax, ymax = settings.bbox
    region = {"type": "Polygon", "coordinates": [[
        [xmin - tol, ymin - tol], [xmax + tol, ymin - tol], [xmax + tol, ymax + tol],
        [xmin - tol, ymax + tol], [xmin - tol, ymin - tol],
    ]]}
    try:
        inside = coll.count_documents({"geometry": {"$geoIntersects": {"$geometry": region}}})
        total = coll.count_documents({})
        out.append(Check("mongo_geo_intersects_region", "mongodb", inside == total,
                         {"intersecting": inside, "total": total},
                         "every document found by a 2dsphere $geoIntersects query on the region"))
    except OperationFailure as exc:
        out.append(Check("mongo_geo_intersects_region", "mongodb", False, "error", "query succeeds",
                         [str(exc)]))
    return out


def storage_bytes(db: Database, collection: str) -> dict[str, int]:
    stats = db.command("collStats", collection)
    return {"data": int(stats["size"]), "storage": int(stats["storageSize"]),
            "indexes": int(stats["totalIndexSize"])}


def publish(db: Database, manifest: dict[str, Any], summary: dict[str, Any]) -> None:
    db[STAGING].rename(PUBLISHED, dropTarget=True)
    db[PUBLICATIONS].insert_one({"snapshot_date": manifest["snapshot_date"], "manifest": manifest,
                                 "summary": summary})
