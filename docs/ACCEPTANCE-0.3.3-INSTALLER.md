# 0.3.3 Windows 安装包验收

2026-09-09 本地正式构建。使用既有生产 Updater 密钥；密码通过当前 Windows 用户 DPAPI 解密，仅传给签名进程，结束后清除环境变量。未更换密钥，未上传私钥。

- 安装包：`output/release/artifacts/Shiwei_0.3.3_x64-setup.exe`
- 大小：408891665 字节（约 390 MiB）。
- SHA-256：`595478939b85d4f61fabb182c2b747d7697a1ed8b2fbc31c6cc860343af2cf26`
- 配套 `.sig` 已生成，使用客户端正式公钥独立验证通过。
- 前端 155 项、发布脚本 15 项测试通过；Release 主程序编译通过。
- 打包 Worker 0.3.3：真实文本 PDF、扫描 PDF 离线 OCR、页码引用及重复导入通过。
- 打包 Worker：5 个模糊召回、8 个负例拒答、历史保存、笔记统一检索通过。
- 打包 Worker：资料、笔记、对话迁移、校验切换、重启、新库写入、原库保留等 10 阶段通过；无真实模型调用。
- 从最终 NSIS EXE 解出程序：Worker SHA-256 与本次构建一致；原生启动通过，产品版本 0.3.3，主程序与 Worker 无可见控制台；提取后的 Worker 再次通过 PDF/OCR 检查。

## 分发范围与限制

可以手动分发 EXE，适用于 Windows x64。Updater 签名不等于 Windows Authenticode 证书；本包没有商业 Windows 代码签名，系统可能提示未知发布者。

本次没有执行安装器写入用户系统，没有覆盖既有安装、操作真实资料库或完成真实 A→B 自动升级。上述启动测试使用从最终安装包提取的文件与隔离资料库。未上传 GitHub Release，未开启后台自动更新分发。不要将本地 staging 的 latest.json 当作已上线更新地址。
