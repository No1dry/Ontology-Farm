# 本体农场 · 当前源码快照

本仓库保存农业 Operational Ontology Platform 的当前源码，包含语义后端、React Studio 原型、数据库迁移和部署工具。已按确认方案实施语义主库与专业存储拆分；前端视觉重做仍暂停。工程证据与现场验收分别记录。

历史快照前里程碑为 **15/29**，见[历史快照状态](docs/status/2026-10-21-source-snapshot.md)。新增多存储的实际结构、原生验证、恢复证据、性能边界及未执行的容器/现场维度见[当前数据库验收](docs/database/acceptance.md)；这些工程结果不构成整个原 P0+P1 产品或生产迁移完成声明。

| 目录 | 职责 |
| --- | --- |
| [backend/](backend/README.md) | Python API、worker、权限治理、语义逻辑、摄取、地图服务、模型及测试 |
| [frontend/](frontend/README.md) | React 工作台与 Studio 原型、静态资源、单元测试及独立 npm 锁文件 |
| [database/](database/README.md) | Alembic 迁移、PostgreSQL 初始化和 Fuseki/TDB2 配置 |
| infra/ | Compose 编排、HTTPS 代理、Keycloak 身份配置及测试镜像 |
| contracts/、packages/ | 共享 API 快照与契约；前端类型由后端 OpenAPI 生成 |
| scripts/、tests/ | 启动与维护入口、共享浏览器测试和可分发测试样本 |
| docs/ | 当前操作文档、状态说明，以及保留原路径的历史设计和验证记录 |

平台使用四个独立 PostgreSQL 数据库和应用角色：治理、PostGIS、月分区时序、资料。Jena Fuseki/TDB2 保存受控 RDF/OWL/SHACL；S3 保存私有原件。UUID、稳定 IRI、不可变事件和后端当前授权连接各域。外部 PostgreSQL/MySQL 是只读来源。Keycloak 的生产编排使用独立身份数据库；原生验收身份服务的隔离方式另有明确记录。

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

具备 Docker Compose 环境时，`python scripts/dev.py prepare` 生成本地环境文件和证书，`python scripts/dev.py up` 启动服务，`python scripts/dev.py test` 使用独立测试项目。默认仅代理公开本机 HTTPS 端口；持久卷与外部数据库均不是源码。迁移需要独立 owner 凭据，详见[数据库说明](database/README.md)及[环境运行手册](docs/operations/environment.md)。当前主机没有 Docker 引擎；配置验证与原生服务测试不能替代容器运行及实际断网部署验收。

环境文件、密钥、运行数据库、日志和依赖不应提交。仓库保留字体和地图样本的许可证；`tests/fixtures/maps` 中的 MBTiles/PMTiles 是有意分发的合成测试样本。历史文档中的 `apps/api`、`apps/web` 路径分别对应现在的 `backend`、`frontend`；完整路径映射见快照状态文档。
