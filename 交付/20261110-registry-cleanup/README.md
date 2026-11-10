# 设备表清理与状态更新（2026-11-10）

范围：本机 operator、波形服务器采集服务、桌面服务器共享安装（`desktop-user-a`、`desktop-user-b` 两个账号）。

## 改动

- 删除 12 台不产生波形文件的反馈/联锁/DMP 设备：
  - FEL2：`FB01`、`ILK01`、`SBMP01_DMP`～`SBMP04_DMP`
  - LTU2：`FB01`、`FB02`
  - BC2：`ILK01`
  - EXT4：`FB01`
  - EMIT：`FB01`
  - DCP：`FDL01`
- 保留 `AC_LA_DCP_BI_CBPM02`：它是 CBPM（正常波形设备），2026-10-30 仍有数据；删除清单原把它归入反馈类，核对设备类型后保留。实测其采集 PV 当前搜索超时（TCP 6310 可连），见下。
- 其余设备统一标记为“已配置”：`epics_port_status=configured`、`review_status=approved`。这是登记状态，不代表重新做了端口验证。
- 结果：显示表 373→**361** 台，采集表 211→**199** 台；`order` 重新连续编号。

## 更新与验证

| 位置 | 动作 | 证据 |
| --- | --- | --- |
| 本机 | 界面重启加载新表 | 进程加载 `devices.operator.json`（361）/`devices.initial.json`（199） |
| 服务端 | 替换 `config/devices.initial211.json`（旧文件备份为 `.bak-20261110-feedback-removal`）并重启 | `--check` 输出 `registry_devices=199, acquisition_devices=199`；服务 active（MainPID 3258704）；health 正常；版本仍为 `20261109-beam-status-r4` |
| 桌面机 | 新包 `waveform-operator-registry-cleanup-20261110` 安装到共享目录并切换启动器 | 启动器备份 `startWaveform.sh.backup-20261110-registry-cleanup`；离屏截图显示注册 BPM **199**、18 个区域 |

桌面包复用 `20261109-beam-status-r4` 的 Debian 10 二进制（代码未变，只更新 registry 配置），BUILD-INFO 中记录 `binary_source`。桌面现保留：新版、上一版 `waveform-operator-beam-status-r4-20261109`、配置源 `waveform-operator-direct-offline-20261004`；`pv-frequency-r2` 及其启动器备份已删除（记录只保存在本机 `outputs/deletion-records/desktop-20261110.txt`）。

## DCP CBPM02 检查（只读）

- TCP 22、6310 均可连接；
- caproto 读取 `AC-LA-DCP-BI:CBPM02-E:RESULT_CAPTURE` 8 秒搜索超时（同一方法读取 L3 `ColdBPM01` 正常，方法有效）；
- 设备上 `bram_result.txt` 停留在 2026-10-30 18:30。

结论：当前**不能采**，需要检查 DCP 侧 IOC 是否在运行、该 PV 是否存在。2026-11-09 那次它也是在同一阶段失败（PV 连不上）。

## 验证边界

- 未发送真实采集请求；只做了注册表加载、服务重启和离屏界面截图。
- 未执行 SHA-256、manifest、二进制身份核验或真实桌面 smoke。
- `desktop-user-b` 的私有桌面会话仍未验证，共享安装与公共路径权限沿用既有 755 设置。
