# 拾微 0.3.2 Mac ARM64 内测：实施与验收状态

日期：2026-09-06。目标测试机器：M1 Pro，macOS 14+。

## 已实现

- 保留当前 0.3.2 的 Windows 无控制台修复与隐私默认行为；不修改检索、生成、笔记正文或数据库。
- Worker 按平台解析文件名及开发 Python 路径；正式版只运行相邻的打包 Worker，缺失时不回退到 uv 或要求安装 Python。
- 独立 `tauri.macos.conf.json`：ARM64 App/DMG、最低 14.0、Ad-hoc 临时签名，无 Apple 公证；禁用内测包自动更新配置。
- Mac Worker 独立 staging，`Contents/MacOS/shiwei-ai-worker` 配合相邻 `_internal`；校验 Mach-O 架构、最低系统版本、动态库绝对路径、符号链接及签名。
- 沿用系统钥匙串，只增加 Mac 对应错误文案和可丢弃的测试凭据；不携带现有用户的资料或密钥。
- 手动 `Mac Apple Silicon test build` 工作流：macos-14 原生构建、回归、实际 DMG 挂载与复制后启动检查，再生成校验值；单独的受保护发布任务只上传允许的安装包、校验文件及内测说明。
- Mac pre-release 使用独立 tag，不设为 Latest，不注册 Windows 稳定更新，不覆盖旧产物。

## 本机实际通过（Windows）

| 检查 | 结果 |
| --- | --- |
| 发布与 Mac 打包约束 | 15 项通过 |
| React 前端 | 82 项通过 |
| Python Worker | 224 项通过 |
| Rust（含 Windows 无窗口与跨平台路径回归） | 31 项通过 |
| Control Plane | 53 项通过 |
| 合计 | 405 项通过 |
| TypeScript 检查、Vite 生产构建 | 通过 |
| 版本一致性、秘密模式扫描、git diff 空白检查 | 通过 |
| Mac Workflow YAML 与所有 Bash run 步骤语法 | 通过 |
| Python 打包校验/安装测试脚本语法 | 通过 |

实际重新编译并启动 Windows 0.3.2 主程序，配合 0.3.2 打包 Worker：GUI 子系统、主窗响应、WebView 启动、无控制台窗口，启动 stdout/stderr 均为 0 字节。隔离路径：`output/qa-release/mac-compat-windows-932c8dc81c6a4ac599f4a9c18eb941f5`。此检查没有修改真实资料库。

既有 Windows 打包 Worker 的 loopback HTTPS/SSE 合成回答保真 smoke 再次通过：数字范围、推断修正、引用分组、会议证据过滤、三个 MongoDB 负例、笔记日期历史和原文不变；不使用真实模型密钥。

新增自动回归包括：4 项 Rust 跨平台路径/发布版禁止开发回退测试，5 项 Node 打包边界测试，9 项 Python Mach-O 校验逻辑测试。Mac 钥匙串专项测试仅在 macOS 执行，未计入上述本机通过数。

## 未通过验收的部分与外部阻塞

浏览器实际访问私有库 Actions 页面，GitHub 明确显示：

> Your account's billing is currently locked. Please update your payment information.

因此无法分配 macOS runner；最近已有运行均为 `startup_failure`。未修改 GitHub 计费、未购买资源、未使用公开库托管私有源码以规避阻塞。

适配源码及工作流已推送私有仓库。首次适配提交 `4c0fdfc49c2a86df8ac773f7ef094fd8e94b20af` 触发的检查运行 `34041610530` 同样启动失败。浏览器已确认 `Mac Apple Silicon test build` 出现在工作流列表，但计费锁定状态下无法启动，Mac 工作流运行数仍为 0。

- **尚未生成 DMG，无 Mac 下载链接与文件校验值。**
- Mac 原生依赖安装、PyInstaller、Tauri 构建、Swift 窗口探针、最终 App 签名校验、Mac 钥匙串与 DMG smoke：代码已准备，尚未实际执行。
- 测试用户从浏览器下载后的 Gatekeeper 授权、Finder 定位、中文输入与来源侧栏操作：待真机验证。
- 若实际原生依赖或签名检查失败，构建必须失败并修复，不能跳过检查发布一个名义上的 Mac 包。

## 恢复构建后的交付条件

账户恢复 Actions 后，手动触发内测工作流，先保持 publish=false 完成构建与原生检查；确认通过后以同一源码提交再次运行并启用 publish，产物只进入安装包库 Pre-release。若受保护跨仓库凭据缺失，保留私有 CI 产物，不降级公开源码或嵌入凭据。

最终记录实际运行链接、提交 SHA、DMG SHA-256、测试 macOS 版本，以及尚未由测试用户确认的事项。测试说明见 `docs/MAC-TEST.md`。
