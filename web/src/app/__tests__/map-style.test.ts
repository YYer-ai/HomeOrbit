import { describe, expect, it } from "vitest";
import { validateStyleMin } from "@maplibre/maplibre-gl-style-spec";
import type {
  LayerSpecification,
  LineLayerSpecification,
  StyleSpecification,
} from "maplibre-gl";

import { createBasemapStyle } from "../map-style";

const ASSET_BASE = "http://homeorbit.test:3000";

function layer(style: StyleSpecification, id: string): LayerSpecification {
  const result = style.layers.find((candidate) => candidate.id === id);
  if (!result) throw new Error(`missing layer ${id}`);
  return result;
}

function lineWidthAtZoom(style: StyleSpecification, id: string, zoom: number): number {
  const width = (layer(style, id) as LineLayerSpecification).paint?.["line-width"];
  if (typeof width === "number") return width;
  if (!Array.isArray(width)) throw new Error(`invalid line-width for ${id}`);
  if (width[0] === "*") {
    const nested = { ...style, layers: [{ ...(layer(style, id) as LineLayerSpecification), paint: { "line-width": width[1] } }] } as StyleSpecification;
    return lineWidthAtZoom(nested, id, zoom) * Number(width[2]);
  }
  if (width[0] !== "interpolate") throw new Error(`unsupported line-width for ${id}`);
  const stops = width.slice(3) as number[];
  let result = stops[1];
  for (let index = 0; index < stops.length; index += 2) {
    if (zoom < stops[index]) break;
    result = stops[index + 1];
  }
  return result;
}

describe("createBasemapStyle", () => {
  it("所有主题的桥隧样式仅沿真实标签道路显示，且位于水域之上", () => {
    for (const theme of ["standard", "transport", "analysis"] as const) {
      const style = createBasemapStyle(theme, ASSET_BASE);
      for (const [id, tag, color] of [
        ["roads_bridges_minor", "is_bridge", "#287C9B"],
        ["roads_tunnels_minor", "is_tunnel", "#7253A3"],
      ]) {
        const road = layer(style, id) as LineLayerSpecification;
        expect(JSON.stringify(road.filter)).toContain(tag);
        expect(road.paint?.["line-color"]).toBe(color);
        expect(style.layers.indexOf(road)).toBeGreaterThan(style.layers.findIndex((item) => item.id === "water"));
      }
      expect((layer(style, "roads_tunnels_minor") as LineLayerSpecification).paint?.["line-dasharray"]).toEqual([3, 2]);
    }
  });

  it("三种主题都通过 MapLibre 最小样式规范验证", () => {
    for (const theme of ["standard", "transport", "analysis"] as const) {
      const errors = validateStyleMin(createBasemapStyle(theme, ASSET_BASE));
      expect(errors.map((error) => error.message)).toEqual([]);
    }
  });

  it("三种主题共用同一离线源、本地字体和 sprite，并包含双重归属", () => {
    for (const theme of ["standard", "transport", "analysis"] as const) {
      const style = createBasemapStyle(theme, `${ASSET_BASE}/`);
      const source = style.sources.basemap;
      expect(source).toMatchObject({
        type: "vector",
        url: `pmtiles://${ASSET_BASE}/tiles/bay-area.pmtiles`,
      });
      expect(JSON.stringify(source)).toContain("OpenStreetMap");
      expect(JSON.stringify(source)).toContain("Protomaps");
      expect(style.glyphs).toBe(`${ASSET_BASE}/protomaps-assets/fonts/{fontstack}/{range}.pbf`);
      expect(style.sprite).toBe(`${ASSET_BASE}/protomaps-assets/sprites/v4/light`);
    }
  });

  it("交通主题增强主要道路、高速和轨道的视觉层级", () => {
    const standard = createBasemapStyle("standard", ASSET_BASE);
    const transport = createBasemapStyle("transport", ASSET_BASE);

    expect(layer(transport, "roads_highway")).toMatchObject({
      paint: { "line-color": "#A33F2A" },
      layout: { visibility: "visible" },
    });
    expect(layer(transport, "roads_major")).toMatchObject({
      paint: { "line-color": "#C55A35" },
      layout: { visibility: "visible" },
    });
    expect(layer(transport, "roads_rail").paint).toMatchObject({
      "line-color": "#554C8C",
      "line-opacity": 0.9,
    });
    expect(layer(transport, "roads_highway").paint).not.toEqual(
      layer(standard, "roads_highway").paint,
    );
  });

  it.each([12, 18])("交通主题在 zoom %i 的三类网络线宽均明显高于标准主题", (zoom) => {
    const standard = createBasemapStyle("standard", ASSET_BASE);
    const transport = createBasemapStyle("transport", ASSET_BASE);

    for (const id of ["roads_highway", "roads_major", "roads_rail"]) {
      const standardWidth = lineWidthAtZoom(standard, id, zoom);
      const transportWidth = lineWidthAtZoom(transport, id, zoom);
      expect(transportWidth).toBeGreaterThanOrEqual(standardWidth * 1.2);
    }
  });

  it("分析主题背景更浅并降低道路与标签竞争", () => {
    const standard = createBasemapStyle("standard", ASSET_BASE);
    const analysis = createBasemapStyle("analysis", ASSET_BASE);

    expect(layer(analysis, "background").paint).toMatchObject({
      "background-color": "#F7F9F6",
    });
    expect(layer(analysis, "roads_major").paint).toMatchObject({
      "line-color": "#C9D2CD",
      "line-opacity": 0.45,
    });
    expect(layer(analysis, "roads_labels_major").layout).toMatchObject({ visibility: "none" });
    expect(layer(analysis, "background").paint).not.toEqual(layer(standard, "background").paint);
  });

  it("每次调用返回深层独立的 source、layer、paint 和 layout", () => {
    const first = createBasemapStyle("transport", ASSET_BASE);
    const second = createBasemapStyle("transport", ASSET_BASE);

    expect(first).not.toBe(second);
    expect(first.sources).not.toBe(second.sources);
    expect(first.sources.basemap).not.toBe(second.sources.basemap);
    expect(first.layers).not.toBe(second.layers);
    expect(first.layers[0]).not.toBe(second.layers[0]);
    expect(first.layers[0].paint).not.toBe(second.layers[0].paint);
    const firstWithLayout = first.layers.find((candidate) => candidate.layout);
    const secondWithLayout = second.layers.find((candidate) => candidate.id === firstWithLayout?.id);
    expect(firstWithLayout?.layout).not.toBe(secondWithLayout?.layout);

    const standard = createBasemapStyle("standard", ASSET_BASE);
    const analysis = createBasemapStyle("analysis", ASSET_BASE);
    const mutableHighway = layer(first, "roads_highway") as LineLayerSpecification;
    mutableHighway.paint!["line-color"] = "#000000";
    expect((layer(standard, "roads_highway") as LineLayerSpecification).paint?.["line-color"]).not.toBe(
      "#000000",
    );
    expect((layer(analysis, "roads_highway") as LineLayerSpecification).paint?.["line-color"]).not.toBe(
      "#000000",
    );
  });
});
