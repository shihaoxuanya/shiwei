# 匿名统计与错误报告

v0.3 的匿名统计与错误报告**默认关闭**。用户可在“设置 → 隐私 → 帮助改进拾微”主动开启或关闭，两类报告共用一个开关。没有配置 PostHog 时，即使开启也不会发送。浏览器预览不发送生产统计。

## 发送什么

- 随机 UUID v4 `installation_id`，保存在本地 Tauri App Local Data 的 `installation.json`；同一安装目录跨版本保持稳定。
- App 版本、OS 类型、架构、事件时间。
- 白名单功能事件：`app_installed`、`app_opened`、`app_version`、`import_started`、`import_completed`、`import_failed`、`note_created`、`question_asked`、`retrieval_succeeded`、`retrieval_abstained`、`first_successful_recall`、`citation_clicked`、`update_available`、`update_started`、`update_completed`、`update_failed`、`app_error`。
- 导入数量区间（1 / 2–10 / 11–100 / 100+）、有限数值的成功/失败数量、耗时。
- 错误类型来自固定枚举；堆栈仅保留允许的应用模块和数字行列，不含错误消息、函数参数、代码片段、用户目录或请求地址。

## 永不作为统计上传的内容

文件/笔记/Chunk/引用正文、文件名、完整路径、问题和回答正文、Embedding、模型/API Key、Authorization、Windows 用户名、真实姓名、邮箱、知识库主题、MAC、SID、CPU/磁盘序列号或硬件指纹。

不安装浏览器自动采集 SDK，不启用 session replay、autocapture、DOM/页面跟踪、cookies 身份识别或请求 breadcrumbs。Rust 统一 `sanitize_telemetry_payload` 是正向白名单，连允许字段的值也限制为枚举或有界数字；未知字段及嵌套对象默认丢弃，而不只靠黑名单删几个敏感名称。

## 身份、IP 与指标限制

`installation_id` 标识匿名安装实例，不代表真实人数。卸载并清除 App Data、换电脑等可能生成新的实例。我们不使用 IP 作为身份；事件关闭 person profile 与 geo-IP，并设置固定 `$ip`。HTTPS 网络与托管服务基础设施仍可能看到连接来源 IP，因此不可宣称网络层完全看不到 IP；需在服务商端核对日志保留与地域配置。

默认关闭意味着统计只覆盖同意参与且发送成功的安装实例，不能推算全部安装量。`app_installed` 是首次同意后观察到的安装，不一定是操作系统首次安装时间。离线、关闭统计期间不补传使用历史。

## 成功召回定义

当前近似定义：Worker 完成既有相关性/证据筛选，最终 `answerKind = knowledge`，至少一条有效引用，未以 `not_found` 拒答。普通聊天、概览、单纯 HTTP 200、没有证据的回答不算成功召回；文件元数据卡片也不算正文证据。

这只是产品早期代理指标，不代表用户已确认答案有用。`Weekly Successful Recalls` 在 PostHog 按周统计 `retrieval_succeeded`。首次成功状态保存在本地，首次成功时尝试发送一次 activation；以后不重复。若首次发生时统计关闭、服务未配置或网络失败，不在后来补报，保证不回放用户未授权的历史。它是 best-effort/at-most-once，不承诺网络 exactly-once。

## 关闭和故障

关闭会使前端统计 no-op，并由原生层更新持久化同意状态、取消未完成上传 future、丢弃旧 consent generation 的任务。已经到达服务商的数据无法通过取消请求追回；不再主动重试或排队补传。

没有持久化事件队列。最多 8 个在途请求、4 秒超时，失败直接丢弃，不影响启动、导入、保存、检索、问答或关闭，不递归上传统计服务自己的异常。开发调试只记录经过清洗的事件名，不打印 key、身份或正文。

## 错误报告范围

当前复用 PostHog 的 `app_error` 事件和结构化安全 frames 做基础错误统计，不引入第二个平台。覆盖前端未处理异常/Promise 拒绝、Worker 调用错误/退出/超时与可捕获 Rust panic；不是携带完整进程内存的 crash dump 系统。进程被强杀、系统断电、原生访问违规等可能来不及上报；完整符号化 native crash 收集尚未实现，不能用现有事件数宣称所有崩溃均已覆盖。

模型服务请求是用户另行配置的功能，与统计开关独立；关闭匿名统计不会关闭正常模型调用。已有远程 Embedding 的文本传输边界仍按模型设置说明执行。

参考：[PostHog Capture API](https://posthog.com/docs/api/capture)。
