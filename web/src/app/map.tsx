"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { addProtocol, Map as MLMap, Marker, NavigationControl, removeProtocol, setWorkerUrl } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";

import { AnalysisPanel } from "./analysis-panel";
import { fetchIsochrone, fetchSiteAnalysis, GisApiError } from "./gis-api";
import type { Duration, FacilityCategory, Isochrone, Origin, SiteAnalysis, TravelMode } from "./gis-types";
import { MapControls, type FacilitySettings } from "./map-controls";
import { syncAnalysisOverlays, type AnalysisOverlayState } from "./map-overlays";
import { createBasemapStyle, type BasemapTheme } from "./map-style";

interface RequestState<T> { loading: boolean; error: string | null; data: T | null; }
const idle = <T,>(): RequestState<T> => ({ loading: false, error: null, data: null });
const initialFacilities: FacilitySettings = {
  education: { enabled: true, weight: 20 }, healthcare: { enabled: true, weight: 20 },
  daily_shopping: { enabled: true, weight: 20 }, parks: { enabled: true, weight: 20 },
  public_transport: { enabled: true, weight: 20 },
};
function safeMessage(error: unknown): string {
  return error instanceof GisApiError ? error.message : "请求失败，请稍后重试";
}

export default function MapView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const appliedThemeRef = useRef<BasemapTheme>("standard");
  const commuteGeneration = useRef(0);
  const siteGeneration = useRef(0);
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [theme, setTheme] = useState<BasemapTheme>("standard");
  const [mode, setMode] = useState<TravelMode>("walking");
  const [minutes, setMinutes] = useState<Duration>(15);
  const [populationVisible, setPopulationVisible] = useState(true);
  const [populationUnavailable, setPopulationUnavailable] = useState(false);
  const [facilitiesVisible, setFacilitiesVisible] = useState(true);
  const [facilities, setFacilities] = useState<FacilitySettings>(initialFacilities);
  const [commute, setCommute] = useState<RequestState<Isochrone>>(idle);
  const [site, setSite] = useState<RequestState<SiteAnalysis>>(idle);
  const [mapStatus, setMapStatus] = useState("加载中");

  const overlayState: AnalysisOverlayState = useMemo(() => ({
    populationVisible: populationVisible && !populationUnavailable,
    facilitiesVisible,
    enabledFacilities: (Object.keys(facilities) as FacilityCategory[]).filter((category) => facilities[category].enabled),
    commute: commute.data?.geometry ?? null,
    site: site.data,
  }), [populationVisible, populationUnavailable, facilitiesVisible, facilities, commute.data, site.data]);
  const overlayStateRef = useRef(overlayState);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const protocol = new Protocol();
    addProtocol("pmtiles", protocol.tile);
    const assetBase = window.location.origin;
    setWorkerUrl(`${assetBase}/maplibre-gl-worker.mjs`);
    const map = new MLMap({ container: containerRef.current, style: createBasemapStyle("standard", assetBase), center: [-122.4194, 37.7749], zoom: 10.5 });
    map.addControl(new NavigationControl(), "bottom-left");
    map.on("click", (event) => setOrigin({ lng: event.lngLat.lng, lat: event.lngLat.lat }));
    map.on("style.load", () => {
      syncAnalysisOverlays(map, overlayStateRef.current);
      setMapStatus("可选点");
    });
    map.on("error", (event) => {
      const sourceId = (event as unknown as { sourceId?: string; source?: { id?: string } }).sourceId
        ?? (event as unknown as { source?: { id?: string } }).source?.id;
      if (sourceId === "population-density") {
        setPopulationUnavailable(true);
        setPopulationVisible(false);
      } else setMapStatus("部分地图资源暂不可用");
    });
    mapRef.current = map;
    return () => {
      markerRef.current?.remove();
      markerRef.current = null;
      map.remove();
      removeProtocol("pmtiles");
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !origin) return;
    if (!markerRef.current) {
      const marker = new Marker({ draggable: true, color: "#A33F2A" }).setLngLat(origin).addTo(map);
      marker.on("dragend", () => {
        const point = marker.getLngLat();
        setOrigin({ lng: point.lng, lat: point.lat });
      });
      markerRef.current = marker;
    } else markerRef.current.setLngLat(origin);
  }, [origin]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || appliedThemeRef.current === theme) return;
    appliedThemeRef.current = theme;
    setMapStatus("切换底图中");
    map.setStyle(createBasemapStyle(theme, window.location.origin));
  }, [theme]);

  useEffect(() => {
    const map = mapRef.current;
    overlayStateRef.current = overlayState;
    if (map?.isStyleLoaded()) syncAnalysisOverlays(map, overlayState);
  }, [overlayState]);

  useEffect(() => {
    if (!origin) return;
    const controller = new AbortController();
    const generation = ++commuteGeneration.current;
    queueMicrotask(() => {
      if (!controller.signal.aborted && generation === commuteGeneration.current) setCommute({ loading: true, error: null, data: null });
    });
    fetchIsochrone({ ...origin, mode, minutes }, controller.signal)
      .then((data) => { if (generation === commuteGeneration.current) setCommute({ loading: false, error: null, data }); })
      .catch((error: unknown) => {
        if ((error as { name?: string })?.name !== "AbortError" && generation === commuteGeneration.current) setCommute({ loading: false, error: safeMessage(error), data: null });
      });
    return () => controller.abort();
  }, [origin, mode, minutes]);

  useEffect(() => {
    if (!origin) return;
    const controller = new AbortController();
    const generation = ++siteGeneration.current;
    queueMicrotask(() => {
      if (!controller.signal.aborted && generation === siteGeneration.current) setSite({ loading: true, error: null, data: null });
    });
    fetchSiteAnalysis(origin, controller.signal)
      .then((data) => { if (generation === siteGeneration.current) setSite({ loading: false, error: null, data }); })
      .catch((error: unknown) => {
        if ((error as { name?: string })?.name !== "AbortError" && generation === siteGeneration.current) setSite({ loading: false, error: safeMessage(error), data: null });
      });
    return () => controller.abort();
  }, [origin]);

  const changeFacility = (category: FacilityCategory, change: Partial<FacilitySettings[FacilityCategory]>) => {
    setFacilities((current) => ({ ...current, [category]: { ...current[category], ...change } }));
  };

  return (
    <main className="map-shell">
      <div ref={containerRef} className="map-canvas" aria-label="湾区选址地图" />
      <div className="map-status" role="status">地图 {mapStatus}{origin ? " · 拖动标记可重新分析" : " · 点击设置起点"}</div>
      <MapControls theme={theme} mode={mode} minutes={minutes} populationVisible={populationVisible}
        populationUnavailable={populationUnavailable} facilitiesVisible={facilitiesVisible} facilities={facilities}
        onThemeChange={setTheme} onModeChange={setMode} onMinutesChange={setMinutes}
        onPopulationVisibleChange={setPopulationVisible} onFacilitiesVisibleChange={setFacilitiesVisible}
        onFacilityChange={changeFacility} />
      <AnalysisPanel origin={origin} commute={commute} site={site} facilities={facilities} />
    </main>
  );
}
