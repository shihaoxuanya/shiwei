# 拾微 0.2.0 笔记与对话删除验收

日期：2026-09-05。生产架构为 Tauri 2 + React/TypeScript + Python Worker。本轮只调整首页职责、单条历史对话删除和极简笔记，文件导入、资料管理、问答、历史、模型配置与既有检索均保留。

## 实现结果

- 首页移除重复的“问问拾微”输入模块，保留外部信息导入，并增加“自己的信息 / 记一下”。点击后进入笔记页并立即创建空白笔记。
- 新增“笔记”导航与克制的左右编辑界面：新建、可选标题、纯文本正文、750ms 防抖自动保存、切换前落盘、更新时间倒序、日期分组、本地标题/正文搜索和确认删除。
- 笔记正文是 Source of Truth。migration 5 增加 `notes` 与 `sources.source_type`；笔记通过 Canonical Document 进入现有 chunk、两套 FTS5、可选 embedding、LanceDB 和混合检索，不建立第二套检索系统。
- 笔记更新只替换受影响文档的派生索引；正文和 SQLite 检索投影在同一事务中更新。删除会清除 document、chunks、FTS、vector 与引用关系，不留下幽灵结果。
- AI 引用笔记时使用 `[N1]`，出处显示笔记标题、创建/修改时间和相关原文，并提供“打开这条笔记”；不展示文件路径或资源管理器操作。
- 历史会话悬停/聚焦显示操作菜单。确认文案为“删除这段对话？”及“删除后无法恢复，但不会删除你导入的资料或笔记。”；当前会话删除后进入空白会话并显示“对话已删除”。
- Worker 事务只删除 conversation、messages、citations 和 message_sources；资料、笔记及知识索引保持不变。
- 补强个人事实边界：类似“黄总之前确认……”的称谓与过去决定问题即使不含“我/我的”，没有本地证据时也不会进入通用模型猜测。

## 自动化结果

| 层 | 通过数 | 范围 |
| --- | ---: | --- |
| Python | 90 | 数据迁移、笔记 CRUD/FTS/vector/重建/删除/引用、对话级联删除与既有导入问答回归 |
| React | 41 | 首页职责、笔记自动保存/搜索/删除、对话确认删除、笔记引用/来源抽屉及既有 UX 回归 |
| Rust | 9 | Worker 协议、错误边界、安全配置与真实 Python Worker 启动 |
| 合计 | 140 | 全部通过 |

TypeScript 类型检查与 Vite 生产构建通过。Python 和 Rust 测试分别在真实 SQLite/FTS/LanceDB 与实际 Worker 进程上执行。

## 隔离交互验收

`qa/ux.html` 使用完整应用界面、真实 WorkerServer、临时 SQLite 和合成资料，只将生成模型替换为确定性测试网关。没有读取用户资料、API Key 或既有数据库。

已实际操作验证：

1. 首页不再出现第二个对话框；“记一下”进入笔记页并自动创建空白记录。
2. 创建“TiDB会议记录”，写入“四套环境”，等待后显示“已保存”；对话回答“四套”并显示 `[N1] TiDB会议记录 · 当前日期`。
3. 点击引用后仅显示笔记标题、创建/修改时间、最新原文和“打开这条笔记”。
4. 将正文改为“五套环境”后重新提问，回答更新为“五套”；旧“四套”内容不再出现在检索上下文。
5. 删除笔记后再次提问，既无 `[N1]`，也不会由通用模型猜测四套或五套。
6. 删除当前对话时显示指定确认文案；成功后进入空白会话并显示 Toast。资料与笔记计数不变。

截图：`output/qa-0.2.0/home.png`、`output/qa-0.2.0/notes-final.png`、`output/qa-0.2.0/note-citation.png`。其中内容均为合成数据。

## 打包 Worker 联调

- PyInstaller Worker 0.2.0 在全新临时资料库完成：中文文件名召回、文件路径卡、连续追问、笔记创建与统一检索、`[N1]`、编辑后索引替换、删除后清理和对话删除。
- 独立 EXE 离线导入程序生成的文字 PDF 与扫描 PDF：本地 OCR、PDF 第 2 页引用、SHA256 重复导入均通过。渲染证据在 `output/qa-0.2.0/pdf/`。
- 用户真实资料库始终未被测试脚本打开；测试未连接真实模型。

## 桌面与安装包

- release 桌面程序在独立 `SHIWEI_DATA_DIR` 实际启动：文件版本和产品版本均为 0.2.0，窗口标题“拾微”，进程 Responding=true，WebView2 与打包 Worker 子进程正常，stdout/stderr 为 0 字节。验证后只停止本次隔离启动的进程。
- Windows x64 NSIS：`apps/desktop/src-tauri/target/release/bundle/nsis/拾微_0.2.0_x64-setup.exe`，407,700,581 字节，文件/产品版本 0.2.0，未签名。
- 安装包 SHA-256：`41B440A482AEE9D8BA1EC5AE2AECDD2427A6D94F68258A179F420F60A21BFC95`。
- Worker 在 PyInstaller 输出、Tauri staging 与最终 release 三处 SHA-256 一致：`F9C1725670AE28AA8E56728E7A35291A2F20143C21E0F4A52AE24884C031EA85`。最终 release Worker 再次通过笔记/对话与 PDF/OCR 两组 smoke test。

旧版安装包与现有用户数据均保留。migration 5 将旧来源默认视为 `imported_file`，不重写原始文件。

## 边界

本版不包含文件夹、标签、双向链接、Markdown 高级编辑、Block Editor、模板、知识图谱、Canvas、插件或协作。笔记是本机纯文本记忆入口。
