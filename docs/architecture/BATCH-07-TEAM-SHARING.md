# 批次 B7：团队共享、RBAC 与远程 Host 声明

## 本次完成范围

- 新增用户、团队、团队成员、项目成员和会话共享的领域契约与关系表。
- 团队角色为 `owner`、`admin`、`member`、`viewer`；团队成员变更和远程 Host 登记要求 owner/admin。
- Demo 登录端点固定使用 `admin / admin`，成功后签发进程内 Bearer token；不读取、不修改
  系统环境变量，且 token 在服务重启后失效。
- Web 工作台已提供相同凭据的登录界面，并将 token 保存在浏览器本地存储；所有 HTTP
  业务 API 都验证 Bearer token。
- 会话 WebSocket 已要求 URL 中的短期 `access_token`；缺失或无效时以关闭码 `4401`
  拒绝连接，Web 工作台会自动携带登录 token。
- 会话拥有者可以授予接收者 `view` 或 `collaborate` 权限，权限记录独立持久化。
- 远程 Host 必须经过“管理员登记 → 一次性配对码 → Host token → 心跳”链路；未配对为
  `pairing_required`，配对后为 `paired`。Host token 不授予工具执行权限。

## 安全边界与剩余工作

认证仅为 Demo 方案：固定凭据和进程内 token 都不能用于生产。后续 B8 必须替换为
可配置凭据、持久化会话和正式身份提供商。

在固定 `admin / admin` 的单管理员 Demo 边界内，B7 已完成。B8 将把固定凭据、进程内
token 与本地 HTTP Host 通道替换为发布级安全配置。
