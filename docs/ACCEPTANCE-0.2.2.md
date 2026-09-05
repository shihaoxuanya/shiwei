# 拾微 0.2.2 — 回答忠实度验收

日期：2026-09-05。范围仅为数字范围、事实/推断、引用、笔记日期、证据筛选和回归测试。没有重构核心 Retrieval，没有新增用户功能、模型厂商、知识图谱或 Agent。

## 1. 根因与数据保真

已通过合成端到端测试复现：原文“单次全量迁移预计耗时38~50天，增量追平需7~14天。”传到前端仍有两个 ASCII 波浪号。旧 `remark-gfm` 默认启用单波浪号删除线，将两个不同区间的 `~` 配成一对 Markdown 删除线定界符。标记不再成为可见字符，DOM 文本变成 `3850天` 和 `714天`。

此外，对现有用户资料库进行了 **mode=ro + PRAGMA query_only** 的只读统计：对应标题的笔记保留 `38~50` / `7~14`；7 条历史助手消息仍保存这两个原始区间，`3850天` / `714天` 计数均为 0。没有修改、迁移或重新导入该资料库，也没有把真实笔记发送给模型。

| 链路层 | 核验结果与修复边界 |
| --- | --- |
| 原始 Note、保存读取 | 字符串保持原样，未规范化保存 |
| Canonical JSON | 原始区间保留 |
| Chunk.content | 原始区间保留 |
| Unicode / trigram FTS | 分词可以插入空格，但区间分隔符保留 |
| document/chunk search_text | 仍为标题＋原文，区间保留 |
| 实际 EmbeddingIndexer 输入 | 通过记录型 Gateway 验证输入是标题＋完整原文，含两个 `~` |
| Retrieval hydrated hit | 原始区间保留 |
| Context Builder / Prompt | 仅派生展示文本中的明确数字范围规范为 `38～50 天`、`7～14 天` |
| 模型输出与历史保存 | 正常输出规范为全角范围；合并数字、未标明计算等会触发一次纠正检查 |
| React / Markdown | 关闭 singleTilde；窄范围规范化同时保护旧历史中的 `~` 区间 |

不是 CSS 掩盖。代码、URL、Markdown 链接、非数字波浪号不做全局替换；双波浪号删除线仍可用。引用抽屉的“对应原文”保留源字符串，作为纯文本正确展示，不经过 Markdown 删除线解析。

## 2. 修改模块

- `services/ai-worker/shiwei_ai/text_fidelity.py`：只处理明确数字范围的派生文本工具，保护代码、URL等。
- `retrieval/context.py`：组装模型上下文时保留区间含义；引用 snippet 仍指向原文。
- `chat/answer_policy.py`：同源段落/连续列表/引导段落与列表的保守引用分组；数字拼接、未经标注的衍生数字/部分显式推测表达、两轮计算检查。
- `chat/service.py`：来源事实和推断分层提示词、简短记忆式回答、只回答当前问题、一次有界纠正、验证后再对 UI 发送正文。不安全草稿不会先闪现在页面上。
- `retrieval/evidence.py` / `hybrid.py`：在既有召回和相关性门槛之后选择回答证据；会议问题要求实际会议内容。保留候选用于开发 Trace，但不把部署报告作为会议证据传给模型。
- `retrieval/query_plan.py`：小范围补齐“会上/会里”“多久”“还有哪些相关资料”的口语词处理，没有改写原有多查询、向量、FTS 或 Fusion 架构。
- `chat/assistant.py`：会议问句走个人记录边界；缺失资料本地拒答；文件卡片只关联真正引用的文档。
- `worker/server.py`：已有 `mentioned_dates` 传到回答及历史引用，无新增数据库迁移。
- `apps/desktop/src/lib/answer-text.ts`、`MessageContent.tsx`：旧/新回答数字区间显示防护。
- `Sources.tsx` / `lib/chat.ts`：笔记主 Chip 只显示 ID＋标题，详细提及日期、创建于、更新于在来源抽屉分别显示。没有将创建时间当成事件日期。
- `AGENTS.md`：记录本轮持久的产品和忠实度边界。

开发者 Trace 保留 `Original Query / Rewritten Queries / FTS / Vector / Fusion / Selected Context`，并增加 `selected_as_evidence` 和 `evidence_reason`。主题相关但不是会议证据的候选标为 `topic_related_but_not_event_evidence`。生产 UI 不展示技术 Trace。

## 3. 自动回归

全量通过：**211 Python + 54 React + 9 Rust = 274 项**，TypeScript 类型检查和生产前端构建通过。本轮新增 35 项 Python 和 12 项前端测试，保留原有测试。

新增测试文件：

- `services/ai-worker/tests/test_answer_fidelity.py`：整条存储/索引/Embedding 输入/上下文/回答链路，源事实、计算、日期与语义召回，会议证据过滤，MongoDB 负例，一次纠正与流式草稿隔离，引用日期和历史持久化。
- `services/ai-worker/tests/test_answer_policy.py`：数字范围与非范围/代码/链接边界，同源引用分组，多源对应关系，明确数字推断及推测性补充的保护。
- `apps/desktop/src/components/chat/AnswerFidelity.test.tsx`：DOM 不再出现拼接数字、代码/删除线兼容、主 Chip 日期、抽屉日期标签、没有提及日期时不猜测、文件页码不回归。

既有检索评测重新执行：在小型合成集（1 会议笔记＋10 OCP 日期干扰文档＋1 设备手册）中，FTS-only 和故意误导的假向量两种模式均为 Recall@1 / MRR / Citation / Negative Abstention = 1.0。这里只是该固定评测集的结果，不代表所有真实问题达到 100%。

打包 Worker（0.2.2）独立核验：

- `smoke-chat-worker.py`：PDF 文件定位、历史追问、笔记统一索引/编辑/删除、5 个模糊召回、8 个负例均通过。
- `smoke-pdf-worker.py`：合成真实 PDF 文件＋扫描页本地 OCR、页码引用、重复导入通过。
- `smoke-answer-fidelity.py`：临时本地 HTTPS/SSE 模型夹具实际连接 EXE，验证上下文范围、逐字符输出、引用分组、会议证据过滤、纠正前不发送错误数字、3 个 MongoDB 负例零生成调用、笔记日期历史、原文未变，全部通过。临时 CA 只注入测试子进程，不关闭生产 HTTPS 验证，不使用真实密钥。

## 4. 实际桌面与真实模型验收

实际运行 Tauri Windows 桌面＋Python Worker，通过 Playwright 接入桌面 WebView 操作输入框、发送、查看引用。不是浏览器假数据 Demo。使用新的隔离资料库，包含根据本轮用户案例构造的会议笔记、TiDB 部署报告、OCP 考试干扰资料。模型使用现有配置的智谱 `glm-5.3`，无密钥输出；语义索引为空，仅本地全文召回。未修改现有模型设置。

以下 6 个问题在同一对话中依次发送并完成，自动比对了落库消息、实际引用 Note ID 与原始 Note 内容：

| 验收问题 | 最终实际结果 |
| --- | --- |
| 你记得我上次开会是什么时候吗？ | 返回记录中的 2025 年 9 月 3 日，引用目标会议笔记 N1 |
| 之前讨论Oracle迁移的会议是什么时候？ | 返回同一日期、同一 Note；未使用 OCP 日期 |
| 之前TiDB迁移会上说了什么？ | 会议笔记概述排期、两轮、测试环境、工具选型；正文只标一次 N1，无部署报告介绍 |
| 那次会议说全量迁移要多久？ | 单次 38～50 天、至少两轮；没有把项目总工期说成会议原话 |
| 增量追平多久？ | 7～14 天，无 714天，无额外影响因素推测 |
| 我之前有没有讨论过MongoDB迁移？ | “没有找到可靠记录”，answerKind=not_found，零引用、零文件卡片 |

额外实际追问“两轮大概要多久？”：先引用单次 38～50 天、至少两轮；再单独说明“如果两轮完全串行且每轮耗时相同，简单计算约76～100天；这只是根据记录推算，不是会议原文明确给出的项目总工期，也未计入增量追平、测试或等待”。

来源抽屉实际显示：类型“笔记”、标题“2025年9月3日会议”、记录中提及日期“2025年9月3日”、创建于/更新于“2026/9/5 …”。主 Chip 不附加 2026 日期。抽屉打开后输入框仍可用。

本地可复核结果：`output/playwright/fidelity-live-report.json`。截图：`fidelity-final-meeting.png`、`fidelity-final-full.png`、`fidelity-final-incremental.png`、`fidelity-final-negative.png`、`fidelity-final-calculation.png`、`fidelity-final-source.png`。

## 5. 边界和已知限制

- 为避免错误数字在校验前闪现，知识回答先缓冲生成结果，校验后再发给前端；首次可见正文的等待比直接边生成边展示更长。最多一次纠正请求，仍不合格则显示带引用的原文摘录，不冒充有效总结。
- 数字与引用检查是有界规则，不是通用事实证明器；任意定性推断、复杂跨来源计算仍依赖提示词和模型遵循。当前同义会议证据规则保守，不是新的通用分类器。本轮固定案例通过，不宣称消灭全部幻觉。
- 主动减少噪声，不做激进跨来源引用合并；不同 Citation ID 即使属于同一 Note，也保留其分块定位。
- 旧笔记和旧消息未被重写；UI 可正确显示其中保留的 `~`。若历史文本本身已经是错误合并数字，则不能无证据自动逆向恢复。本次只读检查没有发现该情况。
- 这次没有做代码签名，Windows 可能提示未知发布者。现有安装版不会被测试进程强行覆盖或关闭。

## 6. 发布产物

安装包：`apps/desktop/src-tauri/target/release/bundle/nsis/拾微_0.2.2_x64-setup.exe`。

- 构建完成，ProductVersion=0.2.2，大小 407,607,076 字节（约 388.7 MiB）。
- SHA-256：`3ED40E05B9E9A1F02E76A7FE04000AA1739FFA8ED29D0349AAB842DC94BCF635`。
- 签名状态：NotSigned。没有声称已签名或消除 Windows 未知发布者提示。
- 0.2.2 发布版实际启动：窗口标题“拾微”、响应正常，打包 Worker 与 WebView 均启动，隔离目录 stdout/stderr 均为 0 字节。
- 发布目录中的 Worker 再次通过本地 HTTPS/SSE 忠实度集成测试。dist、Tauri staging、release 三处 Worker SHA-256 一致：`BC8A85BE1A09BC8387E5B55DF6E1B6C7474A0AD1C315E8F6017C2667C20D99D2`。
- 0.2.1 等旧安装包保留，未覆盖现有安装目录，未结束用户正在运行的 `D:\space\SW\拾微\shiwei-desktop.exe`。本次创建的临时桌面 QA 进程与 Vite 服务已关闭。

汇总记录：`output/qa-0.2.2/regression-results.json`。
