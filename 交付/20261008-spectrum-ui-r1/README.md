# 本机频谱 UI operator r1 发布验收记录

## 结论

本机 operator r1 已构建并通过源码、二进制、offscreen 启动和本地 HDF5 分析检查。真实桌面入口未能确认 r1 进程和窗口，因此验收未通过。`current` 已原子回滚到 `20261008-history-release-r4`。r1 release 保留在本机，但没有保持激活。

## 构建信息

| 项目 | 值 |
| --- | --- |
| Release ID | `20261008-spectrum-ui-r1` |
| 源码 revision | `dbbc0d6ddfdaaf6d438783d7e7378134538f855c` |
| 源码状态 | `working-tree` |
| operator 路径 | `outputs/operator-local/releases/20261008-spectrum-ui-r1/waveform-operator/` |
| Binary SHA-256 | `e75423b9dd6d38f6e68f93338f8fdccc3186be642665a1d7392872c4682b717e` |
| Source manifest SHA-256 | `288118222d6b9d20af59e96c2844cc4d093ad15917e5d4658b31b2afd20299b4` |
| `status.py` SHA-256 | `68073d839f7c6b3ace2f7c36459a1a517fa52a81c784d3a1683879a85d571384` |

正式 PyInstaller 构建使用 `packaging/operator-executable.spec` 和项目 Python 3.10.20。`BUILD-INFO`、`SOURCE-MANIFEST.json`、`MANIFEST.sha256` 均已写入 operator 目录。8,368 项文件 manifest 校验通过，binary hash 与 `BUILD-INFO` 一致，source manifest 包含 `src/waveform_storage/analysis/status.py`。

## 验证

完整质量门通过 211 项测试，以及源码质量、设备注册表和 secret 检查。构建前后的 source manifest 完全一致。

新二进制以 offscreen 模式打开了“频谱分析”页面，并用本机文件 `analysis/high_frequency/inputs/20261006_155512_BPMdata.h5` 完成了高频分析。结果包含 40 个通道，状态计数为 4 个“谱峰明显”、7 个“谱峰较明显”、25 个“观测项”和 4 个“无显著谱峰”。文件没有采样率字段；本次按既有记录手动填入 1,000 Hz，不是从文件自动读取。没有发送采集请求。

- Offscreen 初始页面：[offscreen-spectrum-startup.png](evidence/offscreen-spectrum-startup.png)
- Offscreen 分析结果：[offscreen-hdf-analysis.png](evidence/offscreen-hdf-analysis.png)
- 完整质量门日志：[quality.log](evidence/quality.log)
- PyInstaller 日志：[operator-build.log](evidence/operator-build.log)
- 构建记录：[build-record.json](evidence/build-record.json)
- 切换前检查：[pre-activation-checks.json](evidence/pre-activation-checks.json)

## 桌面入口与回滚

桌面文件 `/home/whloong/桌面/波形存储.desktop` 仍通过 `tools/launch_operator_desktop.sh`。沙箱内启动时，Qt 无法连接 `DISPLAY=:1`；在用户桌面会话中调用 `gio launch` 虽返回 0，但随后没有发现 r1 的 `/proc` executable 或 operator 窗口。由于没有真实桌面运行证据，本次不判定桌面验收通过。

回滚目标为 `outputs/operator-local/releases/20261008-history-release-r4/waveform-operator/`。回滚后 `readlink -f outputs/operator-local/current` 已精确指向该路径，r4 binary SHA-256 为 `8e50fd5e623113ae40aa19a03e9c912182691c80145269e2f849d69a306cf416`，r4 的 8,368 项 manifest 校验通过。完整记录见[回滚验证](evidence/rollback-validation.json)和[桌面验证](evidence/desktop-validation.json)。

## 版本范围

本次只构建和验证本机 operator。r1 构建后已回滚，不是当前发布版本。现场 acquisition server 继续使用 `20261008-history-release-r4`；未修改现场 server、systemd、MongoDB、dashboard、server current 或现场 operator。版本范围记录见[本机 operator 与现有 server](version-pair.md)。

没有执行 Git commit、push 或 tag。
