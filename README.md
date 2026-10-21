# 本体农场 · 当前源码快照

本仓库保存农业 Operational Ontology Platform 的当前开发源码，包含语义后端、React Studio 原型、数据库迁移和部署工具。产品开发目前暂停；本次工作只整合已有源码并分离目录，不代表发布或验收通过。

快照前已接受开发里程碑为 **15/29**。I04 摄取与地图修复尚未全部验收，前端仍需按用户参考重新设计；Compose、离线交付、备份恢复和性能验收仍未完成。具体来源与限制见[快照状态](docs/status/2026-10-21-source-snapshot.md)。

| 目录 | 职责 |
| --- | --- |
| [backend/](backend/README.md) | Python API、worker、权限治理、语义逻辑、摄取、地图服务、模型及测试 |
| [frontend/](frontend/README.md) | React 工作台与 Studio 原型、静态资源、单元测试及独立 npm 锁文件 |
| [database/](database/README.md) | Alembic 迁移、PostgreSQL 初始化和 Fuseki/TDB2 配置 |
| infra/ | Compose 编排、HTTPS 代理、Keycloak 身份配置及测试镜像 |
| contracts/、packages/ | 共享 API 快照与契约；前端类型由后端 OpenAPI 生成 |
| scripts/、tests/ | 启动与维护入口、共享浏览器测试和可分发测试样本 |
| docs/ | 当前操作文档、状态说明，以及保留原路径的历史设计和验证记录 |

平台 PostgreSQL/PostGIS 保存平台状态、权限、修订、任务、来源映射和空间数据；Jena Fuseki/TDB2 保存 RDF 本体、语义投影和查询数据。二者服务于不同的数据职责。外部 PostgreSQL/MySQL 是被读取的数据源，通过受控只读连接接入，不作为平台数据库，也不放入本仓库。Keycloak 使用独立的 PostgreSQL 数据库和角色。

从仓库根目录执行以下已有入口。Python 使用 3.12.7，Node 使用 24.22.0；依赖和服务版本分别锁定在 `backend/requirements.lock`、两个 npm 锁文件和 `infra/versions.lock.json`。

```sh
python -m venv .venv
# Windows 下将 .venv/bin/python 替换为 .venv/Scripts/python.exe
.venv/bin/python -m pip install -r backend/requirements.lock
npm ci
npm ci --prefix frontend
.venv/bin/python scripts/export_openapi.py --check
npm run api:check
npm --prefix frontend run typecheck
npm run web:test
npm run web:build
```

修改后端契约后运行 `python scripts/export_openapi.py`、`npm run api:generate`，提交生成文件。迁移配置在 `database/alembic.ini`；`python -m alembic -c database/alembic.ini heads` 可离线检查迁移头。

具备 Docker Compose 环境时，`python scripts/dev.py prepare` 生成本地环境文件和证书，`python scripts/dev.py up` 启动服务，`python scripts/dev.py test` 使用独立测试项目。默认仅代理公开本机 HTTPS 端口；持久卷与外部数据库均不是源码。迁移需要单独的 schema owner 凭据，详见[数据库说明](database/README.md)及[环境运行手册](docs/operations/environment.md)。这些命令是操作入口，本快照未执行服务启动或数据库迁移。

环境文件、密钥、运行数据库、日志和依赖不应提交。仓库保留字体和地图样本的许可证；`tests/fixtures/maps` 中的 MBTiles/PMTiles 是有意分发的合成测试样本。历史文档中的 `apps/api`、`apps/web` 路径分别对应现在的 `backend`、`frontend`；完整路径映射见快照状态文档。
