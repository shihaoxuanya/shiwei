# 拾微 0.3.2：Windows 无控制台启动

## 范围与原因

2026-09-06 用户报告启动拾微附带黑色日志窗口，关闭该窗口会连同应用退出。

- `src-tauri/src/main.rs` 缺少 release 的 `windows_subsystem = "windows"`，旧二进制 PE Subsystem 为 3（控制台），不是 2（GUI）。
- `worker_client.rs` 的所有 Worker 启动分支共用 `Command::spawn`，但没有设置 Windows `CREATE_NO_WINDOW`。只修主程序仍可能由 Python/打包 Worker 弹出另一个控制台。
- 黑窗中的 `[shiwei-ai]` 是 Rust 转发的 Worker stderr，不是一个可以单独关闭的日志查看器。

## 实现

- 正式主程序使用 GUI 子系统；开发主程序仍保留终端调试能力。
- 统一 Worker 创建策略：Windows `CREATE_NO_WINDOW`，stdin/stdout/stderr 全部保留管道。打包 Worker、开发 Python 和 uv fallback 使用相同策略。
- 不改成 `pythonw` / PyInstaller `--windowed`，以免丢失 JSONL RPC 标准流。
- 不修改原文、检索、生成策略、生产服务或真实用户资料；不关闭用户的现有应用、终端。

## 验证

- 旧 0.3.1 主程序被新增 PE 子系统检查明确拒绝，复现问题。
- Rust 子进程探针实际检查 `GetConsoleWindow` 为空，同时验证三条标准流可读写。
- 打包桌面 smoke 在启动前读取实际 PE header；启动后由独立探针检查主程序和真实打包 Worker 均没有控制台窗口句柄，而非仅依赖 `WindowStyle Hidden`。Windows 的无窗口 Worker 可以保留 headless 控制台对象：AttachConsole 成功但 GetConsoleWindow 为 null；不能仅凭 AttachConsole 成功就误报黑窗。
- 新增隐私测试验证首次 production 安装默认 true、重启维持勾选、手动关闭后继续保持关闭；React 验证从原生状态加载勾选，不伪造 consent 写入。
- 自动测试全部通过：Node 发布 10、React 82、Python Worker 215、Rust 27、Control Plane 53，共 387 项；类型检查、版本一致性和秘密模式扫描通过。
- 实际 0.3.2 主程序＋新 0.3.2 打包 Worker 启动：GUI 子系统、主窗可响应、WebView 已启动、两进程均无控制台窗口；stdout/stderr 启动日志均为 0 字节。隔离装配目录：`output/qa-release/assembly-0.3.2-b6e4048fa689494c83f8b6d77627be91`。
- 控制台探针负对照：人为启动的隐藏 PowerShell 控制台仍被正确拒绝；不是仅检查可见性来放过旧问题。
- `smoke-answer-fidelity.py` 使用新 0.3.2 打包 Worker、合成资料和 loopback HTTPS 模型通过：数字区间上下文/SSE、发出前修正、引用分组、会议证据过滤、3 个 MongoDB 负例、笔记日期历史和原文不变；没有使用真实凭据或向真实模型发送简历。
- 新 Worker SHA256：`D01B678433427D9F90F9A0BCDBEFA84EDDEC28398022C55D9A2A59703713BB56`。

## 同轮校验提示诊断（没有改动回答校验）

“这次生成的数字或推断未能通过核对”来自 `chat/service.py`：生成 → `fidelity_issues` → 最多一次修正 → 再次校验失败（或空修正结果）→ 引用原文兜底。这不是 Note/PDF 保存失败，也不是“没有检索到资料”。

本地合成案例：

| 源/回答 | 当前检查结果 |
| --- | --- |
| 原文和回答都为 80+ 名用户、20 万+ 元、2025 年 11 月（仅空白不同） | 通过 |
| 原文 `本科就读时间2023.09—2027.06` → 回答 `本科就读时间2023年9月至2027年6月` | `unlabelled_derived_figure`，可复现等价年月格式的误拦 |
| 原文全量 38~50 天 → 回答 3850 天 | 正确拦截区间丢失 |

已确认现有校验主要依赖数字＋单位字面匹配，不是通用语义事实核对器，存在误拦边界。未保存用户截图那次被拦截的模型草稿，因此不能声称已定位到其中某一句，也没有为诊断再次向外部模型发送真实简历。此轮用户询问原因，未关闭或放宽事实保护；等价日期表达的修正仍是已知待处理项。

## 隐私默认值

0.3.1 起新 production 安装的基础统计已默认勾选，本轮继续保留并补测试。已有 false（包括旧版默认关闭）不自动改成 true；开发构建和浏览器预览不自动上报。没有把 UI 硬编码为 checked，也没有重置真实安装设置。打包 smoke 通过进程级 kill switch 关闭统计，避免 QA 污染线上指标。

## 参考

- [Rust Windows CommandExt::creation_flags](https://doc.rust-lang.org/std/os/windows/process/trait.CommandExt.html#tymethod.creation_flags)
- [Microsoft Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)
- [Microsoft AttachConsole](https://learn.microsoft.com/en-us/windows/console/attachconsole)

## 最终交付

- 安装包：`apps/desktop/src-tauri/target/release/bundle/nsis/拾微_0.3.2_x64-setup.exe`，408,968,773 字节。
- 安装包 SHA256：`42EF35A6DDE25CFE1EAE650232582313FE8E027079C2461AD30BC9DA670A8DC3`。
- Authenticode：`NotSigned`；没有发布在线更新、修改服务端或创建 GitHub Release。
- 用 7-Zip 从实际 NSIS 安装包提取两份 exe 并核对。Worker 与新构建逐字节相同；主程序除 Tauri 将 `__TAURI_BUNDLE_TYPE_VAR_UNK` 写为 `__TAURI_BUNDLE_TYPE_VAR_NSS` 的 3 字节安装器标记外逐字节相同，不是旧程序。包内主程序 SHA256：`582342DA7865767878FBDAF4EE015D022889CE8AA1F89A7F0A22D8DA9B91B933`。
- 从安装包提取出的实际主程序＋Worker，再配上打包时的 `_internal` 资源，隔离启动通过全部窗口／响应／WebView 检查；主程序和 Worker 均无控制台窗口。该验证未运行系统安装器、改写注册表或覆盖用户当前安装。
- 所有 QA 仅使用临时资料库；原有资料和 0.3.1 及更早安装包保留。

本地产物不等于已签名、已发布的在线更新。
