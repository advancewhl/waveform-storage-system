# 波形存储发布检查清单

本清单适用于正式运行链路。GUI、后端、检索、分析、打包依赖或客户端协议修改触发前端发布；服务、元数据、Mongo 查询、协议、运行时或文件处理修改触发服务端发布。协议变化必须发布并验证兼容的版本对。

## 代码与构建

- [ ] 明确改动范围，保留原始数据格式和兼容接口。
- [ ] 运行相关测试。
- [ ] 使用项目 Python 执行 `python tools/run_quality.py`，保存完整结果。
- [ ] 在构建前记录源文件清单和 SHA-256，构建后确认源码未变化。
- [ ] 记录真实 `source_revision`。工作目录构建标记 `source_state=working-tree`。

统一构建命令如下。工具先运行质量门，再执行真实 PyInstaller 构建。服务端使用 Debian 10 镜像，以匹配现场兼容基线。

```bash
/home/whloong/miniconda3/envs/waveform-storage/bin/python \
  tools/release_waveform.py build-pair --release-id <release-id>
```

工具创建独立目录，已有目录会拒绝覆盖。日志和版本对写入 `outputs/release-pairs/<release-id>/`。源码归档不等于二进制构建成功。

## 前端发布

- [ ] 新二进制位于 `outputs/operator-local/releases/<release-id>/waveform-operator/`。
- [ ] `BUILD-INFO` 包含 kind、日期、release ID、源码状态、源码清单哈希、二进制哈希、Python 与关键依赖版本、协议能力。
- [ ] 校验 `MANIFEST.sha256`，用二进制执行启动验证。
- [ ] 记录 previous_operator_release。
- [ ] 校验后原子切换 `outputs/operator-local/current`，确认 `readlink -f` 结果。
- [ ] `.desktop` 引用 `tools/launch_operator_desktop.sh`，启动器优先解析 current，旧包只作后备。
- [ ] 从真实桌面入口启动，记录运行进程的 `/proc/<pid>/exe`、SHA-256 和 BUILD-INFO。

激活命令会校验二进制与整个清单。

```bash
python tools/release_waveform.py activate-operator \
  outputs/operator-local/releases/<release-id>/waveform-operator
```

启动后的旧窗口继续使用旧代码。验证必须针对本次新进程。

## 服务端发布

- [ ] 服务端二进制、BUILD-INFO、源码清单、文件 manifest 与归档 SHA-256 已生成。
- [ ] 只读确认当前 release、systemd unit、ExecStart、MainPID、环境文件和状态目录。
- [ ] 在维护窗口停止提交新采集，确认没有 accepted/running 采集任务；有任务时等待完成。
- [ ] 在 `/home/storage-user/waveform-storage-v2/releases/<release-id>/` 解包和校验新版本。
- [ ] 沿用现场 service.env、设备注册表、主机密钥、state 和数据目录。
- [ ] 执行新二进制 `--check`，检查配置。
- [ ] 记录 previous_server_release，再原子切换 server current。
- [ ] 重启 `waveform-acquisition-v2.service`，验证 active、监听端口和启动日志。
- [ ] 核对实际 `/proc/<MainPID>/exe` 与本次 current，校验运行二进制 SHA-256。

systemd 固定引用 current，保持下列 ExecStart：

```text
/home/storage-user/waveform-storage-v2/current/waveform-acquisition-v2/waveform-acquisition-v2
```

现场若使用源码模式，应单独规划迁移，备份旧 unit，并记录回滚命令。不能直接用新 unit 覆盖现状。

## 真实集成验收

- [ ] 正式启动器使用的 SSH 隧道连接本地 endpoint 与远端 5001。
- [ ] 实际 endpoint 的 health 成功。
- [ ] list_capture_runs、list_capture_segments、find_waveform_files_by_segment 全部成功。
- [ ] GUI 加载任务，并显示采样率、请求时长和段数。
- [ ] GUI 切换数据段，旧选择、对齐、绘图和历史频谱上下文清空。
- [ ] BPM 选择只包含该段实际文件；真实 sequence 可跳号。
- [ ] 实际完成单段对齐、原始下载和对齐结果导出。
- [ ] 查询、输出目录和导出元数据均使用相同的完整任务及段身份。
- [ ] 有现场部分成功段时验证；没有合适现场数据时记录集成 fixture 验证。
- [ ] 日志页直接只读 MongoDB，distinct 段数与设备-段保存总数分别显示。

可通过真实桌面入口运行受控验收。该模式自动操作真实 GUI 按钮，只读历史文件并写入本地验收目录，不发送采集请求。结果包含实际进程、桌面文件、endpoint、查询身份、输出文件和截图；失败返回非零状态。

```bash
python tools/smoke_operator_desktop.py --date 2026-10-06 \
  --report outputs/release-pairs/<release-id>/gui-history.json
```

`gio launch` 返回只代表发起启动。必须等待并检查报告中的 `status=passed`。验收结束后退出该测试进程。

## 记录与交付

- [ ] operator/server release pair 记录版本、双方 capabilities、兼容要求和部署状态。
- [ ] `docs/CURRENT_STATUS.md` 列出本机前端、现场前端、服务端、日志页和 MongoDB 的真实状态。
- [ ] `交付/<release-id>/` 包含 README、版本对、SHA-256、可用二进制归档、manifest 和验收证据。
- [ ] 记录验收日期和前后端回滚版本。
- [ ] 交付中明确未部署组件，不把历史部署记录当成当前验证结果。

## 回滚与完成判定

前端将 current 原子改回 previous_operator_release。服务端将 current 改回 previous_server_release，再重启服务，重新验证 active 和 health。保留新旧 release，不通过删除文件回滚。

只有源码、测试、二进制、current、桌面入口、systemd 实际进程、协议与数据处理验收、版本记录全部一致，才能报告 `EXECUTED`。缺项时使用 `CODE COMPLETE`、`BUILD COMPLETE`、`DEPLOYMENT NOT VERIFIED` 等准确状态，并逐项说明原因。

## 现场部署工具

下列工具先检查 packaged/current 模式，校验本地及上传归档，沿用现场配置。切换前再次检查未完成任务。启动失败时自动恢复 previous_server_release，并重启旧服务。

```bash
python tools/deploy_candidate_release.py \
  outputs/server-releases/<release-id>/waveform-acquisition-v2 \
  --release-id <release-id> --smoke-date 2026-10-06 \
  --report outputs/release-pairs/<release-id>/server-deployment.json
```

工具复核实际 MainPID、进程路径和二进制哈希，生成机器可读证据。部署事务持有串行锁。进程、哈希和三个历史命令验证成功后才提交；失败或连接中断会触发回滚。`DEPLOYED; PROTOCOL VERIFIED; GUI SMOKE PENDING` 尚未包含 GUI 验收。

协议验收使用真实桌面 endpoint。日期必须已有任务和波形文件，空结果不能当成精确文件查询已验证。

```bash
python tools/verify_history_release.py --date 2026-10-06 \
  --endpoint tcp://127.0.0.1:5001 \
  --report outputs/release-pairs/<release-id>/protocol-smoke.json
```

建议使用下列封装命令。工具调用真实桌面文件，保持验收会话存活，并记录实际 `gio launch` 命令与桌面文件哈希。二进制额外核实父进程是正式 launcher。父应用遗留的 `GIO_LAUNCHED_DESKTOP_FILE` 仅作诊断信息，不作为启动来源判据。

```bash
python tools/smoke_operator_desktop.py --date 2026-10-06 \
  --report outputs/release-pairs/<release-id>/gui-history.json
```
