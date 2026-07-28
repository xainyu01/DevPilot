# Git 与 GitHub 工作流

## 分支职责

- `master`：公开代码分支，跟踪 GitHub `origin/main`；不包含 `docs/learn/`。
- `local-with-learn`：本地资料分支，保留 `docs/learn/` 的历史，不推送到 GitHub。
- `docs/learn/` 已加入根目录 `.gitignore`，在 `master` 上不会被误提交。

## 日常开发

代码开发使用 `master`：

```powershell
git switch master
git add <代码或 docs 文件>
git commit -m "feat: describe the change"
git push origin master:main
```

本地学习资料需要版本跟踪时，切换到本地分支提交：

```powershell
git switch local-with-learn
git add docs/learn/
git commit -m "docs(local): update learning notes"
```

`local-with-learn` 不得执行 `git push`。完成学习资料整理后切回 `master` 继续公开代码开发。

## 当前状态

首次公开推送已使用过滤后的 `master` 历史完成；远程 `main` 不包含 `docs/learn/`，本地 `local-with-learn` 保留完整历史。
