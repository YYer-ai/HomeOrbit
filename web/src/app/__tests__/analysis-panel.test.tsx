import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnalysisPanel } from "../analysis-panel";
import type { SiteAnalysis } from "../gis-types";

const facilities = {
  education: { enabled: true, weight: 1 },
  healthcare: { enabled: true, weight: 1 },
  daily_shopping: { enabled: true, weight: 1 },
  parks: { enabled: true, weight: 1 },
  public_transport: { enabled: true, weight: 1 },
};

afterEach(cleanup);

function analysis(status: SiteAnalysis["score_status"] = "ok"): SiteAnalysis {
  const score = status === "ok" ? 72 : null;
  return {
    origin: { lng: -122.4, lat: 37.7 },
    catchment: { type: "FeatureCollection", features: [] },
    population: status === "ok" ? 4200 : 80,
    population_method: "area_weighted_tract_population",
    metrics: {
      education: { value: 1.2, unit: "count_per_1000_people", score, raw_count: 5, raw_area_hectares: null },
      healthcare: { value: 0.7, unit: "count_per_1000_people", score, raw_count: 3, raw_area_hectares: null },
      daily_shopping: { value: 1.9, unit: "count_per_1000_people", score, raw_count: 8, raw_area_hectares: null },
      parks: { value: 2.5, unit: "hectares_per_1000_people", score, raw_count: null, raw_area_hectares: 10.5 },
      public_transport: { value: 3.1, unit: "count_per_1000_people", score, raw_count: 13, raw_area_hectares: null },
    },
    facilities: { type: "FeatureCollection", features: [] },
    scores: { education: score, healthcare: score, daily_shopping: score, parks: score, public_transport: score },
    score_status: status,
    data_version: "test",
    acs_year: 2023,
    limitations: ["tract 内人口按土地面积比例估算"],
    scoring_method: "湾区人口加权相对分布",
  };
}

describe("AnalysisPanel", () => {
  it("无起点时引导点击地图", () => {
    render(<AnalysisPanel origin={null} commute={{ loading: false, error: null, data: null }} site={{ loading: false, error: null, data: null }} facilities={facilities} />);
    expect(screen.getByText("点击地图设置起点")).toBeInTheDocument();
  });

  it("分别保留通勤与设施错误，并展示数据口径和五类指标", () => {
    render(<AnalysisPanel origin={{ lng: -122.4, lat: 37.7 }} commute={{ loading: false, error: "路网分析服务暂不可用", data: null }} site={{ loading: false, error: null, data: analysis() }} facilities={facilities} />);
    expect(screen.getByText("路网分析服务暂不可用")).toBeInTheDocument();
    expect(screen.getByText("4,200")).toBeInTheDocument();
    expect(screen.getByText(/ACS 2023/)).toHaveTextContent("面积加权");
    expect(screen.getByText(/10.50 公顷/)).toBeInTheDocument();
    expect(screen.getAllByText("相对分 72")).toHaveLength(5);
    expect(screen.getByText(/不是公共服务达标结论/)).toBeInTheDocument();
  });

  it("低人口显示样本不足，零权重显示未计算且不伪造圆环", () => {
    const zeroWeights = Object.fromEntries(
      Object.entries(facilities).map(([key, value]) => [key, { ...value, weight: 0 }]),
    ) as typeof facilities;
    const { rerender } = render(<AnalysisPanel origin={{ lng: -122.4, lat: 37.7 }} commute={{ loading: false, error: null, data: null }} site={{ loading: false, error: null, data: analysis("insufficient_population") }} facilities={facilities} />);
    expect(screen.getAllByText("样本不足").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(/综合指数/)).not.toBeInTheDocument();

    rerender(<AnalysisPanel origin={{ lng: -122.4, lat: 37.7 }} commute={{ loading: false, error: null, data: null }} site={{ loading: false, error: null, data: analysis() }} facilities={zeroWeights} />);
    expect(screen.getByText("未计算")).toBeInTheDocument();
    expect(screen.queryByLabelText(/综合指数/)).not.toBeInTheDocument();
  });
});
