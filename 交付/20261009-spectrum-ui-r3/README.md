# 本机频谱 UI operator r3 发布记录

## 发布状态

本机 operator `20261009-spectrum-ui-r3` 已构建并激活。`outputs/operator-local/current` 已原子切换至 r3，回滚目标为 r2。用户于 2026-10-09 确认已完成人工桌面验证。当前状态为 `LOCAL OPERATOR ACTIVATED / USER MANUAL VALIDATION CONFIRMED`。

TASK_ID：`spectrum-ui-history-rate-and-hidden-block-column-local-release`。

人工验证结论来自用户确认。此前构建阶段仅执行 offscreen 启动与截图检查，未启动真实桌面窗口，未调用 `gio launch`；当时记录中的等待状态保留为历史证据。本次仅保存源码并推送 Git，保持本机 r3 current 不变。现场 acquisition server、systemd、MongoDB、dashboard 和现场 operator 均未修改。

## 功能与审查

结果表继续保留 5 列。“命中块”默认隐藏；“显示命中块列”复选框位于默认折叠的高级分析参数区域，默认未勾选。切换复选框只改变列可见性。折叠、切换模板和分析结果刷新均保留显示偏好。复选框不属于分析参数，在分析期间保持可用；高级区域的展开按钮沿用原有参数锁定行为。

历史任务使用 `CaptureRunSummary.frequency_hz` 记录采样率。`CaptureSegment` 和 `AlignmentResult` 没有采样率字段。历史入口检查当前任务与当前 segment 的 `request_id` 一致，再调用 `SpectrumPanel.use_alignment(alignment_result, sample_rate_hz=run.frequency_hz)`。当前对齐结果沿用既有 generation 检查和切段清理机制，segment 身份继续使用真实 `capture_sequence`。

合法历史采样率自动显示，例如 `1000.000 Hz`、`500.000 Hz`。缺失、非数值、布尔值、非有限值或非正值显示“未提供”，提示填写实际采样率。任务、数据段和日期切换会清空旧采样率及历史频谱上下文。handoff 不新增 backend 或 MongoDB 查询。

自审已检查本轮差异。修改范围为两个 GUI 文件、两个测试文件和发布记录。频谱数值算法、分类规则、5 种状态、原始/Log 语义、自适应 PNG 文件名、结果包格式、原始 HDF5、采集协议及历史查询接口保持原有实现。

## 测试

频谱专项命令如下，最终 27 项测试全部通过。

```bash
MPLCONFIGDIR=/tmp/waveform-storage-mpl \
PYTHONPATH=src QT_QPA_PLATFORM=offscreen \
/home/whloong/miniconda3/envs/waveform-storage/bin/python \
-m unittest discover -s tests -p 'test_spectrum_*.py' -v
```

首次运行中，GUI 和分析测试通过；HTTP API 用例被受限 sandbox 禁止创建 loopback socket，报 `PermissionError: [Errno 1] Operation not permitted`。随后在允许本机 loopback 的环境中重跑，27 项全部通过。该限制与真实代码失败分别记录。

历史专项 5 项通过，包括 1000 Hz、500 Hz、缺失值、任务切换、segment 跳号和按钮传递。完整质量门 216 项通过。compileall、设备注册表、源码质量、secret scan 和 `git diff --check` 均通过。

```bash
MPLCONFIGDIR=/tmp/waveform-storage-mpl QT_QPA_PLATFORM=offscreen \
/home/whloong/miniconda3/envs/waveform-storage/bin/python tools/run_quality.py
```

回归覆盖历史身份、BPM 选择、对齐、原始下载、V2 导出、直接 HDF5、采样率冲突、Archiver 时间戳、原始/Log、数值排序、5 种状态、空筛选、自适应文件名和完整结果包。测试还确认隐藏列不改变导出的 ZIP 内容，`spectrum-blocks.csv` 保留全部分块结果。

## 构建与校验

| 项目 | 值 |
| --- | --- |
| Release ID | `20261009-spectrum-ui-r3` |
| 分支 | `whloong/communication-v2-experiment` |
| 源码 revision | `dbbc0d6ddfdaaf6d438783d7e7378134538f855c` |
| 源码状态 | `working-tree` |
| operator 目录 | `outputs/operator-local/releases/20261009-spectrum-ui-r3/waveform-operator/` |
| binary SHA-256 | `384b824a79327381e2ecb93a2fcca4d0fec6c44b04f89e99ef221f533870865c` |
| source manifest SHA-256 | `f7a38ccbd2ae627629c38a938c00dbca46bd100882af6dd1d9e7139c58452f9a` |
| Release manifest | 8,368 项全部通过 |
| Python / PyInstaller | `3.10.20` / `6.22.3` |
| 回滚 release | `20261009-spectrum-ui-r2` |

使用正式 `packaging/operator-executable.spec`，仅构建 operator。构建前后源码清单一致。`SOURCE-MANIFEST.json` 包含并核实两个修改后的 GUI 模块；二进制哈希与 `BUILD-INFO` 一致。

新二进制在 offscreen 模式下打开频谱页并正常退出，没有 `ModuleNotFoundError`。另从该二进制的嵌入式 PYZ 直接加载项目模块，用构建解释器检查控件和 handoff 行为；该检查不导入工作区项目源码。两张截图经过视觉检查，默认页只有 4 个可见列，展开高级区域后复选框可见，布局无裁切。

正式激活命令为：

```bash
/home/whloong/miniconda3/envs/waveform-storage/bin/python \
tools/release_waveform.py activate-operator \
outputs/operator-local/releases/20261009-spectrum-ui-r3/waveform-operator
```

激活前 `current` 指向 r2；激活后 `readlink -f` 精确指向 r3。`current/waveform-operator` 解析为本次 binary。r2 的二进制与清单文件校验值保持不变。

桌面文件 `/home/whloong/桌面/波形存储.desktop` 仍执行项目的 `tools/launch_operator_desktop.sh`。launcher 优先解析 `current/waveform-operator`，并将该路径传给正常连接入口；离线入口也使用同一 binary。本轮仅做只读链路确认。

## 证据与归档

- [完整质量门](evidence/full-quality.log)
- [最终频谱专项](evidence/spectrum-focused.log)
- [首次 sandbox 环境限制](evidence/spectrum-focused-sandbox.log)
- [历史专项](evidence/history-focused.log)
- [本轮差异](evidence/task-changes.diff)
- [自审与修改范围](evidence/scope-review.json)
- [PyInstaller 构建日志](evidence/operator-build.log)
- [构建记录](evidence/build-record.json)
- [构建前源码快照](evidence/source-manifest-before-build.json)
- [完整 release 清单校验](evidence/manifest-check.log)
- [激活前校验](evidence/pre-activation-checks.json)
- [新二进制 offscreen 截图](evidence/spectrum-page.png)
- [封装代码高级区域截图](evidence/packaged-advanced-page.png)
- [正式激活结果](evidence/activation.json)
- [激活后与桌面入口确认](evidence/activation-verification.json)
- [本机与现场版本记录](release-record.json)
- [用户人工验证确认](evidence/user-manual-validation.json)
- [最终校验](evidence/final-validation.json)
- [二进制归档](waveform-operator-20261009-spectrum-ui-r3.tar.gz)
- [归档 SHA-256](waveform-operator-20261009-spectrum-ui-r3.tar.gz.sha256)

`BUILD-INFO`、`MANIFEST.sha256` 和 `SOURCE-MANIFEST.json` 同时保存在本目录，原件位于不可变 release 目录。

## Git 与回滚

本轮未执行 commit、push 或 tag。原有未跟踪的 `analysis/high_frequency/数据结果/spectrum-results.zip` 和 `tools/deploy_candidate_release.py` 均保持未跟踪，SHA-256 与任务开始时一致。未执行部署脚本。

如需回滚，使用正式激活工具将本机 current 指回 r2。保留 r2 和 r3；回滚不涉及现场组件。

```bash
/home/whloong/miniconda3/envs/waveform-storage/bin/python \
tools/release_waveform.py activate-operator \
outputs/operator-local/releases/20261009-spectrum-ui-r2/waveform-operator
```
