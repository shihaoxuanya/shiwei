# 拾微发布手册

拾微是 MIT 开源、本地优先应用，没有发布后台、遥测或自动更新客户端。

## 仓库与凭据

- 源码：shihaoxuanya/shiwei；安装包：shihaoxuanya/shiwei-releases。
- GitHub release 环境保留 TAURI_SIGNING_PRIVATE_KEY、TAURI_SIGNING_PRIVATE_KEY_PASSWORD 和 SHIWEI_RELEASE_REPOSITORY_TOKEN（仅安装包仓库写权限）。
- 公钥使用 release.config.json 或 SHIWEI_UPDATER_PUBLIC_KEY。该历史变量名用于安装包离线签名，不代表自动更新仍启用。
- 不需要后台发布令牌、统计配置或服务器。私钥不得提交。
- MIT 不取代第三方依赖许可。构建者对自行发行的二进制负责。

## 标准流程

1. pnpm run version <版本号>，更新 CHANGELOG.md。
2. 执行 pnpm version:check、pnpm secrets:scan、pnpm test 和 pnpm build。
3. 提交到 main，创建并推送 v<版本号> tag。
4. Release stable 工作流构建并签名，校验后发布 EXE、.sig 和 SHA256SUMS.txt。不会覆盖已有 Release，不访问旧后台。
5. 用户主动访问 GitHub 下载并安装；客户端不后台检查或自动安装。

离线校验工具为 Cargo example verify-update。Minisign 签名不等于 Windows Authenticode，未购买可信代码签名证书的安装包仍可能显示 SmartScreen 提示。
