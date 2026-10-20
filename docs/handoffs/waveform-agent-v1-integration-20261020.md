# accelerator-rag WaveformStorageClient Integration Handoff

交接日期：2026-10-20。源码完成，部署状态为 NO。本文只定义 waveform-storage v1，不声明 accelerator-rag/Qwen 已接入。

## CONTRACT_VERSION

Waveform Agent HTTP API v1。正式业务前缀为 `/api/agent/v1/waveform`，没有未版本化 alias。底层 Candidate protocol v2 不变。

## BASE_URL

开发 origin：`http://127.0.0.1:8771`。客户端把这个 origin 配成 base URL；endpoint 和 artifact URI 已包含完整路径前缀，不能再重复拼接。

正式启动：

```bash
export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT=tcp://127.0.0.1:5555
python tools/run_waveform_agent_api.py --port 8771 --output outputs/agent-analysis
```

端口 5555 只是配置示例，应换成真实 Candidate 地址。启动器固定绑定 loopback；`--port` 与 `--output` 分别控制 HTTP 端口和 output root。存储连接沿用 `AppSettings` 环境配置，仅服务端需要知道。该脚本依赖仓库的 `apps/operator_gui/candidate_backend.py`；使用具备项目依赖的 Python。

本轮验证环境为 `/home/whloong/miniconda3/envs/waveform-storage/bin/python`。普通 Python 与仓库 `.venv` 缺少 SciPy，不应用它们的失败启动冒充接口结果。

Production origin 尚未部署或分配。跨主机必须经批准的 reverse proxy/gateway/tunnel，并启用 TLS、authentication 和 network ACL；部署层应将其安全 origin 提供给客户端。本服务没有内建 auth、TLS 或 tenant isolation。

## HEALTH

完整请求：

```http
GET /health HTTP/1.1
Host: 127.0.0.1:8771
```

本次实际 HTTP 200 响应：

```json
{
  "status": "ok",
  "api_version": "v1",
  "candidate": "reachable",
  "analysis": "ready"
}
```

Candidate 不可达且 analysis ready：HTTP 503，`status=degraded`、`candidate=unavailable`、`analysis=ready`。Candidate 可达但 manager 已关闭：HTTP 503，`status=degraded`、`analysis=closed`。两者都不可用：HTTP 503，`status=unavailable`。队列满时 analysis 为 `busy`，返回 503。

该探测只执行 Candidate HEALTH，1000 ms 传输超时设置；检查 AnalysisJobs/Agent 队列接收状态，不发起采集、分析、Mongo 写入或 EPICS 控制。它证明 Candidate 可达，不承诺 Mongo/SSH 或某个 waveform 可读。响应没有敏感配置。

## ENDPOINTS

| 方法 | 路径 | 成功状态 |
| --- | --- | --- |
| GET | `/health` | 200；降级为 503 |
| GET | `/api/agent/v1/waveform/runs?date=YYYY-MM-DD` | 200 |
| GET | `/api/agent/v1/waveform/requests/{request_id}/captures` | 200 |
| GET | `/api/agent/v1/waveform/requests/{request_id}/captures/{capture_sequence}` | 200 |
| POST | `/api/agent/v1/waveform/spectrum` | 202 |
| GET | `/api/agent/v1/waveform/jobs/{job_id}` | 200 |
| GET | `/api/agent/v1/waveform/jobs/{job_id}/artifacts/{artifact}` | 200 |

runs 返回 `{"runs":[...]}`，必须有单个 ISO date filter；run 只包含领域摘要。captures 返回 `{"captures":[...]}`；单段响应包含 request_id、capture_sequence、captured_at、storage_date_local、time_sec、device_ids。段号来自真实返回值，不是 GUI display index。

## SPECTRUM_REQUEST

请求 JSON schema：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Waveform Agent HTTP API v1 spectrum request",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "request_id",
    "capture_sequence",
    "device_id",
    "channel",
    "idempotency_key"
  ],
  "properties": {
    "request_id": {
      "type": "string",
      "format": "uuid",
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    },
    "capture_sequence": {
      "type": "integer",
      "minimum": 0
    },
    "device_id": {
      "type": "string",
      "minLength": 1
    },
    "channel": {
      "enum": [
        "X",
        "Y"
      ]
    },
    "profile": {
      "enum": [
        "high_band",
        "low_v1",
        "low_v2"
      ],
      "default": "high_band"
    },
    "idempotency_key": {
      "type": "string",
      "format": "uuid",
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    }
  }
}
```

完整 fixture request：

```json
{
  "request_id": "c4577407-3528-486f-87df-6290da28e00d",
  "capture_sequence": 3,
  "device_id": "AC_LA_INJ1_BI_SBPM01",
  "channel": "X",
  "profile": "high_band",
  "idempotency_key": "e4c07704-7351-4bfc-a259-a77a12e29803"
}
```

布尔 capture_sequence 被拒绝。请求体最多 16 KiB。不要传 path、Mongo、SSH、频率覆盖或分析参数覆盖。

幂等语义：同一 key 加相同规范化请求返回同一 Agent job；省略 profile 与 high_band 等价。同 key 加不同业务字段返回 400/INVALID_ARGUMENT。新 key 创建新 job。每次逻辑请求先生成并保存 key，再发送；网络 retry 保留它。

映射和任务状态共用原子 JSON 记录，常规进程重启后在同一完整 output root 下保留。进程异常退出的未完成 job 标为 failed/UNAVAILABLE，不自动重跑，同 key 仍返回该失败 job。幂等域不是跨任意 root 或集群的全局域；删除 registry、丢失 root 或存储损坏后不保证原绑定。

## POST_RESPONSE

本次真实 HTTP 202 响应：

```json
{
  "job_id": "1bb975c2-0d15-4518-aef4-208bf5c0d2f6",
  "analysis_job_id": null,
  "status": "accepted",
  "analysis_parameters": {
    "profile": "high_band",
    "search_low_hz": 40.0,
    "search_high_hz": 50.0,
    "background_low_hz": 30.0,
    "background_high_hz": 60.0,
    "welch_seconds": 20.0,
    "block_seconds": 5.0,
    "threshold_db": 6.0,
    "minimum_points": 128,
    "minimum_seconds": 5.0,
    "minimum_blocks": 3,
    "minimum_block_ratio": 0.6,
    "peak_match_hz": 1.0,
    "max_gap_factor": 5.0,
    "zero_policy": "keep"
  },
  "created_at": "2026-10-20T01:58:59.769072+00:00",
  "updated_at": "2026-10-20T01:58:59.769090+00:00",
  "request_id": "c4577407-3528-486f-87df-6290da28e00d",
  "capture_sequence": 3,
  "device_id": "AC_LA_INJ1_BI_SBPM01",
  "channel": "X",
  "profile": "high_band",
  "idempotency_key": "e4c07704-7351-4bfc-a259-a77a12e29803"
}
```

新 job 的 accepted 已持久化，catalog/retrieval/alignment 尚未成为 POST 的同步等待。analysis_job_id 此时为 null，后续进入分析后才绑定真实 AnalysisJobs UUID。幂等 retry 可直接返回更晚的状态，仍是 HTTP 202。

## JOB_STATES

```text
accepted → resolving → retrieving → aligning → analyzing → succeeded
任一执行阶段异常 → failed
```

Agent 层单 worker，最多 8 个未完成 job。队列/manager 关闭或已满时新请求返回 503/UNAVAILABLE；已绑定的幂等 retry 仍可返回原 job。所有 catalog、频率校验、retrieval、alignment 和 spectrum 均由 job 执行。

使用 Agent job_id 轮询与下载。analysis_job_id 是内部分析的真实身份，不能替代 HTTP URI 中的 Agent job_id。服务正常关闭先停止接收并排空任务，然后关闭 AnalysisJobs；没有 Agent cancel endpoint。

## POLLING_RESULT

```http
GET /api/agent/v1/waveform/jobs/1bb975c2-0d15-4518-aef4-208bf5c0d2f6 HTTP/1.1
Host: 127.0.0.1:8771
```

本次真实 HTTP 200 succeeded 响应；每次复现会生成新的运行 UUID：

```json
{
  "job_id": "1bb975c2-0d15-4518-aef4-208bf5c0d2f6",
  "analysis_job_id": "f663cca2-a06f-4cca-8cb9-a08a8546c115",
  "status": "succeeded",
  "analysis_parameters": {
    "profile": "high_band",
    "search_low_hz": 40.0,
    "search_high_hz": 50.0,
    "background_low_hz": 30.0,
    "background_high_hz": 60.0,
    "welch_seconds": 20.0,
    "block_seconds": 5.0,
    "threshold_db": 6.0,
    "minimum_points": 128,
    "minimum_seconds": 5.0,
    "minimum_blocks": 3,
    "minimum_block_ratio": 0.6,
    "peak_match_hz": 1.0,
    "max_gap_factor": 5.0,
    "zero_policy": "keep"
  },
  "created_at": "2026-10-20T01:58:59.769072+00:00",
  "updated_at": "2026-10-20T01:58:59.856608+00:00",
  "result": {
    "request_id": "c4577407-3528-486f-87df-6290da28e00d",
    "capture_sequence": 3,
    "device_id": "AC_LA_INJ1_BI_SBPM01",
    "channel": "X",
    "profile": "high_band",
    "idempotency_key": "e4c07704-7351-4bfc-a259-a77a12e29803",
    "sample_rate_hz": 1000.0,
    "data_points": 20000,
    "duration_seconds": 19.999,
    "classification": "stable_candidate",
    "display_status": "clear_peak",
    "reason": "整体谱峰显著，且在本次记录的独立秒级分块中重复出现",
    "peak_hz": 47.6,
    "peak_to_background_db": 240.1270504522734,
    "resolution_hz": 0.05,
    "block_hits": 4,
    "block_count": 4,
    "block_ratio": 1.0,
    "quality": {
      "raw_points": 20000,
      "removed_points": 0,
      "duplicate_points": 0,
      "first_valid_offset_seconds": 0.0,
      "valid_points": 20000,
      "median_interval_seconds": 0.0009999999999998899,
      "interval_mad_seconds": 4.440892098500626e-16,
      "max_gap_seconds": 0.0010000000000012221,
      "nyquist_hz": 500.0,
      "resampled_points": 20000,
      "short_gap_fill_points": 0
    },
    "warnings": [
      "时间轴为相对时间",
      "X/Y 同时为零的点有 1 个；默认保留"
    ],
    "captured_at": "2026-10-08T08:00:03+00:00",
    "storage_date_local": "2026-10-08",
    "sample_rate_source": "capture_run_summary",
    "algorithm_version": "unified-spectrum-1.0",
    "analysis_parameters": {
      "profile": "high_band",
      "search_low_hz": 40.0,
      "search_high_hz": 50.0,
      "background_low_hz": 30.0,
      "background_high_hz": 60.0,
      "welch_seconds": 20.0,
      "block_seconds": 5.0,
      "threshold_db": 6.0,
      "minimum_points": 128,
      "minimum_seconds": 5.0,
      "minimum_blocks": 3,
      "minimum_block_ratio": 0.6,
      "peak_match_hz": 1.0,
      "max_gap_factor": 5.0,
      "zero_policy": "keep"
    },
    "analysis_job_id": "f663cca2-a06f-4cca-8cb9-a08a8546c115",
    "artifact_refs": {
      "job_id": "1bb975c2-0d15-4518-aef4-208bf5c0d2f6",
      "artifacts": [
        {
          "name": "results.json",
          "uri": "/api/agent/v1/waveform/jobs/1bb975c2-0d15-4518-aef4-208bf5c0d2f6/artifacts/results.json"
        },
        {
          "name": "results.zip",
          "uri": "/api/agent/v1/waveform/jobs/1bb975c2-0d15-4518-aef4-208bf5c0d2f6/artifacts/results.zip"
        },
        {
          "name": "spectra.npz",
          "uri": "/api/agent/v1/waveform/jobs/1bb975c2-0d15-4518-aef4-208bf5c0d2f6/artifacts/spectra.npz"
        }
      ]
    }
  },
  "request_id": "c4577407-3528-486f-87df-6290da28e00d",
  "capture_sequence": 3,
  "device_id": "AC_LA_INJ1_BI_SBPM01",
  "channel": "X",
  "profile": "high_band",
  "idempotency_key": "e4c07704-7351-4bfc-a259-a77a12e29803"
}
```

`peak_hz` 仅是 profile search band 内检测到的谱峰频率；high_band 为 40～50 Hz。`peak_to_background_db` 是谱峰背景比。fixture 是理想正弦，其数值不能当作真实设备验收门槛。duration_seconds 是末点时间减首点时间。

job `failed` 是执行/依赖失败，GET 仍为 200 并含 error。job `succeeded` 且 classification 为 `invalid` 是已完成的领域分析结果；检查 reason、quality 和 warnings，不要把它当作 infrastructure failure，也不要为了这个 classification 自动重跑。

## ERROR

本次真实幂等冲突 HTTP 400 响应：

```json
{
  "error": {
    "code": "INVALID_ARGUMENT",
    "reason": "Idempotency key is bound to a different request",
    "diagnostics": {
      "error_code": "INVALID_ARGUMENT",
      "reason": "Idempotency key is bound to a different request",
      "detail": "Idempotency key is bound to a different request"
    }
  }
}
```

| error.code | 同步 HTTP 状态 |
| --- | --- |
| INVALID_ARGUMENT | 400 |
| NOT_FOUND | 404 |
| AMBIGUOUS | 409 |
| DATA_UNREADABLE | 422 |
| ANALYSIS_INVALID | 422 |
| UNAVAILABLE | 503 |
| TIMEOUT | 504 |
| INTERNAL | 500 |

基础参数错误同步返回；任务已接收后的失败返回在 GET job HTTP 200 中，保留实际 error.code。例如 Candidate timeout 为 TIMEOUT，连接失败为 UNAVAILABLE，无法读取 waveform 为 DATA_UNREADABLE，历史频率缺失/冲突为 ANALYSIS_INVALID，分析执行器异常为 INTERNAL。

error.reason 与 diagnostics 仅为安全领域说明。不要依赖英文自然语言文本进行分支，应依赖 code。底层 INTERNAL exception 只进服务器日志；响应不含 traceback、Mongo query/URI、SSH 凭据、原始/存储路径或 env secret。

failed job 形态为普通 job 字段加 `"status":"failed"` 和同样的 error 对象，不包含伪造 spectrum result。

## ARTIFACTS

允许 name 固定为 `results.json`、`results.zip`、`spectra.npz`。URI 形态固定为：

```text
/api/agent/v1/waveform/jobs/{AGENT_JOB_ID}/artifacts/{NAME}
```

`artifact_refs` 包含 `job_id` 与 `artifacts[{name,uri}]`。客户端用 base origin 加 uri；服务器从持久化 job 找到真实 analysis output，不向客户端暴露这个目录。仅 succeeded job 可下载。未知 artifact、绝对路径、`..`、编码 traversal 或 symlink 逃逸被拒绝。

完整三个本次 URI 已在上述 polling result 中给出。results.json 通道顺序对应 spectra.npz 的 frequency_i/psd_i；NPZ 含所选设备的 X/Y 频谱，result 只投影请求通道。ZIP 内 manifest 使用 AnalysisJobs UUID，不是 Agent job UUID。

当前没有自动 expiration/GC，也没有空配置的 retention 功能。output root 包含 agent-v1-jobs/、AnalysisJobs jobs/ 和派生产物目录，应完整保留以恢复绑定和结果。产物被管理员删除后下载可返回 NOT_FOUND；job 仍可能保留成功摘要。registry 被删除会丢失幂等映射。URI 不是永久 content-addressed reference，accelerator-rag 不得假设永久存在；长期证据需及时下载保存。

预冻结版本的旧任务目录保留在原 root 中，v1 不自动迁移旧记录。fixture 使用临时 root，进程退出后所有 URI 失效。

## TIMEOUT

| 客户端操作 | 建议 timeout |
| --- | --- |
| connect | 2 秒 |
| POST spectrum response | 5 秒 |
| GET job response | 5 秒 |
| GET /health response | 5 秒 |
| runs/captures 同步查询 | 15 秒 |

建议每秒 polling 一次，并设置独立的整体等待预算。这些是客户端初始配置，不是服务端 SLA。POST 现在不等待 retrieval/alignment。

POST 超时、连接断开或 503 后可退避 0.5～5 秒重试，必须复用同一 idempotency_key 与同一规范化 payload。已创建的任务会返回同一 job_id；未接收时可在恢复后创建一次。不能因为没收到 job_id 就换 key。400 幂等冲突应修正调用逻辑。failed job 的同 key retry 不会重新执行，确需重跑时明确新建 key。

GET job timeout 后继续 polling 原 job_id，不重新 POST，也不会取消后台任务。到达整体等待预算后可以停止等待，后续仍能恢复 polling。

HTTP 连接读超时 30 秒。Candidate 业务 ZMQ 超时沿用至少 10000 ms 或更大的 WAVEFORM_ZMQ_RECEIVE_TIMEOUT_MS；health 设置为 1000 ms。SSH 沿用既有传输行为。后台没有新增统一执行 deadline，底层 timeout 只以安全领域错误出现在 failed job 中。正常关闭排空任务也可能等待既有后端操作。

## SECURITY

允许 query/read、analyze 和 derived artifact download，以及服务内部派生产物/Agent job 状态写入。

不允许 start_capture、取消采集、delete、rename、move、Mongo write、raw HDF5 overwrite、EPICS write/control。authoritative raw HDF5、Mongo acquisition metadata 和 capture state 保持只读。router 不注册这些能力。

本服务没有 auth、TLS、tenant isolation。开发仅 loopback。跨主机生产接入必须采用批准的 proxy/gateway/tunnel，并配置 TLS、认证和 ACL；当前不直接公开到不可信网络。

## PROVENANCE

| 字段 | 权威来源 |
| --- | --- |
| request_id | canonical 请求 UUID，精确匹配 catalog |
| capture_sequence | retrieve_segments 返回的实际段号 |
| captured_at | 所选 CaptureSegment.captured_at，带时区 |
| storage_date_local | 所选段字段；缺失时取 captured_at 的 Asia/Shanghai 日期 |
| device_id/channel | 段内真实设备；精确选择设备名加 _X 或 _Y 的 ChannelResult |
| sample_rate_hz | 历史 CaptureRunSummary.frequency_hz |
| sample_rate_source | 固定 capture_run_summary |
| profile/analysis_parameters | 本次 AnalysisConfig，科学参数不变 |
| algorithm_version | run_analysis manifest 的真实版本 unified-spectrum-1.0 |
| analysis_job_id | 实际 AnalysisJobs UUID |
| artifact_refs | Agent job UUID 的 v1 HTTP 下载引用 |

HDF5/history 频率冲突仍使用现有检测，不猜测频率，不读取当前 timing PV。warnings 只保留时间轴、数据质量或限制说明；provenance 是结构化字段。sample-rate provenance 同时写入派生 manifest 和 channel metadata，不把来源伪称为用户输入。算法版本来自既有实现，不是 git HEAD。

## FIXTURE

终端一启动：

```bash
python tools/run_waveform_agent_fixture.py --port 8771
```

终端二完整请求：

```bash
curl 'http://127.0.0.1:8771/health'
curl 'http://127.0.0.1:8771/api/agent/v1/waveform/runs?date=2026-10-08'
curl 'http://127.0.0.1:8771/api/agent/v1/waveform/requests/c4577407-3528-486f-87df-6290da28e00d/captures'
curl 'http://127.0.0.1:8771/api/agent/v1/waveform/requests/c4577407-3528-486f-87df-6290da28e00d/captures/3'
curl -X POST 'http://127.0.0.1:8771/api/agent/v1/waveform/spectrum' \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"c4577407-3528-486f-87df-6290da28e00d","capture_sequence":3,"device_id":"AC_LA_INJ1_BI_SBPM01","channel":"X","profile":"high_band","idempotency_key":"e4c07704-7351-4bfc-a259-a77a12e29803"}'
```

拿到 job_id 后每秒 GET v1 jobs/{job_id}，成功后对三个 artifact_refs.uri 逐个 GET。再次 POST 同一 JSON 应返回相同 job_id；保留 key 把 channel 改成 Y 应得到 400/INVALID_ARGUMENT。

固定断言：catalog 段号为 [0,3]；选择真实 sequence 3；设备 AC_LA_INJ1_BI_SBPM01；X；1000 Hz；20000 点；captured_at 为 2026-10-08T08:00:03+00:00；storage_date_local 为 2026-10-08；sample_rate_source 为 capture_run_summary；peak_hz 约 47.6；resolution_hz 约 0.05；duration_seconds 为 19.999；block_hits/block_count 为 4/4；stable_candidate/clear_peak；算法版本存在；三个 URI 均为 v1。

fixture 经真实 in-memory catalog、既有 Candidate 协议和后端、RetrievalService、AlignmentResult、AnalysisJobs、run_analysis、ChannelResult，不连接 Mongo/SSH、不采集。它依赖仓库 tests，不是现场生产数据。

## FILES_CHANGED

本轮业务、测试与文档变更：

- `apps/operator_gui/candidate_backend.py`：只读 HEALTH 探测。
- `src/waveform_storage/application/agent_jobs.py`：新增有界、持久化的 Agent job registry、幂等和恢复。
- `src/waveform_storage/application/agent_service.py`：完整后台生命周期、结果 provenance、health 和安全 artifact 映射。
- `src/waveform_storage/application/agent_http_api.py`：v1 路由、health、idempotency 字段、拒绝写路由。
- `src/waveform_storage/analysis/models.py`：alignment request 的明确来源字段。
- `src/waveform_storage/analysis/loaders.py`：结构化历史来源与无误导 warning。
- `src/waveform_storage/analysis/service.py`：来源 manifest/报告及只读 availability。
- `tools/run_waveform_agent_api.py`：按顺序排空 Agent job 后关闭 AnalysisJobs。
- `tests/test_waveform_agent_api.py`：v1 E2E、生命周期、幂等、恢复、health、安全和错误回归。
- `docs/waveform-agent-api.md`、`docs/spectrum-analysis-usage.md`、`docs/CURRENT_STATUS.md`：当前接口与源码状态。
- 本 handoff 与 `交付/20261020-waveform-agent-v1/`：本轮真实证据。

AOCI 托管索引与 baseline 在稳定状态收尾时按项目规则单独维护。原有 `tools/run_waveform_agent_fixture.py` 入口复用更新后的 fixture，本轮未修改该脚本。

## TESTS_EXECUTED

本轮实际命令：

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

附加真实 HTTP 证据流程验证 health、POST、同 key retry/conflict、polling 和三个下载，输出：

```text
v1 HTTP fixture: health, catalog-backed spectrum, idempotency retry/conflict, polling and 3 artifacts OK
```

日志及 JSON 见 [本轮 evidence](../../交付/20261020-waveform-agent-v1/evidence/)。35 项定向测试含 12 项 Agent v1 测试；本轮完整结果是 255 项，不引用旧的 247 项作为本次结果。

## REMAINING_LIMITATIONS

- 部署状态：**NO**。没有现场 base URL，没有部署 proxy/TLS/auth/ACL，也没有 accelerator-rag/Qwen 集成。
- 服务没有内建 auth、TLS、tenant isolation；幂等 key 属于整个 output root。
- 单 Agent worker、最多 8 个未完成任务；同一 root 不支持多进程并行 owner 或跨 root 的共享幂等。
- 无自动 artifact GC/永久保留承诺；成功结果与幂等恢复依赖保留任务 registry/派生产物的 output root。
- 异常退出后未完成 Agent job 标为失败，不自动恢复执行；确需重跑必须明确创建新 key。未关联完的内部分析产物可能留在 root 中，无自动清理。
- 无统一后台执行 deadline；正常关闭会等待已接收工作。客户端停止 polling 不取消 job。
- 预冻结旧 job 不自动迁移。当前不提供按业务字段查找所有 Agent job 的列表接口；网络 retry 使用同一 key 恢复定位。

本轮未执行现场部署、桌面构建/真实桌面 smoke、SHA-256/完整 manifest/二进制身份检查或完整发布验收。既有版本、配置和原始数据未切换；新增服务停止即退出运行入口，无原始数据迁移需要回滚。
