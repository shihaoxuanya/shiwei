# 0.3.4 Windows 安装包验收

2026-09-10，本地构建，代码提交 `bf62bb50e620e8469ea7b8dcd2cb5a340f9b88ed`。

- 安装包：`output/release/artifacts/Shiwei_0.3.4_x64-setup.exe`
- 大小：409075799 字节。
- SHA-256：`1786f776cda6b27d7a9012233aa5aa73677c1aa0410f2454a92cf3118f3de08d`
- 沿用既有正式 Updater 密钥，独立公钥验签通过。密码仅从当前 Windows 用户 DPAPI 解密到签名进程环境，结束后清理；未提交私钥或 API Key。
- Python 327 通过、1 跳过；前端 155、Rust 56、发布流程 15 项通过。
- 打包 Worker：文本 PDF、扫描 PDF 离线 OCR、页码引用、去重通过；5 个模糊召回与 8 个负例拒答通过。
- 打包 Worker：迁移、校验切换、重启、新库写入、原库保留等 10 阶段通过。
- 最终 EXE 解出的 Worker 与本次构建哈希一致；解出的桌面程序实际启动响应正常，显示版本 0.3.4，主程序和 Worker 无可见控制台，退出清理通过。
- 最终 EXE 解出的 Worker 通过本地 TLS 网络取消测试：故意不返回响应头，取消约 0.047 秒，连接关闭，被取消对话未保存，后续 3 个独立算式变体各仅调用一次流式生成。模拟回答只验证路由，不代表真实模型数学准确率。
- TLS 验证没有关闭：测试仅在子进程信任临时生成的回环服务证书，不改系统信任或正式更新验签。运行命令：`uv run --python services/ai-worker/.venv/Scripts/python.exe --with cryptography --no-project python scripts/smoke-network-worker.py --worker <打包Worker路径>`。

## 分发边界

Windows x64 手动安装包。没有 Windows Authenticode 商业证书，可能提示未知发布者；Updater 签名不等同于 Windows 代码签名。

旧版 0.3.3 保留于 `output/release/artifacts-0.3.3-retained-20260910`。没有覆盖既有安装，没有访问真实资料库，没有执行真实 A→B 自动升级或向服务器登记正式更新。安装包未上传公开 GitHub Release；源码已上传私有库。测试使用解包后的应用与隔离目录，不等同于运行安装器写入用户系统。
