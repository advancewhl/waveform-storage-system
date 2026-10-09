# 频谱 UI r6 本机 operator 交付记录

本机 operator 已更新为 `20261009-spectrum-ui-r6`，`outputs/operator-local/current` 已切换至 r6。打包二进制、manifest、offscreen 启动和包内 GUI 行为检查均通过。当前状态：`LOCAL OPERATOR UPDATED — WAITING FOR USER MANUAL VALIDATION`。

## 本轮修改

- 谱峰频率由共享 formatter 按正数、有限的 `resolution_hz` 选择小数位：0.2 Hz 显示为 `47.6`，0.05 Hz 显示为 `47.55`，约 0.05117 Hz 显示为 `49.84`，0.01 Hz 可显示为 `49.80`。分辨率缺失或无效时使用紧凑有效数字格式，避免暴露浮点噪声。
- 格式化只用于 GUI 结果表、频谱图注和 `report.md`。`ChannelResult.peak_hz`、频率数组、PSD、排序键、图中峰值 X 坐标、JSON 和 CSV 均保留原始值。
- 高频模板默认整体 Welch 窗口为 20 秒，重复性分块为 5 秒。约 20 秒的有效记录可得到约 0.05 Hz 分辨率；Welch 窗口按可用数据长度截断，因此较短但满足现有有效性规则的记录仍可分析。低频 V1/V2 的 300 秒配置不变。
- “设置 Welch 窗口、实际 Welch 窗口、频率分辨率”只显示在默认折叠的“高级分析参数”区域，随当前结果和通道更新。缺失值显示为 `—`，显示与折叠操作不会发起分析。
- r5 的结果包自适应命名、原 ZIP 复制导出、历史真实 `capture_sequence` 命名与重置、命中块列显示偏好、PNG 命名和历史采样率 handoff 均保留。

## 测试与构建

- 频谱 focused：35 项通过。
- 历史 handoff 与切段：6 项通过。
- 全量质量门：225 项通过，包含 unittest、compileall、设备注册表、源码质量、secret scan 和 `git diff --check`。
- 独立 `git diff --check`：通过。
- 使用 `packaging/operator-executable.spec` 和 PyInstaller 6.22.3 构建。`BUILD-INFO` 记录源 revision `694021fcc47909759a174e86d947391f8ba0f7bf`，源状态为 `working-tree`。
- 打包二进制 SHA-256：`fe98830a1a806c95e088d7070b86f97c444d35ff8dfbc1abe43aa11ee2ada6bb`。
- `MANIFEST.sha256` 的 8,368 项校验通过。包内 GUI 验收确认高频窗口、分辨率显示、原始峰值、折叠诊断、历史命名与重置、PNG 命名和显示控件不触发分析。
- 可用 operator 归档 SHA-256：`4f03476c9c1c0c9d69344ea556b1f285c77dab982365b9eb839bcb17474f1b1a`。

## 激活与回滚

- 激活前 `current` 指向 r5；当前指向 `outputs/operator-local/releases/20261009-spectrum-ui-r6/waveform-operator`。
- `/home/whloong/桌面/波形存储.desktop` 调用 `tools/launch_operator_desktop.sh`，launcher 从 `outputs/operator-local/current` 解析到 r6 二进制。
- 回滚目标为已人工验证的 r3：`outputs/operator-local/releases/20261009-spectrum-ui-r3/waveform-operator`。r4、r5 和 r6 均保留。
- Codex 未启动真实桌面 GUI。请关闭旧窗口，再从桌面图标打开 r6 并完成人工验证。

现场 acquisition server、systemd、MongoDB、dashboard 和现场 operator 均未修改。本轮未创建 Git commit、push 或 tag。

## 交付文件

- `waveform-operator-20261009-spectrum-ui-r6.tar.gz`：可用 operator 目录归档。
- `BUILD-INFO`、`SOURCE-MANIFEST.json`、`MANIFEST.sha256`：构建来源与完整性记录。
- `release-record.json`：版本、激活和回滚摘要。
- `evidence/`：测试与构建日志、offscreen 截图、包内 GUI 检查、激活检查和复现脚本。
