import type {
  Config,
  Duration,
  FacilityCategory,
  GeoJsonFeatureCollection,
  Isochrone,
  Origin,
  SiteAnalysis,
  TravelMode,
} from "./gis-types";

const FACILITY_CATEGORIES = [
  "education",
  "healthcare",
  "daily_shopping",
  "parks",
  "public_transport",
] as const;
const GENERIC_MESSAGE = "请求失败，请稍后重试";

export class GisApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "GisApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): error is Error | DOMException {
  return (
    (error instanceof DOMException || error instanceof Error) &&
    error.name === "AbortError"
  );
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isOrigin(value: unknown): value is Origin {
  return (
    isRecord(value) &&
    isFiniteNumber(value.lng) &&
    isFiniteNumber(value.lat)
  );
}

function isFeatureCollection(
  value: unknown,
  requireContour: boolean,
): value is GeoJsonFeatureCollection {
  return (
    isRecord(value) &&
    value.type === "FeatureCollection" &&
    Array.isArray(value.features) &&
    (!requireContour || value.features.length > 0) &&
    value.features.every(
      (feature) =>
        isRecord(feature) &&
        feature.type === "Feature" &&
        isRecord(feature.geometry) &&
        typeof feature.geometry.type === "string" &&
        "coordinates" in feature.geometry &&
        (!requireContour ||
          (isRecord(feature.properties) && isFiniteNumber(feature.properties.contour))),
    )
  );
}

function isContourFeatureCollection(
  value: unknown,
  minutes: number,
): value is GeoJsonFeatureCollection {
  return (
    isFeatureCollection(value, true) &&
    value.features.every(
      (feature) =>
        ["Polygon", "MultiPolygon"].includes(feature.geometry.type) &&
        isRecord(feature.properties) &&
        feature.properties.contour === minutes,
    )
  );
}

function isConfig(value: unknown): value is Config {
  if (!isRecord(value) || !isRecord(value.modes)) return false;
  if (
    value.modes.walking !== "available" ||
    value.modes.driving !== "available" ||
    value.modes.public_transit !== "coming_soon"
  ) {
    return false;
  }
  const durations = value.durations;
  if (
    !Array.isArray(durations) ||
    durations.length !== 4 ||
    ![15, 30, 45, 60].every((duration) => durations.includes(duration))
  ) {
    return false;
  }
  if (!Array.isArray(value.facilities) || value.facilities.length !== 5) return false;
  const facilities = new Map(
    value.facilities.map((item) =>
      isRecord(item) ? [item.id, item] : [undefined, undefined],
    ),
  );
  if (
    !FACILITY_CATEGORIES.every((category) => {
      const item = facilities.get(category);
      return (
        isRecord(item) &&
        typeof item.name === "string" &&
        isFiniteNumber(item.default_weight) &&
        item.default_weight >= 0
      );
    })
  ) {
    return false;
  }
  if (!isRecord(value.data) || !isRecord(value.components)) return false;
  const components = value.components;
  return (
    typeof value.data.coverage === "string" &&
    isFiniteNumber(value.data.acs_year) &&
    typeof value.data.osm_version === "string" &&
    typeof value.data.version === "string" &&
    (typeof value.data.generated_at === "string" || value.data.generated_at === null) &&
    ["valhalla", "postgis", "population_density", "baseline"].every((key) =>
      ["ready", "unavailable"].includes(String(components[key])),
    )
  );
}

function safeServerError(value: unknown): GisApiError {
  if (!isRecord(value) || typeof value.code !== "string" || typeof value.message !== "string") {
    return new GisApiError("UNKNOWN_ERROR", GENERIC_MESSAGE);
  }
  const message = stableErrorMessage(value.code);
  return message
    ? new GisApiError(value.code, message)
    : new GisApiError("UNKNOWN_ERROR", GENERIC_MESSAGE);
}

function stableErrorMessage(code: string): string | undefined {
  switch (code) {
    case "POINT_OUTSIDE_COVERAGE":
      return "所选位置不在当前覆盖范围内";
    case "INVALID_MODE":
      return "暂不支持该交通方式";
    case "INVALID_DURATION":
      return "通勤时间必须为 15、30、45 或 60 分钟";
    case "VALHALLA_UNAVAILABLE":
      return "路网分析服务暂不可用";
    case "SPATIAL_DATA_UNAVAILABLE":
      return "空间数据服务暂不可用";
    case "ANALYSIS_TIMEOUT":
      return "分析请求超时，请稍后重试";
    default:
      return undefined;
  }
}

function isIsochrone(value: unknown): value is Isochrone {
  return (
    isRecord(value) &&
    isOrigin(value.origin) &&
    (value.mode === "walking" || value.mode === "driving") &&
    typeof value.minutes === "number" &&
    [15, 30, 45, 60].includes(value.minutes) &&
    isContourFeatureCollection(value.geometry, value.minutes) &&
    typeof value.data_version === "string" &&
    value.traffic_assumption === "static_network_cost"
  );
}

function hasFiveCategories(value: unknown): value is Record<FacilityCategory, unknown> {
  return (
    isRecord(value) &&
    Object.keys(value).length === FACILITY_CATEGORIES.length &&
    FACILITY_CATEGORIES.every((category) => category in value)
  );
}

function isMetric(value: unknown, category: FacilityCategory): boolean {
  if (
    !isRecord(value) ||
    !isFiniteNumber(value.value) ||
    value.value < 0 ||
    !(
      value.score === null ||
      (isFiniteNumber(value.score) && value.score >= 0 && value.score <= 100)
    )
  ) {
    return false;
  }
  return category === "parks"
    ? value.unit === "hectares_per_1000_people" &&
        value.raw_count === null &&
        isFiniteNumber(value.raw_area_hectares) &&
        value.raw_area_hectares >= 0
    : value.unit === "count_per_1000_people" &&
        Number.isInteger(value.raw_count) &&
        Number(value.raw_count) >= 0 &&
        value.raw_area_hectares === null;
}

function isSiteAnalysis(value: unknown): value is SiteAnalysis {
  const metrics = isRecord(value) ? value.metrics : undefined;
  const scores = isRecord(value) ? value.scores : undefined;
  if (
    !isRecord(value) ||
    !isOrigin(value.origin) ||
    !isFeatureCollection(value.catchment, true) ||
    !isFeatureCollection(value.facilities, false) ||
    !isFiniteNumber(value.population) ||
    value.population < 0 ||
    typeof value.population_method !== "string" ||
    !hasFiveCategories(metrics) ||
    !hasFiveCategories(scores)
  ) {
    return false;
  }
  for (const category of FACILITY_CATEGORIES) {
    const score = scores[category];
    if (
      !isMetric(metrics[category], category) ||
      !(
        score === null ||
        (isFiniteNumber(score) && score >= 0 && score <= 100)
      ) ||
      !isRecord(metrics[category]) ||
      metrics[category].score !== score
    ) {
      return false;
    }
  }
  const scoresAreNull = FACILITY_CATEGORIES.every((category) => scores[category] === null);
  const scoresAreNumbers = FACILITY_CATEGORIES.every((category) =>
    isFiniteNumber(scores[category]),
  );
  return (
    ((value.score_status === "ok" && scoresAreNumbers) ||
      (value.score_status === "insufficient_population" && scoresAreNull)) &&
    typeof value.data_version === "string" &&
    Number.isInteger(value.acs_year) &&
    Array.isArray(value.limitations) &&
    value.limitations.every((item) => typeof item === "string") &&
    typeof value.scoring_method === "string"
  );
}

async function requestJson<T>(
  url: string,
  signal: AbortSignal,
  validate: (value: unknown) => value is T,
  init?: RequestInit,
): Promise<T> {
  try {
    const response = await fetch(url, { ...init, signal });
    let body: unknown;
    try {
      body = await response.json();
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new GisApiError("UNKNOWN_ERROR", GENERIC_MESSAGE);
    }
    if (!response.ok) throw safeServerError(body);
    if (!validate(body)) throw new GisApiError("INVALID_RESPONSE", "服务返回的数据格式无效");
    return body;
  } catch (error) {
    if (isAbortError(error)) throw error;
    if (error instanceof GisApiError) throw error;
    throw new GisApiError("UNKNOWN_ERROR", GENERIC_MESSAGE);
  }
}

function apiBase(): string {
  return `http://${window.location.hostname}:8000`;
}

export function fetchConfig(signal: AbortSignal): Promise<Config> {
  return requestJson(`${apiBase()}/gis/config`, signal, isConfig);
}

export interface IsochroneRequest extends Origin {
  mode: TravelMode;
  minutes: Duration;
}

export function fetchIsochrone(
  request: IsochroneRequest,
  signal: AbortSignal,
): Promise<Isochrone> {
  const url = new URL("/gis/isochrone", `${apiBase()}/`);
  url.search = new URLSearchParams({
    lng: String(request.lng),
    lat: String(request.lat),
    mode: request.mode,
    minutes: String(request.minutes),
  }).toString();
  return requestJson(url.toString(), signal, isIsochrone);
}

export function fetchSiteAnalysis(
  origin: Origin,
  signal: AbortSignal,
): Promise<SiteAnalysis> {
  return requestJson(`${apiBase()}/gis/site-analysis`, signal, isSiteAnalysis, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ origin }),
  });
}
