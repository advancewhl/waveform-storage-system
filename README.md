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
3. 项目内没有一份完整的、能回复 GUI 的采集服务端。
4. 代码依赖 Linux `fcntl`，Windows 只适合阅读、整理和运行不涉及锁的静态检查。
5. 现场 EPICS、SSH、存储路径和 HDF5 schema 仍需确认。

完整问题和验收条件见 [问题清单与改进路线](docs/问题清单与改进路线.md)。

## 目录结构

```text
.
├─ apps/
│  ├─ operator_gui/          # 当前 GUI 主线
│  └─ acquisition_agent/     # 无界面采集/搬运原型
├─ config/                   # 当前配置和历史配置变体
├─ data/
│  └─ samples/               # 本地 HDF5 样例，默认不提交 Git
├─ docs/
│  ├─ 项目功能与文件说明.md
│  └─ 问题清单与改进路线.md
├─ environment/              # 原 Linux Conda 显式环境快照
├─ packaging/                # 原 PyInstaller spec，仅供参考
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
| `apps/operator_gui/main.py` | GUI 入口、采集按钮、检索、绘图和下载流程 |
| `apps/operator_gui/mainWindow.ui` | Qt Designer 界面源文件 |
| `apps/operator_gui/mainWindow.py` | 由 UI 生成的 Python 界面类 |
| `apps/operator_gui/overlap.py` | SSH/SFTP、HDF5 读取、bunchID 对齐、绘图和导出 |
| `apps/operator_gui/zmq_client_dealer.py` | GUI 的 ZMQ DEALER 客户端 |
| `apps/acquisition_agent/main_file_transport_h5df.py` | 采集、TXT 搬运和 HDF5 转换原型 |

## 配置

### 环境变量

不要把密码写进源码或提交到 Git。参考 `.env.example` 设置：

| 变量 | 用途 |
|---|---|
| `WAVEFORM_ZMQ_SERVER_HOST` | GUI 发送采集命令的 ZMQ 主机 |
| `WAVEFORM_STORAGE_HOST` | 中央 HDF5 存储 SSH 主机 |
| `WAVEFORM_STORAGE_USER` | 中央存储 SSH 用户 |
| `WAVEFORM_STORAGE_PASSWORD` | 中央存储 SSH 密码 |
| `WAVEFORM_DEVICE_SSH_USER` | BPM 设备 SSH 用户 |
| `WAVEFORM_DEVICE_SSH_PASSWORD` | BPM 设备 SSH 密码 |
| `WAVEFORM_DEVICE_REMOTE_DIR` | BPM 设备远程 TXT 目录 |
| `WAVEFORM_LOCAL_DATA_ROOT` | 采集端本地数据根目录 |
| `WAVEFORM_CONFIG_DIR` | 可选，自定义配置目录 |

推荐后续改成 SSH key 或系统密钥管理，不长期使用密码环境变量。

### 配置文件

| 文件 | 用途 |
|---|---|
| `config/pv_list_ALL.txt` | GUI 用于设备标准顺序的全量 PV 清单 |
| `config/SWITCH_PV_IP.txt` | 原 GUI/触发工具使用的 12 台设备清单 |
| `config/acquisition_pv_list.txt` | 当前采集端读取的 PV/IP 清单 |
| `config/duration_seconds.txt` | 采集端一次任务的总时长，当前为 600 秒 |
| `config/BPMS_TO_STORE.txt` | 原默认设备草稿；格式有问题，当前主线未读取 |
| `config/variants/` | 历史配置变体，不应直接作为正式配置使用 |

目前 PV 清单采用“每行一个 JSON 对象并带逗号”的历史格式，不是标准 JSON。后续应迁移为一个经过 schema 校验的 YAML 或 JSON 文件。

## 开发环境

目标运行环境是 Linux。建议使用 Python 3.10 创建独立虚拟环境：

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`environment/conda-linux-64-explicit.txt` 是原环境快照，但没有完整覆盖源码依赖，不建议直接把它当作唯一依赖来源。

## 运行

### GUI

先设置 SSH 密码等环境变量，再从仓库根目录执行：

```bash
python apps/operator_gui/main.py
```

GUI 会从顶层 `config/` 读取配置。

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

`overlap.py` 会对多个 BPM 的共同 bunchID 进行对齐，并把 X/Y 按设备交错写入 `Data`。当前元数据在跳过异常 BPM 时可能错位，修复前不要把该格式作为稳定公共接口。

## Git 内容策略

首个 Git 版本有意不跟踪：

- 原始 ZIP；
- 历史试验/备份源码；
- 历史锁文件和状态文件；
- HDF5 二进制样例；
- 本地凭据、虚拟环境、缓存和日志。

这些文件仍保留在本地对应目录中。这样可以保留原始材料，同时避免把明文凭据、运行状态和实验数据写进不可逆的 Git 历史。

## 文档

- [项目功能与文件说明](docs/项目功能与文件说明.md)：原始项目逐文件静态分析。
- [问题清单与改进路线](docs/问题清单与改进路线.md)：按优先级列出的修复任务、风险和验收标准。

## 下一步

建议先完成 Milestone 1：

1. 定义版本化 JSON ZMQ 协议；
2. 实现可测试的采集服务端；
3. 用 fake EPICS/SSH 环境跑通 GUI 到服务端；
4. 修复 bunchID 对齐边界情况和 HDF5 元数据；
5. 再进行 GUI 线程化、日志、打包和部署工作。

