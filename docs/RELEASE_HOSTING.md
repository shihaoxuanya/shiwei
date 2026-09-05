# 已确认的发布托管配置

- 私有源码：`https://github.com/shihaoxuanya/shiwei`
- 公开安装包：`https://github.com/shihaoxuanya/shiwei-releases`
- 发布后台：`https://zhishimanghe.com`，域名专用于拾微，无旧网站需要保留。

## 边界

Actions 从私有源码仓库构建签名安装包，使用独立的受保护 CI 凭据上传公开安装包仓库。公开仓库不得接收源码、用户资料或密钥。其自动生成的源码压缩包仅涉及该空壳发布仓库，而非私有源码仓库。

公开资源采用严格列表：安装包、对应 `.sig`、SHA256SUMS。GitHub Release 不设置 latest，也不上传可绕过后台策略的静态 latest.json。公开可下载表示资源存储就绪，不等于客户端已收到更新。CI 调用后台 artifact-ready 后只能确认 READY，实际分发由管理员决定。

下载地址由 artifactRepository 派生，不使用 Actions 默认的 GITHUB_REPOSITORY（它指向私有源码）。客户端不携带 GitHub 凭据。

## 当前接入状态

双仓库配置、上传流程和 READY 通知已实现；发布脚本 10 项测试通过，全部本地回归合计 345 项通过。两个仓库的权限和私有/公开属性已核验，270 个生产源码文件已同步到私有仓库，远端树与本地暂存树完全一致。公开安装包仓库目前只有说明文档，没有测试包或源码。

GitHub 连接器目前已可读写仓库。本机 Git 存在 ghproxy URL 重写和直连 TLS 问题；未修改全局设置，已通过连接器同步并校验源码。首次 Actions 被 GitHub 账户付款/支出上限限制阻止启动，需要维护者处理 Billing & plans，不属于代码测试失败。

服务器 SSH 已登录，域名解析正确；后台已部署到 `https://zhishimanghe.com/admin`，真实 HTTPS 登录页与证书验证通过。管理员初始化、正式 Secrets 和 A→B 升级仍未完成，详见 `ACCEPTANCE-CONTROL-PLANE.md`；不以本地测试页面替代线上验收。

在后台与客户端动态更新接口完成联调前，不创建正式发布 tag。正式签名密钥、公钥和跨仓库发布权限不能用测试值替代。
