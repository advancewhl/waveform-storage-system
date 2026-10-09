# 波形存储当前状态（2026-10-09）

Production desktop frontend releases apply to both desktop-user-a and desktop-user-b user environments on AC-GT-CN-S2-OPI21. The shared frontend release is incomplete until both accounts are validated.

2026-10-09 现场桌面机公共 launcher 已激活 r6。desktop-user-a 的桌面入口、GUI、二进制哈希、SSH identity、隧道、ZMQ health 和只读历史查询通过；用户现场确认 desktop-user-a、desktop-user-b 两个账号均可使用 r6，并确认真实 HDF5 频谱功能验收通过。desktop-user-a 的技术验证有机器记录；desktop-user-b 使用情况和 HDF5 功能验收属于用户现场确认，没有对应的机器端输出。本次部署没有发送采集请求，采集服务器未修改。详见[现场桌面机 r6 发布记录](../交付/20261009-spectrum-ui-r6-desktop/README.md)。

2026-10-09 已构建并激活本机频谱 UI operator `20261009-spectrum-ui-r6`。`outputs/operator-local/current` 已切换至 r6，正式桌面 launcher 解析到 r6 二进制；offscreen 打包验收和完整性清单校验通过。

峰值频率按实际 `resolution_hz` 决定显示小数位，表格、图注和 `report.md` 共用同一格式化规则，原始峰值及 JSON/CSV 数值不变。高频模板使用 20 秒整体 Welch 窗口和 5 秒重复性分块；实际窗口与分辨率只显示在默认折叠的高级参数区域。

频谱 focused 35 项、历史 focused 6 项和全量质量门 225 项测试通过。Codex 未启动真实桌面 GUI；本机 r6 等待用户手动验证。激活前 `current` 指向 r5，保留已人工验证的 r3 作为回滚目标。

本机 r6 构建阶段没有更新现场 server、systemd、MongoDB 或 dashboard；现场桌面机后来单独部署 r6，详情见[现场桌面机 r6 发布记录](../交付/20261009-spectrum-ui-r6-desktop/README.md)。本次源码保存与远端同步记录见 Git 提交历史。交付记录见[本机频谱 UI r6](../交付/20261009-spectrum-ui-r6/README.md)。

按 2026-10-08 发布记录，现场 acquisition server 使用 `20261008-history-release-r4`。当日的本机前端、server、真实桌面入口、systemd 实际进程和历史数据处理链路已按该版本对完成验证。本次未检查或修改现场 server。

2026-10-08 曾构建本机频谱 UI operator `20261008-spectrum-ui-r1`。源码清单、二进制校验和、offscreen 页面启动及本地 HDF5 分析均通过，但真实桌面入口未能发现对应进程或窗口，未通过桌面验收。随后已将本机 `current` 原子回滚至 `20261008-history-release-r4`；r1 release 保留但未激活。现场 acquisition server、systemd、现场 operator 均未修改。验收记录见[频谱 UI r1 回滚记录](../交付/20261008-spectrum-ui-r1/README.md)。

2026-10-09 已构建并激活本机频谱 UI operator `20261009-spectrum-ui-r2`。`outputs/operator-local/current` 已指向 r2；桌面入口与 launcher 解析路径已核实，Codex 未尝试打开 GUI。当前等待用户从桌面“波形存储”图标手动验证。现场 acquisition server、systemd、MongoDB、dashboard 和现场 operator 均未修改。构建与激活记录见[频谱 UI r2 交付记录](../交付/20261009-spectrum-ui-r2/README.md)。

2026-10-09 本机 operator 已更新为 `20261009-spectrum-ui-r3`，`current` 已原子切换至 r3。结果表默认隐藏“命中块”列，显示选项位于折叠的高级分析参数区域。历史频谱入口从当前任务的 `CaptureRunSummary.frequency_hz` 读取采样率；缺失时仍需填写，切换任务或数据段时清空旧值。完整质量门 216 项测试、release 清单校验和二进制 offscreen 页面检查均通过。同日，用户确认 r3 已完成人工桌面验证。现场 server、systemd、MongoDB、dashboard 和现场 operator 本轮均未修改。记录见[频谱 UI r3 交付记录](../交付/20261009-spectrum-ui-r3/README.md)。

同日短暂构建并激活 r4。打包验收发现，清理历史 metadata 时可能影响独立文件分析结果。r4 未供用户手动验证，随后由修正后的 r5 替代。r4 release 保留，但不作为回滚目标。

2026-10-09 本机 r6 已激活，仍等待用户对本机开发环境进行人工桌面验收。本段状态仅指本机开发环境；现场桌面机验收状态见下表和 r6 现场发布记录。

## 当前版本与部署

| 组件 | 当前 release | current 或部署位置 | 状态 | 验证日期 |
| --- | --- | --- | --- | --- |
| 本机 operator | `20261009-spectrum-ui-r6` | `outputs/operator-local/current → releases/20261009-spectrum-ui-r6/waveform-operator` | 已构建并激活；等待用户人工桌面验证 | 2026-10-09 |
| 现场 operator（desktop-user-a、desktop-user-b） | `waveform-operator-r6-20261009` | `/home/site-user/work/StreamDataPro_new/waveform-operator-r6-20261009` | 已激活并完成双账号验收；desktop-user-a 有机器记录，desktop-user-b 和真实 HDF5 频谱验收为用户现场确认 | 2026-10-09 |
| 现场 acquisition server | `20261008-history-release-r4` | `/home/storage-user/waveform-storage-v2/current → releases/20261008-history-release-r4` | packaged/current 模式，systemd active | 2026-10-08 |
| 日志 dashboard | `20261008-history-release-r4` | `/home/storage-user/waveform-storage-v2/dashboard/tools/` | HTML 和只读查询已更新，真实 HTTP/Mongo 验证通过 | 2026-10-08 |
| MongoDB | `mongo:8.0.29` | 现有 `waveform-mongodb` 容器 | running，采集服务已连接，历史查询正常 | 2026-10-08 |

旧窗口继续使用启动时加载的版本。desktop-user-a 从原桌面入口启动 r6 并保持运行。用户已确认 desktop-user-b 在其实际桌面会话可使用共享前端；本次未采集 desktop-user-b 的机器端运行输出。

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

2026-10-08 发布的 r4 版本对使用 `v2 + history-run-segment extension`。该 operator 需要同时支持下列命令的 server。前后端必须按版本对部署并验证，不能只更新源码或其中一端。

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

- 本机 operator 的回滚目标：`outputs/operator-local/releases/20261009-spectrum-ui-r3/waveform-operator/`。r4 保留，但不作为回滚目标。
- previous_server_release：`/home/storage-user/waveform-storage-v2/releases/20261008-history-release-r3/`。
- 日志页旧 HTML：`dashboard/release-backups/20261008-history-release-r4/`。
- 日志页旧查询脚本：`dashboard/release-backups/20261008-history-release-r2/`。

r6 本机开发环境的桌面手动验证若失败，可将本机 `current` 原子切回已人工验证的 r3。现场 acquisition server 最近记录为 r4；此前 r4 发布的服务端回滚目标为 r3。本次没有检查、切换或回滚 server。保留各本机 release。

## 架构与范围

原始数据保持“一次请求 → 多个 capture_sequence → 每段多个 BPM 文件”。历史处理一次选择一个段。不修改采集 interval、运行时核心循环或原始 HDF5 格式，不自动拼接不连续波形。

整任务分析留待后续。整任务频谱应先逐段计算，再按明确方法汇总。现有单段频谱功能继续使用当前对齐结果，日志页继续直接只读 MongoDB。

用户已确认本机 r3 的人工桌面验证通过。现场桌面机 r6 已于 2026-10-09 激活；现场 acquisition server 未修改，2026-10-08 的 server 验收记录保留为当日状态。本机 `current` 已切换至 r6；本次源码保存与远端同步记录见 Git 提交历史。

## 历史记录

- [2026-10-05 现场前端记录](deployment/20261005/前端更新记录-20261005.md)
- [独立日志前端说明](日志前端使用说明.md)
- [单段频谱分析说明](spectrum-analysis-usage.md)
- [目录整理与迁移说明](工作目录整理_2026-10-07.md)
- [7 月历史文档](history/2026-07/)
- [8 月历史交接](handoffs/)
