# Oracle 19c 恢复测试记录

2026 年 8 月 25 日进行了 Oracle 19c 数据库恢复测试。

恢复完成后发现 PDB 仍然处于 `MOUNTED` 状态，业务连接提示数据库不可用。检查 CDB 与 PDB 状态后，确认数据文件恢复正常，只是子库没有自动打开。

最终处理步骤：

```sql
SHOW PDBS;
ALTER PLUGGABLE DATABASE OPEN;
ALTER PLUGGABLE DATABASE ALL SAVE STATE;
```

执行后 PDB 状态变为 `READ WRITE`，业务连接恢复。复盘结论是恢复完成后必须检查每个 PDB 的打开状态，不能只根据 RMAN 成功信息判断业务已经可用。
