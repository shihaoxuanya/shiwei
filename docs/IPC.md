# Rust ↔ Python Worker 协议

## 传输

Worker 使用 stdin 接收 UTF-8 JSON Lines，stdout 只发送一行一个 JSON 协议对象。stderr 用于脱敏日志。协议版本为 `1.0`。

## 请求

```json
{"jsonrpc":"2.0","protocol_version":"1.0","id":"uuid","method":"ping","params":{}}
```

每个请求包含唯一 `id`、方法和对象参数。`WorkerClient` 统一负责序列化、超时、待处理请求、异常 JSON、Worker 退出和重启；业务组件不得自行拼接协议。

## 成功响应

```json
{"jsonrpc":"2.0","protocol_version":"1.0","id":"uuid","result":{"status":"pong"}}
```

## 错误响应

```json
{"jsonrpc":"2.0","protocol_version":"1.0","id":"uuid","error":{"code":"METHOD_NOT_FOUND","message":"不支持的方法"}}
```

错误对象可以包含仅供开发日志使用的 `details`，不得包含 API Key 或完整敏感文档。

## 事件

```json
{"jsonrpc":"2.0","protocol_version":"1.0","event":"job_progress","data":{"job_id":"uuid","progress":0.65,"message":"正在理解你的资料"}}
```

事件没有响应 `id`，但带有 `requestId` 关联当前 Worker 请求。Rust 丢弃其他请求或过期请求的事件。

聊天生成时使用 `chat_token` 事件逐段发送文本；最终 `chat` 响应仍是唯一可信结果，Rust 和前端只展示通过真实 `chunkId` 校验后的引用。标准输入、标准输出在 Worker 启动时显式固定为 UTF-8，避免 Windows 区域设置改变 JSONL 编码。

Tauri `chat_ask` 参数为 `{query, conversationId?, requestId}`，其中前端 `requestId` 必须为 UUID。Rust 关联 Worker 自身的请求 ID，将相应 token 转发为 `chat-token`：`{token, requestId}`。前端监听器仅接受自己的 ID。0.1.3 按发起会话缓存 token 和最终响应：切换页面不会取消请求，也不会让结果串入当前查看的其他会话；完整卸载应用时客户端监听器随请求结束释放。

0.1.3 的 `list_conversations` 接受可选 `{query, offset}`。查询长度不超过 200 字，offset 为 0–100000 的整数；查询匹配标题或消息正文，转义 LIKE 通配符。返回 `{conversations: [{id, title, createdAt, updatedAt, preview}]}`，每页最多 50 条，按更新时间、ID 倒序。`preview` 是最后一条消息的前 90 个字符，只在本机展示。无新增数据库迁移或模型调用。

0.2.0 新增 `delete_conversation`，参数为 `{conversationId}`。删除由 Worker 在单个 SQLite 事务中完成，外键只级联该会话的消息、引用与文件关联；不删除资料、笔记、文档、分块或索引。不存在的 ID 返回明确错误。

笔记方法为 `list_notes`、`get_note`、`create_note`、`update_note`、`delete_note`：

- `list_notes` 接受可选 `{query}`，仅在本地按标题和正文子串搜索，LIKE 通配符已转义。
- `create_note` 立即创建空白笔记及其 `user_note` 来源；空内容不产生检索分块。
- `update_note` 接受 `{noteId, title, content}`，正文上限 20 万字。笔记真值、Canonical Document、chunk 和两套 FTS 索引在 Worker 中同步更新；向量仅增量替换该文档。
- `delete_note` 删除笔记来源、派生文档、全文索引、向量与关联引用，不影响导入文件。

笔记与文件共用检索上下文，但类型边界明确：笔记引用 ID 为 `[N1]`，文件引用仍为 `[S1]`。响应中的引用与来源卡新增 `sourceType`；笔记还带 `noteId`、`noteCreatedAt`、`noteUpdatedAt`，前端据此打开笔记而不是调用文件系统。

`chat` 响应保留 `mode: ai | local_search` 和 `conversationId`，新增：

- `answerKind`：`general | knowledge | source_lookup | library_overview | not_found | clarification`。
- `sourceMatches`：`[{sourceId, filename, originalPath, storedPath, importedAt, status}]`，从本地数据库读取，和 `citations` 分开。
- `notice?`：目前为 `provider_not_configured`，可本地显示已找到的原文，不冒充模型回答。

`get_conversation` 返回的历史助手消息也包含上述元数据。模型服务失败仍返回 `PROVIDER_ERROR`；Rust 将其区分于传输/协议错误，不改写成“未找到资料”。

## 当前方法

0.2.1 不增加用户 IPC 方法。`index_status` 追加 `searchTextVersion`、`needsRebuild`，用于既有索引状态提示。检索统一加入确定性 Multi-query 和 Relevance Gate；`chat` 协议与历史 Citation 身份保持兼容。`debugTrace` 只供开发 CLI/显式调试实例，生产 Worker 的聊天不启用或展示。

- `ping`：验证 Rust 启动 Worker 和端到端调用。
- `shutdown`：请求 Worker 完成清理并正常退出。
- `import_paths`、`list_sources`、`delete_source`、`reindex_source`：资料生命周期。
- `search_lexical`、`search_hybrid`：精确、语义和融合检索。
- `provider_configure`、`provider_test`、`provider_status`：进程内模型网关；API Key 由 Rust 从 Windows 凭据管理器注入，不写 SQLite。
- `rebuild_embeddings`：按当前 Embedding 配置重建 LanceDB，并切换 active 版本。
- `chat`：检索、上下文构建、回答、引用校验与对话持久化。
- `list_conversations`、`get_conversation`、`delete_conversation`：读取或单条删除本地对话。
- `list_notes`、`get_note`、`create_note`、`update_note`、`delete_note`：轻量笔记生命周期及检索投影。

导入与模型调用由 Tauri 放入阻塞线程执行，不冻结渲染进程。Worker 重启后，Rust 会从系统安全存储恢复模型配置。协议响应中的错误只包含用户可理解的信息，不返回密钥或完整文档。
