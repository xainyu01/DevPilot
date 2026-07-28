# DevPilot 命名迁移

项目的所有公开标识已统一为根目录和远程仓库名称 `DevPilot`。

- 展示名：`DevPilot`
- CLI、Python 项目和服务标识：`devpilot`
- 环境变量前缀：`DEVPILOT_`
- Web 本地存储键：`devpilot_access_token`
- 运行数据目录：`.devpilot/`
- 容器中的 PostgreSQL 用户、数据库、卷和密钥名称：`devpilot`

这是一次破坏性重命名。旧的 `devpilot` CLI、环境变量、HTTP Host 请求头和运行数据目录不再由应用读取。为避免将既有本地运行数据误提交，旧 `.devpilot/` 目录仍被 Git 忽略；如需保留其中的数据，请在使用新版本前自行备份或迁移。
