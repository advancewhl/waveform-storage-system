# 波形存储当前状态

更新时间：2026 年 11 月 9 日。生产状态来自本次对波形服务器的只读检查；未检查的组件标明最近记录日期。历史过程和验收证据见[历史状态快照](history/current-status-through-20261026.md)与各交付目录。

## 生产服务

| 组件 | 版本或位置 | 状态和证据 |
| --- | --- | --- |
| 采集服务 | `20261109-beam-status-r4` | 2026 年 11 月 9 日 16:07 CST 部署（主进程 2696162，active/running）；协议 smoke 通过（3 任务、34 段、545 文件）。束流状态改为仅显示、不阻拦采集；部署后实测 `get_beam_status` 在挡束期间仍返回 `beam_permitted=false`、`beam_stop_alarm="on"`。同日 13:23/13:45 CST 已切换到初测注册表 `devices.initial211.json`（211 台）并将单次设备上限调为 211。版本对、校验与回滚见[束流仅显示交付记录](../交付/20261109-beam-status-r4/README.md)、[束流判定修复记录](../交付/20261109-beam-status-r3/README.md)和[注册表交付记录](../交付/20261109-initial211-registry/README.md)。 |
| Agent API | `20261030-fastapi-r1` | 2026 年 11 月 9 日只读检查：`waveform-agent-api` 容器运行，`agent-api-current` 指向该版本；本机 `/health` 返回正常。 |
| MongoDB | `mongo:8.0.29` | 2026 年 11 月 9 日只读检查：`waveform-mongodb` 容器运行；本次未执行数据库查询。 |
| Waveform MCP、Archiver MCP | 现场容器 | 2026 年 11 月 9 日只读检查：两个容器均为 healthy；本次未调用业务接口。 |
| 现场 operator | `20261109-beam-status-r4` | 2026 年 11 月 9 日共享启动器已切到 r4（Debian 10 兼容构建）；离屏页面显示 211 台、5 Hz、束流“允许”，与当时服务端读数一致；`desktop-user-b` 私有会话未验证。详见[桌面机 r4 发布记录](../交付/20261109-beam-status-r4-desktop/README.md)。 |
| 本机 operator | `20261109-beam-status-r4` | 2026 年 11 月 9 日 `outputs/operator-local/current` 已切到该版本，界面已重启（PID 3283948）并以连接模式运行；束流卡片为显示用途，未截图核验。 |
| 日志 dashboard | `20261008-history-release-r4` | 版本为历史记录，本次未核实运行状态。 |

Agent API 采用 FastAPI 和 Uvicorn，保留原 v1/v2 业务实现。2026 年 10 月 30 日交付记录称生产配置为 v2-only，且旧 API 发布目录和旧容器已删除；本次未读取配置，也未验证旧版本恢复。需要回滚时须先取得旧源码或可靠备份，重新构建和验证。详见 [FastAPI 交付记录](../交付/20261030-agent-fastapi-r1/README.md)。

## 运行与验证边界

2026 年 11 月 9 日的两次束流相关部署（r3 判定修复、r4 改为仅显示）执行了构建、服务重启、协议 smoke 与线上束流状态查询；未发送采集请求，未修改 HDF5 数据，未做真实桌面 smoke、界面重启验收或完整发布验收。r4 的“挡束仍可采集”仅由单测覆盖，未用真实采集复现。协议 smoke 与束流查询通过不代表采集链路或所有客户端均已验证。日志看板所用 MongoDB 任务记录中，13:47 被中断任务已按用户授权由 running 修正为 failed（见 r3 交付记录）。

原始数据保持一次采集请求对应多个真实 `capture_sequence`，每段包含多个 BPM 文件。历史查询使用完整的 `request_id + capture_sequence`，不自动拼接不连续数据段。整任务频谱应逐段计算，再按明确方法汇总。

## 入口

- [项目说明](../README.md)与[文档索引](README.md)
- [Agent API v2 手册](agent-api-v2.md)和 [v1 兼容契约](waveform-agent-api.md)
- [频谱分析使用说明](spectrum-analysis-usage.md)与[日志前端说明](日志前端使用说明.md)
- [发布检查清单](RELEASE_CHECKLIST.md)与[交付索引](../交付/README_先看.md)

历史状态记录反映当时的部署与测试结果，不能作为当前运行状态证明。
