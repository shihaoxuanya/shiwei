# PostHog 早期产品指标

当前不开发自有管理后台，也没有自动创建远程 Dashboard；维护者在自己的 PostHog 项目创建以下图表。统一标注 **Installations / 同意参与统计的安装实例**，不要标成 Registered Users 或全部用户。

## 图表

| 指标 | 定义 |
| --- | --- |
| Observed Installations | `app_installed` 的唯一 distinct_id；仅覆盖首次同意后成功上报的实例 |
| DAU / WAU / MAU | `app_opened` 按日/周/月窗口统计唯一 distinct_id，客户端不计算 |
| 版本分布 / Update Adoption | 活跃实例最近的 `app_version` 属性；不是简单累加历史版本事件 |
| First Import Conversion | opened → 首次有 `success_count > 0` 的 import_completed |
| First Question Conversion | opened → question_asked |
| First Successful Recall | opened → first_successful_recall；注意隐私关闭/离线导致的漏报 |
| Weekly Successful Recalls | 每周 retrieval_succeeded 次数，另看有成功召回的活跃实例数 |
| Citation Click Rate | 有 citation_clicked 的实例 / 有 retrieval_succeeded 的实例，按同一时间窗口 |
| Import Failure Rate | SUM(failure_count) / (SUM(success_count) + SUM(failure_count))，避免部分失败事件重复计数 |
| Retrieval Abstain Rate | retrieval_abstained / (retrieval_succeeded + retrieval_abstained)；不含普通聊天 |
| Observed Error/Crash Rate | app_error 按 error_type 分组；worker_exited/rust_panic 单独观察，不宣称全量崩溃覆盖 |
| Updates | update_available → update_started → update_completed，以及 update_failed 的枚举错误类型 |

导入部分成功的任务会同时有 completed 和 failed 事件。计算文件失败率时只聚合 completed 的 success_count、failed 的 failure_count；不要把两个事件上的同名字段都相加。重复文件跳过不是新导入成功，也不应算失败。没有 job/session 内容标识，图表只能做这种聚合，不做单个文件/单个问题追踪。

`citation_clicked` 是点击有效引用入口，不等于阅读理解或用户满意度。没有对话/问题/文件 ID，不将 citation clicks 伪装成能逐问精确归因的转化率。

## 核心漏斗

App Opened → Import Completed（成功数 > 0）→ Question Asked → First Successful Recall → Citation Clicked。

使用同一安装实例的有序漏斗，建议 7 天转化窗口。笔记是另一条输入路径，可另看 opened → note_created → question_asked → first_successful_recall；当前 note_created 是创建记录，不等于正文已完成。

## 留存

用 `app_opened` 作为回访事件看 D1/D7/D30。进一步用每周至少一次 `retrieval_succeeded` 观察是否持续得到价值，无需新增客户端 weekly 事件。

PostHog 关闭 person profiles、会话录像、自动采集和不必要 geo-IP enrichment。按最小权限授权看板，设置适当的数据保留周期；项目管理 token 不进入客户端。

指标口径和数据限制见 [PRIVACY_TELEMETRY.md](PRIVACY_TELEMETRY.md)。
