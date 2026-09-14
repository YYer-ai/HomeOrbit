import { layers, namedFlavor } from "@protomaps/basemaps";
import type {
  LayerSpecification,
  LineLayerSpecification,
  StyleSpecification,
  SymbolLayerSpecification,
} from "maplibre-gl";

export type BasemapTheme = "standard" | "transport" | "analysis";
type LineWidth = NonNullable<LineLayerSpecification["paint"]>["line-width"];

const ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors · © <a href="https://protomaps.com" target="_blank">Protomaps</a>';

function lineLayer(
  value: LayerSpecification,
): value is LineLayerSpecification {
  return value.type === "line";
}

function scaleZoomWidth(width: LineWidth, factor: number): LineWidth {
  if (typeof width === "number") return width * factor;
  if (!Array.isArray(width) || !["interpolate", "step"].includes(String(width[0]))) {
    return width;
  }

  const scaled = structuredClone(width) as unknown[];
  const firstOutput = scaled[0] === "interpolate" ? 4 : 2;
  for (let index = firstOutput; index < scaled.length; index += 2) {
    const output = scaled[index];
    if (typeof output === "number") scaled[index] = output * factor;
  }
  return scaled as LineWidth;
}

function applyTransportHierarchy(styleLayers: LayerSpecification[]): void {
  const emphasis = {
    roads_highway: { color: "#A33F2A", widthFactor: 1.3 },
    roads_major: { color: "#C55A35", widthFactor: 1.35 },
    roads_rail: { color: "#554C8C", widthFactor: 1.4 },
  };

  for (const styleLayer of styleLayers) {
    const settings = emphasis[styleLayer.id as keyof typeof emphasis];
    if (!settings || !lineLayer(styleLayer)) continue;
    const originalWidth = styleLayer.paint?.["line-width"];
    if (originalWidth === undefined) continue;
    styleLayer.paint = {
      ...styleLayer.paint,
      "line-color": settings.color,
      "line-width": scaleZoomWidth(originalWidth, settings.widthFactor),
      "line-opacity": styleLayer.id === "roads_rail" ? 0.9 : 0.88,
    };
    styleLayer.layout = { ...styleLayer.layout, visibility: "visible" };
  }
}

function quietAnalysisLayers(styleLayers: LayerSpecification[]): void {
  for (const styleLayer of styleLayers) {
    if (styleLayer.id === "background" && styleLayer.type === "background") {
      styleLayer.paint = { ...styleLayer.paint, "background-color": "#F7F9F6" };
    }
    if (
      lineLayer(styleLayer) &&
      ["roads_highway", "roads_major", "roads_rail"].includes(styleLayer.id)
    ) {
      styleLayer.paint = {
        ...styleLayer.paint,
        "line-color": "#C9D2CD",
        "line-opacity": 0.45,
      };
    }
    if (styleLayer.type === "symbol" && styleLayer.id.startsWith("roads_labels_")) {
      const symbolLayer = styleLayer as SymbolLayerSpecification;
      symbolLayer.layout = { ...symbolLayer.layout, visibility: "none" };
    }
  }
}

export function createBasemapStyle(
  theme: BasemapTheme,
  assetBase: string,
): StyleSpecification {
  const base = assetBase.replace(/\/$/, "");
  const styleLayers = layers(
    "basemap",
    namedFlavor(theme === "analysis" ? "white" : "light"),
    { lang: "en" },
  );

  if (theme === "transport") applyTransportHierarchy(styleLayers);
  if (theme === "analysis") quietAnalysisLayers(styleLayers);

  for (const layer of styleLayers) {
    if (!lineLayer(layer) || layer.id.endsWith("_casing")) continue;
    if (layer.id.startsWith("roads_tunnels_")) {
      layer.paint = { ...layer.paint, "line-color": "#7253A3", "line-dasharray": [3, 2], "line-opacity": 1 };
    } else if (layer.id.startsWith("roads_bridges_")) {
      layer.paint = { ...layer.paint, "line-color": "#287C9B", "line-opacity": 1 };
    }
  }

  return {
    version: 8,
    glyphs: `${base}/protomaps-assets/fonts/{fontstack}/{range}.pbf`,
    sprite: `${base}/protomaps-assets/sprites/v4/light`,
    sources: {
      basemap: {
        type: "vector",
        url: `pmtiles://${base}/tiles/bay-area.pmtiles`,
        attribution: ATTRIBUTION,
      },
    },
    layers: styleLayers,
  };
}
