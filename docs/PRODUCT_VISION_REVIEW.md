# 长期愿景与当前实现对照

审阅日期：2026-09-05。对应 [PRODUCT_VISION.md](PRODUCT_VISION.md)。

本次仅静态阅读当前代码、数据模型、接口与产品文档，不修改功能、数据库或用户资料。以下是代码路径可见的风险，不声称已经检查用户历史数据是否受影响；也不是自动获准执行的重构清单。

## 已符合方向的基础

- 生产主线为 Tauri + React/TypeScript + Python Worker，Python 独占知识库写入，界面与模型服务之间有明确边界。
- 原始文件按内容哈希存入本地资料库；笔记标题和正文保存在 SQLite 真值表，检索使用派生投影。见 [raw_store.py](../services/ai-worker/shiwei_ai/storage/raw_store.py)、[notes/service.py](../services/ai-worker/shiwei_ai/notes/service.py)。
- FTS 与向量索引可从来源重建；聊天与 Embedding 配置分离，模型通过 [ModelGateway](../services/ai-worker/shiwei_ai/models/gateway.py) 接入，不要求更换聊天模型时迁移个人资料。
- 当前已有模糊召回、明确日期提取、证据筛选、引用、事实与推断约束及负例拒答。这些直接服务 Stage 1，不需要因愿景重新搭建 Retrieval。

## 1. 历史引用依赖可替换的索引对象

**现状与依据**：

- [NoteService._replace_projection](../services/ai-worker/shiwei_ai/notes/service.py) 更新已有笔记时，会删除旧 `documents` 行，再插入文档及新的 Chunk ID。
- [DocumentIndexer.index_source](../services/ai-worker/shiwei_ai/ingestion/indexer.py) 重新解析文件时，也删除旧文档并生成新文档、分块。
- [数据库 citations 定义](../services/ai-worker/shiwei_ai/storage/database.py) 的 `document_id` 和 `chunk_id` 外键均为 `ON DELETE CASCADE`；外键约束开启。

**影响**：上述替换会级联删除依赖旧文档/分块的历史引用记录，而对话文字仍然存在。即使重新插入相同文档 ID，也不会恢复已经级联删除的引用。这使“重建派生表示”影响到长期可追溯性，是当前最直接的结构性风险。

**长期边界**：历史答案的来源身份、版本与证据不能仅依赖易变化的 Chunk ID。这里只记录约束，不在本轮设计或实施引用快照、版本迁移方案。

## 2. 当前数据生命周期不足以保留长期变化历史

**现状与依据**：

- [NoteService._update_note_truth](../services/ai-worker/shiwei_ai/notes/service.py) 直接更新当前标题、正文和更新时间，没有笔记修订历史表。
- 同文件的 `delete` 删除来源；[Importer.delete_source](../services/ai-worker/shiwei_ai/ingestion/importer.py) 删除来源并移除应用管理的原始副本和解析文件。不是 7 天回收站机制。
- [当前 Worker 接口](../services/ai-worker/shiwei_ai/worker/server.py) 与 [IPC 文档](IPC.md) 未提供产品级一致性导出、备份及恢复闭环。这不意味着本地文件不能手动备份，而是尚未定义和验证应用级恢复流程。

**影响**：同一笔记被覆盖后，无法从当前数据模型恢复先前观点；删除后的恢复依赖其他副本或备份。未来理解决定演变和维护长期资产，不能假设历史已经存在。

**边界**：修订历史、恢复与导出属于尚需明确任务的能力缺口，不是要求现在实现 Memory Consolidation。不得据此自动改写删除语义或迁移资料库。

## 3. 解析文件与数据库提交不是一个原子操作

**现状与依据**：

- [NoteService._replace_projection](../services/ai-worker/shiwei_ai/notes/service.py) 先将临时 JSON 替换到正式 Canonical 路径，再进入 SQLite 事务写入笔记真值和检索数据。
- [DocumentIndexer._write_canonical / index_source](../services/ai-worker/shiwei_ai/ingestion/indexer.py) 同样先写解析文件，再提交数据库。

**影响**：如果文件替换后、数据库提交前失败，SQLite 回滚不会恢复文件，可能出现新解析投影与旧数据库状态并存。文件级原子替换不等于文件系统与数据库的跨存储原子提交。

这是异常中断时的一致性与恢复风险，不是已经发生原始笔记丢失的证明。现有解析文件仍是可重建投影；未来不得未经恢复设计就把它视为唯一的长期记忆真值。本轮不重构写入协议。

## 4. 需要明确的长期隐私边界：远程 Embedding

[EmbeddingIndexer.rebuild / replace_document](../services/ai-worker/shiwei_ai/retrieval/vector_store.py) 会把待建索引的 Chunk `search_text` 分批发送给配置的 Embedding 服务；全量重建覆盖全部待索引分块，不限于当次聊天选中的证据。此行为已在 [README 隐私原则](../README.md) 中说明，语义检索可不启用，不能将其描述成未披露的资料上传。

因此，“聊天只发送必要上下文”与“所有远程模型永远只获得当前回答的片段”不是同一个承诺。后者是更严格的长期愿景，现有远程索引机制尚不完全符合。未来如明确推进该目标，需单独确定本地计算与授权边界；本轮不切换模型、不新增 Provider，也不改已有配置。

## 当前 MVP 与未来能力的边界

当前 MVP 继续由 [PRODUCT.md](PRODUCT.md) 定义：文件/文件夹/笔记输入，可靠召回、回答、引用与拒答。上述代码风险不改变本轮“只报告”的授权范围。

Person / Project / Event 等完整对象、有效时间模型、Memory Consolidation、Active Recall、更多入口、跨应用 MCP/API 目前未完整实现，属于有意保留的阶段边界，不能仅因缺少这些能力就认定现有架构需要重写。

审阅结论：当前本地优先、可替换模型、可重建检索的方向可继续使用。最需要后续关注的是历史证据与派生索引的耦合，以及用户资产的版本、恢复和一致性边界；是否处理、何时处理，应由后续具体任务决定。
