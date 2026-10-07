# 波形存储阶段状态（2026-10-07）

用户已确认波形存储在本机和桌面机可用，日志前端已分离。本次将这些功能和工作目录整理作为一个阶段提交。该确认不代表所有候选设备、账号或采样率均已验证。

## 当前入口

| 工作 | 入口 |
| --- | --- |
| 本机正式桌面前端 | `tools/launch_operator_desktop.sh`，优先使用 `outputs/operator-local/waveform-operator/`，缺失时使用既有桌面包 |
| 前端源码入口 | `apps/operator_gui/main.py` |
| 采集服务源码入口 | `apps/acquisition_server/main.py` |
| 日志前端 | `tools/waveform_log_dashboard.py` 与同目录 HTML |
| 只读元数据查询 | `tools/query_waveform_mongodb.py` |
| 当前前端交付 | [2026-10-05 更新目录](../交付/前端更新-20261005/README.md) |

2026-10-05 更新记录指定当前前端安装包为 `waveform-operator-direct-offline-20261004-executable-0643957c5c96.tar.gz`，本次整理已核对其 SHA-256：`967dd0cd36dd56e0382bb635d1d39f8b664ecd102b92ee970e4c348182113074`。本机和桌面机部署事实见[原更新记录](deployment/20261005/前端更新记录-20261005.md)。

服务端 `20261004` 基线保留给构建脚本；`20261005` 三个 retry 包集中保存在 `archive/build-artifacts/server-candidates/20261005/` 并登记为候选，尚无充分版本记录支持将其中某个标为当前现场部署包。

## 日志与分析边界

日志前端单独提供采集任务查询和展示，读取元数据与日志。GUI 不承担日志网页的运行职责；部署或启动脚本需要按运行环境使用。

波形 HDF5 保留 X/Y 和 bunchID。统一频谱任务服务已接入第三栏，支持 Archiver 与三种 HDF5。低频 V1/V2 和高频频带使用不同模板。使用方法见[频谱分析说明](spectrum-analysis-usage.md)。本机桌面入口使用包含 SciPy 的新包；桌面机的新版本更新尚未执行。

## 历史和整理记录

- [独立日志前端说明](日志前端使用说明.md)
- [目录整理与迁移说明](工作目录整理_2026-10-07.md)
- [7 月历史文档](history/2026-07/)
- [8 月历史交接](handoffs/)
