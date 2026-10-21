# 2026-10-21 当前源码快照

这是当前开发源码的整合与目录分离，不是发布验收。产品开发暂停；本次未继续原产品计划，未添加新功能，未重新设计前端。

## 来源

| 来源 | 固定提交 | 保留内容 |
| --- | --- | --- |
| 主后端 | `418a52ef782e945ded9ed00004862f71890cfe52` | 已有平台后端及 I04 最终修复 |
| Studio | `690c42d21ef733a1988ea346cf4ba4f361b97f08` | U01 工作台、U02 Studio、服务端 producer 与后续修复 |
| 地图 | `82b152c30fa216e3298d1333c025e94b8a521c3c` | U01 等价基础、地图后端、格式校验、合成地图样本及许可证 |
| 共同已接受祖先 | `70c8e09d1f0d4a0ceadd664ab3079892a7c4299c` | 整合的共同基线 |

先合并 Studio，再合并地图。两个分支的 U01 等价实现（`a0a1d73` / `03bdfbb`）按相同源码合并，没有重新实现或重复应用。摄取 schema 保留 I04 时间及导入字段，也保留 U01 服务端只读字段标记；worker 同时保留 I04 摄取注册和 U02 精确发布结果 resolver。唯一文本冲突为第一次合并的 `contracts/openapi.json`，由实际组合后的 API 离线重新导出解决；地图合并后再次导出，并从最终契约重新生成前端类型。

地图尚未交付的 `0301_map_resources` 前驱从分支的 `0203_ingestion_scans_schedules` 调整为主后端 `0204_ingestion_runs_rows`，离线迁移图应只有 `0301_map_resources` 一个头。此前已接受的迁移内容没有改写。这不表示真实数据库迁移已执行。

## 目录映射

| 原路径 | 当前路径 |
| --- | --- |
| apps/api/（不含 Alembic） | backend/ |
| apps/web/ | frontend/ |
| apps/api/alembic/ | database/migrations/ |
| apps/api/alembic.ini | database/alembic.ini |
| infra/fuseki/ | database/fuseki/ |
| infra/compose/init-databases.sh | database/postgres/init-databases.sh |

`infra/` 保留编排、身份、代理及测试镜像；`contracts/`、`packages/`、`scripts/`、`tests/`、`docs/` 仍为共享目录。Docker COPY、Compose 挂载、启动工具、迁移入口、测试根目录计算及前端生成器均改为新路径。历史设计、计划和验证记录保留当时的路径与命令，阅读时按上表映射；其中的运行结果不是本次快照验证证据。

## 状态与边界

快照前已接受 **15/29** 个开发里程碑；I04 及地图修复尚未全部接受。React 工作台/Studio 是现有原型，仍需按用户参考重新设计，不代表最终视觉方案。Compose 部署、离线交付、备份恢复、性能及其余端到端验收未完成。

本次验证限定于迁移图、Python 静态/导入检查、契约生成一致性、受影响的已有纯后端测试，以及前端 API 检查、类型检查、单元测试和构建。没有执行真实数据库测试、迁移升级/降级、共享服务变更、Docker/WSL 操作或部署；本次源码保存不能据此宣称产品完成。

源码验证结果：已有纯后端及启动工具测试 **291 通过**（7 条 RDFLib 弃用警告），前端 **8 个文件、59 个测试通过**，API 生成检查、TypeScript 与 Vite 构建通过。静态核对合并后的 447 个原有文件全部可映射到新位置；18 个原迁移文件和 105 个字体/地图资源及许可证保持原始字节。Compose 仅解析 YAML、锚点、构建目录及静态路径，没有构建或运行容器。

运行数据库、私有环境文件、密钥、日志、依赖、旧 scratch 报告均不属于交付源码。字体、地图合成样本及随附许可证是有意保留的可分发资源。PostgreSQL/PostGIS 平台状态、Jena RDF 语义存储与外部 PostgreSQL/MySQL 只读来源的职责见[根说明](../../README.md)与[数据库说明](../../database/README.md)。
