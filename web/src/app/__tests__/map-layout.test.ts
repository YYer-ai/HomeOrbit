import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";

const appFile = (name: string) => readFileSync(`${process.cwd()}/src/app/${name}`, "utf8");
const maplibreCss = () => readFileSync(`${process.cwd()}/node_modules/maplibre-gl/dist/maplibre-gl.css`, "utf8");

afterEach(() => {
  document.head.querySelector("style[data-homeorbit-map-layout]")?.remove();
  document.body.querySelector("[data-homeorbit-map-shell]")?.remove();
});

describe("地图容器布局契约", () => {
  it("MapLibre 后加载 position:relative 时仍以绝对定位填充父容器", () => {
    const style = document.createElement("style");
    style.dataset.homeorbitMapLayout = "";
    style.textContent = [appFile("globals.css").replace(/@import[^;]+;/g, ""), maplibreCss()].join("\n");
    document.head.append(style);

    const shell = document.createElement("main");
    shell.className = "map-shell";
    shell.dataset.homeorbitMapShell = "";

    const map = document.createElement("div");
    map.className = "map-canvas maplibregl-map";
    shell.append(map);
    document.body.append(shell);

    const computed = window.getComputedStyle(map);
    expect(computed.position).toBe("absolute");
    expect(computed.inset).toBe("0px");
  });
});
