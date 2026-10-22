# Agent API v2 小范围审查整改报告

C2C: EXECUTED

## PRE-CHECK

- 分支：`ui/history-retrieval-compact-v2`。
- 起始 HEAD：`25e6b73985082bb5e2157b77114949597994301f`。
- 初始状态：staged 0、unstaged 9、untracked 286。
- v2 代码、测试和文档仍在工作树；其中部分文件尚未跟踪。已有上轮 v2 改动，以及无关的备份、历史 GUI 产物、PDF 和发布产物。本轮没有清理或覆盖它们。
- 完整起始清单见 [git-initial.json](git-initial.json)。

## FIXED

1. spectrum provenance：现有分析 manifest 定义了真实算法版本，但没有定义稳定的频谱科学源码集合。本轮删除 spectrum 误用的 alignment `algorithm_source_sha256`；保留实际 manifest 的 `algorithm_version`。alignment 的版本和源码 hash 保持正确，并用实际源码与真实 manifest 验证。
2. 审批文档：统一写明所有 control/data POST 强制签名审批；read POST alignment、spectrum 和 analysis job cancel 不要求人工审批，但需要 read service token 和幂等 key。审批代码和 HTTP 路由未修改。
3. version 0：先以真实 HTTP fixture 复现 preview 后、mutation 前返回 404。修复后，根据成功 preview 中已绑定的精确目标身份读取当前原始对象，返回 version 0、当前 checksum 和字节数。GET 不创建 mutation state、operation_id、审批占用或新版本，也不修改原 Mongo。已有 overlay history 继续返回完整历史；缺失、不唯一和不可读对象分别保留安全错误。

## FILES_CHANGED

业务 diff 相对于本轮开始时的文件快照，限于以下 7 个文件：

- `src/waveform_storage/application/agent_v2_service.py`
- `tests/test_waveform_agent_v2.py`
- `docs/agent-api-v2.md`
- `docs/agent-api-v2.openapi.json`
- `docs/CURRENT_STATUS.md`
- `docs/handoffs/waveform-agent-v2-integration-20261021.md`
- `交付/20261021-waveform-agent-v2/README.md`

完整 diff 见 [business-changes.diff](business-changes.diff)。AOCI 托管资产 `.aoci/baseline.json` 和 `aoci.code.txt` 另行维护，与业务修改区分。

原始 2026-10-21 日志和 fixture JSON 保留为历史证据，没有用本轮结果覆盖。

## TESTS

- 修复前，聚焦回归测试按预期失败，实际 HTTP 404；见 [version0-before.log](version0-before.log)。
- 修复后，v2 定向测试 20 项全部通过；见 [focused.log](focused.log)。
- 本轮正式质量门仅运行一次，275 项全部通过；见 [quality.log](quality.log)。compileall、注册表、源码质量、密钥启发式扫描、diff 和 import 检查通过。
- 真实 loopback HTTP → Candidate ZMQ → HDF5 → alignment/spectrum/artifact fixture 继续通过；见 [http-fixture.json](http-fixture.json)。

使用 `/home/whloong/miniconda3/envs/waveform-storage/bin/python`。定向命令为 `PYTHONPATH=src:tests python -m unittest test_waveform_agent_v2 -v`；正式命令为 `python tools/run_quality.py`，均配置 `QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/waveform-mpl`。

本轮没有额外 benchmark、PyInstaller 构建、GUI smoke、真实设备或远端 SFTP 测试、发布二进制身份验证或 manifest 全量扫描。

## CONTRACT CHECK

- read approval：真实 HTTP alignment、spectrum、analysis cancel 均不提交审批 header，测试通过。
- control/data approval：缺少审批均返回 `APPROVAL_REQUIRED`；已有签名、scope、TTL、hash 和 replay 检查保留。
- OpenAPI：3 个 read POST 只声明 ReadToken 和幂等 header；control/data POST 声明各自 token 和必需审批 header。没有新增或修改 HTTP route。
- versions：preview 后、mutation 前返回 version 0；测试对比整个 Agent state、持久 catalog 字节和原始 metadata repository，确认 GET 无持久写入。读取使用当前原始文件，非 preview 的旧 checksum。mutation 后历史、restore_version=0、soft delete、overwrite 和并发冲突测试保留并通过。
- provenance：alignment hash 对应真实 alignment 模块；spectrum 版本等于真实分析 manifest，省略未定义的算法源码 hash。OpenAPI 和文档同步。
- v1 compatibility：v1 代码未修改，完整质量门中的 v1 回归通过。
- 科学实现与协议：与本轮开始快照逐字节比较，确认 analysis service/engine、alignment、retrieval、acquisition protocol v2、审批模块、HTTP 路由和 metadata repository 未改动。

契约摘要见 [contract-results.json](contract-results.json)，范围记录见 [scope-review.json](scope-review.json)。

## REMAINING LIMITATIONS

尚未现场部署。未验证真实设备、远端 SFTP、生产 MongoDB 或 TLS 代理。未实现 EPICS 写入。fixture 不等于 production verification。

v2 overlay 不改变 v1/GUI 原始视图。未 mutation 的 version-0 查询需要保留成功 preview 的身份绑定；未知 target_id 返回 `NOT_FOUND`，不扫描整个 catalog。其他原有能力限制保持不变。

## GIT

- 结束 HEAD：`25e6b73985082bb5e2157b77114949597994301f`，与起始相同。
- staged：0。
- unstaged：9，含原有未提交改动和本轮文档/AOCI更新。
- untracked：301；初始286个未跟踪路径全部保留，新路径均属于本轮整改证据目录。
- 本轮未 commit，未 push，未创建或切换分支。等待用户单独执行 Git 收尾。
- 完整结束清单见 [git-final.json](git-final.json)。

AOCI 收尾：6条受影响语义更新已应用；Verify、Check与Guide均通过，`complete=true`、`next_action=none`，无待恢复事务。首次observe复核因`deterministic_replay_failed`停止，已证明正式索引和Baseline零写入后重新规划，重试成功。治理证据与独立diff见同目录的`aoci-*.json`及[aoci-changes.diff](aoci-changes.diff)。
