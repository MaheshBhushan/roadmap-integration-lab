-- Staging schema for one snapshot. Executed with search_path = staging, public.
-- Geometry columns are deliberately unconstrained here so that bad input can land in staging and be
-- reported by the validation checks; publish() tightens them to geometry(LineString, 4326).

-- OpenStreetMap: one row per highway=* way (Geofabrik PBF). OSM's data model is key/value tags on
-- nodes and ways, so the source schema is the way id/version/timestamp plus an open tag map.
CREATE TABLE osm_ways (
    way_id        bigint PRIMARY KEY,
    version       integer NOT NULL,
    osm_timestamp timestamptz NOT NULL,
    highway       text NOT NULL,
    tags          jsonb NOT NULL,
    geom          geometry NOT NULL
);

-- Overture Maps transportation/segment (GeoParquet). Typed columns; restriction rules are arrays of
-- structs with optional `when` conditions and `between` linear-referencing ranges, kept as jsonb.
CREATE TABLE overture_segments (
    id                     text PRIMARY KEY,
    version                integer NOT NULL,
    subtype                text NOT NULL,
    class                  text,
    subclass               text,
    names                  jsonb,
    connectors             jsonb,
    road_surface           jsonb,
    road_flags             jsonb,
    width_rules            jsonb,
    level_rules            jsonb,
    access_restrictions    jsonb,
    speed_limits           jsonb,
    prohibited_transitions jsonb,
    destinations           jsonb,
    routes                 jsonb,
    sources                jsonb,
    geom                   geometry NOT NULL
);

-- Unified model used for comparison and publication.
CREATE TABLE road_segments (
    source        text NOT NULL CHECK (source IN ('osm', 'overture')),
    source_id     text NOT NULL,
    osm_way_id    bigint,
    osm_version   integer,
    road_class    text NOT NULL,
    name          text,
    maxspeed_kmh  real CHECK (maxspeed_kmh > 0),
    oneway        text CHECK (oneway IN ('forward', 'backward', 'both')),
    motor_access  text CHECK (motor_access IN
                      ('allowed', 'denied', 'destination', 'customers', 'delivery', 'private', 'other')),
    partial_rules boolean NOT NULL DEFAULT false,
    geom          geometry NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE INDEX road_segments_geom_idx ON road_segments USING gist (geom);
CREATE INDEX road_segments_way_idx ON road_segments (osm_way_id);
