# 采集服务切换到初测注册表（211 台）

日期：2026-11-09
范围：波形服务器（`storage-user`）的 `waveform-acquisition-v2` 配置切换。服务端二进制版本不变（`20261026-timing-10hz-r1`）。未修改桌面机。

## 背景

按 2026-11-09 从波形服务器发起的 TCP 可达性检查（1.5 秒超时，32 并发，仅连接测试，未读写 PV）：

- 373 台注册设备中 **211 台 TCP 可达**，**162 台不可达**；
- 不可达设备已在本机 `config/devices.operator.json` 中设为 `enabled=false`；
- 可达设备进入初测表 `config/devices.initial.json`。

## 变更内容（服务端）

| 项目 | 旧值 | 新值 |
| --- | --- | --- |
| `WAVEFORM_SERVER_REGISTRY` | `config/devices.probe47.json`（47 台） | `config/devices.initial211.json`（211 台，均 `acquisition_enabled`） |
| `WAVEFORM_DEVICE_SSH_KNOWN_HOSTS` | `security/known_hosts.probe47` | `security/known_hosts.initial211` |
| `WAVEFORM_MAX_CAPTURE_DEVICES` | 47 | **211**（2026-11-09 13:45 CST 调整） |

新增文件 SHA-256：

- `config/devices.initial211.json`：`b18913f0b7bc0e2d2ccb5eecd156857533b2aed5c8ee2a03ededd97f5b210ea0`（与本机 `config/devices.initial.json` 一致）
- `security/known_hosts.initial211`：`c8299c38604c51879447f6dbc30fd0b0060d43b0e2502dfa223029edaf9da2be`（211 台主机，633 条密钥记录）

回滚参照 SHA-256：

- `config/devices.probe47.json`：`666ad97adcabc08204ec23f2ccfdc66a850f3bf2533e06fa3f7db708fcdf634e`
- `security/known_hosts.probe47`：`923387a940d85b511707c7a0f64e269b6fb4bd697284458d6ec1471668ed87b1`
- `service.env.bak-20261109-132201`：`8017fc37031802e6c238b3ae6f2e4ac859e3c023a1dec808da3b66a858df9e57`（切换注册表前的完整配置）
- `service.env.bak-20261109-134531-max211`：`db39fe1d4577d86b931942460c42f6e18c522fdca86e26cbe4ccc985ce5b263e`（上调设备上限前）
- 当前 `service.env`：`28a592c92f87c2a2013b91a156dbe793fac05977950497500eeab2173a9c323e`

## 切换与验证

- 切换前确认 `state/` 共 43 个任务记录，状态均为终态（succeeded / failed / rejected / cancelled），**无运行中采集**。
- 切换前服务端 `--check` 通过：`registry_devices=211, acquisition_devices=211`。
- 2026-11-09 13:23:52 CST 重启服务：`active (running)`，`MainPID=2623990`。
- 启动日志确认：configuration is valid（211/211）、MongoDB metadata catalog is ready、listening `127.0.0.1:5001`。
- 2026-11-09 13:45:39 CST 再次重启（设备上限调整为 211）：`active (running)`，`MainPID=2630875`；启动日志确认 `max_capture_devices=211`、211/211；`127.0.0.1:5001` 正常监听。重启前再次确认无未完成任务。
- 未发送任何采集请求；未修改 MongoDB 或 HDF5 数据。

## 回滚

```bash
# 只回退设备上限（恢复到 47）：
cp /home/storage-user/waveform-storage-v2/service.env.bak-20261109-134531-max211 /home/storage-user/waveform-storage-v2/service.env
# 回退整个注册表切换（恢复到 probe47 口径）：
# cp /home/storage-user/waveform-storage-v2/service.env.bak-20261109-132201 /home/storage-user/waveform-storage-v2/service.env
sudo systemctl restart waveform-acquisition-v2
```

旧 `devices.probe47.json` 与 `known_hosts.probe47` 均保留未动。

2026-11-10 更新：删除 12 台反馈/联锁/DMP 设备并把全部设备标记为“已配置”，采集表调整为 199 台，见[设备表清理记录](../20261110-registry-cleanup/README.md)。

## 边界与未验证

- 主机密钥通过 `ssh-keyscan` 从波形服务器采集，**未做带外身份核验**；多台设备共享同一公钥材料时，不能单独证明每台主机身份。
- 未执行真实采集；TCP 端口可达不代表 EPICS PV 可读或设备能产出有效数据文件。
- 单次请求设备上限已调为 211；SSH 并发（`WAVEFORM_SSH_MAX_WORKERS`）仍为 17。
- 现场桌面机（`desktop-user-a`、`desktop-user-b`）的 operator 发布包仍为 probe47 口径，未更新；两个账号全程只读，未写入任何文件。当日晚间已把 r4（211 台口径）更新到共享安装，见[桌面机 r4 发布记录](../20261109-beam-status-r4-desktop/README.md)。
