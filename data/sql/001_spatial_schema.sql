CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS tracts (
  geoid text PRIMARY KEY,
  county_name text NOT NULL,
  population integer NOT NULL CHECK (population >= 0),
  aland_m2 bigint NOT NULL CHECK (aland_m2 >= 0),
  geom geometry(MultiPolygon, 4326) NOT NULL,
  representative_point geometry(Point, 4326) NOT NULL,
  CHECK (aland_m2 > 0 OR population = 0)
);

CREATE TABLE IF NOT EXISTS facilities (
  osm_key text PRIMARY KEY,
  category text NOT NULL CHECK (
    category IN (
      'education',
      'healthcare',
      'daily_shopping',
      'parks',
      'public_transport'
    )
  ),
  name text,
  geom geometry(Geometry, 4326) NOT NULL,
  analysis_point geometry(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS facility_baseline_samples (
  geoid text PRIMARY KEY REFERENCES tracts(geoid) ON DELETE CASCADE,
  population_weight integer NOT NULL CHECK (population_weight >= 0),
  education double precision NOT NULL,
  healthcare double precision NOT NULL,
  daily_shopping double precision NOT NULL,
  parks double precision NOT NULL,
  public_transport double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_metadata (
  key text PRIMARY KEY,
  value jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracts_geom
  ON tracts USING GiST (geom);
CREATE INDEX IF NOT EXISTS idx_facilities_analysis_point
  ON facilities USING GiST (analysis_point);
CREATE INDEX IF NOT EXISTS idx_facilities_geom
  ON facilities USING GiST (geom);
