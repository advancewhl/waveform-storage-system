# 束流状态改为仅显示（r4）：服务端与 operator 版本对

日期：2026-11-09
范围：波形服务器（`storage-user`）的 `waveform-acquisition-v2`，以及本机 operator。
未修改桌面机、SHINE-AC 或其他项目。

## 背景

r3 在挡束（`beam_permitted=false`）时于采集入口返回 `NO_BEAM` 拒绝。现场确认：没有束流时采集本身仍可运行，只是采集到的数据最后对不齐。因此束流状态只应作为显示信息，不参与采集准入。

## 改动

`src/waveform_storage/acquisition/server.py`：

- 删除采集前的束流读取与 `NO_BEAM` 拒绝分支，`start_capture` 不再检查束流，直接提交任务；
- `get_beam_status` 查询与界面显示保持不变（挡束时仍显示“禁止”）。

`tests/test_acquisition_server.py`：“挡束拒绝采集”改为“挡束仍受理采集”，并等待任务真正提交后断言；原有“允许时受理”与束流查询测试不变。

界面侧没有改动：束流卡片本来就是显示用途，不拦截开始采集。

## 版本对与校验

| 组件 | 版本 | 二进制 SHA-256 |
| --- | --- | --- |
| 服务端 | `20261109-beam-status-r4` | `32db99f8c7e3fafd0b334b836af515d5b441d00d5b12dd93c7b5f7b2c339ca83` |
| operator（本机） | `20261109-beam-status-r4` | `b5cf307e89c2d2e2123176d6837015d18492f51b24d69da08402b8ed822ce3ae` |

- 源码修订：`ed05f676acca4f623db3eee99329300bf5fdf144`（working-tree）
- 服务端归档 SHA-256：`e815ec30e9ec7e0524a4179d79c67521e91d50401460a81be64ee36eb8af3f6c`
- operator 二进制与 r3 相同（本次只改服务端，PyInstaller 重建为字节一致）
- 构建证据：`outputs/release-pairs/20261109-beam-status-r4/`；部署报告：同目录 `deploy-report.json`

## 部署与验证

- 2026-11-09 16:07 CST：部署事务 COMMIT，`current` → `releases/20261109-beam-status-r4`；服务 `active (running)`，`MainPID=2696162`，进程可执行文件与该版本二进制一致。
- 协议 smoke（`2026-11-07` 真实数据）：health、list_capture_runs、list_capture_segments、find_waveform_files_by_segment 全部通过；3 个任务、34 段、545 个文件。
- 部署后实测 `get_beam_status`：`beam_permitted=false`、`beam_stop_alarm="on"`，8 条明细 PV 正常返回，显示链路不受影响。
- “不阻拦采集”由单测覆盖（挡束状态下 `start_capture` 返回 accepted 并实际提交任务）；没有启动真实采集验证，因为一次完整采集需约 70 分钟。

## 本机 operator

- `outputs/operator-local/current` 已切到 `20261109-beam-status-r4`。
- 界面已重启（PID 3283948），运行 r4，连接模式正常；束流卡片此时应显示“禁止”（仅显示，不阻止采集）。

## 清理（仅本次测试版本）

已删除 r3（束流阻拦版）：

- 本机：`outputs/operator-local/releases/20261109-beam-status-r3`、`outputs/server-releases/20261109-beam-status-r3`、`outputs/release-pairs/20261109-beam-status-r3`
- 服务端：`releases/20261109-beam-status-r3`、`incoming/20261109-beam-status-r3.tar.gz`、`incoming/20261109-beam-status-r3.deploy.sh`；删除清单只保存在本机 `outputs/deletion-records/`（不提交仓库）

服务端按用户要求只保留当前版本与上一版：保留 `releases/20261026-timing-10hz-r1`（上一版）；更早的 `releases/20261008-history-release-r4` 已删除（timing 发布记录曾把它列为回滚目标，现以 `20261026-timing-10hz-r1` 为唯一回滚版本）。本机保留 `20261026-pv-frequency-r2`。其他组件与其他项目的发布、构建产物未触碰。

## 回滚

服务端（r3 已删除，回退到上一个稳定版；该版本没有束流显示）：

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

- 未启动真实采集验证“挡束可采”，只有单测与代码路径证据；真实数据是否如现场所述“最后对不齐”未在本次任务中复现。
- 界面束流卡片未截图核验，仅确认进程与查询链路正常。
- 协议 smoke 使用 2026-11-07 的历史数据。
