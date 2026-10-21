# 数据库与语义存储

这里保存可复现的数据库定义和服务配置，不保存运行数据或密码。

| 路径 | 内容 |
| --- | --- |
| migrations/ | 实际 Alembic `env.py`、模板与版本迁移脚本 |
| alembic.ini | 迁移路径配置，自动将 `../backend/src` 加入 Python 模块路径 |
| postgres/init-databases.sh | Compose 首次初始化时创建平台及 Keycloak 数据库与角色 |
| fuseki/ | Jena Fuseki/TDB2 Dockerfile 和 RDF 数据集配置 |

PostgreSQL/PostGIS 保存平台事务状态、权限、任务和空间字段；Fuseki/TDB2 保存 RDF 本体与语义投影。外部 PostgreSQL/MySQL 仅是平台的只读摄取来源，不由这里的迁移管理。初始化脚本只对新 PostgreSQL 数据目录生效，不是已有部署的升级入口。

从仓库根目录离线查看迁移链：

```sh
python -m alembic -c database/alembic.ini heads
python -m alembic -c database/alembic.ini history
```

当前唯一头为 `0301_map_resources`，其前驱是 `0204_ingestion_runs_rows`。此前已接受的迁移脚本保持原内容。真正升级须在专用部署环境中提供 `LZO_MIGRATION_DATABASE_URL`（schema owner 角色）及后端配置，以 `backend/src` 为 `PYTHONPATH` 执行 `python -m lzo.migrate`；Compose 对应入口为 `migrate` profile 中的 `migrate` 服务。普通 API/worker 使用应用角色。迁移 `env.py` 只接受入口显式传递的 owner URL，不从 Alembic 配置文件读取密码。

Compose 编排位于 `infra/compose/compose.yaml`，挂载这里的 PostgreSQL 初始化脚本并构建这里的 Fuseki 镜像。持久卷、TDB2 文件、数据库备份和本机运行目录均排除在源码外。本次快照仅检查迁移图，没有执行升级、降级或数据库服务测试。
