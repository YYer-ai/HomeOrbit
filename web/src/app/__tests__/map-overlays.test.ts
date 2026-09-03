import { featureFilter, validateStyleMin } from "@maplibre/maplibre-gl-style-spec";
import { GeoJSONVT } from "@maplibre/geojson-vt";
import { describe, expect, it, vi } from "vitest";

import { syncAnalysisOverlays } from "../map-overlays";
import type { SiteAnalysis } from "../gis-types";

const empty = { type: "FeatureCollection" as const, features: [] };

function site(): SiteAnalysis {
  return {
    origin: { lng: -122.4, lat: 37.7 }, catchment: empty, population: 1000,
    population_method: "area_weighted_tract_population",
    metrics: {
      education: { value: 1, unit: "count_per_1000_people", score: 50, raw_count: 1, raw_area_hectares: null },
      healthcare: { value: 1, unit: "count_per_1000_people", score: 50, raw_count: 1, raw_area_hectares: null },
      daily_shopping: { value: 1, unit: "count_per_1000_people", score: 50, raw_count: 1, raw_area_hectares: null },
      parks: { value: 1, unit: "hectares_per_1000_people", score: 50, raw_count: null, raw_area_hectares: 1 },
      public_transport: { value: 1, unit: "count_per_1000_people", score: 50, raw_count: 1, raw_area_hectares: null },
    }, facilities: empty,
    scores: { education: 50, healthcare: 50, daily_shopping: 50, parks: 50, public_transport: 50 },
    score_status: "ok", data_version: "test", acs_year: 2023, limitations: [], scoring_method: "relative",
  };
}

class FakeMap {
  sources = new Map<string, { definition: unknown; setData: ReturnType<typeof vi.fn> }>();
  layers = new Map<string, Record<string, unknown>>();
  addSource = vi.fn((id: string, definition: unknown) => this.sources.set(id, { definition, setData: vi.fn() }));
  getSource = vi.fn((id: string) => this.sources.get(id));
  addLayer = vi.fn((layer: Record<string, unknown>) => this.layers.set(String(layer.id), layer));
  getLayer = vi.fn((id: string) => this.layers.get(id));
  setLayoutProperty = vi.fn((id: string, property: string, value: unknown) => {
    const layer = this.layers.get(id)!;
    layer.layout = { ...(layer.layout as object), [property]: value };
  });
  setFilter = vi.fn((id: string, filter: unknown) => {
    this.layers.get(id)!.filter = filter;
  });
  resetStyle() { this.sources.clear(); this.layers.clear(); }
}

const state = {
  populationVisible: true,
  facilitiesVisible: true,
  enabledFacilities: ["education", "healthcare", "daily_shopping", "parks", "public_transport"] as const,
  commute: empty,
  site: site(),
};

describe("syncAnalysisOverlays", () => {
  it("连续同步不重复 source/layer，现有 GeoJSON source 使用 setData", () => {
    const map = new FakeMap();
    syncAnalysisOverlays(map, state);
    const sourceCount = map.addSource.mock.calls.length;
    const layerCount = map.addLayer.mock.calls.length;
    syncAnalysisOverlays(map, { ...state, commute: { ...empty } });
    expect(map.addSource).toHaveBeenCalledTimes(sourceCount);
    expect(map.addLayer).toHaveBeenCalledTimes(layerCount);
    expect(map.sources.get("commute-isochrone")?.setData).toHaveBeenCalled();
    expect(map.sources.get("site-catchment")?.setData).toHaveBeenCalled();
    expect(map.sources.get("analysis-facilities")?.setData).toHaveBeenCalled();
  });

  it("style 清空后完整恢复人口、两个独立圈和设施图层", () => {
    const map = new FakeMap();
    syncAnalysisOverlays(map, state);
    map.resetStyle();
    syncAnalysisOverlays(map, state);
    expect([...map.sources.keys()]).toEqual([
      "population-density", "site-catchment", "commute-isochrone", "analysis-facilities",
    ]);
    expect([...map.layers.keys()]).toEqual(expect.arrayContaining([
      "population-density-fill", "site-catchment-fill", "site-catchment-line",
      "commute-isochrone-fill", "commute-isochrone-line", "facilities-parks-fill", "facilities-points",
    ]));
  });

  it("全部 overlay layer 通过 MapLibre 样式验证且人口使用实际属性", () => {
    const map = new FakeMap();
    syncAnalysisOverlays(map, state);
    const style = {
      version: 8 as const,
      sources: Object.fromEntries([...map.sources].map(([id, value]) => [id, value.definition])),
      layers: [...map.layers.values()],
    };
    expect(validateStyleMin(style as never).map((error) => error.message)).toEqual([]);
    expect(JSON.stringify(map.layers.get("population-density-fill"))).toContain("population_density_km2");
    expect(map.layers.get("facilities-parks-fill")?.filter).toEqual(expect.arrayContaining(["all"]));
  });

  it("官方 geometry-type 求值将真实 MultiPolygon 归为 Polygon，并保持点层和分类开关隔离", () => {
    const map = new FakeMap();
    syncAnalysisOverlays(map, state);
    const parkFilter = featureFilter(
      map.layers.get("facilities-parks-fill")?.filter as never,
      "layers.facilities-parks-fill.filter",
    ).filter;
    const pointFilter = featureFilter(
      map.layers.get("facilities-points")?.filter as never,
      "layers.facilities-points.filter",
    ).filter;
    const tile = new GeoJSONVT({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { category: "parks" },
          geometry: { type: "MultiPolygon", coordinates: [[[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]] },
        },
        {
          type: "Feature",
          properties: { category: "education" },
          geometry: { type: "Point", coordinates: [0, 0] },
        },
      ],
    }).getTile(0, 0, 0)!;
    const park = tile.features.find((feature) => feature.tags?.category === "parks")!;
    const education = tile.features.find((feature) => feature.tags?.category === "education")!;
    const parkEvaluationFeature = { type: park.type, properties: park.tags ?? {} };
    const educationEvaluationFeature = { type: education.type, properties: education.tags ?? {} };

    expect(park.type).toBe(3);

    expect(parkFilter({ zoom: 12 }, parkEvaluationFeature as never)).toBe(true);
    expect(pointFilter({ zoom: 12 }, parkEvaluationFeature as never)).toBe(false);
    expect(pointFilter({ zoom: 12 }, educationEvaluationFeature as never)).toBe(true);

    syncAnalysisOverlays(map, {
      ...state,
      enabledFacilities: ["education", "healthcare", "daily_shopping", "public_transport"],
    });
    const disabledParkFilter = featureFilter(
      map.layers.get("facilities-parks-fill")?.filter as never,
      "layers.facilities-parks-fill.filter",
    ).filter;
    expect(disabledParkFilter({ zoom: 12 }, parkEvaluationFeature as never)).toBe(false);
  });
});
