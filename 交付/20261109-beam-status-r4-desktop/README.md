# 现场桌面机 r4 发布记录

日期：2026-11-09
范围：桌面服务器 `AC-GT-CN-S2-OPI21` 上的共享前端安装。两个生产环境 `desktop-user-a`（uid 1001）与 `desktop-user-b`（uid 1003）共用同一安装和启动器。未修改 SHINE-AC、采集服务端或其他项目。

## 版本

| 项目 | 值 |
| --- | --- |
| release_id | `20261026` 之后的 `20261109-beam-status-r4`（桌面目录名 `waveform-operator-beam-status-r4-20261109`） |
| 构建环境 | `python:3.10-buster`，glibc 2.28，PyInstaller 6.11.1 |
| 包 SHA-256 | `cef13d9d16d32f5d2829d311e3fef428735b6005915aa3815e1e1f41e6fc6811` |
| 上一版 | `waveform-operator-pv-frequency-r2-20261026` |
| 启动器备份 | `startWaveform.sh.backup-20261109-beam-status-r4` |

构建包含本机 r4 的全部改动：束流状态卡片、采集不再被束流阻拦、211 台初测表。`BUILD-INFO` 由打包脚本写入，`MANIFEST.sha256` 随包生成；按任务范围未做独立 SHA-256/manifest 核验。

## 部署与验证

- 包上传到 `incoming/20261109-beam-status-r4.tar.gz`（269,639,074 字节，与本地一致）。
- 解包到共享安装目录；`connection.env` 与 `credentials` 链接到既有配置源，未改动配置和凭据。
- `chmod -R a+rX` 后，`/home`、安装根目录、`bin`、启动器均为 755，两个账号可读可执行。
- 离屏截图（见 [remote-beam-page.png](evidence/remote-beam-page.png)）：注册 BPM **211**、当前频率 **5 Hz**、束流状态 **允许**，与部署时的服务端读数（`beam_permitted=true`、`beam_stop_alarm=off`）一致。
- 原子切换启动器：先写 `startWaveform.sh.next-r4`，再 `mv -Tf` 覆盖；失败回滚由备份文件保证。切换后启动器指向 `waveform-operator-beam-status-r4-20261109/run-operator.sh`。
- 切换前的启动器内容与当前记录见 [remote-activation.json](evidence/remote-activation.json) 和 [remote-deploy.log](evidence/remote-deploy.log)。

## 清理

- 删除旧版本 `waveform-operator-r6-20261009` 及其启动器备份 `startWaveform.sh.backup-20261009-r6`。
- 保留：当前 r4、上一版 `waveform-operator-pv-frequency-r2-20261026`、配置源 `waveform-operator-direct-offline-20261004`（`connection.env`/`credentials` 指向它，删除会破坏两个账号的配置）。

## 回滚

```bash
cp -p /home/site-user/bin/startWaveform.sh.backup-20261109-beam-status-r4 \
      /home/site-user/bin/startWaveform.sh
```

恢复后重新启动前端即可回到 `20261026-pv-frequency-r2`。

## 验证边界

- 按任务范围未执行 SHA-256、完整 manifest、二进制身份核验或真实桌面 smoke。
- `desktop-user-b` 的私有桌面会话未验证：其 home 为私有权限，`desktop-user-a` 无法登录或读取；共享安装与公共路径权限已确认（755）。
- 只验证了离屏页面渲染，没有在真实桌面会话中点击采集。
