import type { FilterSpecification, LayerSpecification, SourceSpecification } from "maplibre-gl";
import type { FeatureCollection } from "geojson";

import type {
  FacilityCategory,
  GeoJsonFeatureCollection,
  SiteAnalysis,
} from "./gis-types";

export interface OverlayMap {
  getSource(id: string): unknown;
  addSource(id: string, source: SourceSpecification): void;
  getLayer(id: string): unknown;
  addLayer(layer: LayerSpecification): void;
  setLayoutProperty(id: string, name: string, value: unknown): void;
  setFilter(id: string, filter: FilterSpecification | null): void;
}

export interface AnalysisOverlayState {
  populationVisible: boolean;
  facilitiesVisible: boolean;
  enabledFacilities: readonly FacilityCategory[];
  commute: GeoJsonFeatureCollection | null;
  site: SiteAnalysis | null;
}

const EMPTY: GeoJsonFeatureCollection = { type: "FeatureCollection", features: [] };

const sources: Array<[string, SourceSpecification]> = [
  [
    "population-density",
    { type: "geojson", data: "/data/bay-area-tract-density.geojson" },
  ],
  ["site-catchment", { type: "geojson", data: EMPTY as FeatureCollection }],
  ["commute-isochrone", { type: "geojson", data: EMPTY as FeatureCollection }],
  ["analysis-facilities", { type: "geojson", data: EMPTY as FeatureCollection }],
];

const layers: LayerSpecification[] = [
  {
    id: "population-density-fill",
    type: "fill",
    source: "population-density",
    paint: {
      "fill-color": [
        "interpolate", ["linear"], ["get", "population_density_km2"],
        0, "#FFF4C2", 1000, "#E9B44C", 4000, "#C55A35", 10000, "#7C2D12",
      ],
      "fill-opacity": 0.48,
      "fill-outline-color": "#9B6A3B",
    },
  },
  {
    id: "site-catchment-fill",
    type: "fill",
    source: "site-catchment",
    paint: { "fill-color": "#176B63", "fill-opacity": 0.12 },
  },
  {
    id: "site-catchment-line",
    type: "line",
    source: "site-catchment",
    paint: { "line-color": "#176B63", "line-width": 2, "line-dasharray": [2, 1] },
  },
  {
    id: "commute-isochrone-fill",
    type: "fill",
    source: "commute-isochrone",
    paint: { "fill-color": "#C55A35", "fill-opacity": 0.2 },
  },
  {
    id: "commute-isochrone-line",
    type: "line",
    source: "commute-isochrone",
    paint: { "line-color": "#A33F2A", "line-width": 2.5 },
  },
  {
    id: "facilities-parks-fill",
    type: "fill",
    source: "analysis-facilities",
    filter: ["all", ["==", ["geometry-type"], "Polygon"], ["==", ["get", "category"], "parks"]],
    paint: { "fill-color": "#3E7C59", "fill-opacity": 0.52, "fill-outline-color": "#24513A" },
  },
  {
    id: "facilities-points",
    type: "circle",
    source: "analysis-facilities",
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-color": [
        "match", ["get", "category"],
        "education", "#3A6EA5", "healthcare", "#B44242",
        "daily_shopping", "#B7791F", "parks", "#3E7C59",
        "public_transport", "#554C8C", "#4B5563",
      ],
      "circle-radius": 5,
      "circle-stroke-color": "#FFFFFF",
      "circle-stroke-width": 1.25,
    },
  },
];

function setVisibility(map: OverlayMap, ids: string[], visible: boolean): void {
  for (const id of ids) {
    if (map.getLayer(id)) {
      map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    }
  }
}

export function syncAnalysisOverlays(
  map: OverlayMap,
  state: AnalysisOverlayState,
): void {
  for (const [id, source] of sources) {
    if (!map.getSource(id)) map.addSource(id, structuredClone(source));
  }
  for (const layer of layers) {
    if (!map.getLayer(layer.id)) map.addLayer(structuredClone(layer));
  }

  const update = (id: string, data: GeoJsonFeatureCollection) => {
    const source = map.getSource(id);
    if (source && typeof source === "object" && "setData" in source && typeof source.setData === "function") {
      source.setData(data);
    }
  };
  update("site-catchment", state.site?.catchment ?? EMPTY);
  update("commute-isochrone", state.commute ?? EMPTY);
  update("analysis-facilities", state.site?.facilities ?? EMPTY);

  setVisibility(map, ["population-density-fill"], state.populationVisible);
  setVisibility(map, ["site-catchment-fill", "site-catchment-line"], Boolean(state.site));
  setVisibility(map, ["commute-isochrone-fill", "commute-isochrone-line"], Boolean(state.commute));
  setVisibility(map, ["facilities-parks-fill", "facilities-points"], state.facilitiesVisible && Boolean(state.site));

  const categoryFilter = ["in", ["get", "category"], ["literal", [...state.enabledFacilities]]];
  for (const id of ["facilities-parks-fill", "facilities-points"]) {
    if (map.getLayer(id)) {
      const geometryFilter = id === "facilities-parks-fill"
        ? ["==", ["geometry-type"], "Polygon"]
        : ["==", ["geometry-type"], "Point"];
      map.setFilter(id, ["all", geometryFilter, categoryFilter] as unknown as FilterSpecification);
    }
  }
}
