# 本机 operator 与现有 server

| 组件 | Release | 本次状态 |
| --- | --- | --- |
| 本机 operator | `20261008-spectrum-ui-r1` | 已构建并通过 offscreen 与本地 HDF5 分析；真实桌面验收未通过，随后回滚，未保持激活 |
| 现场 acquisition server | `20261008-history-release-r4` | 未修改，继续使用现场现有版本 |
| 现场 operator | 现场现有版本 | 未修改 |

本次源码修改限于频谱分析 UI、状态展示和结果兼容，没有修改 acquisition/history/server 协议。r1 的 `BUILD-INFO` 记录既有 protocol capabilities；本次没有构建 server，也不把 r1 标记为新版本对。

当前本机 `outputs/operator-local/current` 已恢复到 `20261008-history-release-r4/waveform-operator`。
