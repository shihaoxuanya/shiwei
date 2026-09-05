# 拾微版本控制面

## 职责与状态

私有 `shihaoxuanya/shiwei` 仓库中的 Actions 负责测试、Windows 构建、官方签名和上传。公开 `shihaoxuanya/shiwei-releases` 只存安装包与校验资源。`https://zhishimanghe.com/admin` 的独立 FastAPI 服务决定分发策略；Tauri 官方 Updater 负责检查、下载、验证和安装。

正常流程为 `DRAFT → READY → ROLLOUT → PUBLISHED`。READY 只有在后台流式读取公开安装包、校验大小、MZ、SHA256、官方 minisign 签名与公钥后才能获得。签名在后台只读，不能编辑或重新签名。

ROLLOUT / PUBLISHED 可以 PAUSED，PAUSED 可以恢复原比例或扩大范围。READY / ROLLOUT / PUBLISHED / PAUSED 可以 REVOKED；REVOKED 不可恢复，CI 重试也不能重新开启。DRAFT 不可直接发布。普通发布拒绝降低曾发布的最高稳定 SemVer。

操作要求登录会话、同源 Origin、CSRF、当前 revision、手动输入正确版本号和非空原因。过期页面提交返回 409。所有状态变化与审计写入同一个 SQLite 事务。

## API

- `POST /api/auth/login`：管理员邮箱和密码，Argon2id 验证，限流。无公开注册。
- `/api/admin/releases`：已登录管理员创建 DRAFT、查看版本。
- `/api/admin/releases/{version}/actions`：服务端状态校验及事务审计。
- `POST /internal/releases/artifact-ready`：专用 Bearer token；服务器只存令牌 SHA256。接口不接受文件、私钥或任意配置。
- `GET /api/v1/updates/check?current_version={{current_version}}&channel=stable`：安装 UUID 放在 `X-Installation-Id`。

无更新返回 HTTP 204，有更新返回官方动态 manifest 的 `version, notes, pub_date, url, signature`，附加 `mandatory, sha256`。保持官方 Updater 兼容，未另外实现不安全的安装协议。

## 灰度与撤回

使用持久化随机 UUID v4，计算 `SHA256(canonical_uuid + ':' + target_version)` 前 8 字节大端整数 `% 100`。bucket 小于 rollout_percentage 时入组。5→20→50→100 只扩大不缩小；同一版本重复检查结果稳定。

只检查 Stable 当前 target，不为灰度外安装实例随机选择历史版本。PAUSED / REVOKED 返回 204，API 使用 no-store 防止暂停后缓存继续分发。客户端启动后只自动检查一次，设置内可手动检查。下载前再校验当前目标及签名，已暂停、撤销或变更则取消；开始下载后仍必须验证官方签名。

`minimum_supported_version` 以下的客户端得到必要更新提示（仍遵守灰度）；`maximum_supported_version` 限定可更新的旧版上界。比较使用 SemVer 库，不比较字符串。不自动降级。

必要更新需要用户明确点击安装，安装前保存笔记。离线、签名失败、撤回或无法保存时不安装，失败状态允许回到本地资料；不持久化服务器下发的锁定指令，防止服务不可用时永久锁死个人知识库。

## 安全与隐私

- 正式 Updater 私钥只应存在维护者安全离线备份和受保护 GitHub Actions Secrets，禁止上传服务器。当前没有创建生产私钥或配置真实签名 Secrets。
- 后台会话为随机 opaque token，数据库只存 token 哈希；Cookie 使用 HttpOnly / Secure / SameSite=Strict，8 小时过期，新登录撤销旧会话。密码不裁剪空白、不进入日志；重置后撤销会话。
- 三类 API 的鉴权互相独立。HTTPS、Host 校验、32KB 请求上限、单进程基础限流和严格 CSP。生产默认关闭 OpenAPI；部署使用一个进程以保持限流一致。
- GitHub 资源地址严格限定仓库、版本、文件名，重定向只允许 GitHub 安装包 CDN，流式校验有总时限；不接受任意 URL。
- 安装标识用于功能性分组，不是硬件指纹；不写入服务器数据库/审计，不随安装包请求发送到 GitHub，也不因为检查更新而打开统计授权。
- Analytics 未配置时显示暂无数据；配置后可跳转已有 PostHog 面板，不复制知识内容或伪造安装占比。
- 管理后台数据库独立于个人知识库。用户笔记、文件名、问题、模型凭据不进入本服务。

## 部署与初始化

`services/control-plane/deploy` 包含专用用户 systemd 服务、Caddy HTTPS 站点和公开配置示例。数据库在 `/var/lib/shiwei-control`；Python 运行时与源码分别在 `/opt/shiwei-control/venv` 和 `/opt/shiwei-control/current`，仅回环端口 8920，反代仅信任 127.0.0.1。Caddy 和 Uvicorn 不开启请求访问日志。

先只读检查现有服务，再合并站点，不能覆盖未知配置。配置文件使用 0600。管理员通过服务器本地 `python -m shiwei_control.manage create-admin --email ...` 安全输入密码，不经前端源码初始化。

回归测试使用临时 SQLite 和合成资源；`scripts/qa-control-plane.py` 明确是回环隔离界面 fixture，不得部署生产。正式部署与真实 A→B 升级状态以验收记录为准。
