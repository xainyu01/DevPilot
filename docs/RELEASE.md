# B8 发布候选部署、升级与回滚指南

## 发布边界

该版本是 `0.1.0rc1` 发布候选。B7 的四个固定用户继续作为数据库中的正常用户运行，认证令牌已经改为有过期时间的 HS256 JWT，但固定账号和密码将在最终公开上线前以独立、安全审计变更替换。不要将本发布候选直接暴露到公网。

容器默认只绑定 `127.0.0.1:8000`。若需要远程访问，应在受管 HTTPS 反向代理之后部署，并限制来源网络；TLS 证书、反向代理、操作系统补丁和秘密轮换属于部署方责任。

## 首次容器部署

1. 创建仅本机可读、未纳入 Git 的 `deploy/secrets/` 目录。
2. 写入至少 32 字节的 `devpilot_auth_secret` 和强随机 `postgres_password`。两个文件都不应包含示例值、账号密码或换行外的额外内容。
3. 运行 `docker compose config` 检查 Compose 展开结果，再运行 `docker compose up --build -d`。
4. 在宿主机执行 `curl http://127.0.0.1:8000/healthz` 和 `curl http://127.0.0.1:8000/readyz`；两者分别应返回 `ok` 和 `ready`。

容器入口在启动 Uvicorn 前执行 `alembic upgrade head`。迁移失败会让容器退出而非以部分升级状态提供流量。应用通过 Docker secrets 读取 JWT 签名密钥，并通过受保护的 `PGPASSFILE` 向 PostgreSQL 认证；密钥不会写入镜像、数据库 URL 或进程参数。

## 认证与客户端

Web 登录与 CLI 登录使用同一 API。CLI 会打印短期 JWT，操作员在当前 PowerShell 会话中设置它：

```powershell
$login = uv --cache-dir .uv-cache run devpilot auth login <user-id> | ConvertFrom-Json
$env:DEVPILOT_ACCESS_TOKEN = $login.access_token
uv --cache-dir .uv-cache run devpilot project list
```

不要把 JWT 写入仓库、脚本、Shell 历史、文档或截图。JWT 到期后重新登录；更换 `DEVPILOT_AUTH_SECRET` 会使此前签发的 JWT 立即失效。

## 备份、升级与回滚

SQLite 本地部署在升级前创建一致性快照，目标文件必须是新路径：

```powershell
uv --cache-dir .uv-cache run devpilot database backup .\backups\devpilot-before-b8.db
```

PostgreSQL 部署使用数据库管理员工具创建一致性备份，例如由受管服务快照或 `pg_dump`；应用不会代替数据库服务保存密码、备份或执行破坏性恢复。

升级步骤：

1. 记录当前镜像摘要并验证当前 `/readyz`。
2. 创建数据库备份或受管快照。
3. 拉取/构建候选镜像，运行 `docker compose up -d --build`，等待迁移完成。
4. 验证 `/healthz`、`/readyz`、固定用户登录、一次会话读取和受限用户的拒绝路径。

若迁移或验收失败：停止新应用容器，恢复先前镜像摘要和数据库备份/快照，再执行对应版本的迁移回退。仅在已验证备份可恢复且维护窗口批准后运行 `alembic downgrade`；不要用删除卷、重建数据库或覆盖运行中数据替代回滚。

## 运行时恢复

- 会话、消息、工作流、检查点、成员关系和远程 Host 配对摘要均在数据库中保存。
- 未过期用户 JWT 与 Host JWT 使用稳定签名密钥，可在应用进程重启后继续验证。
- 一次性配对码在成功使用后立即删除，或在十分钟后视为无效；需要时由团队管理员重新创建 Host 声明。
- `/healthz` 只表示进程存活；负载均衡和发布脚本应以 `/readyz` 作为接流量条件。
