# desktop-user-b 现场验收

请在 desktop-user-b 图形桌面会话中打开现有 Waveform Data 图标。窗口显示后，在该用户的终端运行：

```bash
bash /tmp/validate-desktop-user-b-session.sh
```

脚本会核对桌面项、公共 launcher、r6 二进制哈希、desktop-user-b 公钥指纹、per-user 状态目录、启动日志、ZMQ health 和 2026-10-06 历史查询。它不会读取私钥内容，也不会发送采集请求。

请另外在 GUI 中确认“频谱分析”导航页可见，再把命令输出及该项确认结果发回本任务。脚本 SHA-256：`def894cf83b8689c2b04c4cca82807244ac79a501214786c27c02c4ce6658313`。
