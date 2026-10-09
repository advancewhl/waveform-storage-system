# 本机频谱 UI operator r2 发布记录

## 结论

本机 operator `20261009-spectrum-ui-r2` 已构建并激活。`outputs/operator-local/current` 已指向该 release，回滚目标为 `20261008-spectrum-ui-r1`。Codex 未尝试打开 GUI；当前等待用户从桌面“波形存储”图标手动验证。

本次只更新本机 operator。现场 acquisition server、systemd、MongoDB、dashboard、现场 operator 和采集协议均未修改。

## 功能变更

“保存频谱图”的建议文件名由当前选中行对应的 `ChannelResult.channel` 和显示模式生成。原始模式使用 `_原始.png`，Log 模式使用 `_Log.png`。通道名中已有的 X 或 Y 保持原样，不额外添加方向；特殊字符只在生成的文件名中替换为 `_`。没有有效选中通道时使用 `spectrum.png`。

切换显示模式只重绘当前频谱，不重新运行分析。GUI 测试验证了文件名规则、非法字符、空选择回退和保存时不创建 `AnalysisJobs`。

## 构建结果

| 项目 | 值 |
| --- | --- |
| Release ID | `20261009-spectrum-ui-r2` |
| 源码 revision | `dbbc0d6ddfdaaf6d438783d7e7378134538f855c` |
| 源码状态 | `working-tree` |
| operator 路径 | `outputs/operator-local/releases/20261009-spectrum-ui-r2/waveform-operator/` |
| binary SHA-256 | `86c7b1e4722f92706eec986073bfd614a9f16449090005054f3eaade0909773f` |
| source manifest SHA-256 | `f02edd22dabd80a6ef9e81a4f89d1addcf8624e137c3d6dbfce0c6cd6aa1f9d0` |
| release manifest | 8,368 项，通过 `sha256sum -c MANIFEST.sha256` |
| Python | 3.10.20 |
| PyInstaller | 6.22.3 |
| 二进制归档 | [waveform-operator-20261009-spectrum-ui-r2.tar.gz](waveform-operator-20261009-spectrum-ui-r2.tar.gz) |
| 归档 SHA-256 | `be4a2e4d800c62d9893edfc6c5df9606423bd7b06f1cee3503abdf4ca76bc1b7` |

`BUILD-INFO`、`MANIFEST.sha256` 和 `SOURCE-MANIFEST.json` 位于 operator 目录。构建前后 operator 输入源码清单一致。

## 测试

- 频谱 GUI：6 项通过。
- 频谱分析：17 项通过。
- 完整质量门：212 项通过；compileall、设备注册表、源码质量、secret scan 和 `git diff --check` 均通过。
- 二进制 `--help` 启动检查通过。该检查未打开 GUI。

首次使用 `test_spectrum*.py` 通配运行时，测试还包含需要绑定本机 loopback 的 HTTP API 用例，受限 sandbox 拒绝了 socket 创建。随后单独运行的频谱 GUI、频谱分析测试均通过；完整质量门也在允许本机 loopback 的环境中通过。

## 激活与桌面入口

激活前，`current` 指向 `20261008-spectrum-ui-r1/waveform-operator`。项目正式激活命令成功后，`readlink -f outputs/operator-local/current` 指向 r2。桌面文件 `/home/whloong/桌面/波形存储.desktop` 仍执行 `tools/launch_operator_desktop.sh`；只读解析确认 launcher 会将 `outputs/operator-local/current/waveform-operator` 解析为 r2 binary。

Codex 未打开 GUI，也未尝试调用桌面入口。用户可从桌面图标重新打开应用，验证文件对话框中的建议名称。

## 交付证据

- [完整质量门日志](evidence/full-quality.log)
- [频谱 GUI 测试日志](evidence/spectrum-gui-tests.log)
- [频谱分析测试日志](evidence/spectrum-analysis-tests.log)
- [PyInstaller 构建日志](evidence/operator-build.log)
- [构建记录](evidence/build-record.json)
- [source manifest 构建前快照](evidence/source-manifest-before-build.json)
- [release manifest 校验日志](evidence/manifest-check.log)
- [激活前检查](evidence/pre-activation-checks.json)
- [正式激活结果](evidence/activation.json)
- [激活后与桌面入口验证](evidence/activation-verification.json)
- [最终校验摘要](evidence/final-validation.json)

## Git 状态

未执行 Git commit、push 或 tag。r1 release 保留，未覆盖或删除。
