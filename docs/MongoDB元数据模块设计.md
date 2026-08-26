# MongoDB 元数据模块设计

## 1. 当前范围

本模块已接入 candidate v2 采集服务和 GUI 历史查询。默认
`WAVEFORM_MONGODB_ENABLED=false`，因此未显式启用时不会连接数据库，也不会改变现有
EPICS、SSH、TXT、HDF5 和设备注册表行为。legacy 模式仍保留原来的 SSH 目录扫描流程。

波形数组继续存放在 HDF5 中。MongoDB 只保存：

- 采集任务及状态历史；
- 每台设备对应的 HDF5 文件路径和紧凑格式描述；
- 本次采集采用的非敏感注册表快照。

## 2. 集合

### `capture_tasks`

`_id` 为 request ID。保存频率、时长、区域、设备、注册表版本、任务状态、状态历史、
设备成功/失败数量以及软件版本。

### `waveform_files`

`_id` 为 `<request_id>:<device_id>`。保存 HDF5 所在存储、路径、文件格式版本、大小、
可选 SHA-256、数据集 shape/dtype、点数、单位和缩放信息。不保存波形数组。

### `device_registry_versions`

`_id` 为调用方计算的注册表内容哈希。保存可追溯的非敏感设备字段。不得写入密码、私钥、
MongoDB URI 或其他凭据。

## 3. 配置

```text
WAVEFORM_MONGODB_ENABLED=false
WAVEFORM_MONGODB_URI=mongodb://user:password@host:27017/?authSource=admin
WAVEFORM_MONGODB_DATABASE=waveform_storage
WAVEFORM_MONGODB_SERVER_SELECTION_TIMEOUT_MS=3000
WAVEFORM_MONGODB_REQUIRED=false
```

URI 属于秘密配置，不进入日志、诊断结果或版本库。`MongoSettings.public_values()` 只报告
URI 是否配置，不返回 URI 内容。

## 4. candidate v2 写入和查询

`MongoMetadataRepository.connect()` 才会导入 PyMongo 和创建客户端。导入
`waveform_storage.metadata` 本身不要求 MongoDB 可用。

服务端当前执行顺序：

1. 服务启动时创建 repository、执行 `ping()` 和 `ensure_indexes()`；
2. request 被接受后写 `capture_tasks`；
3. HDF5 `.part` 原子替换为正式文件后，才写入 `waveform_files.status=completed`；
4. 汇总设备结果并更新任务终态；
5. GUI 通过 candidate v2 请求查询日期下的时刻和指定设备的 HDF5 path；
6. GUI 按服务端返回的精确 path，经现有存储 SSH 通道读取或下载 HDF5。

MongoDB 写入失败不会阻止 HDF5 落盘，错误会进入服务日志。当前尚未实现本地 outbox，
因此数据库故障期间完成的 HDF5 需要后续补录。启用 `WAVEFORM_MONGODB_REQUIRED=true`
会在服务启动时要求 MongoDB 可连接，但运行期间仍以保护波形采集为优先。

## 5. 历史文件回填与本地验证

历史单设备 HDF5 可以通过 `tools/backfill_mongodb.py` 校验并幂等回填。工具从
`YYYYMMDD` 目录和 `RAW-HHMMSS-<device>-S00000.h5` 文件名解析日期、时间和设备，核对
注册表、HDF5 `Device` 属性、dataset shape/dtype，并计算 SHA-256。所有文件通过校验后才
开始写 MongoDB。

```bash
set -a
source .env
set +a

python tools/backfill_mongodb.py ../20251112 --dry-run
python tools/backfill_mongodb.py ../20251112
```

若扫描路径与存储 SSH 账户看到的路径不同，必须显式提供 `--catalog-directory`，确保
MongoDB 保存的是 GUI 可以访问的服务端路径。

重复采集模式下，同一 request/device 会产生多个文件。`waveform_files` 使用
`request_id + device_id + capture_sequence` 唯一索引，文档 `_id` 末尾为 `Sxxxxx`；历史
回填也从文件名保存序列号。服务启动时会将旧的 request/device 唯一索引替换为序列索引。

`tools/verify_mongodb_history.py` 用真实 MongoDB、临时 candidate v2 ZMQ 服务和本地 HDF5
验证查询与对齐链路。`tools/run_local_history_gui.py` 仅用于开发机 GUI 联调：它通过
candidate v2 查询 MongoDB，但显式绕过存储 SSH，直接读取 MongoDB 返回的本地路径。
该模式不代表生产存储 SSH 或真实硬件采集已经验证。

2026-08-25 已将 `20251112` 样本的 22 个 HDF5 回填到本机开发数据库。实测：

- `11:00:23` 返回 10 个文件，严格对齐得到 1023 个公共 bunch ID；
- `15:41:01` 返回 12 个文件，各文件可单独读取，但 12 台设备没有全局公共 bunch ID，
  严格对齐安全返回空结果。

数据库会自动索引接入后的新采集文件。其他既有历史目录仍需逐目录执行回填工具；当前未
实现自动全盘扫描或 MongoDB 故障后的 outbox 重试。
