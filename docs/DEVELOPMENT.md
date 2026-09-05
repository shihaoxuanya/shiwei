# 开发与发布

## 常用命令

```powershell
pnpm install
uv sync --project services/ai-worker --python 3.12
pnpm typecheck
pnpm test
pnpm dev
```

## Worker sidecar

开发模式优先使用 `services/ai-worker/.venv`。发布前使用 PyInstaller 构建单文件 Worker：

```powershell
cd services/ai-worker
uv run pyinstaller --noconfirm --clean shiwei-ai.spec
```

将输出目录中的可执行文件复制为：

```text
apps/desktop/src-tauri/binaries/shiwei-ai-worker-x86_64-pc-windows-msvc.exe
```

同时将 PyInstaller 的 `_internal` 目录复制到 `apps/desktop/src-tauri/binaries/_internal`。Tauri 的 `externalBin` 与 `resources` 将两者放入 NSIS 安装包。目录式 sidecar 避免单文件模式每次启动都解压大型 AI 依赖。安装后的应用优先启动同目录 sidecar；开发仓库则继续使用虚拟环境，通信协议完全一致。

## 发布前验收

1. 运行 Python、Rust、前端测试与 TypeScript 检查。
2. 直接运行打包后的 sidecar，完成 `ping` JSON Lines 往返。
3. 构建 NSIS 安装包。
4. 在干净 Windows 11 x64 环境安装，确认没有 Python、Node、Rust 时仍能启动。
5. 导入 `fixtures/demo-knowledge`，精确检索 `ORA-01034`。
6. 配置 OpenAI-compatible Provider，建立语义索引，询问 PDB 恢复问题并核对引用。

测试服务器只用于构建/发布辅助；拾微运行时不连接该服务器，也不在其中存放用户资料。
