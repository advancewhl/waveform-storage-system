# SHINE Waveform Storage

面向 SHINE 加速器 BPM 波形的采集、检索、bunchID 对齐、绘图与 HDF5 导出项目。

这是第一次整理后的基线版本。原项目由一个独立采集脚本和一个 ZIP 源码包组成，本次仅完成目录归档、主线识别、配置路径整理、敏感凭据移出源码和 Git 初始化准备。现场 EPICS、ZMQ、SSH 链路尚未在本版本中完成端到端验证。

## 当前能力

- PyQt5 操作员界面：
  - 选择 BPM；
  - 读取 EPICS 机器频率；
  - 发送采集命令；
  - 按日期和时刻检索远程 HDF5；
  - 对齐多个 BPM 的共同 bunchID；
  - 显示单个 bunchID 的 X/Y 曲线；
  - 下载原始文件或导出合并 HDF5。
- 无界面采集端原型：
  - 接收 ZMQ 命令；
  - 触发 BPM PV；
  - 并发 SFTP 下载 TXT；
  - 把 TXT 转换为单 BPM HDF5。

## 重要状态

当前版本是“可继续开发的源码基线”，不是已确认可部署版本。

已知最主要的阻断点：

1. GUI 使用 ZMQ `DEALER`，采集端使用 `PULL`，协议尚未统一。
2. GUI 发送复合消息，采集端只接受整数频率。
3. 生产服务器存在监听 5000 端口的外部 `saveApp` 服务端，但其源码、启动方式和响应协议
   尚未进入当前仓库或得到完整确认。
4. 代码依赖 Linux `fcntl`，Windows 只适合阅读、整理和运行不涉及锁的静态检查。
5. 现场 EPICS、SSH、存储路径和 HDF5 schema 仍需确认。

完整问题和验收条件见 [问题清单与改进路线](docs/问题清单与改进路线.md)。

## 目录结构

```text
.
├─ apps/
│  ├─ operator_gui/          # Qt 入口、生成 UI 和兼容 facade
│  └─ acquisition_agent/     # legacy 采集兼容启动入口
├─ src/waveform_storage/
│  ├─ application/           # RetrievalService、CaptureService
│  ├─ acquisition/           # 候选协议和任务状态
│  ├─ config/                # Settings 和设备注册表
│  ├─ gui/                   # Controller、Worker、设备选择和绘图
│  ├─ io/                    # TXT 与 HDF5
│  ├─ processing/            # 纯数据对齐
│  ├─ transport/             # SSH、EPICS 和本地 fake ZMQ
│  └─ legacy/                # 旧行为兼容适配器
├─ config/                   # devices.json 单一事实源和兼容 TXT
├─ tests/                    # 当前实验分支 89 项离线测试
├─ tools/                    # 注册表、质量和敏感信息检查入口
├─ data/
│  └─ samples/               # 本地 HDF5 样例，默认不提交 Git
├─ docs/                     # 当前交接、审计、设计与历史记录
├─ .github/workflows/        # 尚未在远端运行的离线质量 workflow
├─ environment/              # 原 Linux Conda 显式环境快照
├─ packaging/                # 原 spec 和 console script 候选
├─ archive/
│  ├─ source_snapshot/       # 原 ZIP，本地保留、默认不提交
│  └─ legacy/                # 旧版/试验代码，本地保留、默认不提交
├─ .env.example
├─ .gitignore
├─ requirements.txt
└─ README.md
```

### 当前主线文件

| 文件 | 作用 |
|---|---|
| `apps/operator_gui/main.py` | Qt 视图编排、依赖组装、按钮状态和 legacy 锁兼容 |
| `apps/operator_gui/mainWindow.ui` | Qt Designer 界面源文件 |
| `apps/operator_gui/mainWindow.py` | 由 UI 生成的 Python 界面类 |
| `apps/operator_gui/overlap.py` | 新检索服务与旧函数名之间的兼容 facade |
| `apps/operator_gui/zmq_client_dealer.py` | legacy ZMQ sender 的兼容 facade |
| `apps/acquisition_agent/main_file_transport_h5df.py` | legacy 采集原型的兼容启动入口 |
| `src/waveform_storage/application/` | 无 Qt 的检索与采集应用服务 |
| `src/waveform_storage/processing/alignment.py` | strict/coverage 对齐和 `AlignmentResult` |
| `src/waveform_storage/io/hdf5_io.py` | 单 BPM、v2 和 legacy HDF5 读写 |
| `src/waveform_storage/config/registry.py` | 设备注册表校验、导入和兼容格式生成 |

## 配置

### 环境变量

不要把密码写进源码或提交到 Git。参考 `.env.example` 设置：

| 变量 | 用途 |
|---|---|
| `WAVEFORM_ZMQ_SERVER_HOST` | GUI 发送采集命令的 ZMQ 主机 |
| `WAVEFORM_ZMQ_SERVER_PORT` | ZMQ 端口，默认 5000 |
| `WAVEFORM_ZMQ_RECEIVE_TIMEOUT_MS` | ZMQ 接收超时，默认 3000 ms |
| `WAVEFORM_STORAGE_HOST` | 中央 HDF5 存储 SSH 主机 |
| `WAVEFORM_STORAGE_PORT` | 中央存储 SSH 端口，默认 22 |
| `WAVEFORM_STORAGE_USER` | 中央存储 SSH 用户 |
| `WAVEFORM_STORAGE_PASSWORD` | 中央存储 SSH 密码 |
| `WAVEFORM_STORAGE_DATA_ROOT` | 中央存储数据根目录 |
| `WAVEFORM_DEVICE_SSH_USER` | BPM 设备 SSH 用户 |
| `WAVEFORM_DEVICE_SSH_PASSWORD` | BPM 设备 SSH 密码 |
| `WAVEFORM_DEVICE_SSH_PORT` | BPM 设备 SSH 默认端口 |
| `WAVEFORM_DEVICE_REMOTE_DIR` | BPM 设备远程 TXT 目录 |
| `WAVEFORM_LOCAL_DATA_ROOT` | 采集端本地数据根目录 |
| `WAVEFORM_CONFIG_DIR` | 可选，自定义配置目录 |
| `WAVEFORM_ALIGNMENT_EXPORT_FORMAT` | 对齐导出格式：`auto`（默认）、`legacy` 或 `v2` |
| `WAVEFORM_SSH_CONNECT_TIMEOUT_SECONDS` | SSH 连接超时，默认 30 秒 |
| `WAVEFORM_EPICS_CONNECT_TIMEOUT_SECONDS` | EPICS 连接超时，默认 10 秒 |

推荐后续改成 SSH key 或系统密钥管理，不长期使用密码环境变量。
这些值由纯 Python 的 `waveform_storage.config.settings.AppSettings` 统一解析；凭据不会进入
对象表示或公共诊断字典。

### 配置文件

| 文件 | 用途 |
|---|---|
| `config/devices.json` | 设备单一事实源；当前包含 47 台、其中 12 台启用采集 |
| `config/pv_list_ALL.txt` | GUI 用于设备标准顺序的全量 PV 清单 |
| `config/SWITCH_PV_IP.txt` | 原 GUI/触发工具使用的 12 台设备清单 |
| `config/acquisition_pv_list.txt` | 当前采集端读取的 PV/IP 清单 |
| `config/duration_seconds.txt` | 采集端一次任务的总时长，当前为 600 秒 |
| `config/BPMS_TO_STORE.txt` | 原默认设备草稿；格式有问题，当前主线未读取 |
| `config/variants/` | 历史配置变体，不应直接作为正式配置使用 |

`config/devices.json` 是新增或修改设备时应维护的文件。两个旧 JSON-lines 清单仍供现有 GUI/采集原型读取，
由 `tools/device_registry.py` 校验和生成；详见 [设备注册表](docs/设备注册表.md)。

## 开发环境

项目使用 Python 3.10。Windows 本机已经建立并验证 Conda 环境 `waveform-storage`：

```bash
conda env create -f environment.yml
conda activate waveform-storage
```

已有环境可用 `conda env update -f environment.yml --prune` 同步。Linux 电脑也优先使用同一个
`environment.yml` 新建环境；GUI 运行还需要桌面会话和 Qt 所需系统库。`environment/conda-linux-64-explicit.txt`
只是原环境快照，没有完整覆盖源码依赖，不应作为唯一依赖来源。

项目现已采用 `src` layout，可在现有环境中离线执行：

```powershell
python -m pip install -e . --no-deps
python -c "import waveform_storage"
```

当前阶段不新增 console script，原 GUI 与采集脚本入口保持不变。

## 运行

### GUI

先设置 SSH 密码等环境变量，再从仓库根目录执行：

```bash
python apps/operator_gui/main.py
```

GUI 会从顶层 `config/` 读取配置。

### 可扩展实验 GUI

实验入口按束线区域整组选取设备，区域下的 BPM 只读展示，不能单独勾选；界面由设备注册表
动态生成，可用于后续约 400 台设备的布局和交互验证。它不会替换原 GUI，也不会把候选通信
协议接入生产。Windows 本机可双击：

```text
tools\launch_experimental_ui.cmd
```

也可从仓库根目录执行：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
& 'D:\miniconda3_repair\envs\waveform-storage\python.exe' `
  apps\operator_gui\experimental_main.py
```

实际 Windows Qt 渲染截图位于 `docs/ui-preview/experimental-main-windows.png`；结构化错误
弹窗截图位于 `docs/ui-preview/experimental-error-dialog.png`。

### 采集端原型

```bash
python apps/acquisition_agent/main_file_transport_h5df.py
```

采集端目前仍是原型。启动前至少要确认：

- EPICS Channel Access 网络；
- ZMQ 端口；
- BPM SSH 账户和权限；
- `/home/data` 中的文件生成规则；
- `WAVEFORM_LOCAL_DATA_ROOT` 的写权限；
- GUI 与采集端的消息协议。

在协议统一前，不要把 GUI 与采集端当作已经联通的生产系统。

## 数据格式

### 单 BPM HDF5

当前代码预期：

- `BunchIDData`：形状约为 `(1, 1024)`；
- `Data`：形状约为 `(2, 1024)`；
- `Data[0, :]`：X；
- `Data[1, :]`：Y；
- 属性包含设备名和 X/Y 描述。

### 合并 HDF5

`src/waveform_storage/io/hdf5_io.py` 提供版本化 v2 读写和旧合并格式适配器。v2 保存
`uint64` bunchID、X/Y、`valid_mask`、设备 ID、跳过原因、质量报告、参数和元数据。
旧 GUI 导出仍按设备交错写入 `Data`，但已修复大 bunchID 溢出及跳过设备后的元数据错位。
coverage 模式存在缺失单元时只能写 v2，旧格式适配器会明确拒绝，避免把占位值误当真实数据。

## Git 内容策略

首个 Git 版本有意不跟踪：

- 原始 ZIP；
- 历史试验/备份源码；
- 历史锁文件和状态文件；
- HDF5 二进制样例；
- 本地凭据、虚拟环境、缓存和日志。

这些文件仍保留在本地对应目录中。这样可以保留原始材料，同时避免把明文凭据、运行状态和实验数据写进不可逆的 Git 历史。

## 文档

- [Codex 项目交接与改进规划](docs/CODEX_HANDOFF.md)：新任务窗口继续工作时首先阅读。
- [结构重构完成审计](docs/结构重构完成审计_2026-07-25.md)：七阶段提交、最终离线证据、兼容结论和未验证边界。
- [问题清单与改进路线](docs/问题清单与改进路线.md)：当前问题状态、风险和后续验收标准。
- [2026-07-25 本轮工作交接](docs/本轮工作交接_2026-07-25.md)：早期设备注册表和对齐接入阶段的历史快照，不代表当前工作区。
- [结构重构总目标模式提示词](docs/结构重构总目标模式提示词.md)：已执行完成的七阶段历史实施方案，不应再次作为当前任务直接执行。
- [线上运行基线调查](docs/线上运行基线调查.md)：生产服务的只读事实、风险和待补证据。
- [数据对齐 v2 设计基线](docs/数据对齐v2设计.md)：纯函数、覆盖共识和接入边界。
- [设备注册表](docs/设备注册表.md)：47/200 台设备配置、校验和旧格式生成方法。
- [采集协议 v1 候选](docs/采集协议v1候选.md)：仅用于本地/fake 测试的版本化消息和状态模型。
- [通信升级调查目录](docs/通信升级调查/README.md)：生产服务、ZMQ、任务、文件和回滚事实的统一查询模板。
- [项目功能与文件说明](docs/项目功能与文件说明.md)：原始项目逐文件静态分析。

## 本地测试

对齐 v2 已最小接入 GUI 的确认、绘图和导出入口；三条路径按日期、时刻、远程目录和设备清单
共享同一个 `AlignmentResult`，查询条件不变时不重复下载或对齐。导出默认 `auto`：完整结果
保持 legacy 格式，coverage 缺失结果写 v2。从仓库根目录运行：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m unittest discover -s tests -v
```

当前实验分支 89 项离线测试覆盖原行为基线、Settings、SSH/绘图拆分、RetrievalService、GUI
Controller/Worker、严格 TXT、fake EPICS/SSH、CaptureService、本地 fake ZMQ、日志、
双协议通信分派、协议到 CaptureService 的本地组合闭环、对齐、注册表和 HDF5；不连接
EPICS、SSH、生产 ZMQ 或生产存储。`main` 的已审计基线仍为 79 项。

完整本地质量门禁使用当前环境中已有工具：

```powershell
& 'D:\miniconda3_repair\envs\waveform-storage\python.exe' tools\run_quality.py
```

它运行 unittest、compileall、注册表/legacy 一致性、非 legacy 新模块的裸
`except`/`print` 检查、tracked-source secret heuristic、`git diff --check` 和包导入。
Ruff、mypy 和外部 secret scanner 仅有候选配置，本机未安装或运行，不能把配置存在表述为
对应工具已经通过。

## 下一步

七阶段结构重构及其后的 GUI/legacy 边界收缩已经完成。设备注册表、对齐/HDF5、后台
worker、应用服务和本地 fake ZMQ 基础均已建立；尚未部署，也未完成 Linux GUI 点击流或
生产链路验证。下一批按以下顺序推进：

1. 取得约 400 台的新设备表，先生成 candidate 注册表和新旧差异报告，不覆盖现有配置；
2. 用脱敏真实批次比较 legacy 与 v2 的设备选择、ID 数量和耗时；
3. 确认 strict/coverage、不同 bunchID 时基、X/Y 单位、`/1000` 缩放和 1024 点正式语义；
4. 在 Linux/PyQt 环境验证七按钮点击流、后台状态、错误提示和安全取消；
5. 在现有 fake ZMQ、fake EPICS/SSH 基础上补齐组合闭环；生产协议确认前不替换 legacy sender；
6. 获得远端仓库授权后配置 upstream，并让现有 GitHub Actions 在远端实际运行。
