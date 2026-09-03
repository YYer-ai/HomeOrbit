import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchConfig,
  fetchIsochrone,
  fetchSiteAnalysis,
  GisApiError,
} from "../gis-api";

const configResponse = {
  modes: { walking: "available", driving: "available", public_transit: "coming_soon" },
  durations: [15, 30, 45, 60],
  facilities: [
    { id: "education", name: "教育", default_weight: 20 },
    { id: "healthcare", name: "医疗", default_weight: 20 },
    { id: "daily_shopping", name: "日常购物", default_weight: 20 },
    { id: "parks", name: "公园绿地", default_weight: 20 },
    { id: "public_transport", name: "公共交通站点", default_weight: 20 },
  ],
  data: {
    coverage: "San Francisco Bay Area",
    acs_year: 2023,
    osm_version: "test-osm",
    version: "test-v1",
    generated_at: null,
  },
  components: {
    valhalla: "ready",
    postgis: "ready",
    population_density: "ready",
    baseline: "ready",
  },
};

const contour = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: { contour: 30 },
      geometry: {
        type: "Polygon",
        coordinates: [[[-122.5, 37.7], [-122.4, 37.7], [-122.5, 37.7]]],
      },
    },
  ],
};

const categories = [
  "education",
  "healthcare",
  "daily_shopping",
  "parks",
  "public_transport",
] as const;

const siteAnalysisResponse = {
  origin: { lng: -122.4194, lat: 37.7749 },
  catchment: contour,
  population: 1000,
  population_method: "按土地面积比例估算",
  metrics: Object.fromEntries(
    categories.map((category) => [
      category,
      category === "parks"
        ? {
            value: 2,
            unit: "hectares_per_1000_people",
            score: 60,
            raw_count: null,
            raw_area_hectares: 2,
          }
        : {
            value: 1,
            unit: "count_per_1000_people",
            score: 60,
            raw_count: 1,
            raw_area_hectares: null,
          },
    ]),
  ),
  facilities: { type: "FeatureCollection", features: [] },
  scores: Object.fromEntries(categories.map((category) => [category, 60])),
  score_status: "ok",
  data_version: "test-v1",
  acs_year: 2023,
  limitations: ["测试限制"],
  scoring_method: "湾区人口加权相对分数",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GIS API client", () => {
  it("fetchConfig 使用当前 hostname 并原样传递 AbortSignal", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(configResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConfig(signal)).resolves.toEqual(configResponse);
    expect(fetchMock).toHaveBeenCalledWith("http://family-host.test:8000/gis/config", {
      signal,
    });
  });

  it("fetchIsochrone 安全构造查询并验证 contour FeatureCollection", async () => {
    const signal = new AbortController().signal;
    const response = {
      origin: { lng: -122.4194, lat: 37.7749 },
      mode: "driving",
      minutes: 30,
      geometry: contour,
      data_version: "test-v1",
      traffic_assumption: "static_network_cost",
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchIsochrone(
        { lng: -122.4194, lat: 37.7749, mode: "driving", minutes: 30 },
        signal,
      ),
    ).resolves.toEqual(response);
    const [requestedUrl, init] = fetchMock.mock.calls[0];
    const url = new URL(requestedUrl);
    expect(`${url.origin}${url.pathname}`).toBe("http://family-host.test:8000/gis/isochrone");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      lng: "-122.4194",
      lat: "37.7749",
      mode: "driving",
      minutes: "30",
    });
    expect(init).toEqual({ signal });
  });

  it("fetchSiteAnalysis 仅发送 origin JSON 并传递 AbortSignal", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(siteAnalysisResponse));
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchSiteAnalysis({ lng: -122.4194, lat: 37.7749 }, signal),
    ).resolves.toEqual(siteAnalysisResponse);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://family-host.test:8000/gis/site-analysis",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ origin: { lng: -122.4194, lat: 37.7749 } }),
        signal,
      },
    );
  });

  it("保留 AbortError 的取消语义", async () => {
    const aborted = new DOMException("The operation was aborted", "AbortError");
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(aborted));

    await expect(fetchConfig(new AbortController().signal)).rejects.toBe(aborted);
  });

  it("保留 fetch 实现返回的普通 Error AbortError", async () => {
    const aborted = new Error("cancelled");
    aborted.name = "AbortError";
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(aborted));

    await expect(fetchConfig(new AbortController().signal)).rejects.toBe(aborted);
  });

  it("response.json 读取阶段取消时原样重抛 AbortError", async () => {
    const aborted = new DOMException("body cancelled", "AbortError");
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockRejectedValue(aborted) }),
    );

    await expect(fetchConfig(new AbortController().signal)).rejects.toBe(aborted);
  });

  it("仅接受安全的非 2xx code/message", async () => {
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { code: "VALHALLA_UNAVAILABLE", message: "路网分析服务暂不可用" },
          503,
        ),
      ),
    );

    await expect(fetchConfig(new AbortController().signal)).rejects.toEqual(
      new GisApiError("VALHALLA_UNAVAILABLE", "路网分析服务暂不可用"),
    );
  });

  it.each([
    ["/srv 路径", "SPATIAL_DATA_UNAVAILABLE", "读取 /srv/homeorbit/config.toml 失败"],
    ["/app 路径", "SPATIAL_DATA_UNAVAILABLE", "读取 /app/config.toml 失败"],
    ["HTML 标记", "SPATIAL_DATA_UNAVAILABLE", "服务返回 <script>alert(1)</script> 错误"],
  ])("已知错误码忽略服务端%s并使用固定消息", async (_name, code, message) => {
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ code, message }, 503)));

    await expect(fetchConfig(new AbortController().signal)).rejects.toEqual(
      new GisApiError("SPATIAL_DATA_UNAVAILABLE", "空间数据服务暂不可用"),
    );
  });

  it("未知错误码即使携带看似安全的中文消息也返回通用错误", async () => {
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ code: "UNKNOWN_SERVICE_ERROR", message: "服务暂不可用" }, 503),
      ),
    );

    await expect(fetchConfig(new AbortController().signal)).rejects.toEqual(
      new GisApiError("UNKNOWN_ERROR", "请求失败，请稍后重试"),
    );
  });

  it.each(["constructor", "toString", "__proto__"])(
    "原型链键 %s 不是错误白名单 own property",
    async (code) => {
      vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(jsonResponse({ code, message: "伪造安全消息" }, 503)),
      );

      await expect(fetchConfig(new AbortController().signal)).rejects.toEqual(
        new GisApiError("UNKNOWN_ERROR", "请求失败，请稍后重试"),
      );
    },
  );

  it.each([
    ["HTML", new Response("<html>C:/private/stack</html>", { status: 500 }), "UNKNOWN_ERROR", "请求失败，请稍后重试"],
    [
      "内部 URL",
      jsonResponse(
        { code: "VALHALLA_UNAVAILABLE", message: "连接 http://secret/C:/private 失败" },
        503,
      ),
      "VALHALLA_UNAVAILABLE",
      "路网分析服务暂不可用",
    ],
    ["超长文本", jsonResponse({ code: "FAIL", message: "错误".repeat(100) }, 500), "UNKNOWN_ERROR", "请求失败，请稍后重试"],
    ["畸形 JSON", new Response("{not-json", { status: 500 }), "UNKNOWN_ERROR", "请求失败，请稍后重试"],
  ])("%s 错误返回稳定中文消息且不泄露响应", async (_name, response, code, message) => {
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    const error = await fetchConfig(new AbortController().signal).catch((reason) => reason);
    expect(error).toBeInstanceOf(GisApiError);
    expect(error).toMatchObject({ code, message });
    expect(error.message).not.toMatch(/secret|private|stack|html/i);
  });

  it("成功响应若 contour 或五类 metrics/scores 结构无效则使用通用结构错误", async () => {
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    const invalidIsochrone = {
      origin: { lng: -122.4194, lat: 37.7749 },
      mode: "walking",
      minutes: 15,
      geometry: { type: "FeatureCollection", features: "C:/private" },
      data_version: "test",
      traffic_assumption: "static_network_cost",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(invalidIsochrone)));
    await expect(
      fetchIsochrone(
        { lng: -122.4194, lat: 37.7749, mode: "walking", minutes: 15 },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ code: "INVALID_RESPONSE", message: "服务返回的数据格式无效" });

    const missingCategory = structuredClone(siteAnalysisResponse);
    delete missingCategory.metrics.public_transport;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(missingCategory)));
    await expect(
      fetchSiteAnalysis(
        { lng: -122.4194, lat: 37.7749 },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ code: "INVALID_RESPONSE", message: "服务返回的数据格式无效" });
  });

  it.each([
    ["字符串 minutes", { minutes: "30" }],
    ["对象 mode", { mode: { toString: () => "driving" } }],
    ["错误 contour", { geometry: { ...contour, features: [{ ...contour.features[0], properties: { contour: 15 } }] } }],
    ["非面 geometry", { geometry: { ...contour, features: [{ ...contour.features[0], geometry: { type: "LineString", coordinates: [] } }] } }],
  ])("等时圈响应拒绝%s，不做隐式转换", async (_name, override) => {
    const response = {
      origin: { lng: -122.4194, lat: 37.7749 },
      mode: "driving",
      minutes: 30,
      geometry: contour,
      data_version: "test-v1",
      traffic_assumption: "static_network_cost",
      ...override,
    };
    vi.stubGlobal("window", { location: { hostname: "family-host.test" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(response)));

    await expect(
      fetchIsochrone(
        { lng: -122.4194, lat: 37.7749, mode: "driving", minutes: 30 },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ code: "INVALID_RESPONSE", message: "服务返回的数据格式无效" });
  });
});
