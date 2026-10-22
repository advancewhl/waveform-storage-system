# SHINE Waveform Storage

波形存储基础可用阶段于 2026-10-07 确认。本机与桌面机可执行采集、历史检索、BPM 选择、bunchID 对齐、绘图和 HDF5 导出，日志前端已独立出来。最新源码、版本和部署状态以 [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) 为准；具体批次、设备和账号的现场证据保留在各次记录中。

## 从这里开始

- [当前阶段状态与运行入口](docs/CURRENT_STATUS.md)
- [工作目录地图](docs/WORKSPACE.md)
- [前端交付与版本索引](交付/README_先看.md)
- [独立日志前端说明](docs/日志前端使用说明.md)
- [频谱分析使用说明](docs/spectrum-analysis-usage.md)
- [统一频谱分析架构](docs/spectrum-analysis-design.md)
- [Agent API v2 接入与安全边界](docs/agent-api-v2.md)、[OpenAPI](docs/agent-api-v2.openapi.json)
- [Agent API v1 兼容契约](docs/waveform-agent-api.md)
- [Agent API v2 交付与验证记录](交付/20261021-waveform-agent-v2/README.md)
- [中间构建包归档索引](archive/build-artifacts/README.md)
- [本次整理记录](docs/工作目录整理_2026-10-07.md)

## 源码目录

| 目录 | 用途 |
| --- | --- |
| `apps/operator_gui/` | 正式 Qt 前端入口；legacy/experimental 文件保留兼容用途 |
| `apps/acquisition_server/` | Candidate V2 采集服务入口 |
| `apps/acquisition_agent/` | 历史采集代理兼容入口 |
| `src/waveform_storage/` | 采集、元数据、SSH/EPICS、对齐、HDF5 和界面实现 |
| `config/` | 正式操作注册表、47 台采集注册表、候选与灰度配置 |
| `tools/` | 桌面启动、独立日志前端、只读数据库查询、质量检查 |
| `deploy/` | 服务部署配置与 systemd 模板 |
| `packaging/` | 前后端源码包、离线包、可执行包的构建脚本 |
| `tests/` | 离线测试；不用于触发现场设备 |
| `analysis/high_frequency/` | 高频输入与工作区说明 |
| `src/waveform_storage/analysis/` | Archiver / HDF5 适配器、共用频谱引擎和任务服务 |

## 数据、文档和本地产物

`data/samples/` 保存历史采集样例；`docs/` 分开保存当前说明、现场审计、界面参考和历史记录；`交付/` 保存有明确版本记录的交付快照。`outputs/` 仅保留构建所需基线和当前明确前端包。

`dist/` 是桌面启动和打包脚本依赖的本地可执行目录；`build/` 是构建工作目录；`offline-packages/` 保存仍需使用的兼容依赖包。中间修改版本统一移至 `archive/build-artifacts/`，保留压缩包和校验文件。

原始数据、二进制包、本地配置、日志及整理前备份仅在本地保存。Git 管理源码、配置模板、测试、文档和索引。

## 本地检查

```bash
QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/waveform-mpl \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python tools/run_quality.py
```

历史配置语义、bunchID 时基、X/Y 单位和分析参数仍须结合实际设备与采集任务确认。旧 7 月 README 已保存为[整理前历史快照](docs/history/README_整理前_20261007.md)。
