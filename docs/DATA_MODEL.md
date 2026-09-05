# 拾微 MVP 数据模型

SQLite 启用 `WAL`、`foreign_keys=ON`，所有结构变化由顺序 migration 管理，升级禁止删库。

## 核心表

### sources

原始来源记录：`id`、`original_path`、`original_filename`、`stored_path`、`content_hash`、`size`、`mime_type`、`created_at`、`imported_at`、`status`、`error`、`source_type`。

`source_type` 为 `imported_file | user_note`。外部资料的 `content_hash` 使用 SHA256 去重；笔记使用稳定内部身份，不进入资料列表或文件导入生命周期。

### notes

用户直接记录的文本真值：`id`、`source_id`、`title`、`content`、`created_at`、`updated_at`、`deleted_at`。标题允许为空，界面以正文首个非空行或“无标题笔记”作为展示标题。

每条笔记对应一个 `user_note` source；有可检索文本时投影为 `user_note` document、section、chunks、FTS5 与可选向量。更新只替换该文档的派生数据，删除通过来源外键彻底移除检索和引用上下文，不留下旧 chunk。

### documents

解析后文档：`id`、`source_id`、`title`、`parser`、`parser_version`、`canonical_path`、`language`、`created_at`。

### sections

结构节点：`id`、`document_id`、`parent_id`、`kind`、`heading`、`heading_path`、`ordinal`、`page_number`、`sheet_name`、`slide_number`、`content`。

### chunks

检索单元：`id`、`document_id`、`section_id`、`content`、`chunk_index`、`page_number`、`heading_path`、`token_count`、`created_at`。

FTS5 索引 `chunk_content`、`document_title`、`filename` 和 `headings`。

### jobs

后台任务：`id`、`type`、`status`、`progress`、`current_step`、`payload`、`error`、`created_at`、`started_at`、`finished_at`。启动时将无法恢复的 `running` 任务标记为中断，再按类型重试或失败。

### conversations / messages / citations / message_sources

保存本地对话、消息和回答引用。Citation 至少包含 `citation_id`、`message_id`、`document_id`、`chunk_id`、`source_filename`、`page_number`、`heading_path`、`snippet`。

0.1.2 migration 4 在 `messages` 增加可空的 `answer_kind`、`mode`、`notice`；旧消息仍可读取。`message_sources(message_id, source_id, ordinal)` 保存独立文件关联，用外键约束并在来源删除时级联移除，不复制文件路径快照到消息中。历史文件卡片和追问每次从仍存在的来源读取真实路径。

0.2.0 migration 5 增加 `sources.source_type` 和 `notes`。旧来源默认迁移为 `imported_file`，不移动、重写或重新导入原始文件。笔记正文与该次全文检索投影在同一 SQLite 事务中更新；向量是可重建派生数据。

迁移 DDL 与 `schema_migrations` 标记同处一个事务；本次迁移不改写原始文件、Canonical Document、FTS 内容或向量索引。

0.2.1 migration 6 给 documents/chunks 增加派生 `search_text`、`mentioned_dates`（JSON ISO 日期数组），给 embedding_versions 增加 `search_text_version`（旧行默认0，新向量1）。迁移事务内从原有 title/content 补齐投影并重建两套 FTS，不修改 notes.content、chunks.content、原始文件、Canonical、ID、历史引用及旧向量。失败完整回滚。日期仅从标题/正文明确有效日期提取，不能用系统时间或 LLM 猜测。

### settings

只保存非敏感设置。API Key 由系统安全存储持有，SQLite 仅保存凭据引用。

### embedding_versions

记录 `id`、`provider`、`model`、`dimension`、`created_at`、`active`。LanceDB 每条向量绑定版本，重建不修改原始或 Canonical 数据。

## 删除语义

用户确认删除资料后，在一个 Worker 事务/任务中删除 source 关联的 document、section、chunk、FTS、vector 与 citation 关系，随后删除 Raw 文件。删除笔记同样清除其检索投影，但没有 Raw 文件。删除单条对话只级联 conversations 下的 messages、citations 和 message_sources，不触碰任何来源。失败时不留下 UI 假成功状态。
