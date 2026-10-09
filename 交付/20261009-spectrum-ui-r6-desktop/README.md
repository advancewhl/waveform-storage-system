# 现场桌面机 r6 发布记录

## 最终结果

**生产状态：** `PRODUCTION ACTIVATED AND VERIFIED`

**真实 HDF5 频谱功能验收：** `USER-CONFIRMED PASSED`

目标桌面机为 `AC-GT-CN-S2-OPI21`。`desktop-user-a` 和 `desktop-user-b` 是同一台机器上的两个生产用户环境，共用前端安装。2026-10-09，用户确认已完成现场验收，并确认两个账号均可使用 r6，真实 HDF5 频谱功能验收通过。

证据类型需区分：desktop-user-a 的进程、二进制哈希、SSH identity、隧道、ZMQ health 和历史查询有机器记录；desktop-user-b 使用情况及真实 HDF5 频谱验收由用户现场确认。本次没有采集 desktop-user-b 机器端输出，也没有记录 HDF5 路径、频谱数值或内容。部署验收没有发送采集请求；acquisition server 未修改。

## 发布内容

- 共享安装目录：`/home/site-user/work/StreamDataPro_new`。
- 当前 r6：`/home/site-user/work/StreamDataPro_new/waveform-operator-r6-20261009`。
- 当前 r6 二进制 SHA-256：`c305b9f804e3cef40eb7d2a90fc36cc60a28d715d4fb8bac17cdc87145024b93`。
- 软件包 SHA-256：`ea78a3301fe7f79311648874f996f870c222511101a65947358362fecb4f2bae`。
- 保留的上一版：`/home/site-user/work/StreamDataPro_new/waveform-operator-direct-offline-20261004`；二进制 SHA-256：`85e715ecd53c224d99b040d254c96aaee0d171fd94fb735dd83d33941bb45874`。
- 公共启动器：`/home/site-user/bin/startWaveform.sh`，当前调用 r6 的 `run-operator.sh`。
- 启动器备份：`/home/site-user/bin/startWaveform.sh.backup-20261009-r6`；SHA-256：`bd707fd1b37a308f8dd247b21a6f4b06cec9109230046a36c178c2c415e09006`。
- 两个用户原有桌面入口通过公共启动器使用共享前端。

目标系统为 Anolis OS 8.8、x86_64、GLIBC 2.28。r6 目录和二进制可由两个账号读取和执行；没有扩大 desktop-user-a 凭据目录的访问权限。

## desktop-user-a 机器记录

desktop-user-a 原桌面入口保持不变，并通过公共启动器启动 r6。GUI 进程保持运行，实际进程二进制 SHA-256 与预期值一致。desktop-user-a 原有前端 SSH identity 建立隧道；ZMQ health 返回成功。对 `2026-10-06` 的只读 `list_capture_runs` 查询返回 3 个任务。r6 应用包含频谱分析页。

desktop-user-a 前端 SSH 公钥指纹为 `SHA256:jdYBfp4R4yOK/rKUBgUghrOKpx2gjwzOUzbd8lo5SYI`，与运行时 identity 一致。记录中只有公钥指纹，没有私钥内容。

## desktop-user-b 用户现场确认

用户于 2026-10-09 确认已在实际 desktop-user-b 会话完成验收，并确认 desktop-user-b 可通过共用前端使用 r6。这是用户现场确认，不是本任务采集的机器端进程、哈希、SSH、health 或历史查询输出。

历史记录中的 desktop-user-b 公钥指纹为 `SHA256:7ntUrhAwEqqDzQg+S1JftSLZ0mnOL7yqryJZT7uZbeM`。本任务没有读取私钥内容，也未在 desktop-user-b 会话中重新采集或核对该指纹；没有修改 desktop-user-b identity 或账号配置。用户确认结果及其证据边界见 [现场验收记录](evidence/user-on-site-acceptance-20261009.md)。

## SSH 连接记录

跳板机使用 `/home/whloong/.ssh/id_ed25519_waveform_oneclick`。目标桌面机的历史登录使用已有本地 `SSH_ASKPASS` helper 执行密码认证；开发机 agent 公钥不是目标账号的登录身份。目标主机 ED25519 指纹为 `SHA256:WraHgRffV0jnG5UBC03670GhIp84qIO8QHcOTYUOkgE`。没有记录密码或私钥内容。

较早的 SSH 探测曾因可用身份被拒绝而无法确认根因；后续恢复了历史登录方式并完成部署。`historical-ssh-recovery-and-staging.log` 保留了激活前的时间顺序和中间状态，不能将其中的 `BLOCKED BEFORE ACTIVATION` 解读为最终状态。

## 安全范围与回滚

本次采集请求数为 `0`，未修改 acquisition server、systemd、MongoDB、HDF5 源文件或 dashboard。自动化部署前没有将生产 HDF5 导出或复制到 desktop-user-a；之后的真实 HDF5 频谱验收由用户在现场完成并确认通过。

上一版前端和启动器备份均保留，可用于恢复。生产桌面前端发布覆盖同一机器上的 desktop-user-a 和 desktop-user-b 两个用户环境；未来发布必须分别验证两边，双账号都完成验证后才能判定发布完成。

## 证据索引

- [机器侧账号验证摘要](evidence/account-validation-20261009.log)：desktop-user-a 机器记录及 desktop-user-b 用户确认的时间顺序摘要。
- [用户现场验收记录](evidence/user-on-site-acceptance-20261009.md)：desktop-user-b 和真实 HDF5 频谱验收的用户确认及证据分类。
- [质量门](evidence/quality-gate-committed-escalated.log)：225 项测试通过。
- [兼容构建记录](evidence/debian10-build-escalated.log) 与 [二进制信息](evidence/binary-glibc-versions.txt)。
- [软件包清单校验](evidence/package-manifest-verify.log)、[二进制哈希](evidence/production-binary.sha256) 和 [软件包校验摘要](evidence/package-verification.txt)。
- [连接尝试摘要](evidence/target-connection-attempts.log) 及 [历史 SSH 恢复与 staging 记录](evidence/historical-ssh-recovery-and-staging.log)。后者保留已被后续部署记录取代的中间状态。
- [历史查询测试](evidence/test-formal-history.log)、[频谱分析测试](evidence/test-spectrum-analysis.log) 和 [频谱 GUI 测试](evidence/test-spectrum-gui.log)。
- desktop-user-b 的现场校验脚本和操作说明仅作为可选验证辅助；发布完成依据是用户现场确认，不是该脚本输出。
