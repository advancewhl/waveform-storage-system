# Waveform Agent HTTP API v2

## 状态与边界

本版本包含 2026-10-22 的加固修复。v1 继续使用 `/api/agent/v1/waveform`，请求与响应语义保持兼容。v2 使用 `/api/agent/v2`，增加独立认证、受控采集和数据版本目录。现场尚未部署，accelerator-rag 客户端的修改不属于本仓库。

本服务复用 Candidate protocol v2、RetrievalService、HDF5 reader/writer、alignment 和 AnalysisJobs。采集通过既有 ZMQ 服务进入 `RegistryPolicy → PersistentTaskExecutor → JsonTaskStore → ProductionCaptureRunner`。HTTP 进程不另建采集执行器，不改变采集协议和科学算法。

本服务不提供 Agent 编排、审批界面、操作员登录、RBAC、shell、Mongo 直写、任意路径、任意 URL 或 EPICS 写入接口。操作员身份由 accelerator-rag 验证；本服务校验签名、稳定操作员标识和请求绑定。

完整字段定义见 [OpenAPI 3.0.3](agent-api-v2.openapi.json)。实际 HTTP fixture 证据见[交付目录](../交付/20261021-waveform-agent-v2/README.md)。

## 启动

使用项目依赖环境，在仓库根目录启动：

```bash
export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT=tcp://127.0.0.1:5555
export WAVEFORM_AGENT_V2_ONLY=true
# 从部署环境注入以下秘密，勿把真实值写入命令记录或仓库。
# WAVEFORM_AGENT_READ_TOKEN
python tools/run_waveform_agent_api.py --port 8771 \
  --output outputs/agent-analysis --registry config/devices.probe47.json
```

端口 5555 是配置示例。HTTP 固定绑定 loopback。跨主机访问须经过 TLS 反向代理和网络 ACL。服务 token 按能力分离，不能替代操作员登录。

| 环境变量 | 含义与默认值 |
| --- | --- |
| `WAVEFORM_AGENT_V2_ONLY` | 默认 `false`，保留匿名 v1；`true` 仅注册 v2 和 `/health`，v1 路径返回 404；必须同时启用 v2 |
| `WAVEFORM_AGENT_RATE_LIMIT_PER_MINUTE` | 每个 scope token 每 60 s 的请求配额，默认 60，`0` 关闭 |
| `WAVEFORM_AGENT_WRITE_RATE_LIMIT_PER_MINUTE` | control/data 写操作附加配额，默认 10/min，`0` 关闭；preview 不占此配额 |
| `WAVEFORM_AGENT_APPROVAL_SECRET_PREVIOUS` | 可选的上一审批密钥，只用于轮换窗口验签 |
| `WAVEFORM_AGENT_READ_TOKEN` | v2 read token；配置后启用 v2。未配置时保持原 v1 启动方式 |
| `WAVEFORM_AGENT_CONTROL_TOKEN` | control 专用 token |
| `WAVEFORM_AGENT_DATA_TOKEN` | data 专用 token |
| `WAVEFORM_AGENT_CONTROL_ENABLED` | 只有 `true` 才启用，默认关闭 |
| `WAVEFORM_AGENT_DATA_ENABLED` | 只有 `true` 才启用，默认关闭 |
| `WAVEFORM_AGENT_APPROVAL_SECRET` | 启用 control/data 时必须配置的 HMAC 密钥 |
| `WAVEFORM_AGENT_MAX_DOWNLOAD_BYTES` | 单个 raw/artifact 下载上限，默认 67108864 B，配置最大 1 GiB |
| `WAVEFORM_AGENT_DOWNLOAD_TIMEOUT` | 文件快照读取期限，默认 30 s，配置最大 300 s |
| `WAVEFORM_AGENT_MAX_CAPTURE_SECONDS` | HTTP 层采集时长上限，默认 3600 s；后端还会独立校验 |
| `WAVEFORM_AGENT_MAX_CAPTURE_DEVICES` | HTTP 层采集设备上限，默认 32；后端还会独立校验 |
| `WAVEFORM_CANDIDATE_LOCAL_FILES` | `1` 表示通过本机文件系统读取目录中的文件 |
| `WAVEFORM_LOCAL_DATA_ROOT` | 本机 raw 允许根目录，来自 AppSettings |
| `WAVEFORM_STORAGE_*` | 远端存储配置，来自 AppSettings |
| `WAVEFORM_OPERATOR_KNOWN_HOSTS_FILE` | 远端 raw 快照的额外 SSH 信任文件；未知主机密钥拒绝连接 |

三个 scope 的 token 必须不同。开启写 scope 而缺少对应 token 或审批密钥时，启动失败。密钥由独立审批签发端和服务端持有，不能提供给模型或普通 read 客户端。

管理员必须完整备份 output root。AnalysisJobs、v1 job registry 和 v2 catalog 都有进程排他锁。多进程或多主机不得各自使用一份目录副本并声称具有全局幂等性。

## 认证与审批

每个 v2 请求都需要对应 scope 的 `Authorization: Bearer …`。缺失或错误 token 返回 401；使用其他 scope 的有效 token 返回 403。已关闭 scope 返回 503、`CAPABILITY_UNAVAILABLE`。

read scope 的 POST alignment、spectrum 和 analysis job cancel 不要求人工审批或 `X-Approval-*` header，但需要 read service token 和幂等 key。data preview 只需 DataToken 和幂等 key，不要求审批 header，也不消耗 approval。所有 control POST 和 data mutation（update、soft-delete、restore、overwrite）强制签名审批，并需要：

```http
Idempotency-Key: <stable key>
X-Operation-Id: <canonical UUID>
X-Approval-Id: <canonical UUID>
X-Operator-Id: <stable identifier>
X-Approval-Token: <base64url payload>.<hex HMAC-SHA256>
```

标识符只接受 1～128 个英文字母、数字、下划线或连字符。UUID 使用小写带连字符的规范格式。`Idempotency-Key` 也可以通过 body 的 `idempotency_key` 提供；两者同时存在时必须相同。

审批 payload 至少包含：

```json
{
  "approval_id": "8ccca102-89ed-43ac-9e0c-3c74e39a3d1a",
  "operator_id": "operator-01",
  "action": "POST /api/agent/v2/data/soft-delete",
  "action_hash": "<64 lowercase hex characters>",
  "expires_at": 1792569600,
  "scope": "data"
}
```

`expires_at` 是 UTC Unix 秒。规范化 JSON 使用 UTF-8、键排序、无多余空白、`ensure_ascii=False`、`allow_nan=False`。`action_hash` 是规范化 `{"action": action, "body": 完整请求体}` 的 SHA-256。所有 body 字段都参与，包括可选的 `idempotency_key`。

payload 以同一规则序列化，进行无 padding 的 base64url 编码。对编码后的 ASCII 字符串计算 HMAC-SHA256，输出小写十六进制签名，再用一个点连接。签发端可复用 `agent_v2_contract.sign_approval` 和 `action_hash`；API 本身不提供签发入口。

服务器检查签名、action、action_hash、scope、有效期及操作员引用。body 中的 `operator_id`、`approval_id` 必须与 header 一致。审批 ID 首次接收后持久化占用。参数变化必须重新 preview、确认并签发新审批。相同 key 的重试必须保留原 operation、approval 和 operator 引用。

已经登记的同一操作，可以携带原签名在过期后查询原结果。该例外只返回或查询原任务状态，不再执行副作用。新操作的过期审批一律拒绝。审批引用不是操作员身份认证，服务不会证明标识对应哪个自然人。

轮换时先将旧密钥配置为 `WAVEFORM_AGENT_APPROVAL_SECRET_PREVIOUS`，再把新密钥配置为 `WAVEFORM_AGENT_APPROVAL_SECRET`，重启服务后仅用 current 签发新审批。服务先试 current，再试 previous；签名格式与 payload 保持兼容。窗口至少覆盖客户端迁移所需时间，并设定明确结束时间；到期移除 previous 并重启。移除后，旧密钥的重试也会被拒绝，应查询已记录 operation。重启前须等待采集结束；本服务不会重放任务。

限流在认证后按 scope token 独立计数，使用进程内固定 60 s 窗口；不会保存或记录 token。超过任一配额返回 HTTP 429、`RESOURCE_BUSY`。control/data 写操作同时受总配额与写配额约束，preview 只受总配额约束。无审批或无效审批的已认证写请求也计入配额。窗口结束恢复，进程重启清零；多进程配额需由代理统一限制。v1 不受此 v2 限流器约束。

## Read API

以下路径相对于 `/api/agent/v2/read`。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/health` | Candidate 与分析就绪状态 |
| GET | `/runs?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` | 闭区间，最多 31 天；支持 `device_id`、`status` |
| GET | `/runs/{request_id}` | 任务摘要 |
| GET | `/requests/{request_id}/captures` | 可见真实数据段 |
| GET | `/requests/{request_id}/captures/{capture_sequence}` | 段与对象当前版本信息 |
| GET | `/devices?section=&acquisition_only=true` | 注册表设备；不泄露 PV、主机与连接配置 |
| POST | `/alignments` | 异步对齐，202 |
| GET | `/alignments/{job_id}` | 状态，失败任务仍为 200 |
| GET | `/alignments/{job_id}/result` | 质量摘要与 provenance，不含原始数组 |
| GET | `/alignments/{job_id}/export?format=legacy\|v2` | 现有 writer 生成的 HDF5 附件 |
| GET | `/requests/{request_id}/captures/{capture_sequence}/raw/{device_id}` | 精确对象的当前版本下载 |
| POST | `/spectrum` | 异步单设备通道频谱，202 |
| GET | `/jobs/{job_id}` | 对齐或频谱任务状态 |
| POST | `/jobs/{job_id}/cancel` | 请求分析取消，返回实际状态 |
| GET | `/jobs/{job_id}/artifacts/{artifact_name}` | 白名单附件 |

runs、captures、devices、versions 和 operations 的列表均返回 `total`、`truncated`、`next_cursor`。`limit` 默认 100，范围为 1～500。使用原筛选参数和 `next_cursor` 继续读取。列表发生变化时，旧 cursor 返回 409，应重新查询。未知或重复 query 参数会被拒绝。

对齐示例：

```json
{
  "request_id": "c4577407-3528-486f-87df-6290da28e00d",
  "capture_sequence": 3,
  "device_ids": ["AC_LA_INJ1_BI_SBPM01"],
  "parameters": {
    "mode": "strict",
    "min_id_device_coverage": 1.0,
    "min_device_id_coverage": 1.0,
    "zero_policy": "keep",
    "duplicate_policy": "reject_device"
  }
}
```

参数使用现有 `AlignmentParameters`，支持 `strict` 和 `coverage`。最多选择 64 个设备。调用链为安全快照、现有 HDF5 reader、RetrievalService.prepare/align_prepared 和现有 writer。算法不会拼接不同数据段，也不会把 bunchID 当作物理时钟。

带真实段身份的 `RetrievalService.export` 原本强制使用 v2。为提供显式 legacy 下载，v2 边界直接调用同一个 `write_legacy_merged_hdf5`，没有改变 RetrievalService 的既有规则。只有所有单元有效时才提供 legacy 附件。含缺失单元的 coverage 结果不生成 legacy 文件，下载该文件返回 `NOT_FOUND`。

频谱示例：

```json
{
  "request_id": "c4577407-3528-486f-87df-6290da28e00d",
  "capture_sequence": 3,
  "device_id": "AC_LA_INJ1_BI_SBPM01",
  "channel": "X",
  "profile": "high_band"
}
```

频谱仍使用 AnalysisJobs 和统一科学实现。profile 接受 `high_band`、`low_v1`、`low_v2`，默认 `high_band`。采样率来自历史 `CaptureRunSummary.frequency_hz`。缺失或与 HDF5 冲突时失败，不读取当前机器频率代替历史频率。统计分类不表示设备物理故障。

v2 任务由一个后台 worker 执行，最多 8 个未完成任务。频谱与 v1 共用 AnalysisJobs 的单 worker 和最多 8 个待完成任务限制。取消在安全边界生效，终态以 GET 为准。重启将未完成任务置为失败，不重放。

provenance 包含每个源对象的身份、版本、字节数和 SHA-256，以及采样率来源、参数、采集时间和生成时间。alignment 的 `algorithm_version` 绑定现有 alignment 源码摘要，`algorithm_source_sha256` 对应该模块的实际源码。spectrum 的 `algorithm_version` 来自实际分析 manifest；该 manifest 没有定义稳定的频谱源码集合，因此 spectrum 省略 `algorithm_source_sha256`，不沿用 alignment hash。附件 URI 始终使用 v2 job UUID，内部 `analysis_job_id` 是另一身份。

对齐附件为 `alignment-v2.h5`，符合条件时另有 `alignment-legacy.h5`。频谱附件为 `results.zip`、`results.json`、`spectra.npz`、`run-manifest.json`、`report.md`、`spectrum-summary.csv`、`spectrum-blocks.csv`、`data-quality.csv`。下载使用二进制响应，不能把附件直接放进模型上下文。

下载开始发送响应头后发生读写异常，只记录不含路径和异常详情的日志并关闭连接，不追加 JSON 或第二个 HTTP 响应。客户端必须检查实际长度和 checksum，丢弃不完整下载。发送前失败仍返回结构化错误。

下载先以受限大小流式复制到临时磁盘快照，再分块发送。响应提供 `Content-Length`、`X-Content-SHA256` 和 ETag。checksum 对应本次发送的快照；进程不返回底层路径。超限、空 raw、链接逃逸和外部 HDF5 数据引用会被拒绝。分析读取还限制数据集元素数量，避免小文件触发无界解压。

## Control API

以下路径相对于 `/api/agent/v2/control`。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/captures` | 经现有 Candidate 服务提交采集，202 |
| GET | `/captures/{request_id}` | 查询同一请求的真实状态 |
| POST | `/captures/{request_id}/cancel` | 请求取消，202；返回实际后端状态 |
| GET | `/timing-frequency` | 经现有后端读取 timing PV |
| GET | `/health` | 经现有后端读取健康状态 |
| GET | `/operations?limit=&cursor=&operator_id=&action=&request_id=` | control 安全审计列表 |

采集请求包含：

```json
{
  "request_id": "cb8f5b27-9cf5-48bd-8a2a-3a04fc79fc6d",
  "frequency_hz": 1000,
  "duration_seconds": 60,
  "area_ids": ["INJ1"],
  "device_ids": ["AC_LA_INJ1_BI_SBPM01"],
  "operator_id": "operator-01",
  "approval_id": "8ccca102-89ed-43ac-9e0c-3c74e39a3d1a"
}
```

HTTP 层与后端分别使用各自的批准注册表校验设备、区域、最大时长和设备数。支持频率档位沿用运行器：2、5、10、50、100、500、1000 Hz；服务端还检查实际 timing 频率。HTTP 注册表不能放宽真实后端策略。

`request_id` 是业务幂等身份。同一 ID 和相同采集参数返回已有状态；参数不同返回冲突。审批字段不进入底层采集协议。HTTP 回复丢失时，只用原 request_id 查询；不会生成新 ID 或切换采集路径。后端取消回复保留 `accepted/running`，`cancellation_requested=true` 仅表示已请求取消。

重启中断的 accepted/running 采集由现有 JsonTaskStore 标记为 `failed/INTERRUPTED_BY_RESTART`。后续同 ID 的 start 返回失败状态，runner 不会再次运行。

## Data API 与版本模型

以下路径相对于 `/api/agent/v2/data`。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/preview` | 无审批预览；返回精确目标、版本、源 checksum、依赖和可恢复性 |
| POST | `/update` | 修改 `label`、`comment`、`tags` 注释 |
| POST | `/soft-delete` | 新增逻辑删除版本 |
| POST | `/restore` | 清除删除标记，或恢复到指定历史版本 |
| POST | `/overwrite` | 从另一个授权段的同一设备生成新 HDF5 版本 |
| GET | `/operations?limit=&cursor=&operator_id=&action=&target_id=` | data 安全审计列表 |
| GET | `/{operation_id}` | 查询 data 操作参数、审批引用、状态与结果 |
| GET | `/{target}/versions` | 查询版本列表；target 是 preview 返回的 target_id |

目标始终为 `request_id + capture_sequence + device_id`。target_id 是该规范化身份的 SHA-256。当前只支持单个文件对象的操作，不支持整任务批量修改。

preview 示例：

```json
{
  "action": "soft-delete",
  "target": {
    "request_id": "c4577407-3528-486f-87df-6290da28e00d",
    "capture_sequence": 3,
    "device_id": "AC_LA_INJ1_BI_SBPM01"
  },
  "expected_version": 0
}
```

取得 preview 后，提交对应动作，保留相同参数，并新增 `preview_id` 和 `confirm_target`。`confirm_target` 必须精确等于 preview 的 target_id。两个 POST 都需要幂等 key。preview 不要求审批，只写审计、幂等和短期 preview 绑定，不修改数据、版本或审批占用；其 `approval_id=null`、`operator_id=service:data`。操作员查看 preview 后确认一次，再为完整 mutation body 签发审批。签名绑定 `preview_id`、`confirm_target`、`expected_version` 及其余全部参数。preview 有效期为 900 s。任何参数、目标、版本或源 checksum 变化都会使旧 preview 失效。

update 在 preview 和修改请求中增加 `annotations` 对象。只允许 `label`、`comment`、`tags`，不会修改采样率、采集身份、科学参数或原始文件。restore 可增加 `restore_version`，以新版本恢复旧数据指针和注释。版本号只递增，不倒退。

overwrite 在两个请求中增加 `source`，结构同 target。source 必须是同一设备的可读对象，历史采样率必须相同。输入不接受路径、URL、任意数组或上传文件。writer 只接受生产整数 raw 格式；浮点历史文件的版本覆盖返回 `CAPABILITY_UNAVAILABLE`，避免隐式量化。

现有 Mongo repository 仅有 upsert 和读取，没有删除或历史版本。本次新增 `AgentVersionRepository` 作为兼容的 Agent 元数据层，存于 output root 的 `agent-v2/catalog.json`。没有变更 Mongo 集合、索引、HDF5 schema 或 protocol v2。Agent catalog 写入 `schema_version=1`；启动时将现有无版本 catalog 视为兼容的版本 1 并补写字段，拒绝未知版本且不覆盖该文件。未来版本必须先实现显式迁移。版本 0 指原 catalog 文件；新文件写在 `agent-v2/versions/<target_id>/<version>-<operation_id>/`，使用现有 writer，并保存 manifest 和来源。原始文件从不原地改写。

成功 preview 会绑定 target_id 与精确原始对象身份。在首次 mutation 前，GET versions 根据该绑定重新读取当前原始对象，返回 version 0、当前 SHA-256 和字节数。该 GET 不写 Mongo 或 mutation state，不新增 operation_id、审批占用或版本；未知 target_id 返回 `NOT_FOUND`。对象缺失、不唯一或不可读取时，沿用安全读取错误。已有 overlay history 时返回完整的已记录版本历史。

catalog 将版本指针、操作结果、审批占用与幂等绑定原子提交，并使用 fsync 和单所有者锁。文件先写到新的版本目录，catalog 发布指针后才可见。崩溃可能留下未发布的孤立版本文件，但不会自动重放。管理员应保留这些文件供审计，不能仅凭目录存在推断操作成功。

preview、operation、versions（含各版本项）显式返回 `effect_scope="agent-v2-overlay"`、`source_modified=false`、`source_authoritative=true`。overwrite 另返回 `supersedes_version`，表示被取代的 overlay 版本。软删除是 Agent v2 视图隐藏，overwrite 是 v2 版本目录，都不修改源 Mongo/HDF5。软删除只影响 v2 的可见对象、raw 和后续分析输入。v1、原 Mongo catalog 和既有 GUI 继续使用原始历史视图；它们不是删除权限边界。已有分析附件作为历史证据保留。preview 的依赖列表报告当前 Agent 目录内已记录源对象的任务，不代表外部系统中的全部引用。

本轮没有实现 unified 可见性，也没有提供可误启用的 unified 配置。后续迁移需单独引入 `WAVEFORM_AGENT_V2_VISIBILITY=agent-only|unified`（默认 agent-only），让 v1/GUI 显式读取 overlay，并完成跨视图、回滚和授权测试后再开放。

回滚数据使用新的 preview、审批和 restore 操作。回滚服务源码时也须保留整个 output root、原始文件和版本目录；旧 v1 程序不会解释 v2 overlay。当前没有自动 GC、物理删除、跨目录原子事务或跨副本一致性。

分析任务只在获取和校验 overlay generation、提交状态时持有 catalog 锁。文件和网络读取在锁外完成；读取前记录各目标版本和来源，读取后重新校验 checksum，再在锁内比较 overlay generation。中途修改返回 `VERSION_CONFLICT`，不进入算法或发布结果，不自动重放。原 catalog 没有不可变内容 generation，分析因此需要二次读取校验；通过校验后按已获取的快照计算。

preview 内按精确对象去重读取，target/source 相同只读一次。mutation 对每个不同对象重新读取、核验 checksum 一次，并复用解码记录生成 overwrite；不重复调用 source 下载。不同 target/source 的 overwrite，preview + mutation 最多读取原对象 4 次，另有 1 次新本地版本 hash；相同对象为 2 次源读取加 1 次新版本 hash。不能跨 preview/mutation 直接相信旧 checksum。data writer 使用单独的串行锁，文件读取期间不占用 catalog 锁，状态和审计查询可继续。

## 幂等、审计与错误

所有 POST 都支持幂等 key。同 scope、同 key、同 action 和 body 返回原操作或任务；改变请求返回 HTTP 409、`IDEMPOTENCY_CONFLICT`。同一个 operation_id 不能用于其他 key。采集还受后端 request_id 幂等约束。

副作用发生前先写 durable intent，记录 operation_id、operator_id、approval_id、action、完整参数、指纹和时间。执行后保存真实结果或安全错误。重启后的未完成 intent 标记失败，不自动执行。接收后发生超时时，状态可能需要按 request_id 查询，不能把 HTTP 错误理解为设备没有收到请求。

operations 列表要求对应 scope token。过滤条件按 AND 组合；`action` 精确匹配 `POST /api/agent/v2/...`，`target_id` 使用 preview 的目标 ID，control 的 `request_id` 使用采集请求 ID。列表仅返回 operation_id、action、status、operator_id、approval_id、target_id、created_at、updated_at 和可选 error.code，按创建时间倒序排列，不返回完整 body、签名、token、secret 或路径。preview 记录也可查询。单条 data operation 只接受 data 操作 ID，跨 scope 返回 404。

所有 v2 错误使用：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "Version conflict",
    "operation_id": null,
    "retryable": false
  }
}
```

| HTTP | 错误码 |
| --- | --- |
| 400 | `INVALID_ARGUMENT` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN`、`APPROVAL_REQUIRED`、`APPROVAL_INVALID` |
| 404 | `NOT_FOUND` |
| 409 | `AMBIGUOUS`、`CONFLICT`、`IDEMPOTENCY_CONFLICT`、`VERSION_CONFLICT`、`OPERATION_IN_PROGRESS` |
| 422 | `DATA_UNREADABLE`、`ANALYSIS_INVALID` |
| 429 | `RESOURCE_BUSY`（请求限流） |
| 503 | `CAPABILITY_UNAVAILABLE`、`RESOURCE_BUSY` |
| 504 | `TIMEOUT` |
| 500 | `INTERNAL` |

只有 `RESOURCE_BUSY` 和 `TIMEOUT` 的错误信封标记 retryable。采集仍须先查原 request_id。异步任务执行失败通过 GET 200 的任务状态和 error 返回。响应不包含 traceback、Mongo URI、SSH 凭据、服务器路径、Authorization 或 secret。

## Fixture 与迁移

配置测试专用的三个 token 和审批密钥，再运行：

```bash
export WAVEFORM_AGENT_CONTROL_ENABLED=true
export WAVEFORM_AGENT_DATA_ENABLED=true
python tools/run_waveform_agent_fixture.py --v2 --port 8771
```

fixture 同时提供原 v1 和认证 v2，使用临时 HDF5、真实 Candidate ZMQ、真实对齐与频谱引擎，以及只在内存中等待或取消的采集 runner。fixture 采集不会创建新的真实波形；既有 fixture 数据用于 read/data 验证。退出后临时目录删除，服务不打印 token。

自动验证：

```bash
QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/waveform-mpl \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python tools/run_quality.py
```

accelerator-rag 可以继续使用原 v1。迁移到 v2 时，增加对应 scope token，将日期查询改为 start_date/end_date，使用新 job/artifact URI，并处理新的错误信封和 409 幂等冲突。read 调用不需要人工审批。control 调用须先在独立审批端完成操作员确认；data 使用 DataToken 执行无审批 preview → 查看影响并确认一次 → 签名提交 mutation。旧客户端若仍给 preview 发送审批 header，服务会忽略它们、不占用该 approval；应更新客户端并为 mutation 绑定完整 body。

推荐生产设置 `WAVEFORM_AGENT_V2_ONLY=true`，或将旧 v1 放在独立进程、端口并以代理 ACL 限制访问。默认 `false` 时 v1 仍可启用，无 token 要求；与 v2 同时启用会打印“v1 无认证，必须由网络 ACL/代理限制”。`/health` 在 v2-only 模式仍可用。v1 没有内建认证，本次为兼容旧客户端继续保留。部署时必须把 v1 限制在原可信网络或单独代理 ACL 内，不能把 v2 token 当作整个监听端口的统一防护。

## 剩余边界

本机 HTTP fixture 通过不代表现场 EPICS、SSH、MongoDB、TLS 代理或真实设备验收。当前没有生产部署、压力测试、远端 SFTP fixture 或现场采集测试。快照读取有超时，HTTP 发送使用 socket 超时，不提供整个任务的统一硬 deadline。

物理删除、任意上传覆盖、采集科学字段修改、外部依赖全量枚举和跨服务版本同步均未开放。关闭 control/data 或不满足整数 writer 能力时返回 `CAPABILITY_UNAVAILABLE`，不会伪造成功。

未来独立 EPICS Gateway 至少需要 PV 白名单、类型与数值范围、审批 action_hash、TTL、幂等身份、写前读值、写后确认、超时状态、审计及回滚策略。本仓库没有实现该 Gateway，也没有新增 EPICS 写入接口。
