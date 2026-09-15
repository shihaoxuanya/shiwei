# 发布凭据与公开配置

## 需要维护者配置的 GitHub Environment

创建受保护的 `release` environment：

| 名称 | 位置 | 含义 |
| --- | --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY` | Secret | 官方 Tauri 生成的完整私钥文本；CI 用内容，不用开发机路径 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Secret | 私钥密码；不得输出到日志 |
| `SHIWEI_RELEASE_REPOSITORY_TOKEN` | Secret | 仅允许写入公开 `shihaoxuanya/shiwei-releases` 仓库的发布凭据；Contents read/write，不能编入客户端 |
| `SHIWEI_UPDATER_PUBLIC_KEY` | Variable | 官方 `.pub` 文件文本，可以公开 |
| `SHIWEI_WINDOWS_SIGN_COMMAND` | Variable，可选 | 证书服务签名命令模板，见 Windows 签名文档 |




## 生成与保存正式 Updater 密钥

在可信的离线/开发终端使用官方 CLI：

```powershell
pnpm --dir apps/desktop exec tauri signer generate -w C:\SecureOfflineBackup\Shiwei\updater.key
```

让 CLI 交互设置强密码；目录应由开发者明确选定，并受账户权限和加密离线备份保护，不在仓库、OneDrive 同步目录、共享盘或构建输出中。不要通过聊天粘贴私钥，也不要把 CLI 完整输出复制进 issue/日志。

- 私钥及密码分别安全保管，并做可恢复的离线备份。
- 将私钥内容与密码分别录入 GitHub environment Secrets。
- 公钥配置给离线验签工具；在首个公开安装包发布前固定并核对。
- 本轮若缺少 GitHub 权限和正式离线保管位置，只完成签名链路、测试与此文档，不把临时测试密钥当作生产密钥。

## 轮换和丢失

保留原签名密钥用于安装包离线校验；不再提供客户端自动更新。轮换时公开新公钥与可核对的变更说明，用户通过可信渠道手动下载；不得关闭验签来冒充原密钥签名。

`.gitignore` 排除 `.env`、私钥、PFX/P12、PEM 和 secrets 目录。`pnpm secrets:scan` 检查源代码/文档/配置中的常见令牌与私钥模式，仅报告文件，不打印匹配值。它是基础防线，不是所有形式秘密均不存在的证明；正式仓库还应开启 GitHub Secret Scanning / Push Protection。

官方依据：[Tauri 更新签名](https://v2.tauri.app/plugin/updater/#signing-updates)。
