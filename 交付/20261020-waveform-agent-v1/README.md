# Waveform Agent HTTP API v1 整改交付

CONTRACT_VERSION：Waveform Agent HTTP API v1。源码完成，部署状态：NO。

## 接入与交接

[当前 API v1 文档](../../docs/waveform-agent-api.md)定义唯一业务前缀、幂等、持久化 job、health、provenance、artifact 和安全边界。[accelerator-rag Integration Handoff](../../docs/handoffs/waveform-agent-v1-integration-20261020.md)提供完整 request schema、真实 POST/polling/error JSON、客户端 retry/timeout 规则、文件清单与固定 fixture。

本轮保持 Candidate、retrieval、alignment、AnalysisJobs、run_analysis 和 ChannelResult 链路，后台 job 覆盖检索至分析的全部重操作。未修改 Mongo/HDF5 schema、采集 protocol v2、retrieval/alignment 算法、Welch 或科学参数。没有接入 accelerator-rag/Qwen。

## 本次真实验证

定向命令：

```bash
PYTHONPATH=src:tests QT_QPA_PLATFORM=offscreen \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python -m unittest \
  test_waveform_agent_api test_partial_history_integration \
  test_spectrum_analysis test_spectrum_http_api -v
```

```text
Ran 35 tests in 7.944s

OK
```

完整质量门：

```bash
QT_QPA_PLATFORM=offscreen \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python tools/run_quality.py
```

```text
Ran 255 tests in 13.271s

OK
valid registry: 47 devices, 12 acquisition-enabled
legacy files match registry exactly
source quality: no bare except or print in non-legacy modules
secret heuristic: scanned 648 tracked files
local quality gate: OK
```

本轮 12 项 Agent v1 测试覆盖完整 E2E、并发同 key 去重/冲突、新 key、快速 POST、全部执行阶段、重启恢复、不自动重跑、health 三种可用性、错误脱敏、invalid classification 与 job failure 区分、artifact whitelist/traversal/symlink、防止 raw/metadata 写入、队列容量和单 registry owner。旧的 247 项不是本轮结果。

原始证据：

- [定向测试日志](evidence/targeted-tests.log)
- [完整质量门日志](evidence/quality-gate.log)
- [真实 HTTP fixture 日志](evidence/http-fixture.log)
- [spectrum request](evidence/spectrum-request.json)
- [health response](evidence/health-response.json)
- [202 response](evidence/post-response.json)
- [succeeded polling response](evidence/polling-response.json)
- [幂等冲突 response](evidence/idempotency-conflict.json)

HTTP fixture 实测 1000 Hz、20000 点、真实跳号段 3、47.6 Hz 谱峰、0.05 Hz 分辨率与 4/4 命中块，并成功下载三个 v1 artifact。测试仅使用本机临时数据和服务；原始数据文件尺寸/mtime 与 Mongo mutation spy 验证只读边界，不额外执行文件哈希。

## 运行、限制与回滚

本轮没有启动现场正式 API，没有切换前端/服务端，没有桌面构建或现场版本对变更。已有配置、release 和数据保留。当前没有新的运行态回滚版本；停止新 API 进程即可撤销运行入口。

服务仍固定 loopback，没有 auth、TLS 或 tenant isolation。生产跨主机接入必须另行批准并配置安全网关/隧道、TLS、认证和 ACL。output root 是幂等与恢复的作用域，必须保留；artifact 没有自动过期或永久保留承诺。后台没有统一执行 deadline。

未执行 SHA-256、完整 manifest、二进制身份校验、真实桌面 smoke 或完整发布验收。完整离线质量门仅为本轮源码工程验证，不能作为部署验收结论。
