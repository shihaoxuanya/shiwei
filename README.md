# 拾微 Shiwei

> 你不用整理，它替你记住。

拾微是一款 Windows 优先、Local First 的个人 AI 记忆工具。它把用户导入的文件复制到本机资料库，自动解析、建立全文索引，可选开启语义检索。对话优先使用个人资料并给出可核对的出处，也支持普通聊天和通用知识问题。

0.2.0 新增“记一下”：首页专注添加信息，笔记可创建、编辑、自动保存、搜索和删除，并和文件一起进入全文及可选语义检索；笔记引用使用独立的 `[N1]` 标识。对话历史支持单条确认删除，且不会删除资料或笔记。见 [0.2.0 验收记录](docs/ACCEPTANCE-0.2.0.md)。

## MVP 目标

验证普通用户是否能一次导入大量历史资料，并在以后通过自然语言准确找回内容、获得基于资料的回答、核对引用并打开原文件。

## 架构

- `apps/desktop`：Tauri 2、Rust、React、TypeScript、Vite、Tailwind、Zustand。
- `services/ai-worker`：Python 3.11+、Pydantic、SQLite/FTS5、PDFium/RapidOCR、Docling、LanceDB 与 ModelGateway。
- `packages/shared-types`：跨前端边界的稳定类型。
- `fixtures/demo-knowledge`：最终验收使用的中文资料。
- `docs`：产品、架构、数据模型和 IPC 契约。

React 不直接访问文件、数据库或密钥；Rust 管理桌面能力和 Worker 生命周期；Python Worker 是知识数据库和索引的唯一写入者。Rust 与 Python 通过 stdin/stdout JSON Lines 通信，不开放本地 HTTP 端口。

## 开发环境

- Windows 11 x64
- Node.js 22+ 与 pnpm 11+
- Rust stable MSVC toolchain
- Python 3.11–3.13，由 uv 管理
- WebView2 Runtime

```powershell
pnpm install
uv sync --project services/ai-worker --python 3.12
pnpm dev
```

Web UI 独立预览：

```powershell
pnpm dev:web
```

## 测试

```powershell
pnpm typecheck
pnpm test:frontend
pnpm test:python
pnpm test:rust
```

## Build

```powershell
pnpm build:release
```

`build:release` 会先用 PyInstaller 生成 Python Worker onedir 运行时并放入 Tauri sidecar，再生成 NSIS 安装包。若 Worker 已经打包且只需迭代桌面壳，可单独运行 `pnpm build:desktop`。正式安装包会包含全部运行时，最终用户不需要安装 Python、Rust、Node、SQLite、LanceDB 或 Docker。

## 数据位置

Worker 默认资料库在 Windows 用户本地应用目录的 `Shiwei/data`，可用 `SHIWEI_DATA_DIR` 指定测试目录。“设置 → 数据与隐私”显示当前 Worker 实际使用的位置。内部结构为 `raw`、`parsed`、`index`、`cache`、`logs` 与 `shiwei.db`。原始文件使用 SHA256 内容寻址保存；索引可从原始文件和 Canonical Document 重建。模型公开配置单独存放在 Tauri 分配给 `com.shiwei.desktop` 的配置目录，密钥由系统凭据存储保存（Windows 凭据管理器；macOS 内测构建为钥匙串），不回显原值。

## 隐私原则

- 无账号、无云知识库、无云同步；按最新产品决定，生产版基础使用统计与固定类型错误报告默认开启，可在“设置 → 数据与隐私”随时关闭，不使用自动采集 SDK 或会话录像。新安装与旧 true/undecided 状态默认开启，保留明确关闭与历史 false；损坏或不可读设置保持关闭。关闭后取消待发/未完成事件，不补传关闭期间的行为；不会立即删除服务器已接收的历史统计。
- 原始文件、文件名、索引、查询和对话不会发送给拾微服务器。
- 调用对话模型时发送当前问题、最近最多六条对话消息及选中的有限资料片段，不发送整个资料库。复杂未命中问题最多额外调用一次意图识别，只发送当前问题。问候、文件定位和概览可本地完成。
- 用户开启在线智能检索后，建立索引会依次发送待索引资料的全部派生标题/正文文本片段，检索时还会发送查询文本；并非只在提问时发送少量内容。模型请求发往用户所选服务，地址指向本机时由该本地服务处理；不能把“资料不上传到拾微”理解为“资料内容从不离开电脑”。PDF 解析和 OCR 在本地运行，不上传原始文件。
- API Key 不写入普通 SQLite 设置表，也不打印到日志。
- 统计使用随机安装标识，属于去标识化信息而非完全无隐私影响；第一方事件最多保留 90 天，详见 [统计与隐私说明](docs/PRIVACY_TELEMETRY.md)。开发构建、浏览器预览不发送生产统计，关闭统计不影响原有更新检查。

## 桌面使用入口

首页用于添加资料和记一下；已有内容时显示真实处理状态及最多五条最近内容，不重复放聊天输入框。对话用于找回资料和笔记，资料页复用本地索引按名称或正文关键词搜索。设置分为 AI 服务、数据与隐私、关于与更新：模型保存和连接测试分开，在线智能检索的批量重新处理需要另行确认，本地服务版本与协议放在诊断信息中。

## 当前进度与限制

0.1.2 修复中文整句/两字词检索和“任何问题都提示未找到记录”：无需重新导入或开启语义检索；文件定位返回独立卡片，资料追问保留真实来源，模型连接错误与未找到资料分开显示。已完成 107 项回归测试、打包 Worker 与隔离桌面启动检查，详见 [0.1.2 验收记录](docs/ACCEPTANCE-0.1.2.md)。

当前已完成 Tauri/React/Python Worker 的真实竖切：文件/文件夹导入、轻量笔记、SHA256 去重、Canonical Document、SQLite/FTS5、LanceDB 与 embedding 版本、RRF 混合检索、多厂商模型选择、OpenAI-compatible 与 Claude Messages 协议、独立对话/向量服务、Windows 安全凭据、对话持久化、单条对话删除、可验证的文件/笔记引用、来源详情、打开/定位原文件、重建索引和确认删除。无模型时仍可完成记录、导入与本地全文找回。厂商范围与验证边界见 [模型接入说明](docs/MODEL-PROVIDERS.md)。

当前发布目标是 Windows 11 x64。PDF 使用本地 PDFium 提取文本，无文字页使用随包 RapidOCR 模型识别，不依赖运行时下载布局模型；Office 使用 Docling。PDF 页码保留，但复杂多栏、表格和低清扫描的阅读顺序与识别精度仍需继续调优。单文件限制 100MB、PDF 限制 300 页；失败原因与重试入口直接显示。安装包不包含云同步、账号、移动端或拾微云服务。

仓库根部的 `electron/`、旧 `src/`、`gateway/` 与 Sites 文件是早期演示原型，不属于当前生产架构。

## v0.3 发布基础设施

版本真值为 `VERSION`，用 `npm run version 0.3.0` 同步；`pnpm version:check` 校验。签名发布构建 `pnpm build:release` 需要正式 Updater 配置与环境 Secrets；没有凭据时不会假装发布成功。内部未签名测试可运行 `pnpm package:worker` 和 `pnpm build:desktop`。

GitHub 发布、签名、隐私、看板与实际验证边界分别见 [发布手册](docs/RELEASING.md)、[凭据管理](docs/RELEASE_SECRETS.md)、[Windows 签名](docs/WINDOWS_CODE_SIGNING.md)、[匿名统计](docs/PRIVACY_TELEMETRY.md)、[指标看板](docs/ANALYTICS_DASHBOARD.md)、[0.3.0 验收](docs/ACCEPTANCE-0.3.0.md)。未配置远程服务时仍可完整使用本地产品。
