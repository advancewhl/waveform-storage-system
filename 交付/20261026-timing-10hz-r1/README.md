# 10 Hz 机器频率支持：20261026-timing-10hz-r1

> 本文记录 r1 发布时的状态。后续前端移除频率下拉框，见 [PV 频率只读显示 r2](../20261026-pv-frequency-r2/README.md)。当前部署以 [CURRENT_STATUS.md](../../docs/CURRENT_STATUS.md) 为准。

## 问题

正式前端「采集参数 → 当前频率」显示「暂不可用」。链路为 GUI →
[CandidateOperatorBackend.get_timing_frequency()](../../apps/operator_gui/candidate_backend.py) →
SSH 隧道 `tcp://127.0.0.1:5001` → 服务端 `_handle_timing_frequency()` →
[read_timing_frequency()](../../src/waveform_storage/acquisition/runtime.py) →
caproto 读 PV `AC-LA-INJ1-TIM:WRN1:MCFQ8` @ `192.0.2.1:6310`。

发布前实测排除了隧道、ZMQ、systemd 与 EPICS 连接故障：隧道 master 存活、`health`
返回 accepted、服务 active 且无在跑任务，报错内容是频率数值本身而不是连接超时。

根因：定时 PV 当前返回 **10 Hz**，而 `capture_interval_seconds()` 的支持表只有
1000/500/100/50/5/2 Hz。`read_timing_frequency()` 在返回前调用该表做白名单校验，
10 Hz 查不到即抛 `ServerSettingsError`，服务端包成 `TIMING_FREQUENCY_FAILED`，GUI
捕获异常后显示「暂不可用」。发布前线上原文：

```
accepted=False  error_code=TIMING_FREQUENCY_FAILED
message=unsupported capture frequency 10 Hz; supported: 1000, 500, 100, 50, 5, 2 Hz
```

10 Hz 从未被支持：legacy 采集代理
[acquisition_agent_adapter.py](../../src/waveform_storage/legacy/acquisition_agent_adapter.py)
只有 100/50/1000/2 四个分支，500 和 5 为后续补充；原
`tests/test_acquisition_server.py` 明确断言 `capture_interval_seconds(10)` 抛错。

同一校验也挡在采集入口（GUI `start_capture`、Agent API v2 `control/captures`、服务端
`handle()`），因此故障期间 10 Hz 下整条 v2 采集通道不可用，「暂不可用」只是最早暴露的表象。

## 改动

| 文件 | 改动 |
| --- | --- |
| `src/waveform_storage/acquisition/runtime.py` | `_CAPTURE_INTERVAL_SECONDS` 新增 `10.0: 2100` |
| `tests/test_acquisition_server.py` | 断言 `10 → 2100`，拒绝样例改为 20 Hz |
| `src/waveform_storage/gui/operator_window.py` | 「采集频率」显式下拉新增 `10 Hz` |
| `docs/agent-api-v2.openapi.json` | `CaptureStart.frequency_hz.enum` 新增 `10` |

2100 s 由操作员在 2026-10-26 确认，目标为每段 20000 个触发，另留采集余量。
各档等待值属于现场规则，不能对整张表插值。10 Hz 的实际采集结果尚未验证。

## 发布状态

| 组件 | release | 状态 |
| --- | --- | --- |
| 现场 server | `20261026-timing-10hz-r1` | 已切换，systemd active，协议与频率读取已验证 |
| 本机 operator | `20261026-timing-10hz-r1` | **已构建，未激活**；`current` 仍指向 `20261017-history-wording-r6-local` |

服务端切换前确认 `state/` 无 accepted/running 任务、磁盘余量 16T。切换由
`tools/deploy_candidate_release.py` 以加锁事务完成，含未完成任务守卫、`--check`
预检、失败自动回滚与 COMMIT 两段式确认。协议为
`v2 + history-run-segment extension`，未变化，operator/server 仍成对兼容。

### 校验

- 服务端二进制 SHA-256：`35ba6d12037df206893de4cbb21be8f7dfb11d71cb2f7a48e711b4d739773305`
- 服务端归档 SHA-256：`44f7d7fe500e03b91cab89588033ff652c7d39a01c4518d68426ad6b49a4175a`
- operator 二进制 SHA-256（未激活）：`ff67de84c2fa19f113cbbcef3ab83ee0b4a352cea6cd45f0486d4cb08e47967a`
- 构建镜像 `python:3.10-buster`，镜像 ID 与上一版 r4 一致（`31a8498af27d…`）
- 版本对：[release-pair.json](release-pair.json)（`source_state=working-tree`）
- 构建与质量门：[evidence/quality.log](evidence/quality.log)（286 项通过）、
  [evidence/server-build.log](evidence/server-build.log)、
  [evidence/operator-build.log](evidence/operator-build.log)

### 验收结果

- 定向测试：`tests.test_acquisition_server` 18 项、`tests.test_waveform_agent_v2` 31 项、
  `tests.test_formal_history_bpm_selection` 12 项，共 61 项通过。
- 完整质量门 286 项通过，作为授权构建的内置步骤运行一次。
- 协议 smoke（执行于 2026-10-26，查询数据日期为 `2026-10-12`）：health、`list_capture_runs`、`list_capture_segments`、
  `find_waveform_files_by_segment`、legacy `list_capture_times` 全通过；1 任务、5 段、
  125 个精确文件。见 [evidence/protocol-smoke.json](evidence/protocol-smoke.json)。
- **频率恢复**：切换后从 GUI 同一路径调用 `get_timing_frequency`，保存的成功响应为 `accepted=True, frequency_hz=10.0, source=timing_pv`，
  见 [evidence/live-timing-frequency.json](evidence/live-timing-frequency.json)。
  该响应表明服务端已能返回定时 PV 的 10 Hz 读数。
- 服务端 `current`、进程可执行路径、运行中二进制哈希与 BUILD-INFO 一致，
  `MainPID=1991109`。见 [evidence/deploy.json](evidence/deploy.json)。

## 未验证

- 真实桌面 GUI 界面复验未做：`deploy.json` 状态为 `GUI SMOKE PENDING`。「当前频率」
  显示 10 Hz 由上述同路径协议读取推得，未在 Qt 界面内截图确认。
- 未发送任何真实采集请求，因此 10 Hz 下 2100 s 分段的实际采集、下载与 HDF5 落盘
  未经验证。首次 10 Hz 采集建议人工确认分段文件大小。
- 未执行全量 manifest 校验或完整发布验收；运行路径与二进制哈希核对已记于上文；按任务范围限制
  只做上述定向检查。
- `tools/run_quality.py` 之外的历史交付目录与 Mongo 生产链路未重复验证。

## 回滚

旧版本完整保留：`/home/storage-user/waveform-storage-v2/releases/20261008-history-release-r4`。
COMMIT 后事务备份链接已按脚本约定清除，`incoming/` 无残留锁。回滚沿用部署脚本的原子
换链方式（临时链接 + `mv -Tf`），注意旧版本会重新对 10 Hz 报 `TIMING_FREQUENCY_FAILED`：

```bash
base=/home/storage-user/waveform-storage-v2
sudo systemctl stop waveform-acquisition-v2
sudo ln -s "$base/releases/20261008-history-release-r4" "$base/current.rollback-manual"
sudo mv -Tf "$base/current.rollback-manual" "$base/current"
sudo chown -h storage-user:storage-user "$base/current"
sudo systemctl start waveform-acquisition-v2
systemctl show waveform-acquisition-v2 -p ActiveState -p MainPID
```

以上回滚命令本轮未执行，仅作为记录。
