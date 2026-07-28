# 批次 B8：稳定化与发布

## 目标

将前七个批次交付的本地 Agent 服务推进到可验证的发布候选状态：收紧资源级授权，保证认证与远程 Host 配对可在服务重启后恢复，提供数据库迁移和备份路径，并交付可复现的容器部署及升级/回滚文档。

## 本批次范围

- 使用带 `exp`、用途声明和 HS256 签名的 JWT 替代进程内 Bearer token；固定用户的 JWT 在使用相同签名密钥重启后仍可验证。
- 保留 B7 的 `admin`、`admin1`、`admin2`、`admin3` 作为正常持久化用户。JWT 的 `sub` 与 `users.id` 一致，项目、团队和会话权限继续由数据库中的 RBAC 关系裁决。
- 为项目规则、仓库扫描、工作流、PR 文档和记忆接口补齐资源级读写校验，防止“已登录但无成员关系”的读取或写入。
- 将远程 Host 配对码的摘要、过期时间和一次性消费状态持久化；Host token 改为有有效期的签名 JWT。
- 提供 `/healthz`、数据库依赖的 `/readyz`、浏览器安全响应头、登录失败限流、附件大小上限和 SQLite WAL/忙等待配置。
- 提供可恢复的 SQLite 一致性备份命令、Alembic B8 迁移，以及 PostgreSQL Docker Compose 发布候选环境。
- 交付升级、回滚、备份和 TLS 边界说明，并以集成、迁移、安全和构建检查验证。

## 明确不在本批次实现

- 固定 B7 账号或密码的替换、SSO、密码重置、MFA、全局组织管理员模型和 JWT 撤销列表。
- Linux/macOS 的运行时平台验收、远程桌面控制和真实供应商模型调用。
- 自动发布、自动推送、远程 PR 创建或生产环境密钥管理系统。

```python
# TODO（最终公开上线前）：替换 B7 固定账号和密码，接入经安全审计的凭据生命周期、
# 撤销和身份提供方；当前 B8 保留固定用户并返回 signed_jwt / resource_authorized 状态。
```

## 验收标准

1. 固定用户登录后获得三段式 HS256 JWT；改动、过期或用途错误的 token 被拒绝，重启后未过期 token 可继续使用。
2. 所有已认证业务端点都按项目成员、会话分享或记忆所有者执行资源级授权；无授权用户不能读取扫描结果、工作流、PR、项目记忆或他人用户记忆。
3. 配对码只保存摘要、十分钟后失效且成功配对后不能重放；已签发的 Host token 可跨进程重启用于 heartbeat，且不授予工具执行权限。
4. SQLite 数据可用非覆盖式备份命令创建一致性快照；`alembic upgrade head → downgrade 0003_b7_teams → upgrade head` 可完成。
5. Web 静态构建、Docker 镜像定义、健康检查、生产数据库迁移、升级和回滚路径均有文档和测试佐证。

## 验收命令

```powershell
uv --cache-dir .uv-cache sync --group dev
uv --cache-dir .uv-cache run pytest
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run devpilot doctor
pnpm --dir apps/web build
docker compose config
```

最后一条仅验证 Compose 配置；实际容器启动和公网 TLS 终止由部署操作者按发布指南执行。
