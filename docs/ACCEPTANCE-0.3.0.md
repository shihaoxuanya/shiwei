# 拾微 0.3.0 发布基础设施验收

日期：2026-09-05。结论：本地实现、回归测试和内部构建已验证；**尚未完成正式线上发布及 A→B 安装升级验收**。本记录不将 mock、签名测试样本或浏览器预览等同于真实发布成功。

## 本轮交付

- `VERSION` 是 0.3.0 版本真值，同步 package/Tauri/Cargo/Worker/About，校验 tag 和资源版本；当前只发布 stable。
- Windows CI 覆盖 Node/React/Python/Rust、Worker 打包、前端/NSIS 构建与启动；tag 发布工作流先构建、官方签名与验证，再上传 draft 的完整资源集，最后公开。
- 官方 Tauri 2 Updater：启动后延迟异步检查、用户确认、下载进度、强制验签、安装/重启、分类错误提示。安装前保存笔记，并拒绝中断正在处理请求的 Worker。绝无“仍然安装”签名绕过。
- 默认关闭的 AnalyticsService、PostHog 采集适配、随机安装 UUID、统一事件与值白名单、首次召回持久状态；后台 best-effort、无内容队列、无自动重放。
- 基础错误事件复用 PostHog；前端异常帧只保留应用模块/行列，原生/Worker 仅使用固定类型与模块，不上传原始错误消息。
- Settings 增加隐私与关于，保留原纸张/靛蓝视觉。技术版本信息折叠；Feature Flag 只提供本地默认接口。
- 0.2.2 冻结数据库 fixture、升级保留资产、失败事务回滚、拒绝未知新 schema。未新增知识库 schema，未改检索/回答策略。

## 自动化结果

| 验证 | 结果 |
| --- | --- |
| Node 发布脚本 | 6 项通过：SemVer、版本/tag、说明、官方 metadata、公开配置、拒绝旧资源混入 |
| React/Vitest | 71 项通过，含新增统计 no-op/失败隔离、状态机、签名拒绝、明确确认、笔记保存失败和重复请求测试 |
| Python/pytest | 214 项通过，含 3 项迁移保护；原召回、范围保真、引用、拒答测试保留 |
| Rust | 21 项通过，含 UUID 持久化、隐私关闭、事件清洗、首次召回只一次、同意代际失效、缺失 App Data、Worker 更新互斥 |
| TypeScript / VERSION / 锁文件 | typecheck、版本一致性、pnpm/uv frozen install 通过 |
| Workflow 文件 | 2 份 YAML 解析通过；尚未在 GitHub runner 上执行 |
| 官方 Updater signer | 用一次性测试密钥签名样本并以 minisign 验证成功；篡改后拒绝。临时测试密钥已清理，不是正式发布密钥 |
| 缺失发布配置 | `release:prepare` 拒绝继续，没有制造虚假 endpoint 或生成未签名的正式发布集 |
| 源码秘密扫描 | 常见令牌、私钥文件及编码私钥模式扫描通过；排除运行时/生成目录，不声称是完整秘密审计 |

合计 312 项 Node/React/Python/Rust 测试通过。构建时 MSVC 的“正在创建库”被 Rust 标为 linker warning，不是构建失败。

## 实际运行与界面

- 打包后的 Worker 0.3.0：2 份合成真实 PDF 导入、离线扫描 OCR、页码引用、哈希去重通过。
- 打包后的 Worker 对话 smoke：合成 PDF 召回、追问限定来源、历史持久化、笔记统一检索/重建/删除、历史引用通过；5 个模糊召回及 8 个拒答检查通过。使用合成资料与测试模型，不发送真实个人内容。
- 实际启动 Tauri release 程序：窗口响应、WebView2 和打包 Worker 启动正常，版本 0.3.0，stdout/stderr 均为 0 字节；使用隔离 App Data 和资料库。此检查是原生启动/生命周期，不是全流程真实安装更新。
- Playwright 技能用于真实 Edge 浏览器 1440×1024 界面 QA：设置/隐私/About 正常；合成更新通知可关闭；合成签名错误只提供关闭与重试，无绕过按钮；控制台 0 errors。浏览器测试未调用真实安装。
- 截图：`output/playwright/release-settings.png`、`release-update-dialog.png`、`release-signature-rejected.png`。
- 没有修改实际用户资料库，没有替换或卸载用户正在使用的应用；旧版安装包保留。

## 内部安装包

路径：`apps/desktop/src-tauri/target/release/bundle/nsis/拾微_0.3.0_x64-setup.exe`。

- 文件大小：408,963,542 字节（约 390 MiB）。
- SHA-256：`2F90E9FA496C32E6651278CD8D05E2524A4EB76CB13D8B56D602A666F4BA8B23`。
- Windows `Get-AuthenticodeSignature`：`NotSigned`。
- 本包未配置生产 Updater 公钥/endpoint，不附带正式 `.sig` 或 `latest.json`。它是内部构建，不可当成已发布的自动更新源。
- 正式工作流配置完成后才生成 `Shiwei_版本_x64-setup.exe`、官方 `.exe.sig`、`latest.json`、`SHA256SUMS.txt`，并校验全套资源后公开。

## 真实服务与验收缺口

1. 当前目录无 Git 仓库/remote，未创建或发布 GitHub Release；工作流的远端权限、runner 磁盘/时间预算和上传需在指定仓库首跑验证。CI 已关闭非必要 Rust debug symbols/incremental 缓存以降低大 Worker 旁的磁盘压力，不关闭断言和测试。
2. `release.config.json` 的 Updater endpoint/public key 为空，未生成正式生产密钥。当前内部包不是可自动更新的正式分发版。需维护者指定仓库并在受保护 environment 配置签名 Secrets。
3. 未连接真实 PostHog 项目，事件 payload 通过原生测试观察器验证，不代表 PostHog 已接收事件；真实 ingestion、隐私关闭验证、Dashboard 需配置后验收。
4. 未完成隔离 Windows VM 中 A→B 的下载、安装、重启与旧资产校验。正式发布前必做，步骤见发布手册。签名测试和 migration fixture 不能替代这项验证。
5. 未配置 Windows Authenticode 证书，内部安装包可能触发未知发布者/SmartScreen 提示。
6. 错误报告是安全、最小的 `app_error` + frames，不是完整符号化 PostHog Error Tracking/Sentry crash dump。强杀/断电/native access violation 可能没有报告，旧进程退出后的 OS 安装失败也不保证回传；不得据此宣称全量 Crash Rate。
7. 统计默认关闭且 best-effort/at-most-once，只观察同意且成功发送的安装实例；不等于真实人数，不补传关闭期间的首次召回。

## 文档与发布前配置

- [RELEASING.md](RELEASING.md)：本地与 CI 发布、完整资源、真实客户端验证、撤回/回滚。
- [RELEASE_SECRETS.md](RELEASE_SECRETS.md)：正式公私钥、密码、endpoint、公开 PostHog 参数、轮换与丢失处理。
- [PRIVACY_TELEMETRY.md](PRIVACY_TELEMETRY.md)：事件与字段、同意、数据边界、错误与统计限制。
- [WINDOWS_CODE_SIGNING.md](WINDOWS_CODE_SIGNING.md)：Authenticode 独立接入点。
- [ANALYTICS_DASHBOARD.md](ANALYTICS_DASHBOARD.md)：安装实例、漏斗、成功召回、留存、错误和更新指标口径。

生产私钥/密码只放 GitHub Secrets 与安全离线备份；不要通过聊天发送。公开仓库地址、Updater 公钥/endpoint 和 PostHog 公开项目参数确定后，才能进行线上验收。
