# 本机 operator 与现场 server

| 组件 | Release | 本次状态 |
| --- | --- | --- |
| 本机 operator | `20261009-spectrum-ui-r2` | 已构建并激活；等待用户从桌面图标手动验证 |
| 现场 acquisition server | `20261008-history-release-r4` | 本次未构建、未部署、未修改 |
| 现场 operator | 现场现有版本 | 本次未修改 |

本次只修改本机频谱 GUI 的建议文件名。没有修改 acquisition protocol、request identity、历史查询、server/systemd、MongoDB、raw HDF5 或结果包内容。没有创建或发布新的 operator/server 版本对。

本机 `outputs/operator-local/current` 当前指向 `20261009-spectrum-ui-r2/waveform-operator`。如果用户手动验收失败，可将本机 `current` 回滚至 `20261008-spectrum-ui-r1/waveform-operator`。现场 server 未参与本次激活。
