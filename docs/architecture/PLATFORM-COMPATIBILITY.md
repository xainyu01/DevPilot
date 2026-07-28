# 平台兼容边界

## 当前决策

B6 当前只把 Windows 本地 Web 作为功能验收平台。Linux 和 macOS 暂不要求完整运行，但不能把未来兼容性建立在重写领域核心的前提上。核心包继续只依赖 Python 标准库、契约和抽象端口；平台差异放在适配器层。

```text
                 平台无关领域核心
       Agent / Workflow / Policy / Memory / Contracts
                         |
                PlatformAdapter 端口
       +-----------------+------------------+
       |                 |                  |
   WindowsAdapter    LinuxAdapter TODO   MacOSAdapter TODO
   path/process      POSIX path/process   POSIX path/process
   PowerShell        shell capability     shell capability
   browser           browser              browser
```

## 已保留的接口

`packages/platform/ports.py` 定义以下端口：

- `PathResolver`：路径规范化、工作区基准和平台路径风格。
- `ProcessRunner`：受超时、工作目录和输出限制的进程执行。
- `ShellRunner`：Shell 能力和命令适配；领域层不直接拼接 PowerShell/bash。
- `BrowserLauncher`：本地 Web 启动后的浏览器打开能力。
- `PlatformAdapter`：端口集合与 `PlatformCapabilities` 能力声明。

Linux/macOS 的具体实现先保留为 TODO，不在当前阶段伪装成可用能力。未实现能力必须通过结构化状态返回，例如 `status=declared_not_implemented`，不能静默回退到 Windows 命令。

## 不应修改核心代码的边界

未来增加 Linux/macOS 时，正常流程应是：

1. 新增平台适配器，实现现有端口。
2. 为适配器补路径、进程、Shell、浏览器和能力声明契约测试。
3. 在 CLI/Host 启动时选择适配器，并把能力交给策略层。
4. 补目标平台的启动、项目登记和首次对话冒烟。

只有当某项能力的语义本身发生变化时，才修改领域契约；不应把 `if Windows`、`if Linux` 分散到 Agent、工作流、记忆和策略核心中。

## 平台验收分层

| 层级 | 当前 Windows | Linux/macOS 后续 |
|---|---|---|
| 领域单元测试 | 必须通过 | 复用同一套测试 |
| 端口契约测试 | 必须通过 | 新适配器必须通过 |
| API/Web 契约 | 必须通过 | 不应因平台改变 |
| 本地启动与首次对话 | B6 已验证 | 后续批次验证 |
| 原生安装包/桌面壳 | 不实现 | 独立批次评估 |

## TODO 清单

- [ ] 实现 Linux POSIX 适配器并在 Ubuntu runner 冒烟。
- [ ] 实现 macOS POSIX 适配器并在 CI 冒烟。
- [ ] 为每个平台补 Shell 能力探测、命令白名单和路径边界测试。
- [ ] 将 CI 矩阵从构建兼容扩展为真实启动与首次对话。
