# 2026-10-09 用户现场验收记录

## 用户确认

2026-10-09，用户确认已完成现场验收，并报告：

- `desktop-user-a` 和 `desktop-user-b` 均可使用已激活的共享 r6 前端。
- 用户已完成真实 HDF5 频谱功能现场验收，结果通过。

## 证据分类

以上为用户现场确认，不是本任务采集的机器端 telemetry。现存机器记录覆盖 desktop-user-a 的启动、二进制哈希、SSH 隧道、ZMQ health 和只读历史查询；本次没有采集 desktop-user-b 的终端输出、进程哈希、实时公钥指纹或查询响应。

历史记录中的 desktop-user-b 公钥指纹为 `SHA256:7ntUrhAwEqqDzQg+S1JftSLZ0mnOL7yqryJZT7uZbeM`。本任务没有读取或暴露私钥内容，也没有修改 desktop-user-b identity。

自动化部署前没有向 desktop-user-a 导出或复制生产 HDF5。本记录不包含 HDF5 路径、文件内容或频谱数值。部署过程中没有发送采集请求；采集请求数为 `0`，acquisition server 未修改。
