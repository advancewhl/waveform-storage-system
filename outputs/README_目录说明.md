# 构建输出目录

`outputs/operator-local/releases/` 保存本机已生成的 operator release；`outputs/operator-local/current` 是本机正式 launcher 使用的激活入口。最新源码、激活 release 和现场状态以 [docs/CURRENT_STATUS.md](../docs/CURRENT_STATUS.md) 为准。中间构建包已移至 [archive/build-artifacts](../archive/build-artifacts/README.md)。

| 目录 | 本地保留用途 |
| --- | --- |
| `deployment-pair-20261003/` | 构建脚本所需 `7f3dd6632857` 服务端基础包 |
| `deployment-pair-20261004/` | 构建脚本所需 `5c9401ccfad2` 服务端基线与校验文件 |
| `deployment-pair-20261005/` | 历史构建产物；三个 server retry 候选已集中归档 |
| `operator-formal/`、`server-formal/` | 源码包脚本默认输出目录；原中间包已归档 |
| `operator-local/releases/` | 本机已生成的 operator release |
| `operator-local/current` | 本机正式 launcher 使用的激活入口 |
| `pv_list_2026-07-26/` | 已纳入 Git 的设备清单审计与测试输入，保留路径 |

设备排查结果已集中到 `docs/audits/device-readiness-20261003/`，界面预览集中到 `docs/ui-preview/formal/`。
