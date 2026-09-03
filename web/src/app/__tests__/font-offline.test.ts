import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appFile = (name: string) => readFileSync(`${process.cwd()}/src/app/${name}`, "utf8");
const externalCssReference = /(?:@import\s+(?:url\(\s*)?["']?|url\(\s*["']?)(?:https?:)?\/\//i;

function selectorWithCodeFont(selector: string): RegExp {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`${escaped}\\s*\\{[^}]*var\\(--font-code\\)`);
}

describe("全局字体离线契约", () => {
  it("根布局不导入在线字体模块", () => {
    const layout = appFile("layout.tsx");

    expect(layout).not.toMatch(/next\/font\/google/);
  });

  it("全局 CSS 不引用外部字体资源且保留本地相对路径", () => {
    const css = appFile("globals.css");

    expect(css).not.toMatch(externalCssReference);
    expect('@import "tailwindcss";').not.toMatch(externalCssReference);
    expect('@font-face { src: url("./font.woff2"); }').not.toMatch(externalCssReference);
    for (const sample of [
      '@import "https://cdn.example/fonts.css";',
      "@import url('http://cdn.example/fonts.css');",
      "@import url(//cdn.example/fonts.css);",
      "@font-face { src: url(https://cdn.example/fonts.woff2); }",
    ]) {
      expect(sample).toMatch(externalCssReference);
    }
  });

  it("全局字体变量定义系统字体栈并被主题、body、等宽规则使用", () => {
    const css = appFile("globals.css");

    expect(css).toMatch(/--font-ui:[^;]*"Segoe UI"/);
    expect(css).toMatch(/--font-ui:[^;]*"PingFang SC"/);
    expect(css).toMatch(/--font-ui:[^;]*"Microsoft YaHei"/);
    expect(css).toMatch(/--font-ui:[^;]*sans-serif/);
    expect(css).toMatch(/--font-code:[^;]*ui-monospace/);
    expect(css).toMatch(/--font-code:[^;]*monospace/);
    expect(css).toMatch(/@theme inline\s*\{[^}]*--font-sans:\s*var\(--font-ui\)[^}]*--font-mono:\s*var\(--font-code\)/);
    expect(css).toMatch(/body\s*\{[^}]*font-family:\s*var\(--font-ui\)/);

    for (const selector of [
      ".eyebrow",
      ".density-legend",
      ".facility-control output",
      ".loading-label,.score-chip",
      ".mono-reading,.population-reading strong",
      ".home-ring strong",
      ".metric-list li>div+div",
      ".metric-list li>div>span",
    ]) {
      expect(css).toMatch(selectorWithCodeFont(selector));
    }
  });
});
