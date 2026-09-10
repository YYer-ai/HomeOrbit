import { readFileSync } from "node:fs";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MapControls } from "../map-controls";
import MapView from "../map";
import { GisApiError } from "../gis-api";
import type { Isochrone, Origin, SiteAnalysis } from "../gis-types";

const mapMocks = vi.hoisted(() => ({
  maps: [] as Array<{
    handlers: Map<string, (event: never) => void>;
    sources: Map<string, { setData: ReturnType<typeof vi.fn> }>;
    layers: Map<string, Record<string, unknown>>;
    setStyle: ReturnType<typeof vi.fn>;
  }>,
  markers: [] as Array<{ options: unknown; handlers: Map<string, () => void>; point: { lng: number; lat: number } }>,
  fetchIsochrone: vi.fn<(request: unknown, signal: AbortSignal) => Promise<unknown>>(() => new Promise(() => undefined)),
  fetchSiteAnalysis: vi.fn<(origin: unknown, signal: AbortSignal) => Promise<unknown>>(() => new Promise(() => undefined)),
}));

vi.mock("../gis-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../gis-api")>()),
  fetchIsochrone: mapMocks.fetchIsochrone,
  fetchSiteAnalysis: mapMocks.fetchSiteAnalysis,
}));

vi.mock("pmtiles", () => ({ Protocol: class { tile = vi.fn(); } }));
vi.mock("maplibre-gl", () => ({
  addProtocol: vi.fn(), removeProtocol: vi.fn(), setWorkerUrl: vi.fn(),
  NavigationControl: class {},
  Map: class {
    handlers = new Map<string, (event: never) => void>();
    sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
    layers = new Map<string, Record<string, unknown>>();
    setStyle = vi.fn();
    constructor() { mapMocks.maps.push(this); }
    addControl() {}
    on(name: string, handler: (event: never) => void) { this.handlers.set(name, handler); }
    isStyleLoaded() { return true; }
    getSource(id: string) { return this.sources.get(id); }
    addSource(id: string) { this.sources.set(id, { setData: vi.fn() }); }
    getLayer(id: string) { return this.layers.get(id); }
    getStyle() { return { layers: [...this.layers.values()].map((layer) => ({ id: String(layer.id), type: String(layer.type) })) }; }
    addLayer(layer: Record<string, unknown>) { this.layers.set(String(layer.id), layer); }
    setLayoutProperty(id: string, property: string, value: unknown) {
      const layer = this.layers.get(id)!;
      layer.layout = { ...(layer.layout as object), [property]: value };
    }
    setFilter(id: string, filter: unknown) { this.layers.get(id)!.filter = filter; }
    remove() {}
  },
  Marker: class {
    handlers = new Map<string, () => void>();
    point = { lng: 0, lat: 0 };
    constructor(public options: unknown) { mapMocks.markers.push(this); }
    setLngLat(point: { lng: number; lat: number }) { this.point = point; return this; }
    addTo() { return this; }
    on(name: string, handler: () => void) { this.handlers.set(name, handler); }
    getLngLat() { return this.point; }
    remove() {}
  },
}));

const facilities = {
  education: { enabled: true, weight: 20 },
  healthcare: { enabled: true, weight: 20 },
  daily_shopping: { enabled: true, weight: 20 },
  parks: { enabled: true, weight: 20 },
  public_transport: { enabled: true, weight: 20 },
};

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

const empty = { type: "FeatureCollection" as const, features: [] };
function commuteResult(origin: Origin, tag: string): Isochrone {
  return {
    origin, mode: "walking", minutes: 15,
    geometry: {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: { contour: 15, tag }, geometry: { type: "Polygon", coordinates: [] } }],
    },
    data_version: tag, traffic_assumption: "static_network_cost",
  };
}

function siteResult(origin: Origin, population: number, tag: string): SiteAnalysis {
  const score = 60;
  return {
    origin, catchment: { ...empty, features: [{ type: "Feature", properties: { tag }, geometry: { type: "Polygon", coordinates: [] } }] },
    population, population_method: "area_weighted_tract_population",
    metrics: {
      education: { value: 1, unit: "count_per_1000_people", score, raw_count: 1, raw_area_hectares: null },
      healthcare: { value: 1, unit: "count_per_1000_people", score, raw_count: 1, raw_area_hectares: null },
      daily_shopping: { value: 1, unit: "count_per_1000_people", score, raw_count: 1, raw_area_hectares: null },
      parks: { value: 1, unit: "hectares_per_1000_people", score, raw_count: null, raw_area_hectares: 1 },
      public_transport: { value: 1, unit: "count_per_1000_people", score, raw_count: 1, raw_area_hectares: null },
    },
    facilities: { ...empty, features: [{ type: "Feature", properties: { category: "education", tag }, geometry: { type: "Point", coordinates: origin.lng === 0 ? [] : [origin.lng, origin.lat] } }] },
    scores: { education: score, healthcare: score, daily_shopping: score, parks: score, public_transport: score },
    score_status: "ok", data_version: tag, acs_year: 2023, limitations: [], scoring_method: "relative",
  };
}

beforeEach(() => {
  mapMocks.maps.length = 0;
  mapMocks.markers.length = 0;
  mapMocks.fetchIsochrone.mockReset().mockImplementation(() => new Promise(() => undefined));
  mapMocks.fetchSiteAnalysis.mockReset().mockImplementation(() => new Promise(() => undefined));
});
afterEach(cleanup);

describe("MapControls", () => {
  it("提供三种底图、两种可用方式和禁用的公共交通建设中入口", () => {
    render(
      <MapControls
        theme="standard"
        mode="walking"
        minutes={15}
        populationVisible
        facilitiesVisible
        facilities={facilities}
        onThemeChange={vi.fn()}
        onModeChange={vi.fn()}
        onMinutesChange={vi.fn()}
        onPopulationVisibleChange={vi.fn()}
        onFacilitiesVisibleChange={vi.fn()}
        onFacilityChange={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("radio", { name: /标准地图|交通网图|分析浅色图/ })).toHaveLength(3);
    expect(screen.getByRole("radio", { name: "公共交通（建设中）" })).toBeDisabled();
    expect(screen.getAllByRole("radio", { name: /15 分钟|30 分钟|45 分钟|60 分钟/ })).toHaveLength(4);
  });

  it("五类设施都有独立开关和滑杆，交互只回传前端状态", () => {
    const onFacilityChange = vi.fn();
    render(
      <MapControls
        theme="standard"
        mode="walking"
        minutes={15}
        populationVisible
        facilitiesVisible
        facilities={facilities}
        onThemeChange={vi.fn()}
        onModeChange={vi.fn()}
        onMinutesChange={vi.fn()}
        onPopulationVisibleChange={vi.fn()}
        onFacilitiesVisibleChange={vi.fn()}
        onFacilityChange={onFacilityChange}
      />,
    );

    expect(screen.getAllByRole("slider")).toHaveLength(5);
    expect(screen.getAllByRole("slider").every((slider) => slider.getAttribute("max") === "100" && (slider as HTMLInputElement).value === "20")).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "启用教育" }));
    fireEvent.change(screen.getByRole("slider", { name: "教育权重" }), {
      target: { value: "35" },
    });
    expect(onFacilityChange).toHaveBeenNthCalledWith(1, "education", { enabled: false });
    expect(onFacilityChange).toHaveBeenNthCalledWith(2, "education", { weight: 35 });
  });

  it("手机断点保留人口与设施总图层开关", () => {
    const css = readFileSync(`${process.cwd()}/src/app/globals.css`, "utf8");
    const mobile = css.slice(css.indexOf("@media (max-width:560px)"));
    expect(mobile).not.toMatch(/\.layer-toggles\s*\{[^}]*display\s*:\s*none/);
    expect(mobile).toMatch(/\.layer-toggles\s*\{[^}]*grid-template-columns\s*:\s*repeat\(2,1fr\)/);
  });
});

describe("MapView 请求依赖", () => {
  it("无起点不请求；权重与主题不请求；时长只重发通勤；拖动重发两路并取消旧请求", async () => {
    render(<MapView />);
    expect(screen.getAllByRole("slider").map((slider) => (slider as HTMLInputElement).value)).toEqual(["20", "20", "20", "20", "20"]);
    expect(mapMocks.fetchIsochrone).not.toHaveBeenCalled();
    expect(mapMocks.fetchSiteAnalysis).not.toHaveBeenCalled();

    const map = mapMocks.maps[0];
    act(() => map.handlers.get("click")?.({ lngLat: { lng: -122.4, lat: 37.7 } } as never));
    await waitFor(() => expect(mapMocks.fetchIsochrone).toHaveBeenCalledTimes(1));
    expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(1);
    expect(mapMocks.markers[0].options).toMatchObject({ draggable: true });

    fireEvent.change(screen.getByRole("slider", { name: "教育权重" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("radio", { name: "交通网图" }));
    expect(mapMocks.fetchIsochrone).toHaveBeenCalledTimes(1);
    expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(1);
    expect(map.setStyle).toHaveBeenCalledTimes(1);

    const firstCommuteSignal = mapMocks.fetchIsochrone.mock.calls[0][1] as AbortSignal;
    fireEvent.click(screen.getByRole("radio", { name: "30 分钟" }));
    await waitFor(() => expect(mapMocks.fetchIsochrone).toHaveBeenCalledTimes(2));
    expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(1);
    expect(firstCommuteSignal.aborted).toBe(true);

    const firstSiteSignal = mapMocks.fetchSiteAnalysis.mock.calls[0][1] as AbortSignal;
    act(() => {
      mapMocks.markers[0].point = { lng: -122.3, lat: 37.8 };
      mapMocks.markers[0].handlers.get("dragend")?.();
    });
    await waitFor(() => expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(2));
    expect(mapMocks.fetchIsochrone).toHaveBeenCalledTimes(3);
    expect(firstSiteSignal.aborted).toBe(true);
  });

  it("新点立即清除旧数据，过期响应晚返回也不能覆盖最新点", async () => {
    const commute = [deferred<Isochrone>(), deferred<Isochrone>(), deferred<Isochrone>()];
    const site = [deferred<SiteAnalysis>(), deferred<SiteAnalysis>(), deferred<SiteAnalysis>()];
    commute.forEach((item) => mapMocks.fetchIsochrone.mockImplementationOnce(() => item.promise));
    site.forEach((item) => mapMocks.fetchSiteAnalysis.mockImplementationOnce(() => item.promise));
    render(<MapView />);
    const map = mapMocks.maps[0];
    const origins = [
      { lng: -122.4, lat: 37.7 },
      { lng: -122.3, lat: 37.8 },
      { lng: -122.2, lat: 37.9 },
    ];

    act(() => map.handlers.get("click")?.({ lngLat: origins[0] } as never));
    await waitFor(() => expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(1));
    await act(async () => {
      commute[0].resolve(commuteResult(origins[0], "first"));
      site[0].resolve(siteResult(origins[0], 1111, "first"));
      await Promise.resolve();
    });
    expect(await screen.findByText("1,111")).toBeInTheDocument();

    act(() => map.handlers.get("click")?.({ lngLat: origins[1] } as never));
    await waitFor(() => expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("1,111")).not.toBeInTheDocument();
    expect(screen.getByText("计算中")).toBeInTheDocument();
    expect(screen.getByText("分析中")).toBeInTheDocument();

    act(() => map.handlers.get("click")?.({ lngLat: origins[2] } as never));
    await waitFor(() => expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(3));
    await act(async () => {
      commute[2].resolve(commuteResult(origins[2], "latest"));
      site[2].resolve(siteResult(origins[2], 3333, "latest"));
      await Promise.resolve();
    });
    expect(await screen.findByText("3,333")).toBeInTheDocument();

    await act(async () => {
      commute[1].resolve(commuteResult(origins[1], "stale"));
      site[1].resolve(siteResult(origins[1], 2222, "stale"));
      await Promise.resolve();
    });
    expect(screen.getByText("3,333")).toBeInTheDocument();
    expect(screen.queryByText("2,222")).not.toBeInTheDocument();
    const siteSource = map.sources.get("site-catchment")?.setData;
    expect(siteSource).toHaveBeenLastCalledWith(expect.objectContaining({ features: [expect.objectContaining({ properties: { tag: "latest" } })] }));
  });

  it("AbortError 不显示错误，通勤单路失败仍保留设施结果、marker 和控制", async () => {
    const aborted = new DOMException("cancelled", "AbortError");
    mapMocks.fetchIsochrone.mockRejectedValueOnce(aborted);
    mapMocks.fetchSiteAnalysis.mockResolvedValueOnce(siteResult({ lng: -122.4, lat: 37.7 }, 4200, "site-ok"));
    render(<MapView />);
    const map = mapMocks.maps[0];
    act(() => map.handlers.get("click")?.({ lngLat: { lng: -122.4, lat: 37.7 } } as never));
    expect(await screen.findByText("4,200")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mapMocks.markers).toHaveLength(1);
    expect(screen.getByRole("radio", { name: "标准地图" })).toBeChecked();

    cleanup();
    mapMocks.maps.length = 0;
    mapMocks.markers.length = 0;
    mapMocks.fetchIsochrone.mockReset().mockRejectedValueOnce(new GisApiError("VALHALLA_UNAVAILABLE", "路网分析服务暂不可用"));
    mapMocks.fetchSiteAnalysis.mockReset().mockResolvedValueOnce(siteResult({ lng: -122.4, lat: 37.7 }, 4300, "site-kept"));
    render(<MapView />);
    const failedMap = mapMocks.maps[0];
    act(() => failedMap.handlers.get("click")?.({ lngLat: { lng: -122.4, lat: 37.7 } } as never));
    expect(await screen.findByText("路网分析服务暂不可用")).toBeInTheDocument();
    expect(screen.getByText("4,300")).toBeInTheDocument();
    expect(mapMocks.markers).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: "圈内设施" })).toBeEnabled();
  });

  it("设施单路失败仍保留通勤结果和控制", async () => {
    mapMocks.fetchIsochrone.mockResolvedValueOnce(commuteResult({ lng: -122.4, lat: 37.7 }, "commute-ok"));
    mapMocks.fetchSiteAnalysis.mockRejectedValueOnce(new GisApiError("SPATIAL_DATA_UNAVAILABLE", "空间数据服务暂不可用"));
    render(<MapView />);
    const map = mapMocks.maps[0];
    act(() => map.handlers.get("click")?.({ lngLat: { lng: -122.4, lat: 37.7 } } as never));
    expect(await screen.findByText("空间数据服务暂不可用")).toBeInTheDocument();
    expect(screen.getByText("15", { selector: ".mono-reading" }).closest("p")).toHaveTextContent("15 分钟 · 步行");
    expect(screen.getByRole("radio", { name: "步行" })).toBeChecked();
  });

  it("主题 style.load 使用最新 overlay 和 visibility 恢复，且不产生空间请求", async () => {
    const origin = { lng: -122.4, lat: 37.7 };
    const commute = commuteResult(origin, "theme-latest");
    const site = siteResult(origin, 5000, "theme-latest");
    mapMocks.fetchIsochrone.mockResolvedValueOnce(commute);
    mapMocks.fetchSiteAnalysis.mockResolvedValueOnce(site);
    render(<MapView />);
    const map = mapMocks.maps[0];
    act(() => map.handlers.get("click")?.({ lngLat: origin } as never));
    expect(await screen.findByText("5,000")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "人口密度" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "圈内设施" }));
    const commuteCalls = mapMocks.fetchIsochrone.mock.calls.length;
    const siteCalls = mapMocks.fetchSiteAnalysis.mock.calls.length;
    fireEvent.click(screen.getByRole("radio", { name: "分析浅色图" }));
    expect(map.setStyle).toHaveBeenCalledTimes(1);
    map.sources.clear();
    map.layers.clear();
    act(() => map.handlers.get("style.load")?.({} as never));

    expect(map.layers.get("population-density-fill")?.layout).toMatchObject({ visibility: "none" });
    expect(map.layers.get("facilities-points")?.layout).toMatchObject({ visibility: "none" });
    expect(map.sources.get("commute-isochrone")?.setData).toHaveBeenLastCalledWith(commute.geometry);
    expect(map.sources.get("site-catchment")?.setData).toHaveBeenLastCalledWith(site.catchment);
    expect(mapMocks.fetchIsochrone).toHaveBeenCalledTimes(commuteCalls);
    expect(mapMocks.fetchSiteAnalysis).toHaveBeenCalledTimes(siteCalls);
  });
});
