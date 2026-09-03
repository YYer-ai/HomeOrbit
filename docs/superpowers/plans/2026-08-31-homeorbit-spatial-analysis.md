# HomeOrbit 交通可达性与配套分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在完全离线运行条件下，为 HomeOrbit 增加三种底图、步行/驾车真实路网等时圈、人口密度图层和当前选点的 15 分钟步行设施充足度分析。

**Architecture:** 本地 PMTiles 负责三种地图主题，Valhalla 负责 `pedestrian`/`auto` 等时圈，PostGIS 负责 tract 与 OSM 设施空间统计，FastAPI 提供稳定接口，Next.js 前端管理选点、图层和权重。人口和设施先生成可校验的中间产物，再导入数据库；权重变化只在前端重算综合指数。

**Tech Stack:** Python 3.13、uv、FastAPI、HTTPX、Psycopg 3、Shapely、Pyproj、Pyshp、Pyosmium 4、PostGIS 17-3.5、Valhalla、Next.js 16、React 19、TypeScript、MapLibre GL 6、PMTiles、Vitest。

**Spec:** `docs/superpowers/specs/2026-08-31-homeorbit-spatial-analysis-design.md`

## Global Constraints

- 运行时必须完全离线；浏览器请求只允许本机地址。
- 通勤模式只开放 `walking` 和 `driving`；`public_transit` 必须显示 `coming_soon` 且不可执行。
- 驾车是纯驾车静态路网成本，不含实时拥堵、停车或末端步行。
- 通勤分钟数只允许 15、30、45、60；设施分析始终为独立的 15 分钟纯步行圈。
- Valhalla 失败时禁止返回圆形缓冲区或任何伪等时圈。
- 五类设施固定为 `education`、`healthcare`、`daily_shopping`、`parks`、`public_transport`。
- 圈内估算人口低于 100 时不输出分项或综合分数。
- 相对分数必须标注为湾区居民加权参考指数，不得称为法规达标率。
- 所有新增说明、错误文案和 Git 提交说明使用简体中文。
- PowerShell 命令使用 PowerShell 7 语法、正斜杠路径和带空格路径双引号。
- `E:/HomeOrbit` 当前不是 Git 工作树；不得自行 `git init`。各任务的提交步骤仅在执行时确认已存在 Git 工作树后运行，否则把同一检查点写入 `progress.md`。
- 前端编码前阅读 `web/AGENTS.md` 以及本地 Next.js 16 文档：`web/node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md`、`web/node_modules/next/dist/docs/01-app/02-guides/testing/vitest.md`。
- Valhalla 采用 `polygons=true` 的单一时间 contour；官方接口定义见 `https://valhalla.github.io/valhalla/api/isochrone/`。
- Pyosmium 面处理使用 `FileProcessor(...).with_areas()` 与 `GeoJSONFactory`；不自行实现 relation 拼接。

## 文件结构

### 数据与服务

- `data/scripts/spatial_common.py`：湾区范围、设施标签映射、SHA256 和 NDJSON 公共函数。
- `data/scripts/prepare_population.py`：合并 ACS 与 Census tract，生成 tract NDJSON 和简化人口密度 GeoJSON。
- `data/scripts/prepare_facilities.py`：从 California PBF 提取五类设施并生成 NDJSON。
- `data/scripts/load_spatial_data.py`：执行 schema 并批量导入 tract、设施及元数据。
- `data/scripts/build_facility_baseline.py`：为 tract 代表点构建 15 分钟步行参考样本。
- `data/scripts/build_valhalla.sh`：只负责一次性生成 Valhalla 配置、辅助库、路由图和 extract。
- `data/sql/001_spatial_schema.sql`：PostGIS 表、约束和索引。
- `data/processed/bay-area-tracts.ndjson`：完整 tract 导入产物。
- `data/processed/bay-area-facilities.ndjson`：五类设施导入产物。
- `data/processed/spatial-data-metadata.json`：数据版本、数量、范围和哈希。
- `web/public/data/bay-area-tract-density.geojson`：前端人口密度图层。

### API

- `api/app/config.py`：环境变量和湾区边界配置。
- `api/app/gis/schemas.py`：请求、响应、枚举和 GeoJSON 类型边界。
- `api/app/gis/errors.py`：稳定错误码和 HTTP 映射。
- `api/app/gis/valhalla.py`：Valhalla HTTP 客户端。
- `api/app/gis/repository.py`：PostGIS 空间统计和基准查询。
- `api/app/gis/scoring.py`：人口加权百分位函数。
- `api/app/gis/service.py`：路由与选点评估编排。
- `api/app/gis/router.py`：`/gis/config`、`/gis/isochrone`、`/gis/site-analysis`。
- `api/app/main.py`：应用生命周期、连接池、HTTP 客户端、CORS 和路由挂载。

### 前端

- `web/src/app/gis-types.ts`：与 API 一致的 TypeScript 类型。
- `web/src/app/gis-api.ts`：可取消的请求客户端和安全错误解析。
- `web/src/app/map-style.ts`：三种 PMTiles 样式生成器。
- `web/src/app/map-overlays.ts`：分析 GeoJSON source/layer 的幂等恢复。
- `web/src/app/analysis-score.ts`：分类开关、权重归一化和综合指数。
- `web/src/app/map-controls.tsx`：底图、图层、模式、时间控件。
- `web/src/app/analysis-panel.tsx`：人口、原始供给、权重和评分面板。
- `web/src/app/map.tsx`：地图容器、Marker、请求状态和组件编排。
- `web/src/app/globals.css`：桌面侧栏与移动端底部抽屉样式。

---

### Task 1: 锁定 GIS 契约、依赖与验证规则

**Files:**
- Modify: `api/pyproject.toml`
- Modify: `api/uv.lock`
- Create: `api/app/config.py`
- Create: `api/app/gis/__init__.py`
- Create: `api/app/gis/schemas.py`
- Create: `api/app/gis/errors.py`
- Create: `api/tests/test_gis_contracts.py`

**Interfaces:**
- Produces: `TravelMode`, `FacilityCategory`, `Origin`, `IsochroneResponse`, `SiteAnalysisResponse`, `GisError`, `Settings`。
- Consumes: 设计文档中的模式、时间、边界、分类和错误码。

- [x] **Step 1: 写失败的契约测试**

```python
from pydantic import ValidationError

from app.gis.schemas import IsochroneQuery, Origin


def test_isochrone_query_rejects_unapproved_duration() -> None:
    try:
        IsochroneQuery(origin=Origin(lng=-122.4194, lat=37.7749), mode="walking", minutes=20)
    except ValidationError:
        return
    raise AssertionError("20 分钟必须被拒绝")


def test_origin_rejects_point_outside_bay_area() -> None:
    try:
        Origin(lng=-118.2437, lat=34.0522)
    except ValidationError:
        return
    raise AssertionError("湾区外坐标必须被拒绝")
```

- [x] **Step 2: 运行测试并确认红灯**

Run:

```powershell
cd "E:/HomeOrbit/api"
$env:UV_CACHE_DIR = "E:/HomeOrbit/.uv-cache"
uv run --group dev pytest "tests/test_gis_contracts.py" -v
```

Expected: FAIL，原因是 `app.gis.schemas` 尚不存在。

- [x] **Step 3: 添加最小依赖和明确类型**

在 `pyproject.toml` 增加运行依赖 `httpx>=0.28,<1`、`psycopg[binary,pool]>=3.2,<4`；增加 `dev` 组 `pytest>=8.4,<9`、`pytest-asyncio>=1,<2`、`pytest-httpx>=0.35,<1`；增加 `data` 组 `osmium>=4.3,<5`、`pyproj>=3.7,<4`、`pyshp>=2.3,<3`。增加以下 pytest marker 配置后运行 `uv lock`：

```toml
[tool.pytest.ini_options]
markers = ["integration: requires local PostGIS and/or Valhalla"]
```

`schemas.py` 的核心约束固定为：

```python
TravelMode = Literal["walking", "driving"]
FacilityCategory = Literal[
    "education", "healthcare", "daily_shopping", "parks", "public_transport"
]
ALLOWED_DURATIONS = frozenset({15, 30, 45, 60})
BAY_AREA_BBOX = (-123.3, 37.2, -121.7, 38.0)
```

`GisError` 必须接受稳定 `code`、中文 `message` 和 HTTP 状态码；禁止保存堆栈到响应体。

- [x] **Step 4: 运行契约测试**

Run: `uv run --group dev pytest "tests/test_gis_contracts.py" -v`

Expected: PASS。

- [x] **Step 5: 建立任务检查点**

若 `git -C "E:/HomeOrbit" rev-parse --is-inside-work-tree` 返回 `true`，仅暂存本任务文件并提交：

```powershell
git -C "E:/HomeOrbit" add -- "api/pyproject.toml" "api/uv.lock" "api/app/config.py" "api/app/gis" "api/tests/test_gis_contracts.py"
git -C "E:/HomeOrbit" commit -m "新增 GIS 接口契约与校验"
```

否则在 `progress.md` 记录“Task 1 验证通过；非 Git 工作树，未提交”。

### Task 2: 生成湾区人口与五类设施数据产物

**Files:**
- Create: `data/scripts/spatial_common.py`
- Create: `data/scripts/prepare_population.py`
- Create: `data/scripts/prepare_facilities.py`
- Create: `data/tests/fixtures/facilities.osm`
- Create: `data/tests/test_prepare_population.py`
- Create: `data/tests/test_prepare_facilities.py`
- Create: `data/processed/bay-area-tracts.ndjson`
- Create: `data/processed/bay-area-facilities.ndjson`
- Create: `data/processed/spatial-data-metadata.json`
- Create: `web/public/data/bay-area-tract-density.geojson`

**Interfaces:**
- Produces: 每行包含 `geoid`、人口、`aland_m2`、GeoJSON geometry 的 tract NDJSON；每行包含 `osm_key`、`category`、`name`、`geometry`、`analysis_point` 的设施 NDJSON。
- Consumes: `data/processed/bay-area-acs-2023.json`、`data/raw/cb_2023_us_tract_500k.zip`、`data/raw/california-latest.osm.pbf`。

- [x] **Step 1: 写人口关联与设施分类失败测试**

```python
def test_normalize_acs_geoid() -> None:
    assert normalize_acs_geoid("1400000US06075010100") == "06075010100"


def test_classify_facility_tags() -> None:
    assert classify_facility({"amenity": "school"}) == "education"
    assert classify_facility({"shop": "supermarket"}) == "daily_shopping"
    assert classify_facility({"leisure": "park"}) == "parks"
    assert classify_facility({"highway": "bus_stop"}) == "public_transport"
```

人口 fixture 必须创建一个微型 shapefile ZIP，并断言 ACS `1400000US...` 与 shapefile 11 位 `GEOID` 正确关联、密度单位为人/平方公里。OSM fixture 必须包含 school node、supermarket node、bus stop node、hospital polygon 和 park polygon。

- [x] **Step 2: 运行数据测试并确认红灯**

Run:

```powershell
cd "E:/HomeOrbit/api"
$env:UV_CACHE_DIR = "E:/HomeOrbit/.uv-cache"
uv run --group data --group dev python -m pytest "../data/tests/test_prepare_population.py" "../data/tests/test_prepare_facilities.py" -v
```

Expected: FAIL，原因是新脚本尚不存在。

- [x] **Step 3: 实现人口产物**

`prepare_population.py` 必须：

1. 使用 `zipfile` 解压到临时目录，使用 Pyshp 读取 `STATEFP`、`COUNTYFP`、`GEOID`、`ALAND` 和几何。
2. 只保留州 `06` 和九县 FIPS；将 ACS 前缀移除后按 11 位 `GEOID` 关联。
3. 使用 Pyproj 将 Census NAD83 几何显式转换为 EPSG:4326。
4. 完整几何写入 NDJSON；前端副本用 `simplify(0.0001, preserve_topology=True)`。
5. GeoJSON 属性至少包含 `geoid`、`county_name`、`population`、`population_density_km2`、`acs_year`。
6. 缺少人口、`ALAND<=0`、重复或未关联 `GEOID` 时失败退出，不静默跳过。

- [x] **Step 4: 实现设施产物**

`prepare_facilities.py` 必须使用：

```python
processor = osmium.FileProcessor(input_path).with_areas()
factory = osmium.geom.GeoJSONFactory()
```

对 node 使用 `create_point`，对 area 使用 `create_multipolygon`；用 Shapely 生成 `analysis_point=geometry.representative_point()`。设施键使用 `n<id>`、`w<orig_id>` 或 `r<orig_id>`，并对同名同类、30 米内公共交通站点去重。公园保留面 geometry；其他设施仍保留原 geometry 和分析点。只输出与湾区 bbox 相交的记录。

- [x] **Step 5: 运行测试和正式 ETL**

Run:

```powershell
cd "E:/HomeOrbit/api"
$env:UV_CACHE_DIR = "E:/HomeOrbit/.uv-cache"
uv run --group data --group dev pytest "../data/tests/test_prepare_population.py" "../data/tests/test_prepare_facilities.py" -v
uv run --group data python "../data/scripts/prepare_population.py"
uv run --group data python "../data/scripts/prepare_facilities.py"
```

Expected: 测试 PASS；人口产物为 1,772 个 tract；四个输出文件存在、非空、JSON/NDJSON 可逐条解析，元数据包含 SHA256 和记录数。

- [x] **Step 6: 建立任务检查点**

Git 存在时提交上述脚本、测试和小型 fixture；大型生成产物是否进入 Git 只按现有项目规则执行，不擅自新增跟踪。中文提交说明：`生成湾区人口与设施空间数据`。无 Git 时记录到 `progress.md`。

### Task 3: 建立 PostGIS schema 与可重复导入

**Files:**
- Create: `data/sql/001_spatial_schema.sql`
- Create: `data/scripts/load_spatial_data.py`
- Create: `data/tests/test_load_spatial_data.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `tracts`、`facilities`、`facility_baseline_samples`、`dataset_metadata` 表和 `SpatialRepository` 所需字段。
- Consumes: Task 2 的三个处理产物。

- [x] **Step 1: 写 schema 和导入失败测试**

测试必须断言：同一 `geoid`/`osm_key` 重复导入后行数不增加；未知分类被数据库 CHECK 拒绝；metadata 更新为最新值。

核心 schema 固定为：

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS tracts (
  geoid text PRIMARY KEY,
  county_name text NOT NULL,
  population integer NOT NULL CHECK (population >= 0),
  aland_m2 bigint NOT NULL CHECK (aland_m2 > 0),
  geom geometry(MultiPolygon, 4326) NOT NULL,
  representative_point geometry(Point, 4326) NOT NULL
);
CREATE TABLE IF NOT EXISTS facilities (
  osm_key text PRIMARY KEY,
  category text NOT NULL CHECK (category IN ('education','healthcare','daily_shopping','parks','public_transport')),
  name text,
  geom geometry(Geometry, 4326) NOT NULL,
  analysis_point geometry(Point, 4326) NOT NULL
);
CREATE TABLE IF NOT EXISTS facility_baseline_samples (
  geoid text PRIMARY KEY REFERENCES tracts(geoid) ON DELETE CASCADE,
  population_weight integer NOT NULL CHECK (population_weight >= 0),
  education double precision NOT NULL,
  healthcare double precision NOT NULL,
  daily_shopping double precision NOT NULL,
  parks double precision NOT NULL,
  public_transport double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_metadata (
  key text PRIMARY KEY,
  value jsonb NOT NULL
);
```

并为 `tracts.geom`、`facilities.analysis_point`、`facilities.geom` 建立 GiST 索引。

- [x] **Step 2: 运行测试并确认红灯**

Run: `uv run --group data --group dev pytest "../data/tests/test_load_spatial_data.py" -v`

Expected: FAIL，schema 和 loader 尚不存在。

- [x] **Step 3: 实现事务性导入**

`load_spatial_data.py` 使用 Psycopg 事务、`executemany` 和 `ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))` 导入 tract。每类数据先导入临时表，核对期望行数后在同一事务中 upsert 正式表；失败必须回滚，不截断既有正式数据。

`docker-compose.yml` 的 PostGIS service 增加 `pg_isready` healthcheck，不改变现有数据库名、用户名、密码、端口和 volume。

- [x] **Step 4: 运行真实 PostGIS 集成验证**

在确认 5432 端口无其他进程且 Docker 容器归属为 HomeOrbit 后执行：

```powershell
docker compose -f "E:/HomeOrbit/docker-compose.yml" up -d postgis
cd "E:/HomeOrbit/api"
$env:HOMEORBIT_DATABASE_URL = "postgresql://homeorbit:homeorbit_dev@127.0.0.1:5432/homeorbit"
uv run --group data python "../data/scripts/load_spatial_data.py"
uv run --group data --group dev pytest "../data/tests/test_load_spatial_data.py" -v -m integration
```

Expected: 四张表存在；tract 为 1,772；设施大于 0；GiST 索引存在；重复执行行数不变。

- [x] **Step 5: 建立任务检查点**

Git 存在时提交 `docker-compose.yml`、SQL、loader 和测试，中文说明 `建立空间数据库与可重复导入`；否则记录检查点。

### Task 4: 构建并运行本地 Valhalla 路由服务

**Files:**
- Create: `data/scripts/build_valhalla.sh`
- Modify: `docker-compose.yml`
- Create: `data/tests/test_valhalla_compose.py`
- Create at runtime: `data/valhalla/valhalla.json`
- Create at runtime: `data/valhalla/tiles.tar`

**Interfaces:**
- Produces: 本机 `http://127.0.0.1:8002/status` 和 `/isochrone`。
- Consumes: `ghcr.io/valhalla/valhalla:latest` 离线镜像与 `data/raw/california-latest.osm.pbf`。

- [x] **Step 1: 写 compose 静态验证测试**

测试解析 `docker compose config --format json`，断言存在 `valhalla-build` profile service 和 `valhalla` runtime service；两者共用 `data/valhalla`，只有 runtime 暴露 8002。

- [x] **Step 2: 运行测试并确认红灯**

Expected: FAIL，两个 service 尚未定义。

- [x] **Step 3: 编写幂等的一次性建图脚本**

脚本必须 `set -euo pipefail`，验证 PBF 存在；若 `valhalla.json` 与 `tiles.tar` 都存在则安全退出；若只存在其中一个则报错并要求人工处理，不自动删除。构建顺序固定为：

```bash
valhalla_build_config --mjolnir-tile-dir /data/valhalla/tiles --mjolnir-tile-extract /data/valhalla/tiles.tar --mjolnir-timezone /data/valhalla/tz_world.sqlite --mjolnir-admin /data/valhalla/admin.sqlite > /data/valhalla/valhalla.json
valhalla_build_admins -c /data/valhalla/valhalla.json /data/raw/california-latest.osm.pbf
valhalla_build_timezones > /data/valhalla/tz_world.sqlite
valhalla_build_tiles -c /data/valhalla/valhalla.json /data/raw/california-latest.osm.pbf
valhalla_build_extract -c /data/valhalla/valhalla.json -v
```

- [x] **Step 4: 添加 build/runtime compose service**

`valhalla-build` 使用 profile `build` 并执行 `/bin/bash /opt/homeorbit/build_valhalla.sh`。`valhalla` 执行：

```yaml
command: ["valhalla_service", "/data/valhalla/valhalla.json", "1"]
ports:
  - "8002:8002"
```

两者只读挂载 `data/raw` 和脚本，可写挂载 `data/valhalla`。

- [x] **Step 5: 加载离线镜像并构建真实路由图**

在执行前核对 Docker Desktop、磁盘空间、容器名和 8002 端口归属；获得实施授权后执行：

```powershell
docker image inspect "ghcr.io/valhalla/valhalla:latest" *> $null
if ($LASTEXITCODE -ne 0) { docker load -i "E:/HomeOrbit/data/docker-images/valhalla.tar" }
docker compose -f "E:/HomeOrbit/docker-compose.yml" --profile build run --rm valhalla-build
docker compose -f "E:/HomeOrbit/docker-compose.yml" up -d valhalla
```

随后向 `/status` 和两个 `/isochrone` 请求发送本地 HTTP 验证：同一点 15 分钟 `pedestrian` 与 `auto` 均返回 FeatureCollection，geometry 为 Polygon/MultiPolygon，且面积或坐标不相同。

- [x] **Step 6: 建立任务检查点**

Git 存在时提交脚本、compose 和测试，中文说明 `接入本地 Valhalla 路由服务`；不提交运行生成的 tiles，除非项目规则明确要求；否则记录检查点。

### Task 5: 用真实 Valhalla 替换圆形占位接口

**Files:**
- Create: `api/app/gis/valhalla.py`
- Create: `api/app/gis/router.py`
- Create: `api/app/gis/service.py`
- Create: `api/tests/test_valhalla_client.py`
- Create: `api/tests/test_isochrone_api.py`
- Modify: `api/app/main.py`

**Interfaces:**
- Produces: `ValhallaClient.isochrone(origin, mode, minutes) -> dict[str, Any]` 和 `GET /gis/isochrone`。
- Consumes: Task 1 类型与 Task 4 的 8002 服务。

- [x] **Step 1: 写失败的 Valhalla 请求与错误映射测试**

```python
@pytest.mark.asyncio
async def test_client_maps_driving_to_auto(httpx_mock) -> None:
    httpx_mock.add_response(json={"type": "FeatureCollection", "features": []})
    await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "driving", 15)
    payload = json.loads(httpx_mock.get_request().url.params["json"])
    assert payload["costing"] == "auto"
    assert payload["polygons"] is True
    assert payload["contours"] == [{"time": 15}]
```

API 测试还必须覆盖 20 分钟、公共交通、越界坐标、Valhalla timeout，以及响应中 `traffic_assumption="static_network_cost"`。

- [x] **Step 2: 运行测试并确认红灯**

Run: `uv run --group dev pytest "tests/test_valhalla_client.py" "tests/test_isochrone_api.py" -v`

Expected: FAIL，新 client/router 尚不存在。

- [x] **Step 3: 实现最小真实等时圈链路**

`ValhallaClient` 使用共享 `httpx.AsyncClient`，请求超时取 `Settings.valhalla_timeout_seconds=5.0`。请求体只包含 location、costing、单一 contour、`polygons=true`、`show_locations=true`。验证返回 FeatureCollection 至少含一个 Polygon/MultiPolygon；空或错误结构映射为 `VALHALLA_UNAVAILABLE`，不得构造 fallback geometry。

`main.py` 使用 FastAPI lifespan 打开/关闭 HTTP client 和数据库 pool；保留 CORS，删除 `/gis/isochrone-demo`，挂载 GIS router。

- [x] **Step 4: 运行单元与真实集成测试**

Run:

```powershell
uv run --group dev pytest "tests/test_valhalla_client.py" "tests/test_isochrone_api.py" -v
uv run --group dev pytest "tests/test_isochrone_api.py" -v -m integration
```

Expected: PASS；代码检索 `rg "buffer\(|isochrone-demo" "api"` 无业务命中。

- [x] **Step 5: 建立任务检查点**

Git 存在时中文提交 `用真实路网替换圆形等时圈`；否则记录检查点。

### Task 6: 实现人口、设施、基准和选点评估

**Files:**
- Create: `api/app/gis/repository.py`
- Create: `api/app/gis/scoring.py`
- Modify: `api/app/gis/service.py`
- Modify: `api/app/gis/router.py`
- Create: `data/scripts/build_facility_baseline.py`
- Create: `api/tests/test_scoring.py`
- Create: `api/tests/test_site_analysis_api.py`
- Create: `data/tests/test_build_facility_baseline.py`

**Interfaces:**
- Produces: `SpatialRepository.analyze_catchment(geojson) -> SpatialStats`、`weighted_percentile(value, samples) -> float`、`POST /gis/site-analysis`。
- Consumes: Task 3 数据表、Task 4/5 Valhalla client、Task 1 响应模型。

- [x] **Step 1: 写评分和编排失败测试**

```python
def test_population_weighted_percentile() -> None:
    samples = [(1.0, 100), (2.0, 300), (3.0, 600)]
    assert weighted_percentile(2.0, samples) == 40.0


def test_low_population_has_no_scores() -> None:
    result = build_scores(population=99.9, raw_metrics=RAW_METRICS, baselines=BASELINES)
    assert result.status == "insufficient_population"
    assert all(metric.score is None for metric in result.metrics)
```

API 测试必须证明 `/gis/site-analysis` 永远调用 `pedestrian,15`，返回五类原始值，且 PostGIS 失败不会使 `/gis/isochrone` 失效。

- [x] **Step 2: 运行测试并确认红灯**

Run: `uv run --group dev pytest "tests/test_scoring.py" "tests/test_site_analysis_api.py" "../data/tests/test_build_facility_baseline.py" -v`

Expected: FAIL，新 repository/scoring/builder 尚不存在。

- [x] **Step 3: 实现空间统计查询**

`repository.py` 使用一个 CTE 将 GeoJSON 转为 SRID 4326 polygon。人口按 `ST_Area(ST_Intersection(... )::geography) / ST_Area(tract.geom::geography)` 加权。四类点设施按 `analysis_point` 是否被圈覆盖计数；公园先 `ST_UnaryUnion(ST_Collect(geom))`，再与圈相交并换算公顷，避免重叠重复面积。

原始指标固定为四类 count/每千人、公园 hectare/每千人。基准百分位查询按 `population_weight` 加权；数值相同的样本使用 `metric <= selected_value`。

- [x] **Step 4: 实现基准构建器**

构建器按 `tracts.geoid` 顺序遍历代表点，逐点调用 15 分钟 pedestrian contour 和同一 `analyze_catchment`。每个成功样本立即 upsert 并提交，支持 `--resume` 跳过已有 geoid；失败记录 geoid 和原因并以非零状态结束。最终必须有 1,772 个样本且所有五类指标非负，才写入 `dataset_metadata.baseline_ready=true`。

- [x] **Step 5: 实现 site-analysis 和配置/健康接口**

`POST /gis/site-analysis` 只接受 origin；返回 catchment、population、五类 raw value/unit/score、设施 FeatureCollection、ACS/OSM 版本和 limitations。`GET /gis/config` 返回模式、时间、分类、默认 20 权重和组件状态。`GET /gis/health` 返回 `ok` 或 `degraded` 以及 API/Valhalla/PostGIS/人口文件/基准状态。

- [x] **Step 6: 运行测试、构建基准和性能验证**

Run:

```powershell
cd "E:/HomeOrbit/api"
$env:HOMEORBIT_DATABASE_URL = "postgresql://homeorbit:homeorbit_dev@127.0.0.1:5432/homeorbit"
$env:HOMEORBIT_VALHALLA_URL = "http://127.0.0.1:8002"
uv run --group dev pytest "tests/test_scoring.py" "tests/test_site_analysis_api.py" "../data/tests/test_build_facility_baseline.py" -v
uv run --group data python "../data/scripts/build_facility_baseline.py" --resume
```

Expected: 单元测试 PASS；基准表 1,772 行；同一点 site-analysis 重复结果一致；服务就绪后单请求不超过 5 秒。

- [x] **Step 7: 建立任务检查点**

Git 存在时中文提交 `增加人口与设施相对评分`；否则记录检查点。

### Task 7: 建立前端类型、API、评分和三种底图

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Create: `web/vitest.config.ts`
- Create: `web/vitest.setup.ts`
- Create: `web/src/app/gis-types.ts`
- Create: `web/src/app/gis-api.ts`
- Create: `web/src/app/analysis-score.ts`
- Create: `web/src/app/map-style.ts`
- Create: `web/src/app/__tests__/analysis-score.test.ts`
- Create: `web/src/app/__tests__/map-style.test.ts`
- Create: `web/src/app/__tests__/gis-api.test.ts`

**Interfaces:**
- Produces: `fetchConfig`、`fetchIsochrone`、`fetchSiteAnalysis`、`computeCompositeScore`、`createBasemapStyle`。
- Consumes: Task 1/5/6 API JSON 契约和现有 PMTiles/字体/sprite。

- [x] **Step 1: 阅读本地 Next.js 16 文档并写失败测试**

测试必须覆盖：默认五类等权；关闭分类后重新归一化；权重和为零返回 `null`；三个 style 都使用同一 `pmtiles://.../bay-area.pmtiles`；交通主题道路/轨道更突出；分析主题背景更浅；source attribution 同时包含 OpenStreetMap 和 Protomaps。

```ts
expect(computeCompositeScore({ education: 80, healthcare: 40 }, { education: 1, healthcare: 1 })).toBe(60);
expect(computeCompositeScore({ education: 80 }, { education: 0 })).toBeNull();
```

- [x] **Step 2: 安装测试依赖并确认红灯**

在 `package.json` 增加脚本 `"test": "vitest"`。

Run:

```powershell
cd "E:/HomeOrbit/web"
$env:npm_config_cache = "E:/HomeOrbit/.npm-cache"
npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom
npm test -- --run
```

Expected: FAIL，目标模块尚不存在。

- [x] **Step 3: 实现纯函数和请求客户端**

`gis-api.ts` 基于 `window.location.hostname:8000`，每个函数接收 `AbortSignal`。非 2xx 时只读取 `code/message`；解析失败显示通用中文错误，禁止把 HTML 或堆栈显示给用户。

`map-style.ts` 以 `namedFlavor("light")` 为标准、克隆 light 并调整 `roads_highway`/`roads_major`/`roads_rail` 为交通主题、以 `namedFlavor("white")` 为分析浅色主题。函数每次返回新对象，避免主题间共享可变 layer。

- [x] **Step 4: 运行前端单元、lint 和构建**

Run:

```powershell
npm test -- --run
npm run lint
npm run build
```

Expected: 全部 PASS。

- [x] **Step 5: 建立任务检查点**

Git 存在时中文提交 `增加前端 GIS 类型与底图主题`；否则记录检查点。

### Task 8: 实现选点、图层控制和设施分析界面

**Files:**
- Create: `web/src/app/map-overlays.ts`
- Create: `web/src/app/map-controls.tsx`
- Create: `web/src/app/analysis-panel.tsx`
- Modify: `web/src/app/map.tsx`
- Modify: `web/src/app/globals.css`
- Create: `web/src/app/__tests__/map-controls.test.tsx`
- Create: `web/src/app/__tests__/analysis-panel.test.tsx`
- Create: `web/src/app/__tests__/map-overlays.test.ts`

**Interfaces:**
- Produces: 完整地图用户路径；`syncAnalysisOverlays(map, state)` 幂等恢复 source/layer。
- Consumes: Task 7 纯函数/API、Task 5/6 GeoJSON、人口密度静态文件。

- [x] **Step 1: 写交互和图层恢复失败测试**

测试必须覆盖：公共交通按钮可见且 disabled；分钟只显示四档；底图三选一；五类开关和 slider；零权重显示“未计算”；`syncAnalysisOverlays` 连续调用不重复 addSource/addLayer；样式切换后可恢复等时圈、人口和设施。

- [x] **Step 2: 运行测试并确认红灯**

Run: `npm test -- --run`

Expected: FAIL，新组件和 overlay helper 尚不存在。

- [x] **Step 3: 实现地图状态机和可拖动 Marker**

`map.tsx` 必须维护 `origin`、`theme`、`mode`、`minutes`、layer visibility、isochrone/site-analysis loading/error/data。地图 click 设置 marker；`new Marker({ draggable: true })` 的 `dragend` 更新 origin。通勤和设施使用两个独立 `useEffect`/AbortController；通勤依赖 origin/mode/minutes，设施只依赖 origin。

任何新请求开始时清除对应旧 overlay；失败只更新该分析错误，不移除底图、标记和控件。切换主题调用 `setStyle`，在 style load 后调用 `syncAnalysisOverlays`。

- [x] **Step 4: 实现控制区、结果面板和响应式布局**

桌面宽度 `>=1024px`：左上控制区、右侧 360px 结果面板；移动端：控制区顶部紧凑排列，结果为最大高度 45vh 的底部抽屉。人口图例显示人/平方公里和 ACS 2023；设施面板显示原始 count/公园公顷、分项相对分和综合指数说明。

- [x] **Step 5: 运行前端验证**

Run:

```powershell
npm test -- --run
npm run lint
npm run build
```

Expected: 全部 PASS；`rg "isochrone-demo" "web/src"` 无命中；页面无裸露英文内部错误。

- [x] **Step 6: 建立任务检查点**

Git 存在时中文提交 `完成地图选点与配套分析界面`；否则记录检查点。

### Task 9: 文档、清单、真实服务与断网用户验收

**Files:**
- Modify: `README.md`
- Modify: `docs/项目说明文档1.md`
- Modify: `docs/离线资源下载清单.md`
- Modify: `data/offline-assets-manifest.json`
- Create: `docs/acceptance/homeorbit-spatial-analysis-desktop.png`
- Create: `docs/acceptance/homeorbit-spatial-analysis-mobile.png`
- Create: `docs/acceptance/homeorbit-spatial-analysis-report.md`

**Interfaces:**
- Produces: 可复核启动/停止/验证步骤、数据哈希与截图证据。
- Consumes: Tasks 2–8 的最终产物和服务。

- [x] **Step 1: 更新文档和 manifest**

文档必须列出：一次性 Valhalla build、PostGIS 导入、基准构建、日常启动、健康检查、前后端启动、局部降级验证和仅停止本项目进程的方法。manifest 增加 tract NDJSON、facility NDJSON、population GeoJSON、metadata、Valhalla config/tiles 的大小和 SHA256；不得收录数据库密码或浏览器 profile。

- [x] **Step 2: 运行全量静态与单元测试**

```powershell
cd "E:/HomeOrbit/api"
$env:UV_CACHE_DIR = "E:/HomeOrbit/.uv-cache"
uv run --group data --group dev pytest "tests" "../data/tests" -v
cd "E:/HomeOrbit/web"
$env:npm_config_cache = "E:/HomeOrbit/.npm-cache"
npm test -- --run
npm run lint
npm run build
```

Expected: 全部 PASS。

- [x] **Step 3: 核对端口/PID 后启动最小完整链路**

先读取 3000、5432、8000、8002 监听 PID 与命令行，不停止无法确认归属的进程。只启动 HomeOrbit 的 PostGIS、Valhalla、FastAPI 和 Next.js；记录每个 PID/container ID。

- [x] **Step 4: 执行真实功能与性能验收**

使用旧金山市中心同一坐标：

1. 对步行/驾车分别请求 15、30、45、60 分钟。
2. 断言每个 geometry 有效且非圆形占位，步行/驾车结果不同。
3. 请求 site-analysis，断言五类字段、人口、原始供给、分项分数和 limitations 完整。
4. 连续执行 20 次，记录 p50/p95；每个单请求必须不超过 5 秒。
5. 改变前端权重，确认 Network 面板没有新增 site-analysis 请求。

- [x] **Step 5: 执行失效外网代理和局部降级验收**

设置无效 HTTP/HTTPS 代理后打开页面，确认请求域名仅为 `127.0.0.1`/`localhost`。依次停止 Valhalla、PostGIS，每次都截图并验证地图、标记和控件保留，错误为安全简体中文；恢复服务后重新验证。

- [x] **Step 6: 完成桌面与移动端截图验收**

桌面截图至少展示交通网图、驾车等时圈、人口密度和完整评分面板；移动截图验证底部抽屉不遮挡起点。检查控制台无错误、无外网失败、无 4xx/5xx。报告记录截图路径、请求清单、性能、数据行数、SHA256 和已知限制。

- [x] **Step 7: 精确停止本轮服务并建立最终检查点**

只停止 Step 3 记录的 HomeOrbit PID/container；确认 3000、8000、8002 已释放，PostGIS 是否保留运行按用户当次授权执行。Git 存在时显式暂存本任务文档和验收文件，运行 `git diff --cached --check` 后中文提交 `完善空间分析文档与验收证据`；否则把最终验证结果写入 `progress.md`。

## 完成定义

- Tasks 1–9 全部勾选并各自验证通过。
- 真实 Valhalla/PostGIS 集成、前后端测试、生产构建和断网验收通过。
- 三种底图、步行/驾车四档等时圈、人口密度、五类设施、动态权重和局部降级均有截图证据。
- 浏览器无外网请求，API 无圆形 fallback，公共交通仍为明确的“建设中”。
- 文档、manifest、数据数量、SHA256、端口/PID 和停止结果可复核。

## 计划自检结果

- 设计第 1–4 节的系统分层、接口和数据流由 Tasks 1、3、4、5、6、7、8 覆盖。
- 设计第 5–9 节的数据产物、评分、交互和局部降级由 Tasks 2、3、6、7、8 覆盖。
- 设计第 10–13 节的健康状态、测试、验收、实施顺序和限制由 Tasks 4、5、6、8、9 覆盖。
- 未发现缺失需求、占位标记、未定义接口或前后不一致的模式、分类和分钟字段。
- 当前唯一流程限制是项目尚无 Git 工作树；计划已明确禁止擅自初始化，并为每个任务提供非 Git 检查点。
