# 数据库与语义存储

这里保存可复现的数据库定义和服务配置，不保存运行数据或密码。

| 路径 | 内容 |
| --- | --- |
| migrations/ | 实际 Alembic `env.py`、模板与版本迁移脚本 |
| alembic.ini | 迁移路径配置，自动将 `../backend/src` 加入 Python 模块路径 |
| postgres/init-databases.sh | Compose 首次初始化时创建平台及 Keycloak 数据库与角色 |
| fuseki/ | Jena Fuseki/TDB2 Dockerfile 和 RDF 数据集配置 |

治理库 `lzo` 保存身份、权限、不可变修订、审批、任务和 outbox；独立 `lzo_gis` 保存 PostGIS 空间索引，`lzo_timeseries` 保存按 UTC 月分区的观测和媒体索引，`lzo_catalog` 保存资料版本索引。Fuseki/TDB2 保存 RDF/OWL/SHACL 和受控语义投影；私有 S3 保存原件字节。外部 PostgreSQL/MySQL 仅是只读摄取来源，不由平台迁移管理。初始化脚本只对新 PostgreSQL 数据目录生效。

从仓库根目录离线查看迁移链：

```sh
python -m alembic -c database/alembic.ini heads
python -m alembic -c database/alembic.ini history
```

当前核心头为 `0406_storage_backups`；GIS 领域版本 4，时序与资料领域版本 3。历史迁移保留，新增变更追加。升级须提供四个独立 owner URL 和显式 `LZO_STORAGE_MODE=split`，以 `backend/src` 为 `PYTHONPATH` 执行 `python -m lzo.migrate`。普通 API/worker 只使用应用账号。迁移入口不从 Alembic 配置读取密码。

实际结构见[数据库关系与数据流](../docs/database/schema-guide.md)及[机器可读原生结构清单](../docs/database/schema-inventory.json)。升级、审批切换、备份、全新目标恢复及回滚见[迁移恢复手册](../docs/database/migration-recovery.md)；本机与容器边界见[可复现验收](../docs/database/acceptance.md)。运行数据、TDB2、原件、备份和凭据均排除在源码外。
