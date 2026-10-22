# Waveform Agent HTTP API v1

## 范围与版本

2026-10-21 起，新增并行的 [Agent API v2](agent-api-v2.md)。v1 保留全部端点、请求、错误和只读语义。v2 增加独立 token、对齐、raw、受控采集与数据版本接口。本文的无内建认证说明仅适用于 v1；v2 的认证不能替代部署层对 v1 的网络隔离。

2026-10-22 加固补充：`WAVEFORM_AGENT_V2_ONLY` 默认 `false`，保留匿名 v1；设为 `true` 后 v1 路径返回 404，`/health` 和认证 v2 继续可用。生产推荐 v2-only 或独立进程、端口与网络 ACL；该开关不会给 v1 客户端静默增加 token 要求。

正式 contract 为 **Waveform Agent HTTP API v1**。唯一业务路由前缀是 `/api/agent/v1/waveform`，不保留未版本化 alias。`GET /health` 是独立的版本就绪检查入口。源码已实现，尚未部署现场服务；accelerator-rag、Qwen 和 RAG 集成均未实施。

调用链保持为 `WaveformAgentService → CandidateOperatorBackend → RetrievalService → AlignmentResult → AnalysisJobs → run_analysis → ChannelResult`。本轮只调整 Agent 任务编排、持久化、接口和 provenance，不修改 catalog、retrieval/alignment 算法、科学参数、Mongo/HDF5 schema 或采集 protocol v2。

[accelerator-rag Integration Handoff](handoffs/waveform-agent-v1-integration-20261020.md)包含完整 JSON schema、真实 POST/polling 响应、错误、客户端重试规则及固定 fixture。[交付记录](../交付/20261020-waveform-agent-v1/README.md)保存本次真实测试输出。

## 启动与 base URL

在仓库根目录、具备项目依赖的 Python 环境中运行：

```bash
export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT=tcp://127.0.0.1:5555
python tools/run_waveform_agent_api.py --port 8771 --output outputs/agent-analysis
```

Candidate 地址须换为实际配置，上述端口不是现场部署声明。存储沿用 `AppSettings.from_env()` 的 `WAVEFORM_STORAGE_*`、`WAVEFORM_STORAGE_SSH_CONTROL_SOCKET`；`WAVEFORM_CANDIDATE_LOCAL_FILES=1` 用于本机可读文件。外部调用方只配置 HTTP base URL，不接触存储环境配置。

开发 base URL 为 `http://127.0.0.1:8771`，启动器固定绑定 `127.0.0.1`，可通过 `--port` 改端口，通过 `--output` 改派生产物及任务状态目录。每个 output root 仅允许一个 AnalysisJobs 管理器和一个 Agent job registry 所有者。

当前服务本身**没有 authentication、TLS 或 tenant isolation**。跨主机生产接入必须经批准的 reverse proxy、gateway 或 tunnel，并配套 TLS、authentication 和 network ACL。不要将端口直接暴露到不可信网络。本轮不搭建这些部署设施。

## Health contract

```http
GET /health
```

健康时 HTTP 200：

```json
{"status":"ok","api_version":"v1","candidate":"reachable","analysis":"ready"}
```

| 字段 | 值与含义 |
| --- | --- |
| `status` | `ok`：两者均就绪；`degraded`：其中一项不就绪；`unavailable`：Candidate 不可达且分析不就绪 |
| `candidate` | `reachable` 或 `unavailable`；仅通过既有 Candidate HEALTH 命令探测可达性 |
| `analysis` | `ready`、`busy`、`closed`；同时反映 AnalysisJobs 与 Agent 队列能否接收新任务 |

非 `ok` 均返回 HTTP 503。health 响应证明 HTTP 进程可响应、Candidate 可达性及分析接收状态，不证明 Mongo/SSH 数据可读。探测不触发采集、分析、Mongo 写入或 EPICS 控制，不返回配置、凭据、路径或 traceback。Candidate health 使用 1000 ms 的传输超时设置。

## Endpoints

| 方法 | 精确路径 | 成功响应 |
| --- | --- | --- |
| GET | `/api/agent/v1/waveform/runs?date=2026-10-08` | 200，`{"runs":[CaptureRunSummary]}` |
| GET | `/api/agent/v1/waveform/requests/{request_id}/captures` | 200，`{"captures":[CaptureSegment]}` |
| GET | `/api/agent/v1/waveform/requests/{request_id}/captures/{capture_sequence}` | 200，单个 CaptureSegment |
| POST | `/api/agent/v1/waveform/spectrum` | 202，Agent job 状态 |
| GET | `/api/agent/v1/waveform/jobs/{job_id}` | 200，Agent job 状态；成功时包含 `result` |
| GET | `/api/agent/v1/waveform/jobs/{job_id}/artifacts/{artifact}` | 200，白名单派生产物附件 |

runs 必须提供一个 `YYYY-MM-DD` 日期。请求及段查询不返回文件路径；不存在的请求段返回 `NOT_FOUND`。段号使用真实 `capture_sequence`，不使用显示下标。对于 `[0,3]`，第二段必须传 `3`。

## Spectrum request 与幂等

```json
{
  "request_id":"c4577407-3528-486f-87df-6290da28e00d",
  "capture_sequence":3,
  "device_id":"AC_LA_INJ1_BI_SBPM01",
  "channel":"X",
  "profile":"high_band",
  "idempotency_key":"e4c07704-7351-4bfc-a259-a77a12e29803"
}
```

`request_id`、`idempotency_key` 都必须是带连字符的小写 canonical UUID。`capture_sequence` 是非负整数，布尔值无效。`device_id` 为非空字符串，`channel` 只允许 `X/Y`。`profile` 可省略，默认 `high_band`，也接受 `low_v1/low_v2`。其他字段一律拒绝，不接受文件路径、采样率覆盖或科学参数覆盖。请求体上限为 16 KiB。

同一 key 与相同规范化请求重复提交返回同一 Agent `job_id`，不再次检索或创建 AnalysisJobs。省略 profile 与显式 `high_band` 等价。同一 key 搭配不同请求返回 HTTP 400、`INVALID_ARGUMENT`；新 key 表示新工作。key 的作用域是这个 output root，未按用户或租户隔离。

映射与任务状态存于同一个原子 JSON 记录，受单所有者锁及线程锁保护；先持久化 accepted，再分派重操作。完整保留 output root 时，成功、失败及幂等绑定均可恢复。重启时未完成 Agent job 统一标为 `failed/UNAVAILABLE`，不自动重跑；同 key 重试仍返回这个失败任务。需要重新执行时，由调用方明确使用新 key。不要把这个机制解释为跨任意存储故障、状态删除或多集群的永久 exactly-once 保证。

## Agent job lifecycle

```text
accepted → resolving → retrieving → aligning → analyzing → succeeded
     任一执行阶段失败 → failed
```

POST 只做基础参数校验、队列接收检查和任务持久化。新 job 返回 `status="accepted"`、真实 Agent `job_id`，此时 `analysis_job_id=null`。catalog 查询、历史频率校验、波形读取、对齐和频谱分析均在后台执行。Agent 层单 worker，最多 8 个未完成 job；队列不可用时返回 HTTP 503、`UNAVAILABLE`。

同 key 重试返回该任务的当前状态，不保证仍是 accepted。进入 analyzing 后，`analysis_job_id` 才填入实际 AnalysisJobs UUID。两个 ID 的职责不同；轮询与下载始终使用 Agent `job_id`。

轮询 GET 不驱动任务执行。`failed` 是终态，HTTP 200，带统一 `error` 对象。`succeeded` 时检查 `result.classification`：`invalid` 表示分析程序成功形成一个数据/分析不可用的领域结果，不是 job infrastructure failure。Agent API 不提供取消能力。正常退出先停止接收并排空 Agent job，再关闭 AnalysisJobs；异常退出后的恢复遵循上述失败规则。

## Result 与 provenance

成功结果包含以下字段，不返回原始 waveform array：

| 分组 | 字段与语义 |
| --- | --- |
| 身份 | `request_id`、真实 `capture_sequence`、`device_id`、`channel`、`idempotency_key` |
| 采集时间 | `captured_at`：该 CaptureSegment 的带时区时间；`storage_date_local`：该段存储日期，缺失时按采集时间的 Asia/Shanghai 日期解析 |
| 采样率 | `sample_rate_hz`：历史 run 的实际频率；`sample_rate_source` 固定为 `capture_run_summary` |
| 可复现性 | `profile`、`analysis_parameters`、`algorithm_version` |
| 数据 | `data_points`、`duration_seconds`、`quality`、`warnings` |
| 判定 | `classification`、`display_status`、`reason` |
| 频谱 | `peak_hz`、`peak_to_background_db`、`resolution_hz` |
| 分块 | `block_hits`、`block_count`、`block_ratio` |
| 派生产物 | 实际 `analysis_job_id` 与 `artifact_refs` |

采样率权威来源只有所选请求的 `CaptureRunSummary.frequency_hz`；缺失时任务失败为 `ANALYSIS_INVALID`，不会读取当前 timing PV 或猜测。原始 HDF5 metadata 与历史频率冲突时沿用现有分析层检测规则，任务同样失败为 `ANALYSIS_INVALID`。AnalysisRequest 使用 `source_type="alignment"`，明确携带历史频率与来源；派生产物的 metadata、manifest 和报告保留同一来源。

`warnings` 仅保留相对时间、数据质量、修复或分析限制，不用自然语言 warning 承担采样率 provenance。`algorithm_version` 从真实分析 manifest 透传；当前标识为 `unified-spectrum-1.0`，表示既有共享频谱实现版本，结合 profile 和参数解释，不是 git HEAD。本轮没有修改该算法或科学参数。

`peak_hz` 仅表示当前 profile 的 search band 内检测到的谱峰频率；`high_band` 的搜索频带为 40～50 Hz。`peak_to_background_db` 是该分析方法的谱峰背景比。`duration_seconds` 沿用末采样时间减首采样时间。无可用指标时相关数值可为 null，质量与 reason 说明原因。没有新增 RMS、mean、std、THD 或 harmonics。

## Artifact contract 与 retention

`artifact_refs` 固定为 Agent job UUID 加白名单 name 和同 origin 的相对 HTTP URI：

```json
{
  "job_id":"AGENT_JOB_UUID",
  "artifacts":[
    {"name":"results.json","uri":"/api/agent/v1/waveform/jobs/AGENT_JOB_UUID/artifacts/results.json"},
    {"name":"results.zip","uri":"/api/agent/v1/waveform/jobs/AGENT_JOB_UUID/artifacts/results.zip"},
    {"name":"spectra.npz","uri":"/api/agent/v1/waveform/jobs/AGENT_JOB_UUID/artifacts/spectra.npz"}
  ]
}
```

客户端用 base origin 加 URI 下载，不解释服务器目录。只有成功 Agent job 可下载。未知名称、绝对路径、`..`、编码 traversal、文件/目录 symlink 逃逸都被拒绝；API 不提供任意 output 文件读取。

ZIP 沿用分析层结果包，内部 manifest 的 job UUID 是 AnalysisJobs UUID。NPZ 包含该设备 X/Y 通道频谱，结构化 result 只选择请求通道；`results.json` 通道顺序对应 `frequency_i/psd_i`。in-memory alignment 产物不包含原始存储路径。

当前**没有自动过期、GC 或 retention 配置**。output root 保存 Agent registry、AnalysisJobs 状态和派生产物；管理员需在 API 外规划保留及备份。重启恢复应完整保留该 root。删去产物后，历史 job 仍可能显示成功，但下载返回 `NOT_FOUND`；删去任务记录会破坏对应幂等绑定。URI 是可定位的 job 引用，不是永久 content-addressed reference。accelerator-rag 不得假设它永久存在，需要长期证据时应在有权限的生命周期内下载并保存。

## Error contract

错误对象统一包含 `code/reason/diagnostics`，diagnostics 固定包含 `error_code/reason/detail`。响应只保留已脱敏的领域说明，底层 INTERNAL exception 仅进服务器日志；不输出 traceback、Mongo query/URI、SSH 凭据、存储路径或环境 secret。

| code | 同步 HTTP | 情况 |
| --- | --- | --- |
| INVALID_ARGUMENT | 400 | 基础字段或 JSON 错误、幂等冲突 |
| NOT_FOUND | 404 | 查询对象、job、artifact 或 endpoint 不存在 |
| AMBIGUOUS | 409 | 请求对应多个 run/段等记录 |
| DATA_UNREADABLE | 422 | 数据或 metadata 无法读取/对齐 |
| ANALYSIS_INVALID | 422 | 分析前提无效，或请求下载尚未成功的 artifact |
| UNAVAILABLE | 503 | 队列关闭/已满或后端连接失败 |
| TIMEOUT | 504 | 同步后端查询超时 |
| INTERNAL | 500 | 未分类内部错误 |

上述 HTTP 列适用于同步操作。POST 基础合法且被接收后，后端错误都进入 job：POST 为 202，GET job 为 200、`status="failed"`，`error.code` 保留实际领域错误。例如历史频率冲突为 `ANALYSIS_INVALID`，后台 Candidate timeout 为 `TIMEOUT`，分析执行器异常为 `INTERNAL`。health 降级单独使用 HTTP 503 与组件状态对象。

## Timeout 与 retry

客户端建议：连接超时 2 秒，POST response timeout 5 秒，polling 单次 response timeout 5 秒，health response timeout 5 秒；同步 runs/captures 查询可用 15 秒。它们是客户端配置建议，不是服务端 SLA。正常 POST 不等待 Candidate、SSH、alignment 或 spectrum。

POST 超时、断线或 503 后，使用**相同 key 与相同请求**重试，可退避 0.5～5 秒。同 key 已存在时即使队列满或任务已结束，仍返回原 job；不会重新计算。400 幂等冲突须修正调用逻辑，不应盲目换 key；failed job 不会被重试 POST 重新执行。确需重新执行一个失败任务时，用户或上层工作流应明确创建新 key。

polling 超时独立于任务生命周期，继续用原 Agent job UUID 轮询，不重新提交 spectrum。建议每秒轮询一次，达到调用方总等待预算时停止等待；停止等待不会取消后台工作。

HTTP 连接读超时为 30 秒。Candidate 业务 ZMQ 往返沿用至少 10000 ms 或更大的 `WAVEFORM_ZMQ_RECEIVE_TIMEOUT_MS`；health 探测另用 1000 ms。SSH 沿用既有连接/传输行为。本轮不增加统一执行 deadline；内部 timeout 最终映射为 job 的 `TIMEOUT/UNAVAILABLE/DATA_UNREADABLE` 等稳定错误，不向客户端透出底层 exception。正常退出排空任务也可能等待现有后端操作。

## Read-only boundary

Agent router 只注册 query/read、analyze 与派生产物下载。没有 start_capture、取消采集、delete、rename、move、Mongo write、raw HDF5 overwrite 或 EPICS write/control。允许写入的仅为派生产物和 Agent/Analysis job 状态。authoritative raw HDF5、Mongo acquisition metadata 和 capture state 保持只读。

## 固定 fixture

```bash
python tools/run_waveform_agent_fixture.py --port 8771
curl 'http://127.0.0.1:8771/health'
curl 'http://127.0.0.1:8771/api/agent/v1/waveform/runs?date=2026-10-08'
curl 'http://127.0.0.1:8771/api/agent/v1/waveform/requests/c4577407-3528-486f-87df-6290da28e00d/captures'
curl -X POST 'http://127.0.0.1:8771/api/agent/v1/waveform/spectrum' \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"c4577407-3528-486f-87df-6290da28e00d","capture_sequence":3,"device_id":"AC_LA_INJ1_BI_SBPM01","channel":"X","profile":"high_band","idempotency_key":"e4c07704-7351-4bfc-a259-a77a12e29803"}'
```

fixture 的真实 catalog 返回段 `[0,3]`，使用段 3 的可用设备、1000 Hz、20000 点、47.6 Hz 正弦信号。预期谱峰 47.6 Hz、分辨率 0.05 Hz、4/4 命中块、`stable_candidate/clear_peak`。它不连接 Mongo/SSH，不采集；退出时临时数据、job 与产物全部删除。UUID、JSON 和三个下载请求详见 Integration Handoff；示例响应中的运行 UUID 每次重新生成。
