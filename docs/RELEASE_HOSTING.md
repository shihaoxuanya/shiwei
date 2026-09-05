# 已确认的发布托管配置

- 私有源码：`https://github.com/shihaoxuanya/shiwei`
- 公开安装包：`https://github.com/shihaoxuanya/shiwei-releases`
- 发布后台：`https://zhishimanghe.com`，域名专用于拾微，无旧网站需要保留。

## 边界

Actions 从私有源码仓库构建签名安装包，使用独立的受保护 CI 凭据上传公开安装包仓库。公开仓库不得接收源码、用户资料或密钥。其自动生成的源码压缩包仅涉及该空壳发布仓库，而非私有源码仓库。

公开资源采用严格列表：安装包、对应 `.sig`、SHA256SUMS。GitHub Release 不设置 latest，也不上传可绕过后台策略的静态 latest.json。公开可下载表示资源存储就绪，不等于客户端已收到更新。CI 调用后台 artifact-ready 后只能确认 READY，实际分发由管理员决定。

下载地址由 artifactRepository 派生，不使用 Actions 默认的 GITHUB_REPOSITORY（它指向私有源码）。客户端不携带 GitHub 凭据。

## 当前接入状态

本地已修改双仓库配置、上传流程和 READY 通知脚本；发布脚本 10 项测试通过。尚未完成线上权限核验、推送、后台部署和真实 A→B 升级。

本次读取 GitHub 连接器没有返回这两个仓库。本机 Git 访问被已有 ghproxy URL 重写转往失效入口；未修改全局设置。仓库授权需要在正式推送前确认，不代表仓库不存在。

在后台与客户端动态更新接口完成联调前，不创建正式发布 tag。正式签名密钥、公钥和跨仓库发布权限不能用测试值替代。
