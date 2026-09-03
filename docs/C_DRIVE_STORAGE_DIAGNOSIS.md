# C 盘空间占用排查与优化建议

更新时间：2026-09-03

## 1. 结论

HomeOrbit 的 API 数据、Valhalla 路网和前端缓存主要位于 E 盘，但 PostGIS 使用 Docker named volume `homeorbit_postgis_data`。Docker Desktop 的统一 VHDX 位于 `C:/Users/YY/AppData/Local/Docker/wsl/disk/docker_data.vhdx`，因此数据库卷和镜像会长期占用 C 盘。

当前项目明确关联的 Docker 数据包括：PostGIS 镜像约 886.8 MB、Valhalla 镜像约 957.9 MB、PostGIS volume 约 378.6 MB。受控启动 Valhalla 一次时，Docker VHDX 增长 32 MiB；停止容器后 VHDX不会自动缩回。

## 2. 排查过程与证据

- Compose 工作目录为 `E:/HomeOrbit`。
- `homeorbit-postgis` 将 `/var/lib/postgresql/data` 挂载到 Docker named volume，因此物理数据进入 C 盘 Docker VHDX。
- `homeorbit-valhalla` 的 `/data/raw` 和 `/data/valhalla` 均 bind 到 E 盘，路网业务数据未写入 C 盘。
- API 默认数据目录、npm cache 和 uv cache 均配置在 E 盘。
- 容器可写层只有几十 KiB，主要空间来自镜像、named volume 和 VHDX 分配。
- 本次只做健康检查，未下载地图、未抓取 OSM 瓦片、未重建 Valhalla 或基线。

## 3. 本次清理结果

- 保留 HomeOrbit 两张运行镜像和 PostGIS 正式 volume，未删除业务数据库。
- 全局未引用 Docker 构建缓存已清理，但不会改变 HomeOrbit 正式镜像和 volume。
- 执行 `fstrim` 后识别出 Docker 内部可回收块；由于 VHDX 不是 sparse 文件，空间未完整返还给 NTFS。
- `wsl --manage docker-desktop --set-sparse true` 要求额外使用 `--allow-unsafe`，本次未绕过安全提示。
- 审计结束后 PostGIS、Valhalla均恢复为停止状态，5432/8002 已释放。

## 3.1 项目级启停复测（2026-09-03）

- 已新增 `scripts/Start-HomeOrbit.ps1` 与 `scripts/Stop-HomeOrbit.ps1`：启动脚本将本轮进程的 `TEMP`、`TMP`、npm cache、uv cache 指向 E 盘，并记录 PID/容器归属；停止脚本依据状态文件和 Compose labels 精确停止，不执行 prune，不删除镜像或 volume。
- Docker Desktop 从 Resource Saver 唤醒时可能暂处于 `starting`。启动脚本会等待其进入 `running`，避免 Docker CLI 在引擎唤醒阶段无期限等待。
- 已使用 `scripts/Measure-HomeOrbitStorage.ps1` 连续执行 5 轮 PostGIS/Valhalla 启动、健康检查和停止。VHDX 初始与最终大小均为 `45,316,308,992` 字节，净增长 `0`；容器可写层峰值 `49,152` 字节。
- 本次复测不能重现“每次启停固定增长 32 MiB”。先前 32 MiB 更可能是首次分配、Docker/WSL 后台状态变化或一次性写入，现有证据不支持将其判断为稳定的线性泄漏。
- 原始测量数据保存在 `tmp/storage-measurement/20260903-163708/samples.csv` 和同目录 `summary.json`。

## 4. 预期优化方案

### P0：将 PostGIS 数据迁移到 E 盘

1. 在 Compose 中增加明确配置项，例如 `HOMEORBIT_POSTGIS_DATA_DIR`，目标目录放在 `E:/HomeOrbit/data/postgis`。
2. 不要直接复制正在运行的 PostgreSQL 数据目录。应先备份，并优先使用 `pg_dump`/`pg_restore` 或经过验证的停库迁移流程。
3. 迁移后先核对数据库版本、扩展、表数量、关键行数和空间数据，再删除旧 named volume。
4. 保留回滚窗口；旧 volume 的删除必须是独立、可审计步骤。

### P1：控制 Docker 主机空间

1. 启动脚本输出 Docker data-root、VHDX 路径、镜像/volume占用和容器初始状态。
2. 开发构建完成后只清理本项目无引用 cache，不执行全局 prune。
3. 为 Docker VHDX 压缩提供单独运维文档；涉及 `--allow-unsafe`、WSL 导出/导入或磁盘压缩时必须人工确认和备份。

### P1：防止外部数据意外进入 C 盘

1. 对 API、npm、uv、临时下载和生成文件执行启动期路径断言；当前统一启动脚本已将本轮进程的 `TEMP`、`TMP`、npm cache 和 uv cache 注入 E 盘项目目录。
2. 路网和离线资源继续只允许 E 盘受控目录；禁止从公共瓦片服务批量预取。

## 5. 验收标准

- 新部署的 PostGIS 数据目录可从宿主机确认位于 E 盘。
- 创建测试表并重启容器后数据仍存在，数据库完整性与迁移前一致。
- 启停 HomeOrbit 时，C 盘不再因 PostGIS 数据增长而持续扩大；镜像自身占用需单独记录。
- 迁移、回滚、旧 volume 删除分别有独立命令和验证记录。
