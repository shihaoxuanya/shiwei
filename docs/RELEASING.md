# 拾微发布手册

v0.3 只增加发布控制面；知识库、笔记、原始文件、对话和索引仍在本地。当前实际发布/验收状态以 [ACCEPTANCE-0.3.0.md](ACCEPTANCE-0.3.0.md) 为准。

## 前置配置

1. 私有源码库为 `shihaoxuanya/shiwei`，公开安装包库为 `shihaoxuanya/shiwei-releases`；用户不需要 GitHub 登录即可下载。
2. 按 [RELEASE_SECRETS.md](RELEASE_SECRETS.md) 创建 GitHub `release` environment，并建议启用人工审批、限定受保护 tag 的访问权限。
3. 配置官方 Updater 公钥、私钥与 `https://zhishimanghe.com/api/v1/updates/check?current_version={{current_version}}&channel=stable`。不再使用 GitHub latest.json 分发入口，以免绕过发布后台。应用不携带 GitHub token。
4. release.config.json 已配置自有统计 Host，空 analyticsKey 表示第一方传输；服务器独立配置 SHIWEI_ANALYTICS_SALT，永不把盐或管理员令牌嵌入客户端。环境变量非空时可覆盖 Host；保留旧 PostHog 公开项目作为替换配置。新生产安装默认开启基础统计，已有关闭选择不变。没有商业 Windows 证书可以做内部测试，但不等于安装包已有 Authenticode 签名。

## 版本与本地验证

`VERSION` 是 App Release Version 唯一来源。运行 `npm run version 0.3.1`（或 `pnpm run version 0.3.1`）同步根/desktop/shared-types package.json、Tauri 配置、Cargo.toml/Cargo.lock、Worker pyproject/uv.lock、Worker `__version__` 和前端生成常量。当前 Worker 与 App 同版本，协议和数据库 schema 独立演进。

```powershell
npm run version 0.3.1
# 编辑 CHANGELOG.md，增加 ## 0.3.1 与面向普通用户的简短说明。
pnpm version:check
pnpm secrets:scan
pnpm test
pnpm test:signing
pnpm build
```

`test:signing` 用临时目录中的一次性测试密钥调用官方 Tauri signer，验证正常签名及篡改拒绝；不是生产签名密钥。测试会删除自己创建的临时密钥。

正式签名构建：在安全 shell 中设置签名环境变量，执行 `pnpm build:release`。它先验证版本与配置，生成仅含公开配置的 `output/release/tauri.release.json`，再打包 Worker、前端、Tauri NSIS 与官方 `.sig`。缺少私钥/公钥/endpoint 会失败，不伪装成已签名发布。

`pnpm package:worker` 加 `pnpm build:desktop` 可构建未配置更新服务的内部安装包。不得用它替代正式 release pipeline。旧 0.2.x 没有 Updater，首次进入 0.3.x 需要手动安装，保留同一应用 identifier 和数据目录。

## 自动发布

```powershell
git add <本次已审阅的源文件>
git commit -m "Release 0.3.1"
git tag v0.3.1
git push origin <当前分支>
git push origin v0.3.1
```

- `.github/workflows/ci.yml`：Push/PR 执行版本校验、秘密模式扫描、Node 发布测试、React/Python/Rust 测试、真实 Worker 打包、前端与 NSIS 构建、签名篡改测试及原生启动检查。
- `.github/workflows/release.yml`：tag 触发，等待 CI 成功，构建签名资源，用独立跨仓库凭据上传公开安装包库的 draft。只上传安装包、签名和 SHA256SUMS，核对名称与大小后公开资源但不设置 latest；随后向控制面登记 READY。管理员进入后台，5%→20%→50%→100% 逐步发布。GitHub 资源公开不等于客户端获准更新。
- 私钥只暴露给受保护发布任务的签名步骤，不传入 PR 构建或服务器。源码库 GITHUB_TOKEN 只读；跨仓库 token 只用于上传资源。Secrets 缺失时失败，不使用临时测试值替代。

发布资源：

```text
Shiwei_0.3.1_x64-setup.exe
Shiwei_0.3.1_x64-setup.exe.sig
SHA256SUMS.txt
```

中文产品名保持“拾微”；标准分发文件使用 `Shiwei_版本_x64-setup.exe`。本地 staging 的 latest.json 仅作为构建元数据，不上传公开资源库。客户端从控制面接收官方动态 manifest，内含 `.sig` 内容。SHA256 不能代替 Updater 签名。

发布前运行 `verify-update`，验证安装包与客户端公钥匹配。当前资源 Provider 严格限定公开 GitHub 仓库；将来使用其他 CDN 需要新增经验证的 Storage Provider，不能直接放宽 URL 白名单。稳定版不接收 prerelease、不自动降级；beta 仅预留 channel 概念。

## 客户端更新验收

使用隔离 Windows 测试账户或 VM 和合成资料库，不对真实用户资料库做安装实验：

1. 安装使用测试公钥和独立 HTTPS endpoint 的版本 A（0.3.0）。创建笔记、导入文件、保存有引用的对话并建立索引。
2. 在独立测试分发源提供更高稳定 SemVer 的版本 B（例如 0.3.1），签名必须与 A 公钥匹配，不发布到正式 stable endpoint。
3. 检查启动异步发现、版本与更新说明；选择“稍后”应继续工作，不自动安装。
4. 点击“立即更新”，验证笔记保存、下载百分比/未知总量状态、签名验证、安装与 NSIS 自动重启。安装前 Worker 必须空闲；尚有任务时停止更新而不是强制杀任务。
5. 重启后核对 App/Worker 版本，原始文件哈希、笔记正文、对话与引用、索引仍在。只有新进程版本符合本地 pending target 才记录 `update_completed`。
6. 测试离线、坏 metadata、缺失 artifact、中断下载、签名不符与安装启动失败。签名不符永远无绕过按钮。

当前 unit/mock 测试或 signer 测试通过，不等于完成真实 A→B 安装升级；两者须分别记入验收记录。Windows 官方安装程序启动之后的 OS/UAC/安装失败不都能回传给已退出旧进程；若下次仍运行旧版，不报告更新完成。

## 回滚与停止分发

- 严重问题在后台暂停或撤销当前版本。回滚使用专门的“回滚发布目标”操作，不能通过 GitHub latest 标记绕过后台。详见 [ROLLBACK.md](ROLLBACK.md) 和 [CONTROL_PLANE_RELEASES.md](CONTROL_PLANE_RELEASES.md)。
- 已安装较新版本的客户端不会被强制降级。优先发布版本号更高的修复版本，而不是让旧代码打开未知的新 schema。
- v0.3.0 不新增知识库 schema；带版本的旧数据库升级测试检查资产保留。将来每项 migration 都必须有失败回滚、旧资料保留和重复启动测试；不得先发布不可逆且破坏数据的迁移。
- 真正手动降级前关闭应用，并备份完整资料目录与配置；SQLite WAL 活跃时不要只复制主 db 文件。旧程序必须能理解当前 schema，否则应使用升级前备份或前向修复版本，不修改 migration marker 伪装兼容。
- Updater 签名不保证内容本身无 bug，正式公开前仍需隔离升级验收、分批邀请测试与证书配置。

官方依据：[Tauri Updater](https://v2.tauri.app/plugin/updater/)、[GitHub 发布流程](https://v2.tauri.app/distribute/pipelines/github/)。
