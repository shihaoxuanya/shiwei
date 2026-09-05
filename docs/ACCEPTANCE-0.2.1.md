# 拾微 0.2.1（v0.21）检索可靠性验收

验证日期：2026-09-05。唯一范围：自然语言模糊回忆的 Recall、Ranking、Citation、Abstention。无新导航/用户功能、知识图谱、Agent Framework 或 Reranker。

## A–I 落地

| 项目 | 实现与证据 |
| --- | --- |
| A 回归 | `test_fuzzy_recall.py`：三个原始回归及另两个指定问法均为同一 Note、Top 1、N1。 |
| B 检索投影 | migration 6，title + body 的 search_text 同供 Unicode/trigram FTS 与 Embedding；引用重新读取原正文。原始来源、笔记正文、ID 与历史保留。 |
| C 标准化 | QueryPlan 独立确定性 Normalize/Rewrite；保留主题、技术名、否定和明确日期条件。无回答生成或模型训练。 |
| D 多路检索 | 原 Query + 2–3 Rewrite，每路 FTS/Vector，chunkId 去重后 RRF Fusion。 |
| E 时间元数据 | documents/chunks 的 mentioned_dates 从有效明确日期提取；不把创建/导入时间当事件日期。 |
| F 时间排序 | 仅对主题相关、日期条件满足的候选增加有限 temporal boost。 |
| G 相关性门槛 | 原始问题 Gate 在生成前剔除无关候选；标题弱匹配和 LLM planner 不能绕过门槛。 |
| H 开发 Trace | 显式 debug 模式含原问句、Rewrite、各路 Hits、Fusion、拒绝原因、分数和最终 Context；默认无正文/路径，不在生产 UI 展示。 |
| I 重评估 | 两种模式固定回归集均通过；没有出现需 Reranker 处理的持续低排名，本轮不引入。 |

## 测试结果

- Python：**176 passed in 24.13s**，包含原有回归、QueryPlan、迁移回滚、FTS/向量输入、模型/向量故障回退、日期边界、笔记编辑删除、Worker 引用历史和拒答。
- React：**42 passed**；TypeScript 检查通过。
- Rust：**9 passed**，包含启动真实 Python Worker 的 RPC 往返。
- 源码 Worker 烟测：临时库的文件定位、连续追问、笔记 CRUD、N1、5 个指定问法和 8 个负例通过；未配置真实模型。
- 源码 PDF 烟测：合成文字 PDF、离线扫描 OCR、页码引用、哈希去重通过。
- 打包后的 PyInstaller Worker 0.2.1 重复同一烟测：5个指定问题、8个负例、N1/历史、文件定位/路径、笔记编辑删除、文本PDF/离线OCR/页码/去重全部通过。额外检查嵌入程序的 Python 归档确实包含最终 QueryPlan，而不只验证源码。证据：`output/qa-0.2.1/packaged-worker-smoke.json`。
- Tauri release 桌面0.2.1在独立资料目录/WebView目录实际启动，窗口“拾微”、Responding=true、打包Worker与WebView2子进程正常、stdout/stderr为0。验证后仅停止本次测试进程，未停止用户已安装应用。此项是原生启动/生命周期检查，检索交互通过实际打包Worker RPC验证，不冒称做过全部原生鼠标点击。证据：`output/qa-0.2.1/desktop-smoke.json`。

## 检索评测

临时合成库含日期会议笔记、10 个 OCP 考试日期干扰文件和会议室设备维护手册。误导向量模式故意把目标排到第 12 名，OCP 给 0.99、手册 0.96、目标 0.72；不是实际提供商 Embedding 准确率测量。

| Query | 仅 FTS | 误导向量 + FTS | Citation |
| --- | --- | --- | --- |
| 2025年9月3日我开会了吗？ | Top 1 | Top 1 | 同一 Note / N1 |
| 2025年9月3日会议内容是什么？ | Top 1 | Top 1 | 同一 Note / N1 |
| 我之前开了个会是在几号？ | Top 1 | Top 1 | 同一 Note / N1 |
| 之前那个Oracle迁移的会议是哪天？ | Top 1 | Top 1 | 同一 Note / N1 |
| 我什么时候讨论过TiDB迁移？ | Top 1 | Top 1 | 同一 Note / N1 |

两个模式 Candidate Recall、Recall@1/5/12、MRR、Citation Correctness 均为1.0。Kubernetes扩容、SAP采购、PostgreSQL采购、财务预算、员工调薪和不存在的2032年会议六类负例全部无上下文/引用，Abstention为1.0。另测非法2月30日、未记录的9月4日：不能放宽成9月3日会议。同正文但不同日期/来源的会议保留两个独立引用，不隐藏歧义。

详细指标与默认不含正文的逐路 Trace 在 `output/qa-0.2.1/retrieval-evaluation.json`、`fuzzy-query-debug-trace.json`。上述指标仅针对固定合成回归集，不代表全部自然语言或所有模型的总体准确率。

## Windows 交付

已生成 `apps/desktop/src-tauri/target/release/bundle/nsis/拾微_0.2.1_x64-setup.exe`（407,680,483字节）。文件/产品版本与SHA256及签名状态见 `output/qa-0.2.1/installer.json`。旧版0.2.0安装包保留；没有替换用户当前正在运行的旧版应用或修改真实资料库。安装包未代码签名。

## 升级与边界说明

真实旧资料库仅通过 SQLite `mode=ro` 生成临时快照：源库 schema 始终为5、原始列哈希未变；快照升级到6后，1条笔记、5个来源、5个文档、33个章节、29个分块的原始数据与ID全部保持。五个指定问法纯FTS均为目标笔记Top1/N1、无无关上下文。没有读取模型配置或调用外网，验证后销毁快照。摘要见 `output/qa-0.2.1/upgrade-verification.json`。

- 启动自动事务迁移本地 FTS/search_text/日期，不重新导入或改写原文；失败回滚。旧向量保留，不混用正文版与标题+正文版。
- 未更新的旧向量暂不参与检索，既有设置状态明确提示；本地全文回忆可直接工作，不隐式向服务商上传全库。通过既有索引流程更新后恢复语义增强。
- 长文日期与主题隔在不同片段、没有明确日期、未覆盖的口语表达仍可能保守拒答；不直接散播整篇所有日期，以免把另一会议日期错误关联。
- 本轮不以回答文案为验收目标，不声称对所有用户问题已达到完整语义理解。签名、云服务、账号、额外产品功能均未增加。
