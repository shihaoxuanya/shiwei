# 基础使用统计与错误报告

## 开关与兼容

按 2026-09-06 的明确产品决定，0.3.1 新生产安装默认开启基础统计，不弹确认框。“设置 → 隐私 → 帮助改进拾微”说明字段和用途，并可随时关闭。已有安装的关闭状态原样保留；损坏／不可读设置关闭上报。开发构建默认关闭，浏览器预览不发送生产统计。打包应用验收设置 SHIWEI_TELEMETRY_DISABLED=1，不读取或改写真实安装统计设置。

“不含正文”不等于没有隐私影响。随机安装标识和使用时间仍属于去标识化遥测，网络基础设施仍可看到连接 IP。

## 字段白名单

- 原生层生成的 UUID v4 安装标识和每请求随机事件 ID；无真实身份或硬件指纹。
- App 版本、操作系统、架构、事件时间。
- 固定事件：app_installed、app_opened、app_version、import_started、import_completed、import_failed、note_created、question_asked、retrieval_succeeded、retrieval_abstained、first_successful_recall、citation_clicked、update_available、update_started、update_completed、update_failed、app_error。
- 导入数量区间、有限成功／失败数、耗时；错误类型固定枚举。堆栈仅允许应用模块和有限数字行列，服务端只保存模块，不保存行列或错误原文。

禁止上传文件、笔记、Chunk、引用正文、文件名、路径、问题、回答、Embedding、模型名称／Key、Authorization、Windows 用户名、姓名、邮箱、知识库主题、MAC、SID、硬件序列号。无自动采集 SDK、DOM 跟踪、录像、身份 cookies、请求 breadcrumbs。

Rust sanitize_telemetry_payload 按字段和值清洗；服务端再次按事件类型严格校验，拒绝未知字段、任意错误字符串和不合理数字。数据库只有明确标量列，不存任意 JSON。验证失败固定返回，不回显输入。

## 自有服务与保留

默认由原生层 HTTPS 发送到 https://zhishimanghe.com/api/v1/telemetry/events，不携带 cookies 或管理员凭据。analyticsHost 非空、analyticsKey 为空选择第一方传输；保留已有 PostHog 公开 phc_ 项目配置作为可替换传输，默认不依赖它。

独立 control-analytics.db 与版本库并列，不打开用户知识库。安装和事件 UUID 经服务器专用 HMAC-SHA256 盐处理，原 UUID 不落库。盐仅存服务器受限环境文件，不进入源码或客户端。只有管理员汇总 API，没有单个安装画像、原始事件或内容查看接口。

事件最多保留 90 天，启动、收数／汇总和每小时任务都会清理。90 天不活跃的安装摘要也清理；活跃安装仅保留首次／最近观测时间。SQLite secure_delete 与 WAL 清理启用，生产 umask=0077。不要把统计库加入无限期备份；更换盐会改变去重身份，不能将前后窗口拼成连续人数。

应用与代理不启用访问日志；Caddy 运行错误日志过滤完整 request／headers，避免错误请求额外留下 IP、URI 或安装标识。依据 [Caddy 日志过滤文档](https://caddyserver.com/docs/caddyfile/directives/log)，部署前使用隔离代理错误测试验证。不宣称云服务商网络基础设施也完全不保留 IP。

## 故障与限制

客户端最多 8 个在途请求，4 秒超时，无磁盘事件队列、无补传、无递归异常报告。关闭取消未完成请求并作废旧任务；已经收到的数据无法通过取消追回，按保留周期清理。

服务端限制 4KB JSON、5 秒读请求、每 IP 每分钟 600 次、全局每分钟 1200 次、单安装 24 小时 2000 条，总计 200000 条事件／20000 个安装摘要。重复事件幂等；时间只接受过去 24 小时至未来 5 分钟，略快时钟截到接收时刻。满额、断网或统计数据库异常不影响本地工作流、登录和版本检查。

公开采集接口没有秘密客户端令牌；嵌入客户端的凭据不能证明真人身份。限流不能消除伪造事件，因此这些统计不用于计费或安全审计。

## 口径和错误范围

只统计成功上报的安装实例，不代表全部用户。首次观测不是系统安装日期，重装可能产生新实例。指标详见 [ANALYTICS_DASHBOARD.md](ANALYTICS_DASHBOARD.md)。

“有证据的资料回答”沿用最终 answerKind=knowledge 且有有效正文引用的代理定义，普通聊天／文件卡片／单纯 HTTP 200 不算；这不是准确率。首次成功事件 best-effort、at-most-once，关闭或离线期间不补报。

错误范围包括前端异常、Worker 错误／退出／超时、可捕获 Rust panic，不采集进程内存或完整 crash dump。断电、强杀等可能漏报，不能宣称全量崩溃覆盖。模型调用与统计开关独立，模型传输边界仍按设置说明。
