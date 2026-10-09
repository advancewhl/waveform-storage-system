# 频谱 UI r4 本机 operator 交付记录

该 release 曾短暂激活为本机 operator。打包复核发现历史 reset 可能清除独立文件分析结果，因此在用户手动验证前由修正后的 `20261009-spectrum-ui-r5` 替代。r4 二进制和记录保留，不作为当前版本或回滚目标。

## 本轮修改

- 结果包建议名称根据 HDF5 文件、输入目录或 Archiver 文件生成。历史对齐结果使用当前 `request_id` 和真实 `capture_sequence`，格式为 `S{capture_sequence:05d}`。无法取得有效主体时回退为 `spectrum-results.zip`。非法字符和控制字符替换为 `_`。
- 保存结果包仍复制本次 `results.zip`。ZIP 字节、文件列表、分析任务和 `last_run` 均保持不变。
- GUI 结果表、频谱图注和 `report.md` 将谱峰频率格式化为小数点后 2 位。排序、标记位置、`results.json`、`spectrum-summary.csv` 和算法继续使用完整浮点值。
- PNG 自适应命名逻辑没有修改。原始、Log、特殊字符和无当前通道的回归测试均通过。

## 测试与构建

- 频谱 focused：31 项通过。
- 历史 handoff 与切段：6 项通过。
- 全量质量门：220 项通过。
- `git diff --check`：通过。
- operator 使用 formal PyInstaller spec 构建。`BUILD-INFO` 记录 `source_revision=694021fcc47909759a174e86d947391f8ba0f7bf`、`source_state=working-tree`。
- `MANIFEST.sha256` 的 8,368 项校验通过。二进制 SHA-256：`2097939a783a741cccbd2d5e8db750d6a1670ddd5ac64bc3a22719d319366b37`。
- 打包二进制 offscreen 启动和频谱页面截图通过。打包代码验证了真实历史 sequence 命名、输入命名、两位小数显示和原始浮点值。

## 激活与回滚

- 激活前 `current` 指向 `20261009-spectrum-ui-r3`。
- 当前 `current` 精确指向 `outputs/operator-local/releases/20261009-spectrum-ui-r4/waveform-operator`。
- 回滚目标保留为 `outputs/operator-local/releases/20261009-spectrum-ui-r3/waveform-operator`。
- `/home/whloong/桌面/波形存储.desktop` 继续调用 `tools/launch_operator_desktop.sh`。launcher 从 `outputs/operator-local/current` 解析二进制。
- Codex 未启动真实桌面 GUI。用户请关闭旧窗口，再从桌面图标重新打开验证。

本目录记录 r4 原始构建与短暂激活过程。当前版本和最终验证状态以[频谱 UI r5 交付记录](../20261009-spectrum-ui-r5/README.md)为准。

现场 acquisition server、systemd、MongoDB、dashboard 和现场 operator 均未修改。本轮未创建 Git commit、push 或 tag。

## 文件

- `waveform-operator-20261009-spectrum-ui-r4.tar.gz`：可用 operator 二进制目录归档。
- `BUILD-INFO`、`SOURCE-MANIFEST.json`、`MANIFEST.sha256`：构建和完整性记录。
- `release-record.json`：release、激活和回滚摘要。
- `evidence/`：测试日志、构建日志、校验记录、offscreen 截图和可复现脚本。
