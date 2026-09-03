import type { BasemapTheme } from "./map-style";
import type { Duration, FacilityCategory, TravelMode } from "./gis-types";

export interface FacilitySetting {
  enabled: boolean;
  weight: number;
}

export type FacilitySettings = Record<FacilityCategory, FacilitySetting>;

interface MapControlsProps {
  theme: BasemapTheme;
  mode: TravelMode;
  minutes: Duration;
  populationVisible: boolean;
  populationUnavailable?: boolean;
  facilitiesVisible: boolean;
  facilities: FacilitySettings;
  onThemeChange(theme: BasemapTheme): void;
  onModeChange(mode: TravelMode): void;
  onMinutesChange(minutes: Duration): void;
  onPopulationVisibleChange(visible: boolean): void;
  onFacilitiesVisibleChange(visible: boolean): void;
  onFacilityChange(category: FacilityCategory, change: Partial<FacilitySetting>): void;
}

const facilityNames: Record<FacilityCategory, string> = {
  education: "教育",
  healthcare: "医疗",
  daily_shopping: "日常购物",
  parks: "公园绿地",
  public_transport: "公共交通站点",
};

export const facilityCategories = Object.keys(facilityNames) as FacilityCategory[];

export function MapControls(props: MapControlsProps) {
  return (
    <aside className="map-controls" aria-label="地图控制台">
      <header className="control-heading">
        <p className="eyebrow">HOMEORBIT · 家环</p>
        <h1>湾区选址测量台</h1>
      </header>

      <fieldset>
        <legend>底图</legend>
        <div className="segmented three">
          {(["standard", "transport", "analysis"] as const).map((theme) => (
            <label key={theme}>
              <input type="radio" name="theme" checked={props.theme === theme} onChange={() => props.onThemeChange(theme)} />
              {{ standard: "标准地图", transport: "交通网图", analysis: "分析浅色图" }[theme]}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>通勤方式</legend>
        <div className="segmented three">
          {(["walking", "driving"] as const).map((mode) => (
            <label key={mode}>
              <input type="radio" name="mode" checked={props.mode === mode} onChange={() => props.onModeChange(mode)} />
              {mode === "walking" ? "步行" : "驾车"}
            </label>
          ))}
          <label className="coming-soon">
            <input type="radio" name="mode" disabled aria-label="公共交通（建设中）" />
            公共交通 <small>建设中</small>
          </label>
        </div>
        <p className="control-note">驾车为静态纯驾车路网，不含实时拥堵、停车与末端步行。</p>
      </fieldset>

      <fieldset>
        <legend>通勤时长</legend>
        <div className="segmented four">
          {([15, 30, 45, 60] as const).map((duration) => (
            <label key={duration}>
              <input type="radio" name="minutes" checked={props.minutes === duration} onChange={() => props.onMinutesChange(duration)} />
              {duration} 分钟
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>地图图层</legend>
        <div className="layer-toggles">
          <label>
            <input type="checkbox" checked={props.populationVisible} disabled={props.populationUnavailable} onChange={(event) => props.onPopulationVisibleChange(event.target.checked)} />
            人口密度
          </label>
          <label>
            <input type="checkbox" checked={props.facilitiesVisible} onChange={(event) => props.onFacilitiesVisibleChange(event.target.checked)} />
            圈内设施
          </label>
        </div>
        {props.populationUnavailable ? <p role="status" className="control-error">人口密度数据暂不可用</p> : <p className="density-legend"><span />低 <i />中 <b />高 · 人/平方公里 · ACS 2023</p>}
      </fieldset>

      <fieldset className="facility-controls">
        <legend>设施权重</legend>
        {facilityCategories.map((category) => {
          const setting = props.facilities[category];
          const name = facilityNames[category];
          return (
            <div className="facility-control" key={category}>
              <label>
                <input type="checkbox" aria-label={`启用${name}`} checked={setting.enabled} onChange={(event) => props.onFacilityChange(category, { enabled: event.target.checked })} />
                <span>{name}</span>
              </label>
              <input type="range" aria-label={`${name}权重`} min="0" max="100" step="5" value={setting.weight} disabled={!setting.enabled} onChange={(event) => props.onFacilityChange(category, { weight: Number(event.target.value) })} />
              <output>{setting.weight}</output>
            </div>
          );
        })}
        <p className="control-note">设施范围固定为 15 分钟步行圈；调整开关和权重只在本机重新计算指数。</p>
      </fieldset>
    </aside>
  );
}
