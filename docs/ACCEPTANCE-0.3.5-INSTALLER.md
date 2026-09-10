# 0.3.5 Windows 安装包验收

2026-09-10，本地正式构建。源码提交：`e8083dbd67d16585e866d2b3cc5a362546cbc876`（私有仓库 main），上传后的 Git tree 与本地审查快照完全一致。未推版本 tag、未创建公开 Release、未向服务器登记或启动更新分发。

## 安装包

- 文件：`output/release/artifacts/Shiwei_0.3.5_x64-setup.exe`
- 大小：409094777 字节（约 390 MiB）。
- SHA-256：`66eb9f73d459b2d362b88b8cb4c0a65bff75c2440c3cb8bbd22bd32bd8442bed`
- 使用现有正式 Tauri Updater 密钥，生成 `.sig`；按客户端正式公钥独立验签通过，修改安装包一个字节后验签拒绝。
- 签名密码仅在构建子进程内从 Windows DPAPI 解密到环境，结束后清理；私钥及密码不进入源码、安装包配置或 GitHub 提交。
- 使用正式 `com.shiwei.desktop` 标识及 HTTPS 更新配置，不是上一轮独立应用标识的 QA 构建。没有 Windows Authenticode 商业证书，可能显示未知发布者；Updater 签名不等同于 Windows 代码签名。

## 检查结果

- 本轮重新运行：前端 164、Python 420、Rust 60、发布配置 15 项通过；Python 1 项因现有 Windows symlink 权限跳过。TypeScript 类型检查与生产构建通过。
- 最终 EXE 解包目录：`output/qa-release/extracted-035-20260910/`。解出 Worker 与本次构建 SHA-256 相同：`b61b0708f2a43fc62264ac585aef85830c66f540bfde79ee4972af6db4a47962`。
- 对解出 Worker 实际验证 101MiB、精确 1GiB、301/3000 页 PDF 导入，页进度和末页引用正确；1GiB + 1 字节、3001 页拒绝。
- 网页真实 HTTPS 保存、去重、本地快照、重启、重建索引、检索问答引用、历史、整库迁移及删除等 17 阶段通过，旧文件与笔记保留。
- 文本 PDF、扫描 PDF 本地 OCR、引用和去重通过；会议模糊召回 5 项、负例拒答 8 项、追问和历史通过。使用合成资料，不调用真实模型。
- 最终 EXE 解出的桌面程序实际启动，产品／文件版本均为 0.3.5，界面响应正常，新链接入口和 1GB／3000 页说明可见。主程序 PE 为 GUI 子系统，主程序和实际 Worker 均无可见控制台。
- 原生启动使用新建测试资料目录，确认该目录创建 SQLite，未读取真实资料库；正式 App ID 仍可读取该 Windows 账户既有模型配置，因此不宣称配置完全隔离。未测试模型、未修改模型设置，生产统计在测试进程关闭。测试窗口保留供查看，本轮未单独再次验证其正常退出。

报告：`output/qa-release/035-large.json`、`output/qa-release/035-web/result.json`、`output/qa-release/native-035-20260910/console-probe.json`、`output/release/verification-035.json`。

## 范围与保留

打包 Worker 的重启后流程只执行本地操作，未强制整机断网。大文件使用合成有效文档，不代表任意复杂 1GB 文件都有同等解析速度。本轮未运行安装器写入用户系统，未执行真实 A→B 自动升级。

旧 0.3.4 完整保留于 `output/release/artifacts-0.3.4-retained-20260910/`，其 SHA-256 仍为 `1786f776cda6b27d7a9012233aa5aa73677c1aa0410f2454a92cf3118f3de08d`。旧安装和用户资料未被覆盖。生成的本地 `latest.json` 不是已经上线的更新地址。
