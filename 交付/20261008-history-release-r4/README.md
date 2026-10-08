# 历史任务查询正式发布：20261008-history-release-r4

## 发布状态

本机前端与现场采集服务已成对发布，并通过真实桌面入口验收。构建来自工作目录，`source_state=working-tree`。现场桌面机未部署本次前端，最近部署记录仍为 2026-10-05；本次没有把旧记录当作新版本验收。

| 组件 | release | 当前路径 | 状态 |
| --- | --- | --- | --- |
| 本机 operator | 20261008-history-release-r4 | `/home/whloong/waveform-storage/waveform-storage-system/outputs/operator-local/releases/20261008-history-release-r4/waveform-operator` | 已激活，真实桌面验收通过 |
| 现场 server | 20261008-history-release-r4 | `/home/storage-user/waveform-storage-v2/releases/20261008-history-release-r4` | systemd active，协议与进程校验通过 |
| 日志页 | 20261008-history-release-r4 | `/home/storage-user/waveform-storage-v2/dashboard/tools/` | 只读 Mongo 查询与网页已验证 |

协议为 `v2 + history-run-segment extension`。前后端必须同时支持 `list_capture_runs`、`list_capture_segments`、`find_waveform_files_by_segment`。旧日期与时刻接口保留。

## 构建与校验

- 前端二进制 SHA-256：`8e50fd5e623113ae40aa19a03e9c912182691c80145269e2f849d69a306cf416`。
- 服务端二进制 SHA-256：`4ff9896c4eb811c72cc0dc9ad0e676865b07025330f87d266a1cb63b57941fc3`。
- 归档校验：[SHA256SUMS](SHA256SUMS)。
- 版本对：[release-pair.json](release-pair.json)。
- 两端 BUILD-INFO、文件 manifest、源码清单位于 [operator/](operator/) 和 [server/](server/)。
- 编译日志和完整质量门日志保留在 `outputs/release-pairs/20261008-history-release-r4/`。

每个归档包含真实二进制、依赖、BUILD-INFO 和 manifest。先核对归档 SHA-256，再在解包目录执行 `sha256sum -c MANIFEST.sha256`。源码快照中的全部输入哈希与最终工作目录一致，未改写既有版本的 BUILD-INFO。

最终质量门通过 206 项测试及全部后续检查。结果见 [final-quality.log](evidence/final-quality.log)。

## 验收结果

2026-10-06 的真实协议查询返回 3 个有数据任务、87 个数据段、1740 个精确匹配文件。日志页保留 5 个任务，含零数据任务；段数与文件数相同。

真实桌面 `.desktop` 经 launcher 启动 current 二进制。验收进程 PID 为 355716，父 launcher PID 为 355698。SSH 主进程 PID 为 145015，转发 `127.0.0.1:5001 → remote 127.0.0.1:5001`。服务 MainPID 为 1129649，实际进程路径与 current 一致。

GUI 显示“16:02:35 | 1000 Hz | 10 分钟 | 当日 21 段”。完成切段、BPM 选择、20000 点对齐、原始文件下载和 V2 导出。完整身份为 `a4d92ba9-553f-4652-a821-353c6d830942 + capture_sequence=1`。查询、下载目录和导出 metadata 一致。未发送采集请求。

现场所选任务没有部分成功段。`test_partial_history_integration.py` 使用真实 InMemory catalog、ZMQ inproc server、正式 CandidateBackend 和 GUI，验证请求 2 台、实际 1 台、sequence 0/3 跳号，以及对齐、下载、导出完整链路。

证据位于 [evidence/](evidence/)。包括协议结果、GUI 报告与截图、实际进程、SSH 和 Mongo 状态。验收 HDF5 位于 acceptance-data 归档。

## 回滚

- previous_operator_release：`/home/whloong/waveform-storage/waveform-storage-system/outputs/operator-local/releases/20261008-history-release-r3/waveform-operator`。
- previous_server_release：`/home/storage-user/waveform-storage-v2/releases/20261008-history-release-r3`。
- 前端原子切回 previous_operator_release，重新打开窗口。
- 服务端原子切回 previous_server_release，重启 `waveform-acquisition-v2.service`，重新验证 active 和 health。
- 日志页备份：`/home/storage-user/waveform-storage-v2/dashboard/release-backups/20261008-history-release-r4/`；查询脚本较早备份位于同目录的 `20261008-history-release-r2/`。

旧 release、r2 首次桌面来源校验失败报告、r3 成功报告均保留。回滚通过切换 current 完成，不删除新旧目录。

## 架构与范围

保持“一次请求 → 多个 capture_sequence → 每段多个 BPM 文件”。不修改采集 interval、运行时核心循环或原始 HDF5 格式。整任务频谱属于后续工作，应先逐段计算，再按明确方法汇总。

固定发布流程见 [RELEASE_CHECKLIST](../../docs/RELEASE_CHECKLIST.md)。本次范围内未解决问题：None。
