# 拾微 MVP 架构

## 运行时边界

```text
React/TypeScript UI
        │ Tauri commands/events
        ▼
Rust Desktop Core
        │ JSON Lines request/response/events
        ▼
Python AI Worker ── SQLite/FTS5 + LanceDB + Raw/Parsed Store
        │
        └── ModelGateway → 用户配置的模型服务（兼容接口 / 已有原生协议适配）
```

## 职责

### React

只负责用户界面和瞬时状态。使用 Vite、Tailwind、shadcn 风格源组件与 Zustand；不直接访问文件系统、数据库或模型密钥。

### Rust/Tauri

负责应用生命周期、文件和文件夹选择、拖放、App Data 路径、打开文件、资源管理器定位、安全凭据、Python Worker 启停、IPC 超时与崩溃恢复。Rust 不写知识数据库。

### Python Worker

是 SQLite、FTS5、LanceDB 和派生索引的唯一写入者。负责文件导入、笔记真值与检索投影、哈希去重、Docling 解析、Canonical Document、结构化切分、后台任务、混合检索、上下文构建、ModelGateway、引用映射和对话持久化。

## 数据目录

```text
Shiwei/data/
  raw/       # SHA256 内容寻址原始文件
  parsed/    # 可重建索引所依赖的 Canonical Document
  index/     # LanceDB 等派生索引
  cache/     # 可安全删除的临时缓存
  logs/      # 脱敏日志
  shiwei.db  # SQLite、FTS5 与迁移版本
```

## 关键决策

> All architecture decisions should preserve the long-term separation between user-owned canonical memory, rebuildable indexes, and replaceable AI models.

这是长期架构约束，详见 [PRODUCT_VISION.md](PRODUCT_VISION.md)，不授权新增功能或重构当前稳定模块。当前 MVP 范围仍以 [PRODUCT.md](PRODUCT.md) 为准。现有 `CanonicalDocument` 是可重建的解析投影，不应与未来用户长期资产 `Canonical Memory` 混同。

- 原始文件和 Canonical Document 是重建基础；向量库只是缓存。
- Python 是唯一数据库写入者，避免跨语言 SQLite 写锁与迁移竞争。
- Rust 与 Worker 使用 stdin/stdout JSON Lines，避免桌面端开放 localhost 端口。
- stdout 只传协议消息，Worker 日志写 stderr 或滚动文件。
- 聊天与 Embedding Provider 分离；聊天模型切换不触发重建，智能检索服务/模型变更会提示需要重新处理，只有用户另行确认后才批量重建，并保留已有索引直到安全切换。
- MVP 不依赖用户提供的云服务器；测试服务器仅用于自动化验证和发布辅助，不存储用户资料。

## v0.3 最小发布控制面

独立于 Personal Memory Core：官方 Tauri Updater 经 HTTPS 读取可配置分发源，下载后强制验签；Rust `AnalyticsService` 依据持久化开关及双侧白名单校验，向第一方拾微统计接口发送无内容事件。生产版默认开启，可在设置关闭；默认值不代表主动同意。已有 PostHog 适配仅作为可替换传输保留，不是默认统计目的地。安装实例/隐私状态单独保存在 App Local Data JSON，不引入跨语言知识库写入或云端真值。`FeatureFlagService` 当前仅本地默认值，不依赖网络启动。

按 2026-09-08 最新决定，隐私迁移将新安装、旧 `enabled: true` 和 `consent_choice: undecided` 设为开启；显式 disabled 和无标记的旧 false 保持关闭，损坏／不可读文件不能当作新安装开启。`consent_choice` 保留兼容状态，不作为主动同意的证明；功能性更新分组标识保持独立。关闭通过代次取消未完成发送，不补传关闭期间事件；开发构建和浏览器预览始终禁用生产统计传输。详见 [PRIVACY_TELEMETRY.md](PRIVACY_TELEMETRY.md)。

版本统一、发布与回滚见 [RELEASING.md](RELEASING.md)。无账号、无知识同步，控制面故障不应影响核心导入/记录/召回。

## 本地与外发边界

原始文件、笔记、对话、SQLite 和派生索引默认在本机。PDF 解析和 OCR 也在本地运行。在线聊天仅发送当前问题、必要近期对话及选定证据上下文；在线语义索引依次处理待索引资料的全部标题/正文派生文本片段，查询时另生成查询向量。因此不能把在线 Embedding 描述为“仅提问时上传少量片段”。这些请求通过 ModelGateway 发往用户所选服务；本机地址由相应本地服务处理，不应一律宣称必然外发互联网。

可选统计只发送受限基础元数据，不发送上述内容；统计开关不替代用户对模型配置/重新处理操作的选择。系统凭据由原生层管理，不回显给 React，也不写入知识库设置表。

## 旧原型

仓库根部 `electron/`、旧 `src/`、`gateway/` 和 Sites 文件属于 2026-09-03 之前的演示原型。生产主线从 `apps/desktop` 与 `services/ai-worker` 开始，旧代码不再扩展，并将在功能切片迁移完成后移除。
