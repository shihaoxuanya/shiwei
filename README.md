# 拾微 Shiwei

> 你不用整理，它替你记住。

拾微是一款个人 AI 记忆工具。它把用户导入的文件复制到本机资料库，自动解析、建立全文索引，可选开启语义检索。对话优先使用个人资料并给出可核对的出处，也支持普通聊天和通用知识问题。

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

- 不运行拾微后台，不采集或上传使用统计、错误日志；不自动检查或安装更新。
- 原始文件、笔记、对话和索引保存在本机。设置支持校验后迁移整个资料库，失败保留原库。
- 用户自行选择模型。在线对话发送当前问题、必要历史及选中证据；在线语义索引会处理全部派生文本。选择在线服务不等于完全离线，其保留规则由所选服务决定。
- API Key 使用系统凭据存储，不进入普通数据库或日志；PDF/OCR 在本地完成。

## 开源许可

项目代码采用 [MIT](LICENSE)。第三方依赖、OCR 模型和运行时分别遵循自身许可。
源码与安装包分别位于公开源码仓库和公开安装包仓库。仅主动点击“前往 GitHub 下载”才打开浏览器，不连接原管理后台。

## 桌面使用入口

首页用于添加资料和记一下；已有内容时显示真实处理状态及最多五条最近内容，不重复放聊天输入框。对话用于找回资料和笔记，资料页复用本地索引按名称或正文关键词搜索。设置分为 AI 服务、数据与隐私、关于：模型保存和连接测试分开，在线智能检索的批量重新处理需要另行确认，本地服务版本与协议放在诊断信息中。

## 当前进度与限制

0.1.2 修复中文整句/两字词检索和“任何问题都提示未找到记录”：无需重新导入或开启语义检索；文件定位返回独立卡片，资料追问保留真实来源，模型连接错误与未找到资料分开显示。已完成 107 项回归测试、打包 Worker 与隔离桌面启动检查，详见 [0.1.2 验收记录](docs/ACCEPTANCE-0.1.2.md)。

当前已完成 Tauri/React/Python Worker 的真实竖切：文件/文件夹导入、轻量笔记、SHA256 去重、Canonical Document、SQLite/FTS5、LanceDB 与 embedding 版本、RRF 混合检索、多厂商模型选择、OpenAI-compatible 与 Claude Messages 协议、独立对话/向量服务、Windows 安全凭据、对话持久化、单条对话删除、可验证的文件/笔记引用、来源详情、打开/定位原文件、重建索引和确认删除。无模型时仍可完成记录、导入与本地全文找回。厂商范围与验证边界见 [模型接入说明](docs/MODEL-PROVIDERS.md)。

当前发布目标是 Windows 11 x64。PDF 使用本地 PDFium 提取文本，无文字页使用随包 RapidOCR 模型识别，不依赖运行时下载布局模型；Office 使用 Docling。PDF 页码保留，但复杂多栏、表格和低清扫描的阅读顺序与识别精度仍需继续调优。单文件限制 1 GiB、PDF 限制 3000 页；失败原因与重试入口直接显示。安装包不包含云同步、账号、移动端或拾微云服务。

仓库根部的 `electron/`、旧 `src/`、`gateway/` 与 Sites 文件是早期演示原型，不属于当前生产架构。

## 发布

版本真值为 `VERSION`。通过 `pnpm run version <版本号>` 同步后提交并打 `v<版本号>` 标签，CI 测试、构建并签名，将安装包发布到 GitHub。
保留离线安装包签名，不再向后台登记或推送更新。详见 [发布手册](docs/RELEASING.md)。历史验收文档描述旧版本，不代表当前后台或统计功能仍然存在。
## 贡献者
感谢 [rosyrongrong](https://github.com/rosyrongrong) 参与项目共创。
