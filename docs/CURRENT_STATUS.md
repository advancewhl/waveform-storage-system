# 波形存储当前状态（2026-10-08）

本机前端与现场采集服务已发布 `20261008-history-release-r4` 版本对。源码、二进制、current、真实桌面入口、systemd 实际进程和历史数据处理链路已验证。构建来自工作目录，标记 `source_state=working-tree`。

## 当前版本与部署

| 组件 | 当前 release | current 或部署位置 | 状态 | 验证日期 |
| --- | --- | --- | --- | --- |
| 本机 operator | `20261008-history-release-r4` | `outputs/operator-local/current → releases/20261008-history-release-r4/waveform-operator` | 新入口和真实桌面 GUI 验收通过 | 2026-10-08 |
| 现场 operator（桌面机） | 最近记录为 2026-10-05 | `waveform-operator-direct-offline-20261004` | 本次未部署新前端；旧现场窗口不作为本次验收证据 | 最近记录 2026-10-05 |
| 现场 acquisition server | `20261008-history-release-r4` | `/home/storage-user/waveform-storage-v2/current → releases/20261008-history-release-r4` | packaged/current 模式，systemd active | 2026-10-08 |
| 日志 dashboard | `20261008-history-release-r4` | `/home/storage-user/waveform-storage-v2/dashboard/tools/` | HTML 和只读查询已更新，真实 HTTP/Mongo 验证通过 | 2026-10-08 |
| MongoDB | `mongo:8.0.29` | 现有 `waveform-mongodb` 容器 | running，采集服务已连接，历史查询正常 | 2026-10-08 |

已打开的旧窗口继续使用旧代码。重新打开真实桌面图标后加载当前 release。验收窗口按计划退出；没有关闭用户既有窗口。

## 当前入口

| 工作 | 入口 |
| --- | --- |
| 本机正式桌面前端 | `/home/whloong/桌面/波形存储.desktop → tools/launch_operator_desktop.sh → current` |
| 前端源码入口 | `apps/operator_gui/main.py` |
| 采集服务 | `waveform-acquisition-v2.service` |
| 服务 ExecStart | `/home/storage-user/waveform-storage-v2/current/waveform-acquisition-v2/waveform-acquisition-v2` |
| 日志页 | `http://192.0.2.2:8765/` |
| 只读元数据查询 | `tools/query_waveform_mongodb.py` |
| 正式交付 | [20261008-history-release-r4](../交付/20261008-history-release-r4/README.md) |
| 固定发布规范 | [RELEASE_CHECKLIST](RELEASE_CHECKLIST.md) |

## 版本对与验收证据

协议为 `v2 + history-run-segment extension`。此 operator 需要同时支持下列命令的 server。前后端必须按版本对部署并验证，不能只更新源码或其中一端。

- `list_capture_runs`
- `list_capture_segments`
- `find_waveform_files_by_segment`

两端 BUILD-INFO 包含源码 revision、工作目录状态、源码清单哈希、二进制 SHA-256、Python 和关键依赖版本、协议能力。完整版本对见[release-pair.json](../交付/20261008-history-release-r4/release-pair.json)。

2026-10-06 的真实协议查询返回 3 个有数据任务、87 个数据段和 1740 个精确匹配文件。旧 list_capture_times 接口仍工作。日志页列出该日创建的全部 5 个任务，其中 2 个没有波形文件；这与历史页的 3 个可用任务口径不同。

真实桌面验收加载“1000 Hz｜10 分钟｜21 段”的任务，完成切段、BPM 选择、20000 点对齐、原始下载和 V2 导出。实际使用完整 request_id 与真实 capture_sequence，查询和导出身份一致。验收未发送采集请求。

现场所选任务没有部分成功段。整链路 integration fixture 验证了请求 2 台、实际 1 台、sequence 0/3 跳号，并经真实 ZMQ、正式 backend 和 GUI 完成对齐、下载、导出。

服务 MainPID 为 1129649，实际进程路径与 r4 current 一致。SSH 主进程 PID 为 145015，监听本地 127.0.0.1:5001 并转发至远端 127.0.0.1:5001。启动日志确认 47 台设备全部启用、MongoDB connected，未发现启动错误。PID 是本次验收快照，重启后会变化。

完整证据位于交付目录的 [evidence/](../交付/20261008-history-release-r4/evidence/)。构建前质量门通过 205 项测试；补齐部分成功段整链路测试后，最终质量门通过 206 项测试。

## 回滚版本

- previous_operator_release：`outputs/operator-local/releases/20261008-history-release-r3/waveform-operator/`。
- previous_server_release：`/home/storage-user/waveform-storage-v2/releases/20261008-history-release-r3/`。
- 日志页旧 HTML：`dashboard/release-backups/20261008-history-release-r4/`。
- 日志页旧查询脚本：`dashboard/release-backups/20261008-history-release-r2/`。

r3 已通过真实桌面与服务验收。前端原子切回 previous_operator_release；服务端切回 previous_server_release，重启服务并重新验证 active 和 health。保留新旧 release。

## 架构与范围

原始数据保持“一次请求 → 多个 capture_sequence → 每段多个 BPM 文件”。历史处理一次选择一个段。不修改采集 interval、运行时核心循环或原始 HDF5 格式，不自动拼接不连续波形。

整任务分析留待后续。整任务频谱应先逐段计算，再按明确方法汇总。现有单段频谱功能继续使用当前对齐结果，日志页继续直接只读 MongoDB。

本次本机、服务端及日志页发布范围内，未解决问题为 None。现场桌面机新前端尚未部署，这一状态已在表中明确标注。

## 历史记录

- [2026-10-05 现场前端记录](deployment/20261005/前端更新记录-20261005.md)
- [独立日志前端说明](日志前端使用说明.md)
- [单段频谱分析说明](spectrum-analysis-usage.md)
- [目录整理与迁移说明](工作目录整理_2026-10-07.md)
- [7 月历史文档](history/2026-07/)
- [8 月历史交接](handoffs/)
