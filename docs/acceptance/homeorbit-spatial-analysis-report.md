# HomeOrbit 空间分析真实验收报告

状态：**主控制器实测完成；最终独立审查 Important 1/2 已关闭；待用户验收**  
首次真实验收：2026-09-01；最终回归：2026-09-03（Asia/Shanghai）  
范围：真实 API、失效外网代理、浏览器交互、性能、局部降级、桌面/移动截图与进程归属

## 1. 数据与静态门禁

### 1.1 数据口径

- Census/ACS：2023 五年数据；湾区九县 1,772 tract。
- 设施：35,052 条；五类为教育、医疗、日常购物、公园绿地、公共交通站点。
- baseline：1,772 条；11 个零人口权重，其中 7 个水域 tract。
- 通勤：walking/driving，15/30/45/60 分钟；公共交通入口为“建设中”。
- 设施范围：固定 15 分钟步行圈。
- 驾车：静态纯驾车路网，不含实时路况、停车或末端步行。

### 1.2 离线 manifest

- JSON：PASS。
- artifact path：23/23 唯一。
- presence/bytes/SHA256：23/23 与当前磁盘文件匹配。
- Protomaps asset tree：773 文件、11,135,784 B，tree SHA256 `9C0308D1A06057C2E8FE95C395E28851104BD20F0808E83EC5A1366BA61671B9`。
- manifest 的运行时依赖范围仅为当前 workspace 已存在并匹配的 `api/.venv`、`web/node_modules`、Node/Python/uv/Docker；Python wheelhouse、uv cache、Node/npm 依赖和 portable production bundle 均明确排除，干净、从未安装匹配依赖的完全断网目标机不在交付范围内。
- baseline database snapshot（非磁盘 artifact）：`facility_baseline_samples` 1,772 条、11 条零人口权重；`dataset_metadata.key=facility_baseline`；`hash_algorithm=sha256`、`hash_format=homeorbit-facility-baseline-jsonl-v2`，canonical SHA256 `12e34d9bf137e4b5ade176635a71831806e7aa3537b2a4d30e159d14d4308ebb`。
- metadata-only upgrade：`processed=0 skipped=1772 failures=0`；sample `xmin=853..2627` 未变，metadata `xmin=2727`；这些 xmin 仅为当前数据库证据，不是跨库稳定 ID。
- 独立 live oracle integration test：PASS（`1 passed`；仅现有 pytest 未注册 integration marker warning）。

### 1.3 自动化验证

- API + data 最终全量：`200 passed in 37.17s`。覆盖 baseline v2 canonical/hash 与 live PostGIS/Valhalla 集成；三个 ETL key 仍严格必需，`facility_baseline` 为受严格结构约束的可选第 4 项。
- Web 最终全量：`8 files / 64 tests passed`；ESLint 与 production build 均 PASS。
- 2026-09-03 首次 API/data 重跑期间两个依赖容器收到外部 stop，产生的失败被判定为无效环境结果；所有审查代理结束后，先以 4 个真实集成测试确认依赖恢复，再完成上述 200/200 稳定重跑。未因此修改产品代码。
- 字体离线契约：3/3 PASS；源码与 production artifact 不含 `next/font/google`、Google/Gstatic 或外部 CSS 字体引用。
- 地图布局 CSSOM 契约：1/1 PASS；真实 MapLibre CSS 后加载时最终仍为 `position:absolute; inset:0`。
- ESLint：PASS。
- Next.js 16.3.2 production build：PASS；TypeScript 与静态页面生成成功。
- 已知 warning：Vitest/Vite native config loader 前瞻兼容提示，不影响当前测试。
- `npm audit --audit-level=high`：10 个 high severity 传递依赖；未执行会引入 breaking change 的 `npm audit fix --force`。

## 2. 端口、PID 与容器归属

启动前 3000/8000 空闲；5432/8002 已由 HomeOrbit Compose 健康容器占用。主控制器没有按端口盲目结束进程。

| 组件 | 端口 | 归属证据 | 本轮动作 | 真实验收运行时快照与最终状态 |
|---|---:|---|---|---|
| Web | 3000 | listener PID `35384`；`node.exe E:/HomeOrbit/web/node_modules/next/dist/server/lib/start-server.js` | 本轮启动 | 验收时运行；最终已精确停止，3000 无监听 |
| API | 8000 | PID `6724`；`uvicorn app.main:app --host 127.0.0.1 --port 8000` | 本轮启动 | 验收时运行；最终已精确停止，8000 无监听 |
| PostGIS | 5432 | container `d41b94540f42`；labels `homeorbit/postgis` | 既有容器；为降级验收精确停止并恢复 | 最终保留为 `running/healthy`，5432 正常监听 |
| Valhalla | 8002 | container `318ae9e6a6b0`；labels `homeorbit/valhalla` | 既有容器；为降级验收精确停止并恢复 | 验收与回归时运行；最终已精确停止，8002 无监听 |

API/Web/Edge 仅注入进程级失效代理：`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:9`，`NO_PROXY=127.0.0.1,localhost`。未修改系统代理。

## 3. API 真实功能

测试点：`lng=-122.4194`、`lat=37.7749`。

- `/gis/health`：HTTP 200，API、Valhalla、PostGIS、population density、baseline 五组件均 `ready`。
- `/gis/config`：walking/driving available、public transit coming soon、四档时长、五类设施和数据版本完整。
- 八组真实等时圈均返回非空 Polygon，规范化几何哈希八组唯一：

| 模式 | 15 分钟面积 | 30 分钟面积 | 45 分钟面积 | 60 分钟面积 |
|---|---:|---:|---:|---:|
| walking | 0.00040004 | 0.00158398 | 0.00343119 | 0.00516383 |
| driving | 0.02040029 | 0.12407322 | 0.39822610 | 0.77998517 |

面积随时间增长，walking/driving 明显不同；没有圆形回退。walking 15 规范化哈希前缀为 `ac4c5d`。

- `POST /gis/site-analysis`：HTTP 200；人口 `63,463.1659`，五类 metrics/scores、catchment、facilities、limitations、数据版本完整。
- 浏览器拖动后的另一位置返回估算人口 `10,454` 和五类分项，证明不是固定占位响应。

## 4. 性能

恢复健康后连续运行各 20 次，全部 HTTP 200，首次冷请求计入结果。

### 4.1 driving 30 分钟等时圈

- 原始秒数：`[2.7083,0.7023,0.7313,0.7104,0.7187,0.6850,0.8106,0.7436,0.7308,0.6801,0.6882,0.6932,0.6893,0.7505,0.7234,0.7090,0.7030,0.7092,0.6839,0.7057]`
- p50：`0.7090s`；p95：`0.8106s`；max：`2.7083s`；20/20 `<5s`：PASS。

### 4.2 site analysis

- 原始秒数：`[0.3983,0.0790,0.0788,0.0817,0.0795,0.0993,0.0810,0.0800,0.0803,0.0791,0.0771,0.0769,0.0852,0.0775,0.0790,0.0808,0.0785,0.0807,0.0775,0.0806]`
- p50：`0.0795s`；p95：`0.0993s`；max：`0.3983s`；20/20 `<5s`：PASS。

## 5. 失效外网代理与浏览器网络

Edge/CDP 使用独立 profile 和失效外网代理完成审计：

- HTTP(S) host 只有 `127.0.0.1:3000`、`127.0.0.1:8000`；external HTTP requests 为 0。
- console errors：0；正常场景 4xx/5xx：0。
- 快速切换 45→60→15→30 分钟产生 3 个 `net::ERR_ABORTED`，全部 `canceled=true`，属于旧请求主动取消；最终稳定显示 30 分钟结果。
- 主题切换没有新增分析请求。
- 教育权重 20→80 时综合指数 72→71，`/gis/site-analysis` 计数保持 2→2，证明权重只在前端重算。
- 修复后不再请求 Google Fonts 或其他外部字体/CDN。

## 6. 浏览器交互

以下均 PASS：

- 点击地图设置起点；画布实际尺寸 `1414×807`，点击命中 `CANVAS.maplibregl-canvas`。
- 拖动 marker 后等时圈请求计数 6→7，起点和分析结果更新。
- 标准地图、交通网图、分析浅色图均可切换。
- 步行、驾车与 15/30/45/60 分钟均可操作，最终结果与所选参数一致。
- 人口密度、圈内设施、五类设施开关均可操作。
- 公共交通 radio 为 disabled，并显示“建设中”。
- 驾车说明明确不含实时拥堵、停车和末端步行。

## 7. 局部降级与恢复

每次只停止一个已核实归属的 HomeOrbit 容器，恢复健康后才测试下一个。

### 7.1 Valhalla unavailable

- API health：`degraded`；Valhalla unavailable，其余静态/数据库组件 ready。
- `/gis/isochrone` 与依赖 15 分钟步行圈的 `/gis/site-analysis` 均返回预期 503。
- 页面显示两处安全中文“路网分析服务暂不可用”。
- 底图、静态人口密度、`1414×807` 画布、标记、控件均保留；没有伪圆形等时圈。
- 浏览器请求仍只访问本机，无 console/network failure。
- 精确恢复后容器 healthy，API 五组件恢复 ready。

### 7.2 PostGIS unavailable

- API health：`degraded`；PostGIS unavailable、baseline unavailable，Valhalla ready。
- 15 分钟步行等时圈继续成功；只有 `/gis/site-analysis` 返回预期 503。
- 页面显示安全中文“空间数据服务暂不可用”。
- 底图、等时圈、`1414×807` 画布、标记、控件均保留。
- 浏览器请求仍只访问本机，无 console/network failure。
- 精确恢复后容器 healthy；API 连接池在 3 秒内恢复，五组件重新 ready。

## 8. 截图证据

| 场景 | 文件 | 尺寸 | bytes | SHA256 | 目视结果 |
|---|---|---:|---:|---|---|
| 桌面完整分析 | `docs/acceptance/homeorbit-spatial-analysis-desktop.png` | 1414×807 | 543,870 | `672A827A3DF694308DF9FB1504BA78D4EDD9EBF4175FDAE4C5D040CFF30A5969` | PASS |
| 移动端 | `docs/acceptance/homeorbit-spatial-analysis-mobile.png` | 390×844 | 136,159 | `90E230D9EF7B5244271787BDEA287A4F9F848025A2BDC57CA569775C19944E68` | PASS |
| Valhalla 降级 | `docs/acceptance/homeorbit-spatial-analysis-valhalla-degraded.png` | 1414×807 | 601,239 | `2DD6AE39A63CB77B44C0C4C7E09F0BE727706927886FFBF1BE2466A8B607DD2B` | PASS |
| PostGIS 降级 | `docs/acceptance/homeorbit-spatial-analysis-postgis-degraded.png` | 1414×807 | 604,647 | `BC7DCACCDC7A83CDC2C545F7885F1587397512882096777F1760F8EC8F83691C` | PASS |

移动端分析面板高度 `379.796875/844 = 0.449996`，符合 `<=45vh`；地图与 marker 在上下两块面板之间仍可见。

## 9. 安全停止结果

状态：**最终独立总审查已完成；控制器已按核实归属完成精确收尾。**

- 只停止本轮 API/Web 控制会话，确认 3000/8000 释放。
- 按已批准计划精确停止 `homeorbit-valhalla`，确认 8002 释放。
- 保留并再次确认 `homeorbit-postgis` healthy，5432 继续监听。
- 不影响相邻服务。

## 10. 已知风险与限制

- `npm audit` 当前有 10 个 high severity 传递依赖；未做破坏性升级。
- Vitest 当前有 Vite native config loader 前瞻 warning。
- Task 1 独立审查遗留 minor：响应 model 使用 `extra="allow"`；当前用于显式扩展字段，作为已知限制记录，不影响本次交付证据。
- 人口按 tract 土地面积比例估算，假设 tract 内人口均匀分布。
- OSM 设施覆盖与更新时间不均；7 个候选因无有效几何未进入正式产物。
- 公共交通路线、实时路况、停车和末端步行未实现。
- 相对分不是法规、规划规范或公共服务达标结论。

## 11. 当前结论

数据、API、性能、当前 workspace 运行时完全断网网络、桌面/移动交互、局部降级与恢复均已由主控制器真实验证。最终审查 Important 1（依赖范围）通过收窄声明并排除未打包依赖 bundle 关闭；Important 2（baseline 内容证据）通过 database snapshot、metadata-only upgrade 和 live oracle PASS 关闭。当前交付不宣称干净目标机可直接离线部署，也不宣称用户已验收；下一状态为用户验收。
