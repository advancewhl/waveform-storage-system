# Codex 项目交接与改进规划

> 用途：在新的 Codex 任务窗口中继续本项目时，先完整阅读本文。  
> 项目目录：`D:\study_file\postgraduate\x射线自由电子激光\波形存储系统`  
> 更新日期：2026-07-25  
> 当前 Git 分支：`main`  
> 当前基线提交：`e432d0b chore: establish initial project baseline`

## 1. 用户当前目标

在保持现有整体业务功能不变的前提下，逐步完成三项主要改进：

1. BPM 数量从当前几十台扩展到约 200 台；
2. 优化多 BPM 的 bunchID 数据对齐、质量判断和 HDF5 输出；
3. 优化 GUI 技术架构，并在用户后续提供具体需求后增加界面功能。

不要一次性重写整个项目。推荐顺序是：

```text
确认线上基线
→ 统一设备配置
→ 建立测试
→ 统一采集协议和任务模型
→ 优化数据对齐
→ 改造动态 GUI 和后台任务
→ 添加用户提出的具体界面功能
→ 分批现场验证
```

## 2. 开始工作前必须阅读

按以下顺序阅读：

1. `README.md`
2. `docs/CODEX_HANDOFF.md`
3. `docs/问题清单与改进路线.md`
4. `docs/项目功能与文件说明.md`
5. 与当前任务直接相关的源码

每次开始改动前执行：

```powershell
git status --short
git log -1 --oneline --decorate
```

当前基线在上次复核时工作区干净。不要覆盖用户后续产生的未提交修改。

## 3. 当前仓库结构

```text
.
├─ apps/
│  ├─ operator_gui/
│  │  ├─ main.py
│  │  ├─ mainWindow.ui
│  │  ├─ mainWindow.py
│  │  ├─ overlap.py
│  │  ├─ utils.py
│  │  └─ zmq_client_dealer.py
│  └─ acquisition_agent/
│     └─ main_file_transport_h5df.py
├─ config/
│  ├─ pv_list_ALL.txt
│  ├─ acquisition_pv_list.txt
│  ├─ SWITCH_PV_IP.txt
│  ├─ BPMS_TO_STORE.txt
│  ├─ duration_seconds.txt
│  └─ variants/
├─ docs/
├─ environment/
├─ packaging/
├─ data/samples/
└─ archive/
```

原始 ZIP、历史源码、运行时锁和 HDF5 样例仍保留在本地，但被 `.gitignore` 排除。

不要误删：

- `archive/source_snapshot/StreamDataPro.zip`
- `archive/legacy/`
- `data/samples/`

这些文件不进入 Git，但仍用于核对原始实现和数据格式。

## 4. 已经完成的工作

### 4.1 项目整理

- 解压并分类了原 ZIP 中的 36 个文件；
- 当前 GUI 主线放入 `apps/operator_gui/`；
- 采集端原型放入 `apps/acquisition_agent/`；
- 历史 GUI、通信、文件锁和报警项目代码放入本地 `archive/legacy/`；
- 原始 ZIP 移到 `archive/source_snapshot/`；
- HDF5 样例放入 `data/samples/`；
- 建立 README、问题文档、依赖和 Git 忽略规则。

### 4.2 已做的保护性修复

- 活动主线中的明文 SSH 密码改为环境变量；
- GUI 和采集端改为读取顶层 `config/`；
- 修复了 `main.py` 中阻断 ZMQ 发送的无参数 `time.strptime()`；
- 删除了 GUI 重复执行 `acquire_lock()` 的调用；
- 采集端不再使用明文 `root/root`；
- 创建 `config/duration_seconds.txt`；
- 创建 `config/acquisition_pv_list.txt`，供整理后的采集端读取。

注意：`acquisition_pv_list.txt` 不是原压缩包中的原文件。原采集脚本需要 `pv_list.txt`，但项目缺少该文件，因此整理时基于 `SWITCH_PV_IP.txt` 建立了这个名称更明确的配置。

### 4.3 已验证范围

- 活动目录中的 Python 文件均通过语法解析；
- 现有 JSON-line 设备配置可逐行解析；
- 首次 Git 提交不包含 ZIP、HDF5、锁文件或已知明文密码；
- 没有完成现场 EPICS、SSH、ZMQ、GUI 和 HDF5 端到端运行验证。

后续回答或文档中必须区分静态验证和现场运行验证。

## 5. 三个设备 TXT 的当前含义

### `config/pv_list_ALL.txt`

系统认识的全量 BPM 名单和正式排序，目前 47 条。

用途：

- 远程 HDF5 设备名识别；
- bunchID 对齐时的设备顺序；
- 绘图和导出顺序。

### `config/acquisition_pv_list.txt`

整理后采集端实际读取的设备名单，目前 12 条。

用途：

- 读取采集 PV；
- 拼接 EPICS CA 地址；
- 获取设备 IP；
- 连接设备并下载原始文件。

其中 `port` 是 EPICS CA 端口，不是 SSH 端口。当前 SSH 默认端口仍是 22。

### `config/SWITCH_PV_IP.txt`

原项目遗留的旧采集名单，目前也是 12 条。部分旧工具函数仍读取它。

长期目标不是继续同步维护三个 TXT，而是建立唯一设备注册表，再自动生成旧格式兼容文件。

## 6. 当前线上服务器信息

用户已成功 SSH 登录 waveform 服务器：

```text
IP：192.0.2.2
用户：storage-user
系统：Deepin 20.9 GNU/Linux
主机提示符：storage-user@storage-user-PC
```

截图确认：

```text
~/saveApp/
├─ nohup.out
├─ pv_list_ALL.txt
├─ saveApp
└─ zmq-server
```

服务器上的 `pv_list_ALL.txt` 当前为 47 行。

基于本地源码推断，这台服务器很可能同时承担：

1. 接收 GUI 的 ZMQ 采集命令；
2. 协调或执行 EPICS 触发；
3. SSH/SFTP 收集 BPM 原始数据；
4. 转换或保存 HDF5；
5. 作为 `/SHINE_Data` 中央存储的访问入口。

但以下内容尚未确认：

- `saveApp` 是文件、目录、脚本还是 PyInstaller 程序；
- `zmq-server` 是文件还是目录；
- 当前运行的进程；
- ZMQ 监听端口和 socket 类型；
- 实际启动命令；
- 当前日志状态；
- 当前真正使用的消息协议；
- 当前程序是否与本地源码同版本。

不要把推断当作确认事实。

建议先在服务器执行只读检查：

```bash
cd ~/saveApp

pwd
ls -lah
file saveApp zmq-server
ps -ef | grep -E 'saveApp|zmq' | grep -v grep
ss -lntp | grep -E ':5000|saveApp|zmq'
tail -n 100 nohup.out
sha256sum saveApp pv_list_ALL.txt
```

不要在确认启动和回滚方法之前覆盖服务器程序或配置。

## 7. 当前代码的关键事实

### 7.1 GUI

入口：

```text
apps/operator_gui/main.py
```

当前特点：

- `mainWindow.ui/mainWindow.py` 中静态写死约 38 个 BPM 按钮；
- `pv_list_ALL.txt` 已有 47 台，配置和按钮已经不完全一致；
- `main.py` 中另有 12 台默认选中 BPM 常量；
- 点击按钮后遍历固定 `gridLayout` 收集设备；
- 网络、SSH、下载、HDF5、对齐和绘图都在 GUI 主线程；
- 确认、绘图、导出会重复执行 `main_dataprocess()`；
- matplotlib 默认把全部设备名放在横坐标，无法直接适配 200 台。

### 7.2 采集端

入口：

```text
apps/acquisition_agent/main_file_transport_h5df.py
```

当前意图：

```text
等待 ZMQ 消息
→ 读取设备 PV
→ EPICS 执行 0→1→0
→ 最多 17 线程 SSH/SFTP 拉 TXT
→ TXT 转 HDF5
```

当前主要问题：

- 使用 ZMQ `PULL`；
- 只接受能转换为整数的频率消息；
- 不回复 GUI；
- GUI 使用 `DEALER` 并发送频率、时长、设备列表；
- 使用 `sftp.listdir()[0]` 选择文件；
- 采集时长用 `YYYYMMDDHHMMSS` 整数相减；
- TXT 固定假设 1024 点但没有严格校验；
- 所有设备共用 SSH 用户和全局远程目录。

### 7.3 数据对齐

入口：

```text
apps/operator_gui/overlap.py
```

当前方法：

1. 逐个远程下载 HDF5 到临时文件；
2. 读取 `BunchIDData`、X、Y；
3. 用 Python `set` 逐台求交集；
4. 如果交集低于首台有效设备长度的 50%，跳过当前设备；
5. 用 `list.index()` 为每个共同 bunchID 查找索引；
6. 抽取 X/Y 并保存。

主要问题：

- 对齐结果依赖设备加入顺序；
- 200 台严格交集容易被少数坏设备拖小；
- `list.index()` 接近设备数乘 bunchID 数平方；
- 空输入、无交集、重复 bunchID 和未知设备处理不足；
- 跳过设备后 HDF5 元数据可能错位；
- 网络读取串行；
- 相同数据被重复下载和对齐。

## 8. 新 PV 表到达后的处理原则

用户正在向老师索取统计后的新 PV list，目标约 200 台。

不要直接覆盖本地或服务器的 `pv_list_ALL.txt`。

建议处理流程：

```text
老师原始文件
→ 原样保存并记录日期/来源
→ 解析
→ 字段和重复项校验
→ 与当前 47 台生成差异报告
→ 人工确认
→ 转换为统一设备注册表
→ 自动生成旧格式兼容文件
```

如果原始清单包含内部信息或凭据，未经检查不要提交 Git。

建议统一设备字段：

```yaml
id:
section:
display_name:
device_type:
capture_pv:
epics_host:
epics_port:
ssh_host:
ssh_port:
credential_profile:
remote_data_dir:
file_pattern:
expected_points:
x_unit:
y_unit:
scale_factor:
order:
enabled:
default_selected:
tags:
```

必须区分：

- EPICS 地址与 SSH 地址；
- EPICS 端口与 SSH 端口；
- 系统全量设备与本次实际采集设备；
- 凭据名称与真实密码。

真实密码不得写入设备注册表或 Git。

## 9. 推荐实施顺序和完成条件

### 阶段 0：线上运行基线

目标：明确服务器真实工作方式，不修改生产程序。

任务：

- 检查 `saveApp`、`zmq-server` 类型；
- 获取进程、端口、启动命令和日志；
- 确认 `/SHINE_Data` 目录、权限和容量；
- 获取一份真实 TXT/HDF5 样例；
- 确认停止、启动和回滚方式；
- 确认允许测试的 BPM 和时间窗口。

完成条件：

- 可以画出实际线上数据流；
- 知道哪个进程负责哪一步；
- 知道修改失败时如何恢复。

### 阶段 1：统一设备注册表

对应问题：WF-018、WF-014。

任务：

- 设计设备 schema；
- 编写新 PV 表导入器；
- 校验重复、缺失、命名和排序；
- 生成当前 47 台与新表的差异报告；
- 自动生成旧格式 `pv_list_ALL.txt` 和采集清单；
- 不修改业务逻辑。

完成条件：

- 设备增删只需要修改一处；
- 可以稳定加载约 200 台；
- 配置错误在启动前被发现；
- 旧程序仍可读取生成的兼容文件。

### 阶段 2：建立测试基线

对应问题：WF-019、WF-020、WF-005。

至少测试：

- 配置解析；
- PV 到设备 ID 规范化；
- 文件名到设备 ID；
- TXT 三列解析；
- 单 BPM HDF5 round-trip；
- 当前 bunchID 对齐；
- 空输入；
- 含 0；
- 无交集；
- 重复 bunchID；
- 未知设备；
- 跳过设备后的元数据。

完成条件：

- 不连接现场服务器也能运行核心测试；
- 后续算法变化可以比较新旧结果。

### 阶段 3：统一采集协议和任务模型

对应问题：WF-001、WF-002、WF-009、WF-010、WF-013。

建议协议：

```json
{
  "version": 1,
  "request_id": "uuid",
  "command": "start_capture",
  "frequency_hz": 100,
  "duration_seconds": 600,
  "devices": ["AC_LA_INJ1_BI_SBPM08"]
}
```

服务端至少返回：

```json
{
  "request_id": "uuid",
  "accepted": true,
  "status": "running",
  "message": "capture started"
}
```

要求：

- 有明确请求 ID；
- 重试不会重复启动任务；
- 支持错误码和状态查询；
- 设备级超时、重试和结果；
- 有限制的并发，默认可从 16～32 起测；
- 单台失败不阻断全批；
- 可保留旧协议适配器，避免一次性破坏线上系统。

### 阶段 4：数据对齐 v2

对应问题：WF-005、WF-006、WF-007、WF-012、WF-017。

先拆出纯数据模块，不依赖 Qt、SSH 或 matplotlib。

建议结果结构：

```text
AlignmentResult
├─ bunch_ids
├─ device_ids
├─ x
├─ y
├─ valid_mask
├─ skipped_devices
├─ quality_report
└─ parameters
```

处理流程：

```text
单设备质量检查
→ 剔除明确坏数据
→ 选择严格交集或覆盖率模式
→ NumPy 向量化索引
→ 生成结构化结果
→ 绘图/导出适配器
```

至少支持：

- 严格共同交集；
- 可配置设备覆盖率；
- 空数据和无交集；
- bunchID 为 0；
- bunchID 重复或乱序；
- 设备缺失；
- 不同长度；
- 设备级质量报告；
- 新 HDF5 schema；
- 在 schema 未确认前保留旧格式导出。

性能方向：

- 避免 `list.index()`；
- 使用 `numpy.intersect1d`、`searchsorted` 或索引映射；
- 限制并发下载；
- 缓存同一日期/时刻/设备集合的读取结果；
- 确认、绘图、导出共享同一个 `AlignmentResult`。

### 阶段 5：动态 GUI 和后台任务

对应问题：WF-008、WF-016、WF-025。

不要继续为每台 BPM 手工增加按钮。

建议结构：

```text
MainWindow
├─ DeviceModel
├─ CaptureController
├─ RetrievalController
├─ AlignmentService
├─ DownloadWorker
└─ PlotController
```

基础要求：

- 使用 `QTreeView` 或 `QTableView` 动态显示约 200 台；
- 按区段组织；
- 设备模型来自统一注册表；
- 使用 `QThreadPool/QRunnable` 或 `QThread`；
- SSH、下载、HDF5 和对齐不在主线程；
- 建立 IDLE、CAPTURING、DOWNLOADING、ALIGNING、READY、FAILED 等状态；
- 不重复下载和对齐；
- 绘图不默认一次显示 200 个设备标签。

具体界面新增功能由用户后续提出，不要提前替用户决定最终交互。

### 阶段 6：分批现场验证

顺序：

```text
离线 fixture
→ 1 台只读 EPICS
→ 1 台实际触发
→ 3 台采集和对齐
→ 一个区段
→ 20～30 台
→ 100 台
→ 约 200 台
```

记录：

- PV 连接成功率；
- 触发成功率；
- 文件生成时间；
- SSH 下载耗时；
- 缺失和坏设备；
- bunchID 有效率；
- 对齐数量；
- CPU、内存、网络和磁盘；
- GUI 响应；
- 失败和恢复。

## 10. Git 工作方式

当前 `main` 是整理后的初始基线。

建议后续分支：

```text
feature/device-registry
feature/acquisition-protocol
feature/alignment-v2
feature/gui-v2
```

原则：

- 每一阶段单独提交；
- 不把设备表、协议、对齐和 GUI 重构混在一个巨大提交；
- 修改前先检查工作区；
- 不覆盖用户未提交文件；
- 每次提交前执行语法、测试、配置校验和 secret scan；
- 不提交 ZIP、HDF5、锁文件、日志、真实密码或未审查的老师原始清单。

## 11. 安全与现场操作边界

在用户明确授权并确认测试窗口之前：

- 不批量执行 `caput`；
- 不触发约 200 台 BPM；
- 不停止 waveform 服务器进程；
- 不覆盖服务器 `saveApp`、`zmq-server` 或 `pv_list_ALL.txt`；
- 不删除服务器数据；
- 不修改 `/SHINE_Data`；
- 不把密码写入命令输出、日志、源码或 Git；
- 不假设 `192.0.2.2` 上的程序与本地源码一致。

对服务器进行修改前必须：

1. 确认目标文件；
2. 保存带时间戳备份和哈希；
3. 确认停止/启动命令；
4. 确认回滚步骤；
5. 从 1 台测试设备开始。

## 12. 不建议现在做的事情

- 不要先全面美化或格式化全部历史代码；
- 不要手工添加约 200 个 Qt 按钮；
- 不要直接把老师的新列表覆盖三个 TXT；
- 不要在没有测试的情况下重写对齐算法；
- 不要在 HDF5 schema 未确认前取消旧格式；
- 不要把所有工作堆进 `main.py` 或 `overlap.py`；
- 不要因为能 SSH 登录就认为已经具备部署条件；
- 不要声称现场运行验证，除非实际完成并有证据。

## 13. 新 Codex 窗口的第一个任务

如果新 PV 表尚未拿到：

1. 先读取本文和问题文档；
2. 检查 Git 状态；
3. 帮助用户完成 waveform 服务器的只读基线调查；
4. 根据命令输出更新事实，不修改服务器；
5. 明确 `saveApp`、`zmq-server`、端口、进程和日志。

如果新 PV 表已经拿到：

1. 不直接覆盖现有配置；
2. 读取原文件格式；
3. 确认字段是否包含 PV、IP、EPICS 端口、区段和排序；
4. 生成数量、重复、缺失和新旧差异报告；
5. 先提出设备 schema 和导入方案；
6. 用户确认后再实现统一设备注册表。

## 14. 建议用户在新窗口发送的提示

```text
继续维护 D:\study_file\postgraduate\x射线自由电子激光\波形存储系统。

请先完整阅读：
1. docs/CODEX_HANDOFF.md
2. README.md
3. docs/问题清单与改进路线.md

先检查 git status，不要覆盖我的现有修改，不要修改 waveform 生产服务器。
当前目标是在整体功能不变的前提下支持约 200 台 BPM、优化数据对齐并改进 GUI。
根据交接文档，从当前阶段继续。
```

