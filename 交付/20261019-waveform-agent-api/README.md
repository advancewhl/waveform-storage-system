# Agent-facing Waveform API 源码交付

本轮交付领域服务、独立 HTTP API、完整链路回归和 accelerator-rag Integration Handoff。代码完成，尚未部署。

## 接入交接

[Integration Handoff 与精确接口](../../docs/waveform-agent-api.md)包含 base URL 配置、启动命令、request/response/error JSON、任务轮询、artifact URI、timeout 行为、文件清单和可复现 fixture。

## 本次真实验证

依赖环境：`/home/whloong/miniconda3/envs/waveform-storage/bin/python`。默认 Python 和仓库 `.venv` 缺少 SciPy，本次没有以它们运行成功的名义报告结果。HTTP 测试需要本机套接字权限，已在允许本机监听的执行环境中运行。

定向命令：

```bash
PYTHONPATH=src:tests QT_QPA_PLATFORM=offscreen \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python -m unittest \
  test_waveform_agent_api test_partial_history_integration \
  test_spectrum_analysis test_spectrum_http_api -v
```

真实输出：

```text
Ran 27 tests in 2.502s

OK
```

质量门命令：

```bash
QT_QPA_PLATFORM=offscreen \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python tools/run_quality.py
```

真实输出：

```text
Ran 247 tests in 8.576s

OK
valid registry: 47 devices, 12 acquisition-enabled
legacy files match registry exactly
source quality: no bare except or print in non-legacy modules
secret heuristic: scanned 638 tracked files
local quality gate: OK
```

原始日志：[定向测试](evidence/targeted-tests.log)、[完整质量门](evidence/quality-gate.log)。[fixture 实际结果](evidence/fixture-response.json)来自本次真实领域链路，47.6 Hz 谱峰、20000 个点、19.999 秒、4/4 命中块。测试还覆盖 HTTP 查询、提交、轮询、三个 artifact 下载和任务结果重启恢复。

初次完整质量门发现 SSH 自定义 reader 不提供 metadata 的兼容问题；已修复为可选 metadata 透传，并重新通过质量门。历史 integration 测试已适配当前 GUI 的显式确认弹窗，GUI 行为未修改。

## 版本与运行状态

本次交付为当前仓库源码，Agent API 尚无正式 release 版本或现场 URL。前端和采集服务未切换，不声明新的前后端运行版本对。既有本机 operator release 记录仍为 `20261017-history-wording-r6-local`，此次未核验其运行状态。

未修改 Mongo schema、频谱算法、retrieval/alignment 算法。没有原始数据迁移或覆盖。未运行 SHA-256、完整 manifest、二进制身份、真实桌面 smoke 或完整发布验收。部署前需配置真实 Candidate 地址与存储环境；跨主机接入还需部署层认证和 TLS。

本次没有切换服务，因此没有新的运行态回滚版本；既有版本、配置和数据保留。源码中的新 API 可通过停止对应进程撤销运行入口。
