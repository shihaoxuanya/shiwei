# Windows 安装包签名

**Authenticode 与 Tauri Updater 签名是两套机制。**

- Updater 签名：客户端验证更新内容来自其信任的发布密钥；本轮正式 release pipeline 必需。
- Authenticode：Windows 验证软件发布者，改善安装时的身份提示；需要受信任的证书或签名服务。

没有商业证书时，允许未做 Authenticode 的内部/MVP 安装包，不阻塞开发，但应明确标识“内部测试、未签名发布者”，不要承诺没有 SmartScreen 提示。正式公开发布前应配置代码签名，并做干净 Windows VM 安装验收。

Release Workflow 已预留签名服务客户端安装位置，`SHIWEI_WINDOWS_SIGN_COMMAND` 传给 Tauri `bundle.windows.signCommand`。按照所选证书提供方配置签名工具和 `%1` 目标文件占位符，使用 SHA-256 与受信任时间戳。签名命令的证书凭据必须经 environment Secret 或服务端/HSM 获取，不放进公开 Variable 命令文本。

这只是可配置接入点，不代表证书已购买、签名服务已开通。选择 Azure Trusted Signing、HSM 或其他服务后，再添加与其对应的最小认证步骤；不要自行上传 PFX 到源码。

正式验收至少核对：应用/安装包签名状态、证书主体、签名链与时间戳、Worker 打包后能启动；用 PowerShell `Get-AuthenticodeSignature -LiteralPath <安装包>` 检查结果。对最终经 Authenticode 处理后的安装包生成 Updater 签名；签完 Updater 后再改文件会导致验证失败。

官方依据：[Tauri Windows 签名](https://v2.tauri.app/distribute/sign/windows/)。
