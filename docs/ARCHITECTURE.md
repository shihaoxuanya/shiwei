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
- MVP 不依赖用户提供的云服务器；原发布服务器已退役，构建仅使用本地或 GitHub CI。

## 本地开源边界（2026-09-15）

已移除发布控制面、统计、错误上报与自动更新插件。启动不连接拾微服务器，GitHub 下载仅响应主动点击；保留 CI 安装包离线签名。Python 仍是知识库唯一写入者，模型网关仍可由用户配置在线或本地服务。

## 本地与外发边界

原始文件、笔记、对话、SQLite 和派生索引默认在本机。PDF 解析和 OCR 也在本地运行。在线聊天仅发送当前问题、必要近期对话及选定证据上下文；在线语义索引依次处理待索引资料的全部标题/正文派生文本片段，查询时另生成查询向量。因此不能把在线 Embedding 描述为“仅提问时上传少量片段”。这些请求通过 ModelGateway 发往用户所选服务；本机地址由相应本地服务处理，不应一律宣称必然外发互联网。

不存在拾微统计服务。系统凭据由原生层管理，不回显给 React，也不写入知识库设置表。

## 旧原型

仓库根部 `electron/`、旧 `src/`、`gateway/` 和 Sites 文件属于 2026-09-03 之前的演示原型。生产主线从 `apps/desktop` 与 `services/ai-worker` 开始，旧代码不再扩展，并将在功能切片迁移完成后移除。
