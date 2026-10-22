# 后端

`src/lzo` 包含 API、独立 worker 和核心语义逻辑；权限、治理、摄取、地图及 PostgreSQL 模型都在此目录。`tests` 保留已有单元和服务集成测试，`requirements.lock` 锁定 Python 依赖。数据库迁移实际位于仓库的 `database/migrations`。

从仓库根目录安装并执行：

```sh
python -m pip install -r backend/requirements.lock
python scripts/export_openapi.py --check
python -m pytest -c backend/pyproject.toml backend/tests/unit
```

部分单元契约测试需要 `LZO_DATABASE_URL`、`LZO_FUSEKI_URL`、`LZO_OIDC_ISSUER`、`LZO_OIDC_DISCOVERY_URL` 和 `LZO_FILE_ROOT` 来构造应用；这些测试不连接服务，可使用虚构的 localhost 地址和临时文件目录。服务集成测试需要专用测试数据库及身份环境，不能对共享或生产数据库直接执行。

在已配置服务和环境变量的情况下，以 `backend/src` 为 `PYTHONPATH`，API 入口是 `python -m uvicorn lzo.main:create_app --factory`，worker 入口是 `python -m lzo.worker`。从仓库根目录构建镜像使用 `docker build -f backend/Dockerfile .`；镜像同时包含独立数据库迁移目录，工作目录为 `/app/backend`。Compose 和本机启动工具统一保留在 `scripts`。

后端生成共享的 `contracts/openapi.json`，前端据此生成类型；前端并不承载核心语义规则。本目录属于当前开发快照，完整产品与服务验收状态见[快照说明](../docs/status/2026-10-21-source-snapshot.md)。

多存储运行时位于 `storage/`、`synchronization/`、`gis/`、`catalog/`、`telemetry/`。治理事务接受不可变事件，专业库同事务提交版本与 inbox，核心回执确认后才允许对应 RDF 发布；失败重试保留原事件。所有查询重新检查核心当前权限。附件进入语义对象时使用完整对象修订；历史遗留附件尾可通过显式、带 If-Match 的修复命令追加恢复。资料终态失败通过精确版本 retry 命令恢复，操作审计不推进资料元数据头。接口和运行步骤见[验收与运维](../docs/database/acceptance.md)。
