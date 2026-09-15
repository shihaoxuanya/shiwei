# 发布托管

- 公开源码： https://github.com/shihaoxuanya/shiwei ，MIT。
- 公开安装包： https://github.com/shihaoxuanya/shiwei-releases 。
- 原管理后台已退役；不再提供版本登记、分发策略或统计 API。

CI 通过受保护的跨仓库凭据上传安装包、签名与校验值。客户端不携带 GitHub 凭据，不查询最新版本；下载由用户主动发起。

源码仓库不得包含私钥、真实用户资料或本机配置。安装包仓库仍仅包含发布产物。历史提交已经过启发式敏感信息扫描；扫描不构成不存在所有秘密的绝对保证。
