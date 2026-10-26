# 当前频率只读显示：20261026-pv-frequency-r2

## 改动

采集页移除“采集频率”下拉框，仅显示服务端定时 PV 的当前读数。启动时读取一次；
采集前重新读取并跟随最新值。读取失败时阻止提交，服务端继续检查请求频率与 PV 是否一致。
确认弹窗说明频率跟随服务端当前 PV，不把启动时的显示值当作采集请求的固定参数。

本次保留服务端已发布的 10 Hz 支持，等待参数为 2100 s。协议、设备批准范围、
`request_id + capture_sequence` 段身份、历史采样率来源及分析算法均未改变。

已检查当前说明文档，补齐 Agent API 的 10 Hz 档位，删除过时的 GUI 频率覆盖说明。
安装说明同步改为任务和数据段查询，删除把 bunchID 差值当作秒数的旧说明。
历史交付记录保留原始时点，并导航到当前状态；r1 版本对的部署状态与已有部署证据对齐。

## 验证

- [定向回归](evidence/targeted-tests.log)：正式 GUI、Candidate 后端与采集服务共 41 项通过。
- [采集提交补测](evidence/capture-submission-test.log)：1 项通过，确认 GUI 不传频率覆盖值。
- [源码界面](evidence/capture-page.png)和[打包界面](evidence/packaged-capture-page.png)：下拉框已移除，布局正常。
- [连接现有服务的打包界面](evidence/connected-pv-page.png)：实际 PV 显示为 `10 Hz`。
- 远程兼容构建最初遇到依赖下载不完整，pip 校验拒绝；未绕过校验，改用清华 PyPI 镜像重新下载。

## 版本与部署

本机前端已构建并激活；[切换记录](evidence/local-activation.json)保存 current 和回滚目标。
远程兼容包已完成 Debian 10 / glibc 2.28 构建，共享启动器已切换至
`/home/site-user/work/StreamDataPro_new/waveform-operator-pv-frequency-r2-20261026`。
[远程离屏界面](evidence/remote-pv-page.png)显示实际 PV 为 `10 Hz`，下拉框已移除。
[远程切换记录](evidence/remote-activation.json)记录当前入口、旧版备份、两个账号和公共路径权限。
`desktop-user-a` 与 `desktop-user-b` 共用安装均已更新，原有配置和账号凭据沿用；desktop-user-b 独立会话仍未验证。

预检首次执行后，共享入口仍指向旧版；检查确认 SSH 子进程消耗了脚本标准输入。
已为预检命令隔离标准输入，并用[激活脚本](evidence/activate-desktop.sh)完成剩余切换。
[激活输出](evidence/remote-activation.log)和独立读取的入口记录均确认切换成功。
最终版本与验证边界见 [release-pair.json](release-pair.json)。
现场服务端本次只读确认是 `20261026-timing-10hz-r1`，systemd active，无未完成采集任务。

## 验证边界

本次未发送真实采集请求。未运行 SHA-256、完整 manifest、二进制身份、真实桌面 smoke、
完整发布验收或额外全量测试。10 Hz 下的真实采集、下载与落盘仍未验证。
`desktop-user-a` 无 sudo 权限，无法读取 `desktop-user-b` 的私有桌面目录或启动其独立会话。

## AOCI 收尾

AOCI 收尾：两份测试文件的 observe 复核事务已应用，正式语义索引仍待同步。后续维护调用被自动审批拒绝，理由是可能存在并发或重放风险；本次保留该待办，未绕过审批。

## 回滚

- 本机：保留 `outputs/operator-local/releases/20261017-history-wording-r6-local/waveform-operator`，可将 `outputs/operator-local/current` 原子切回。
- 远程：保留 `/home/site-user/work/StreamDataPro_new/waveform-operator-r6-20261009`，启动器备份为 `/home/site-user/bin/startWaveform.sh.backup-20261026-pv-frequency-r2`。恢复该文件后重新启动前端即可回滚。
- 服务端：本次未切换；r1 的回滚版本和限制见 [r1 发布记录](../20261026-timing-10hz-r1/README.md)。
