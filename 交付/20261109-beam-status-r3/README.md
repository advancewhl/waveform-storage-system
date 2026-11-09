# 束流状态判定修复（r3）：服务端与 operator 版本对

> 后续版本：r4 已取消“挡束拒绝采集”，束流改为仅显示。本记录保留为历史，回滚与现状以 [r4 交付记录](../20261109-beam-status-r4/README.md) 为准。

日期：2026-11-09
范围：波形服务器（`storage-user`）的 `waveform-acquisition-v2`，以及本机 operator。
未修改桌面机、SHINE-AC 或其他项目。

## 解决的问题

r2 读取 MPS 挡束链时把 PV 的**原始枚举下标**当成了开关值：

- `AC-LA-INJ1-MPS:MPS00:STPBEAM_ALARM` 的枚举定义是 **0 = `on`（挡束）、1 = `off`（放行）**；
- r2 把数值 `0` 解释为 off，实测返回 `beam_stop_alarm: "0", beam_permitted: true`，在挡束期间会错误放行采集。

现场对照：桌面机 `desktop-user-a` 上 `caget -t` 显示 `on`，`caget -t -n` 显示 `0`。

## 修复内容

`src/waveform_storage/acquisition/runtime.py`：

1. 读取改为向 IOC 请求 DBR_STRING（`data_type=ChannelType.STRING`），取得枚举标签文本（`on`/`off`、`error`/`normal`），不再使用原始数值。
2. 判定只认标签：`on`/`stopped`/`beam_stop` → 禁止；`off`/`normal`/`ok`/`permitted` → 允许；其他值（包括数值回落）→ 未知，不猜测。
3. 不硬编码每个 PV 的数字映射：同一链条上 `STPBEAM_ALARM` 是 0=on，而 `BEAMSTP2_FM_L4` 是 0=normal、`FM_L3` 是 1=error，方向并不统一。

单测同步覆盖标签读取路径（`tests/test_acquisition_server.py`），并通过 `tools/run_quality.py`。

## 版本对与校验

| 组件 | 版本 | 二进制 SHA-256 |
| --- | --- | --- |
| 服务端 | `20261109-beam-status-r3` | `b06b4ac340ba1d77a827683adc5dc90bee7e877f98ea5ebddbda3e287fe560ab` |
| operator（本机） | `20261109-beam-status-r3` | `b5cf307e89c2d2e2123176d6837015d18492f51b24d69da08402b8ed822ce3ae` |

- 源码修订：`ed05f676acca4f623db3eee99329300bf5fdf144`（working-tree）
- 服务端归档 SHA-256：`39a32b015072b3df5e28c0fbf4a43844bb31886f2ce9354b961cf484054dec08`
- 构建证据：`outputs/release-pairs/20261109-beam-status-r3/`（quality.log、server-build.log、operator-build.log、release-pair.json）
- 部署报告：`outputs/release-pairs/20261109-beam-status-r3/deploy-report.json`

## 部署与验证

- 2026-11-09 15:40 CST：部署事务 COMMIT，`current` → `releases/20261109-beam-status-r3`；服务 `active (running)`，`MainPID=2682241`，进程可执行文件与该版本二进制一致。
- 协议 smoke（`2026-11-07`，真实历史数据）：health、list_capture_runs、list_capture_segments、find_waveform_files_by_segment 全部通过；3 个任务、34 段、545 个文件。见同目录 `protocol-smoke.json`。
- 线上实测 `get_beam_status`（部署后、挡束期间）：

```
beam_permitted = False
beam_stop_alarm = "on"
BEAMSTP_FM_BC2/EXT/L3/UN = "error", FM_L4 = "normal", FROM_BC1 = "error", FROM_FPGA01/TO_FPGA = "off"
```

  r2 在同一现场状态返回 `beam_permitted: true`，r3 已修正为禁止。

## 本机 operator

- `outputs/operator-local/current` 已由 `20261026-pv-frequency-r2` 切换到 `20261109-beam-status-r3`。
- 界面进程（PID 3089878）仍在运行旧版 `20261026-pv-frequency-r2`，**未重启**；重启后才会加载 r3。未执行桌面 smoke。

## 采集任务记录修正（用户授权）

13:47:54 的 70 分钟任务 `7cbcd9e7-e6aa-4a3d-9da0-257de19f5bfd` 在服务重启时被中断：

- 任务状态文件已是 `failed / INTERRUPTED_BY_RESTART`；
- MongoDB `capture_tasks` 文档仍停在 `status: "running"`、`completed_at: null`，导致日志看板持续显示“运行中”。

原因是重启恢复分支只写状态文件、未同步 MongoDB 目录文档（`src/waveform_storage/acquisition/server.py` 的恢复逻辑）。经用户确认，只修正这一条记录：`status=failed`、`error={code: INTERRUPTED_BY_RESTART, message: …}`、`completed_at=2026-11-09T07:53:59Z`。修改前完整文档备份在服务端 `diagnostics/task-7cbcd9e7-pre-20261109.json`；其他记录未改动，看板无需重启。恢复逻辑本身未修，留待后续版本。

## 清理（仅本次测试版本）

已删除：

- 本机：`outputs/operator-local/releases/20261109-beam-status-r1`、`-r2`；`outputs/server-releases/20261109-beam-status-r1`、`-r2`；`outputs/release-pairs/20261109-beam-status-r1`、`-r2`
- 服务端：`releases/20261109-beam-status-r2`（135M）、`incoming/20261109-beam-status-r2.tar.gz`、`incoming/20261109-beam-status-r2.deploy.sh`；删除清单只保存在本机 `outputs/deletion-records/`（不提交仓库）

保留未动：服务端 `releases/20261026-timing-10hz-r1`（上一个稳定版）、`releases/20261008-history-release-r4`（timing 发布记录中的回滚目标）；本机 `20261026-pv-frequency-r2`（正在运行的界面版本）。其他组件与其他项目的发布、构建产物未触碰。

## 回滚

服务端（沿用发布记录的原子换链方式）：

```bash
base=/home/storage-user/waveform-storage-v2
sudo systemctl stop waveform-acquisition-v2
sudo ln -s "$base/releases/20261026-timing-10hz-r1" "$base/current.rollback-manual"
sudo mv -Tf "$base/current.rollback-manual" "$base/current"
sudo chown -h storage-user:storage-user "$base/current"
sudo systemctl start waveform-acquisition-v2
```

本机 operator：

```bash
python tools/release_waveform.py activate-operator outputs/operator-local/releases/20261026-pv-frequency-r2/waveform-operator
```

## 未验证与边界

- 界面未重启，r3 的束流显示（允许/禁止/未知）未做真实界面确认。
- 未执行真实采集；束流禁止时的采集拒绝只在服务端逻辑与单测层面验证过。
- 协议 smoke 使用 2026-11-07 的真实历史数据，未覆盖 2026-11-09 数据。
