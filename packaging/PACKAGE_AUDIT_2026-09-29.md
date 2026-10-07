# 正式版打包清点（2026-09-29）

> 历史打包审计。当前交付入口见 [交付索引](../交付/README_先看.md)。原输出包于 2026-10-07 归入 `archive/build-artifacts/`；下表 `dd8f2a1ec88e` 是当时记载的前端包，整理前工作区中已不存在该文件，本次没有重新生成。

## 旧产物核对

| 旧产物 | 清点结果 | 结论 |
|---|---|---|
| `archive/source_snapshot/StreamDataPro.zip` | 37 项，含 `main_bak251105.py`、多个测试脚本、锁状态文件和样本 HDF5 | 旧工程归档，不能当正式发布包 |
| `archive/legacy/waveform-storage-server-323121c.tar.gz` | 46 项，仅服务端源码；含纯 Python 的 `gui/beamline_selection.py`，不含操作界面或 demo 后端 | 旧修订版，需用当前源码重打 |
| `archive/build-artifacts/dist/deepin20.9/waveform-acquisition-v2-323121c-*.tar.gz` | 1043 项，是服务端 PyInstaller 包；其中 154 项是第三方依赖的 tests/examples/ioc_examples（`caproto` 102、`h5py` 50、`zmq` 2） | 没有项目 GUI，但不是最小包；其他同类旧版本也不作为本次交付 |
| `archive/build-artifacts/environment/waveform-storage-conda-linux-x86_64-py310-20260929.tar.gz` | 20560 项，是完整 Conda 环境；含标准库/依赖的测试与示例。其 editable `.pth` 指向开发机绝对路径 `/home/whloong/waveform-storage/waveform-storage-system/src` | 不含完整项目源码，也不是可独立迁移的正式程序包 |
| `packaging/legacy-main.spec` | 旧入口的 PyInstaller 配置，未随本次正式包进入产物 | 不用于新包 |

## 本次正式包

| 产物 | 内容与验证 |
|---|---|
| `outputs/operator-formal/waveform-operator-formal-dd8f2a1ec88e.tar.gz` | 35 个文件，58,288 字节；Candidate V2 正式入口、正式窗口、正式后端、383 台展示注册表、47 台采集注册表、必要源码和依赖清单。解压后从包内 `run-operator.sh` 离屏启动并生成截图。 |
| `archive/build-artifacts/outputs/server-formal/waveform-server-formal-88455f6a1652.tar.gz` | 35 个文件，44,947 字节；Candidate V2 服务入口、服务运行源码、与 GUI 一致的 47 台采集注册表、必要源码和依赖清单。解压后从包内 `run-server.sh --check` 通过。 |

服务包的束线分组规则已移至 `config/beamline_areas.py`，新服务包不再包含 `gui/` 目录。

两包都使用明确的文件允许清单：`operator-formal-files.txt` 与 `server-formal-files.txt`。构建脚本检查解压文件集合、逐项哈希、旧版/demo/测试文件排除项和运行时导入链。每包含 `MANIFEST.sha256`、`BUILD-INFO`，旁边另有压缩包 `.sha256`。两包重复构建哈希一致，本次回归测试 144 项通过。

包内**不含**旧 Designer GUI、demo 后端、旧 ZMQ facade、`tests/`、样本数据、`.env`、密码、SSH 私钥、日志或 Python/Conda 环境。源文件中仍可能有共用协议的兼容分支，但正式入口只装载 Candidate V2 后端。

## 运行边界

这两份是**经离线启动验证的源码包**，不是自带 Python 的独立可执行文件。目标机器需按包内 `requirements-*.txt` 准备兼容环境、提供服务地址及现场凭据；GUI 连接服务的 SSH 隧道仍由工作站配置。构建检查不连接现场设备，也不证明现场 EPICS、SSH、MongoDB 或采集全流程已通过。服务默认单请求最多 32 台，虽然两端注册表均包含 47 台可采集设备；要扩大并发范围需另行评估服务配置。
