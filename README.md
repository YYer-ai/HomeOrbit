# HomeOrbit（家环）

HomeOrbit 是一个面向美国湾区的离线优先住房选址分析原型。当前版本聚焦交通可达性与步行配套，不包含尚在规划中的 AI 对话、学区、安全、房源推荐或多人 Pareto 优化。

## 当前已交付

- 三种本地 PMTiles 底图：标准地图、交通网图、分析浅色图。交通网图展示道路与轨道层级，不表示实时拥堵。
- 基于本地 Valhalla 路网的步行、纯驾车等时圈，支持 15/30/45/60 分钟；无圆形回退。
- 可达方向支持“从选点出发”和“到达选点”：将工作地点设为目的地，可查看指定时间内能到达的范围。固定 15 分钟步行配套统计独立计算。
- ACS 2023 湾区九县人口密度图层。
- 当前选点固定 15 分钟步行圈内的教育、医疗、日常购物、公园绿地、公共交通站点五类设施分析。
- 以湾区同口径样本和 tract 人口加权得到的 0–100 相对分；分类开关和权重只在前端重算综合指数。
- Valhalla、PostGIS 或人口文件单项不可用时的局部降级。

公共交通路线计算仍为“建设中”。驾车结果采用静态纯驾车路网成本，不含实时路况、停车搜索、停车可用性或末端步行。

## 数据口径

| 数据 | 当前版本/数量 |
|---|---:|
| Census/ACS | 2023 五年数据；湾区九县 1,772 个 tract |
| 设施 | California OSM 快照；35,052 条五类设施 |
| 评分基准 | 1,772 个 tract 样本；11 个零人口权重，其中 7 个为水域 tract |
| 底图 | Protomaps `20260827.pmtiles` 的 `-123.3,37.2,-121.7,38.0`、z0–z14 抽取 |

人口按 tract 土地面积比例估算，隐含 tract 内人口均匀分布假设。设施完整度受 OSM 覆盖与更新时间影响；相对分不是法规、规划规范或公共服务达标结论。公共 API 的坐标覆盖框是 `-123.3,37.2,-121.7,38.0`，评分基准则覆盖完整湾区九县 tract，两者范围不同。

评分基准的 canonical snapshot 为 `homeorbit-facility-baseline-jsonl-v2`：`facility_baseline_samples` 1,772 条、11 条零人口权重，按 `geoid` 升序计算的 SHA256 为 `12e34d9bf137e4b5ade176635a71831806e7aa3537b2a4d30e159d14d4308ebb`。本次 metadata-only upgrade 结果为 `processed=0 skipped=1772 failures=0`；sample `xmin=853..2627`、`dataset_metadata.key=facility_baseline` 的 metadata `xmin=2727`，均只是当前数据库证据，不是跨库稳定 ID。独立 live oracle integration test：PASS。

## 离线交付边界

当前已验证的完全断网运行范围是：本 workspace 已存在并匹配的 `api/.venv`、`web/node_modules` 以及 Node/Python/uv/Docker 环境。manifest 校验的是静态数据、镜像、工具和 runtime 产物，不包含 Python wheelhouse、uv cache、Node/npm 依赖或 portable production bundle。

因此当前交付不支持在一个干净、从未安装匹配依赖的完全断网目标机上直接部署。迁移前必须在联网阶段安装并验证匹配依赖；若未来构建 wheelhouse、npm 依赖包或 portable production bundle，必须先纳入 manifest 并重新校验。`UV_CACHE_DIR` 与 `npm_config_cache` 只是项目内缓存位置，不等于经 manifest 验证的可移植依赖 bundle。已校验的 Docker image tar、处理后数据、PMTiles 和 Valhalla runtime 仍可支持当前 workspace 的运行时断网证据。

## 运行组成

| 层 | 实现 |
|---|---|
| Web | Next.js 16、React 19、TypeScript、MapLibre GL、PMTiles |
| API | FastAPI、Python 3.13、uv |
| 路由 | Valhalla 3.8.3，本地 California 路由图 |
| 空间统计 | PostGIS 17-3.5、Psycopg 3 |
| 数据处理 | Pyosmium、Shapely、Pyproj、Pyshp |

## 启动与验证

Windows 已安装 PowerShell 7、Docker Desktop、uv、Node.js 和项目依赖后：

- 双击根目录 `Start-HomeOrbit.cmd`：等待数据库、路由、API 和网页就绪后自动打开 http://127.0.0.1:3000 。Docker Desktop 需提前启动。
- 双击根目录 `Stop-HomeOrbit.cmd`：核对记录的进程及容器归属后关闭本轮服务，保留数据和 Docker Desktop。
- PowerShell 7 命令：`./scripts/Start-HomeOrbit.ps1` 和 `./scripts/Stop-HomeOrbit.ps1`。
- 日志位于 `tmp/runtime/logs/`。如提示已有状态文件，先执行关闭脚本，再启动。

用户验证：启动后确认地图显示，点击湾区地图选点，检查步行等时圈及设施统计；关闭后刷新网页应无法连接，再启动应恢复。

一次性镜像导入、PostGIS 数据导入、Valhalla 建图、评分基准构建、日常启动、API 示例与安全停止步骤见 [项目说明文档 1](docs/项目说明文档1.md)。资源来源、离线边界、署名与完整性校验见 [离线资源下载清单](docs/离线资源下载清单.md)。

所有文件的实际大小和 SHA256 记录在 [离线资源 manifest](data/offline-assets-manifest.json)。真实浏览器、性能、断网代理、局部降级和截图证据记录在 [空间分析验收报告](docs/acceptance/homeorbit-spatial-analysis-report.md)；主控制器实测证据与用户验收状态分开记录。

## 目录

```text
E:/HomeOrbit/
├── api/                 # FastAPI 与 GIS 编排
├── data/                # 原始数据、ETL、PostGIS schema、Valhalla 产物
├── docs/                # 运行、资源与验收文档
├── web/                 # Next.js 前端与本地静态资源
└── docker-compose.yml   # HomeOrbit PostGIS/Valhalla services
```

项目当前没有 Git 元数据；不要在交付流程中自行 `git init`。
