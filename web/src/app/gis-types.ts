export type TravelMode = "walking" | "driving";
export type Duration = 15 | 30 | 45 | 60;
export type FacilityCategory =
  | "education"
  | "healthcare"
  | "daily_shopping"
  | "parks"
  | "public_transport";
export type Availability = "available" | "coming_soon";
export type ComponentStatus = "ready" | "unavailable";
export type GisErrorCode =
  | "POINT_OUTSIDE_COVERAGE"
  | "INVALID_MODE"
  | "INVALID_DURATION"
  | "VALHALLA_UNAVAILABLE"
  | "SPATIAL_DATA_UNAVAILABLE"
  | "ANALYSIS_TIMEOUT";

export interface Origin {
  lng: number;
  lat: number;
}

export interface GeoJsonGeometry {
  type: string;
  coordinates: unknown;
}

export interface GeoJsonFeature {
  type: "Feature";
  properties: Record<string, unknown> | null;
  geometry: GeoJsonGeometry;
}

export interface GeoJsonFeatureCollection {
  type: "FeatureCollection";
  features: GeoJsonFeature[];
}

export interface FacilityConfig {
  id: FacilityCategory;
  name: string;
  default_weight: number;
}

export interface Config {
  modes: Record<TravelMode, "available"> & {
    public_transit: "coming_soon";
  };
  durations: Duration[];
  facilities: FacilityConfig[];
  data: {
    coverage: string;
    acs_year: number;
    osm_version: string;
    version: string;
    generated_at: string | null;
  };
  components: {
    valhalla: ComponentStatus;
    postgis: ComponentStatus;
    population_density: ComponentStatus;
    baseline: ComponentStatus;
  };
}

export interface Isochrone {
  origin: Origin;
  mode: TravelMode;
  minutes: Duration;
  geometry: GeoJsonFeatureCollection;
  data_version: string;
  traffic_assumption: "static_network_cost";
}

export interface FacilityMetric {
  value: number;
  unit: "count_per_1000_people" | "hectares_per_1000_people";
  score: number | null;
  raw_count: number | null;
  raw_area_hectares: number | null;
}

export interface SiteAnalysis {
  origin: Origin;
  catchment: GeoJsonFeatureCollection;
  population: number;
  population_method: string;
  metrics: Record<FacilityCategory, FacilityMetric>;
  facilities: GeoJsonFeatureCollection;
  scores: Record<FacilityCategory, number | null>;
  score_status: "ok" | "insufficient_population";
  data_version: string;
  acs_year: number;
  limitations: string[];
  scoring_method: string;
}

export interface GisError {
  code: GisErrorCode;
  message: string;
}
