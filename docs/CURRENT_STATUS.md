# 波形存储当前状态

更新时间：2026 年 11 月 9 日。生产状态来自本次对波形服务器的只读检查；未检查的组件标明最近记录日期。历史过程和验收证据见[历史状态快照](history/current-status-through-20261026.md)与各交付目录。

## 生产服务

| 组件 | 版本或位置 | 状态和证据 |
| --- | --- | --- |
| 采集服务 | `20261026-timing-10hz-r1` | 2026 年 11 月 9 日只读检查：`waveform-acquisition-v2.service` 为 active/running，`current` 指向该版本。 |
| Agent API | `20261030-fastapi-r1` | 2026 年 11 月 9 日只读检查：`waveform-agent-api` 容器运行，`agent-api-current` 指向该版本；本机 `/health` 返回正常。 |
| MongoDB | `mongo:8.0.29` | 2026 年 11 月 9 日只读检查：`waveform-mongodb` 容器运行；本次未执行数据库查询。 |
| Waveform MCP、Archiver MCP | 现场容器 | 2026 年 11 月 9 日只读检查：两个容器均为 healthy；本次未调用业务接口。 |
| 现场 operator | `20261026-pv-frequency-r2` | 2026 年 10 月 26 日交付记录显示共享启动器已切换；本次未检查现场桌面。 |
| 本机 operator | `20261026-pv-frequency-r2` | 2026 年 11 月 9 日本地 `outputs/operator-local/current` 指向该版本；本次未启动界面。 |
| 日志 dashboard | `20261008-history-release-r4` | 版本为历史记录，本次未核实运行状态。 |

Agent API 采用 FastAPI 和 Uvicorn，保留原 v1/v2 业务实现。2026 年 10 月 30 日交付记录称生产配置为 v2-only，且旧 API 发布目录和旧容器已删除；本次未读取配置，也未验证旧版本恢复。需要回滚时须先取得旧源码或可靠备份，重新构建和验证。详见 [FastAPI 交付记录](../交付/20261030-agent-fastapi-r1/README.md)。

## 运行与验证边界

本次检查只读取服务状态、版本链接和 API 健康响应，没有发送采集或数据修改请求，也没有执行真实桌面 smoke、重启验收或完整发布验收。容器运行和健康响应不代表采集、历史数据查询或所有客户端均已验证。

原始数据保持一次采集请求对应多个真实 `capture_sequence`，每段包含多个 BPM 文件。历史查询使用完整的 `request_id + capture_sequence`，不自动拼接不连续数据段。整任务频谱应逐段计算，再按明确方法汇总。

## 入口

- [项目说明](../README.md)与[文档索引](README.md)
- [Agent API v2 手册](agent-api-v2.md)和 [v1 兼容契约](waveform-agent-api.md)
- [频谱分析使用说明](spectrum-analysis-usage.md)与[日志前端说明](日志前端使用说明.md)
- [发布检查清单](RELEASE_CHECKLIST.md)与[交付索引](../交付/README_先看.md)

历史状态记录反映当时的部署与测试结果，不能作为当前运行状态证明。
