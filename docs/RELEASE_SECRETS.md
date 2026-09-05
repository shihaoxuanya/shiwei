# 发布凭据与公开配置

## 需要维护者配置的 GitHub Environment

创建受保护的 `release` environment：

| 名称 | 位置 | 含义 |
| --- | --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY` | Secret | 官方 Tauri 生成的完整私钥文本；CI 用内容，不用开发机路径 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Secret | 私钥密码；不得输出到日志 |
| `SHIWEI_RELEASE_REPOSITORY_TOKEN` | Secret | 仅允许写入公开 `shihaoxuanya/shiwei-releases` 仓库的发布凭据；Contents read/write，不能编入客户端 |
| `SHIWEI_RELEASE_PUBLISH_TOKEN` | Secret | CI 登记 artifact-ready 的专用令牌；不是管理员密码，也不是签名私钥 |
| `SHIWEI_UPDATER_PUBLIC_KEY` | Variable | 官方 `.pub` 文件文本，可以公开 |
| `SHIWEI_UPDATER_ENDPOINT` | Variable | 公开可访问的 HTTPS metadata URL |
| `SHIWEI_ANALYTICS_HOST` | Variable，可选 | PostHog 采集域名，例如 `https://us.i.posthog.com` 或 EU 对应域名 |
| `SHIWEI_ANALYTICS_KEY` | Variable，可选 | PostHog 公开项目 key `phc_…`，不是个人管理 token |
| `SHIWEI_WINDOWS_SIGN_COMMAND` | Variable，可选 | 证书服务签名命令模板，见 Windows 签名文档 |

`GITHUB_TOKEN` 由 Actions 注入，仅用于私有源码仓库；它不能默认写入另一个仓库。双仓库发布使用单独、最小权限的 `SHIWEI_RELEASE_REPOSITORY_TOKEN`，只在上传步骤注入。源码仓库为 `shihaoxuanya/shiwei`，安装包仓库为公开的 `shihaoxuanya/shiwei-releases`。没有真实 PostHog 项目时配置保持空白；不得使用别人的 key 或编造已接入状态。

公开默认配置集中在 `release.config.json`，构建环境变量优先。Analytics Host/项目 key 编译进入 Rust，只是公开采集参数。不要设置宽泛的前端环境变量透传；真正私钥不会进入 Vite bundle、Tauri JSON 或安装包。

构建脚本拒绝非 `phc_` 公开项目 key，避免把 PostHog 个人管理 token 或模型密钥误编入安装包。

## 生成与保存正式 Updater 密钥

在可信的离线/开发终端使用官方 CLI：

```powershell
pnpm --dir apps/desktop exec tauri signer generate -w C:\SecureOfflineBackup\Shiwei\updater.key
```

让 CLI 交互设置强密码；目录应由开发者明确选定，并受账户权限和加密离线备份保护，不在仓库、OneDrive 同步目录、共享盘或构建输出中。不要通过聊天粘贴私钥，也不要把 CLI 完整输出复制进 issue/日志。

- 私钥及密码分别安全保管，并做可恢复的离线备份。
- 将私钥内容与密码分别录入 GitHub environment Secrets。
- 公钥配置给客户端；在首个公开安装包发布前固定并核对。
- 本轮若缺少 GitHub 权限和正式离线保管位置，只完成签名链路、测试与此文档，不把临时测试密钥当作生产密钥。

## 轮换和丢失

旧客户端只信任内置公钥。正常轮换需要仍持有旧私钥：先用旧私钥签名发布一个内置新公钥的过渡版本，让用户安装后再用新私钥签后续版本。必须验证过渡期间旧用户的升级路径，不能直接替换公钥并假设所有旧客户端会接受。

固定 `latest.json` 不能同时照顾尚未安装过渡版本的旧公钥客户端。正式轮换时需保留旧信任链的过渡分发入口（或制定可信手动安装路径）；当前版本未实现多信任链自动迁移，不要直接将 stable 指向只有新密钥签名的包。

旧私钥丢失后，不能为信任该公钥的已安装客户端生成可信新更新；通常需要用户从可信分发渠道手动安装新信任链的版本。不要关闭签名验证解决这个问题。

`.gitignore` 排除 `.env`、私钥、PFX/P12、PEM 和 secrets 目录。`pnpm secrets:scan` 检查源代码/文档/配置中的常见令牌与私钥模式，仅报告文件，不打印匹配值。它是基础防线，不是所有形式秘密均不存在的证明；正式仓库还应开启 GitHub Secret Scanning / Push Protection。

官方依据：[Tauri 更新签名](https://v2.tauri.app/plugin/updater/#signing-updates)。
