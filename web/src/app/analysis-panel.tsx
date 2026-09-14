import type { CSSProperties } from "react";
import { CollapsiblePanel } from "./collapsible-panel";

import { computeCompositeScore } from "./analysis-score";
import type { FacilitySettings } from "./map-controls";
import { facilityCategories } from "./map-controls";
import type { FacilityCategory, Isochrone, Origin, SiteAnalysis, TravelDirection } from "./gis-types";

interface RequestState<T> {
  loading: boolean;
  error: string | null;
  data: T | null;
}

interface AnalysisPanelProps {
  origin: Origin | null;
  direction?: TravelDirection;
  commute: RequestState<Isochrone>;
  site: RequestState<SiteAnalysis>;
  facilities: FacilitySettings;
}

const names: Record<FacilityCategory, string> = {
  education: "教育", healthcare: "医疗", daily_shopping: "日常购物",
  parks: "公园绿地", public_transport: "公共交通站点",
};

function MetricRow({ category, analysis }: { category: FacilityCategory; analysis: SiteAnalysis }) {
  const metric = analysis.metrics[category];
  const raw = category === "parks"
    ? `${metric.raw_area_hectares?.toFixed(2)} 公顷`
    : `${metric.raw_count ?? 0} 处`;
  const perCapita = category === "parks"
    ? `${metric.value.toFixed(2)} 公顷/千人`
    : `${metric.value.toFixed(2)} 处/千人`;
  return (
    <li>
      <div><strong>{names[category]}</strong><span>{raw}</span></div>
      <div><span>{perCapita}</span><span className="score-chip">{metric.score === null ? "样本不足" : `相对分 ${Math.round(metric.score)}`}</span></div>
    </li>
  );
}

export function AnalysisPanel({ origin, direction = "outbound", commute, site, facilities }: AnalysisPanelProps) {
  if (!origin) {
    return <CollapsiblePanel className="analysis-panel empty" label="选址分析结果"><p className="eyebrow">选址分析</p><h2>{direction === "inbound" ? "点击地图设置目的地" : "点击地图设置起点"}</h2><p>也可在设置后拖动标记，重新测量通勤与周边配套。</p></CollapsiblePanel>;
  }

  const weights = Object.fromEntries(
    facilityCategories.map((category) => [category, facilities[category].enabled ? facilities[category].weight : 0]),
  );
  const composite = site.data?.score_status === "ok"
    ? computeCompositeScore(site.data.scores, weights)
    : null;
  const zeroWeight = Object.values(weights).every((weight) => weight === 0);

  return (
    <CollapsiblePanel className="analysis-panel" label="选址分析结果">
      <header className="panel-heading">
        <p className="eyebrow">选址分析</p>
        <h2>{origin.lat.toFixed(5)}, {origin.lng.toFixed(5)}</h2>
      </header>

      <section aria-labelledby="commute-title">
        <div className="section-title"><h3 id="commute-title">通勤等时圈</h3>{commute.loading && <span className="loading-label">计算中</span>}</div>
        {commute.error && <p role="alert" className="panel-error">{commute.error}</p>}
        {!commute.loading && !commute.error && commute.data && <p><strong className="mono-reading">{commute.data.minutes}</strong> 分钟 · {commute.data.mode === "walking" ? "步行" : "驾车"} · {commute.data.direction === "inbound" ? "到达选点" : "从选点出发"}</p>}
        {commute.data && <p className="method-note">{commute.data.direction === "inbound" ? `橙色为能在 ${commute.data.minutes} 分钟内到达选点的路网范围。` : `橙色为从选点出发 ${commute.data.minutes} 分钟内的路网可达范围。`}保留内部空洞，湖面不显示为可达陆地。边界为近似估算，沿实际道路的用时可能不同。</p>}
        {!commute.loading && !commute.error && !commute.data && <p className="muted">等待通勤分析</p>}
      </section>

      <section aria-labelledby="site-title">
        <div className="section-title"><h3 id="site-title">15 分钟步行配套</h3>{site.loading && <span className="loading-label">分析中</span>}</div>
        {site.error && <p role="alert" className="panel-error">{site.error}</p>}
        {site.data && (
          <>
            <p className="method-note">绿色虚线为配套统计使用的固定 15 分钟步行范围，与所选通勤时长分开计算。</p>
            <div className="population-reading"><span>估算人口</span><strong>{Math.round(site.data.population).toLocaleString("en-US")}</strong></div>
            <p className="method-note">ACS {site.data.acs_year} · tract 土地面积加权估算，假设区内人口均匀分布。</p>
            {site.data.score_status === "insufficient_population" ? (
              <div className="score-message"><strong>样本不足</strong><span>估算人口低于 100，仅展示原始供给，不计算综合指数。</span></div>
            ) : composite === null ? (
              <div className="score-message"><strong>未计算</strong><span>{zeroWeight ? "启用分类的权重总和为 0。" : "有效分项不足。"}</span></div>
            ) : (
              <div className="home-ring" style={{ "--score": `${composite * 3.6}deg` } as CSSProperties} role="img" aria-label={`综合指数 ${Math.round(composite)}`}>
                <div><strong>{Math.round(composite)}</strong><span>综合指数</span></div>
              </div>
            )}
            <ul className="metric-list">{facilityCategories.map((category) => <MetricRow key={category} category={category} analysis={site.data!} />)}</ul>
            <p className="relative-note">相对分表示在湾区同口径、人口加权参考样本中的相对位置，不是公共服务达标结论。</p>
          </>
        )}
        {!site.loading && !site.error && !site.data && <p className="muted">等待设施分析</p>}
      </section>
    </CollapsiblePanel>
  );
}
