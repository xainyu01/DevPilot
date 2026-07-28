# 本地运行设置

DevPilot 将机器相关设置保存到工作区 `.devpilot/settings.json`。该目录已被 Git 忽略，适用于
Windows、Linux 和 macOS，不依赖 `.env` 文件。

使用固定 `admin` 账号登录 Web 工作台后，可以在右侧的 **Local settings** 区域修改：

- 空闲自动关闭时间（默认 5 分钟）；只有没有用户操作且没有正在输出的模型运行时，服务才会退出。
- 默认模型提供商和模型名：`fake`、`openai`、`anthropic` 或 `ollama`。OpenAI 与 Anthropic
  的 API 密钥仍仅从各自的环境变量读取；Ollama 目前会返回明确的未实现状态。
- 本地用户。新增用户的密码会立即转换为 PBKDF2 哈希后写入配置，并通过正常的 users、JWT 与
  RBAC 链路认证。

配置文件只保存密码哈希，不能直接填写明文密码。建议始终通过 GUI 添加用户；需要手工恢复配置时，
可使用下列结构并提供已有的 PBKDF2 哈希：

```json
{
  "idle_shutdown_minutes": 5,
  "model": { "provider": "fake", "name": "fake-model" },
  "users": [
    {
      "id": "alice",
      "display_name": "Alice",
      "password_hash": "pbkdf2_sha256$..."
    }
  ]
}
```

`admin`、`admin1`、`admin2`、`admin3` 为发布前必须保留的固定用户，不能在本地配置中覆盖。
