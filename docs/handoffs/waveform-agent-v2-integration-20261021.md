# accelerator-rag Waveform API v2 Integration Handoff

交接日期：2026-10-21；加固更新：2026-10-22。当前交付为源码，现场部署状态为 NO。服务端方案、字段、错误与启动配置见 [API 手册](../agent-api-v2.md)，机器契约见 [OpenAPI](../agent-api-v2.openapi.json)。

## IMPLEMENTED

v2 已实现 read、control、data 三个命名空间。v1 保留原端点和只读语义。对齐、频谱、采集和 HDF5 写入均复用现有实现。没有新增 Agent 编排、审批 UI、操作员登录或 EPICS 写入 API。

## API

read 支持历史区间查询、任务详情、真实段查询、对象检查、设备目录、原始 HDF5 下载、异步对齐及 legacy/v2 导出、频谱任务、任务取消和白名单附件。

control 支持 start/status/cancel、timing-frequency 和 health。请求通过已有 Candidate ZMQ 服务，沿用服务端 RegistryPolicy、PersistentTaskExecutor、JsonTaskStore 和 ProductionCaptureRunner。fixture 使用同一个 executor 和 protocol，runner 为合成后端，不访问设备。

data 支持 preview、update、soft-delete、restore、overwrite、操作查询和版本列表。overwrite 的 source 为另一个授权段中的同一设备，不能提交任意路径、URL、数组或上传文件。

## AUTH / APPROVAL

read/control/data 使用不同 Bearer service token。control/data 默认关闭，生产必须显式启用。写 scope 开启时强制配置 HMAC 审批密钥。可配置 APPROVAL_SECRET_PREVIOUS，在有限轮换窗口内兼容旧签名；新审批只用 current 签发。

read POST（alignment、spectrum、analysis job cancel）不要求人工审批，但需要 read service token 和幂等 key。data preview 无需审批，只需 DataToken 与幂等 key；control POST 和 data mutation 强制签名审批，需要 operation、approval、operator 引用和签名 token。签名绑定完整 body、action、scope 和过期时间。审批 ID 在服务端持久化占用；更改参数后旧 token 失效。data 流程为无审批 preview → 查看影响并确认一次 → 签名 mutation。完整 mutation body（含 preview_id、confirm_target、expected_version）参与签名；preview 不消耗审批。

本服务验证审批引用和签名，不验证自然人身份。accelerator-rag 负责操作员登录、确认及可信签发；共享密钥不能提供给 Agent。

## DATA MODEL

新增 AgentVersionRepository，保存版本目录、操作审计、审批占用、任务状态和幂等绑定。Mongo/HDF5 schema 与 protocol v2 保持不变。Agent catalog 增加 schema_version=1，无版本旧目录兼容补齐，未知版本拒绝启动。

alignment 的 algorithm source hash 对应实际 alignment 模块；spectrum 只返回实际分析 manifest 的 algorithm_version，省略未定义的 algorithm_source_sha256。

版本 0 指原始 catalog 文件。成功 preview 后、首次 mutation 前，versions 通过已绑定的目标身份重新读取当前原始对象，返回 version 0 及当前 checksum。GET 不创建 mutation state、operation_id、审批占用或版本；已有 overlay history 时仍返回完整版本历史。覆盖通过现有整数 raw writer 生成新 HDF5 和 manifest，保留原文件及旧版本。restore 生成递增的新版本，不重写旧版本。

preview、operation、versions 显式返回 effect_scope=agent-v2-overlay、source_modified=false、source_authoritative=true。overwrite 另返回 supersedes_version。软删除影响 v2 可见视图。原 Mongo、v1 和现有 GUI 仍可读取原始历史，这是明确的兼容边界。已有结果附件作为历史证据保留。不能把 v2 软删除当作其他接口的访问控制机制。

## FILES_CHANGED

- 新增 `application/agent_v2_contract.py`：错误、scope 认证、规范化签名审批。
- 新增 `application/agent_v2_http.py`：v2 路由、分页、严格请求体和下载响应。
- 新增 `application/agent_v2_service.py`：只读编排、受控采集和版本操作。
- 新增 `application/agent_v2_storage.py`：有大小、时间和 checksum 限制的文件快照。
- 新增 `metadata/agent_version_repository.py`：持久化版本、审计与幂等目录。
- 更新 `application/agent_http_api.py`：附加 v2 路由，保留 v1。
- 更新两个 Agent 启动工具：生产 v2 装配与可复现 v2 fixture。
- 新增 `tests/test_waveform_agent_v2.py`，为原 v1 fixture 增加可选合成 runner 注入。
- 新增 API 手册、OpenAPI、交接文档与本次交付记录；更新 v1 文档和 CURRENT_STATUS。

路径均相对于 `src/waveform_storage/`，文档、测试和工具路径除外。AOCI 托管资产的收尾结果另见交付记录。

## TESTS

2026-10-22 加固：新增 v2-only、无审批 preview、单次审批、下载中断、慢读并发冲突、overlay 字段、审计列表、限流、目录版本及密钥轮换测试。最新结果与真实 HTTP 证据见[加固报告](../../交付/20261021-waveform-agent-v2/evidence/harden-20261022/report.md)。以下小范围整改与初次交付结果保留为历史记录。

2026-10-22 小范围整改：20 项 v2 定向测试和一次正式质量门的 275 项测试通过。原 version-0 查询 404 已通过真实 HTTP 测试复现。新 fixture 和整改 diff 见[本轮证据](../../交付/20261021-waveform-agent-v2/evidence/review-fix-20261022/report.md)。

实现前完整质量门：255 项通过。最终质量门和新增定向测试的实际结果见[交付记录](../../交付/20261021-waveform-agent-v2/README.md)。

覆盖范围包括 v1 兼容、scope 隔离、缺失/错误签名、过期/scope/hash/replay、幂等冲突、精确 raw 授权、路径穿越、链接、大小、checksum、外部 HDF5 链接、采集重复请求、取消与重启不重放、并发版本冲突、软删除恢复和版本化覆盖。

## REAL HTTP E2E

测试创建真实 loopback HTTP 服务、真实 Candidate ZMQ 服务及 HDF5 fixture。read 链路实际执行对齐、legacy/v2 writer、统一频谱引擎和附件下载。已知 47.6 Hz 信号用于验证频谱链路。

control 经真实协议进入持久 executor，只运行合成 runner；同 request_id 不重复执行。data 在临时文件副本上完成 preview、修改、软删除、恢复、覆盖和回滚，并验证原始文件字节保持不变。测试没有把 mock 返回值当作 E2E 成功证据。

## COMPATIBILITY

旧 accelerator-rag v1 客户端可继续调用原路径。v1 的原有错误信封和同 key 冲突返回 400 的规则保留；v2 使用 message/operation_id/retryable 信封，幂等冲突返回 409。两个版本的 job UUID 与 URI 不混用。

WAVEFORM_AGENT_V2_ONLY 默认 false，v1 仍可启用；true 时 v1 路径返回 404，/health 和认证 v2 可用。共用端口时启动日志明确警告匿名 v1 风险，生产推荐 v2-only 或独立进程和端口。v1 没有新增强制 token，以免旧客户端失效。v2 的 token 不能防护同监听端口上的 v1；部署时必须对 v1 单独施加代理或 ACL 限制。

## SECURITY

新增 data/control operations 列表，按 scope 鉴权，支持分页和 operator/action/target 或 request 筛选，只输出安全摘要。每个 scope 默认 60 请求/min，control/data mutation 附加 10 次/min；超额返回 429 RESOURCE_BUSY。两个配额可分别配置为 0。

文件读取移出 catalog 锁，读取前后校验版本与 checksum。data writer 使用独立锁，查询不等待文件读取。下载开始响应后异常只断开连接，不再追加 JSON。

没有任意路径、URL、shell、Mongo 直写、物理删除或 EPICS 写入接口。下载从已授权 catalog 身份解析，限制根目录、文件大小和时间，并提供 checksum。原始数据及二进制附件不进入 JSON。

本机快照使用 no-follow 目录 FD 逐级读取。远端 raw 快照使用 SFTP、受限存储根和 RejectPolicy；不执行远端 shell，也不使用既有 AutoAddPolicy 回退。分析拒绝外部 HDF5 链接、虚拟数据集和超限数据集。

## REMAINING LIMITATIONS

源码已完成，尚未部署现场。没有构建新前端/采集服务二进制，没有切换 current 或重启现场服务。没有验证现场设备、MongoDB、远端 SSH/SFTP、TLS 代理或生产性能。

`CAPABILITY_UNAVAILABLE` 仍适用于关闭的 control/data scope，以及现有 writer 无法无损覆盖的浮点历史 raw。物理删除、任意上传、采集科学字段修改和跨系统依赖枚举没有开放。v2 overlay 不同步修改原 Mongo/v1/GUI 视图。unified 未实现；未来必须单独引入 feature flag、让 v1/GUI 读取 overlay 并验证。限流为单进程配额，不等于代理的多实例限流。

持久幂等以完整保留同一个 output root 为前提。没有多主机分布式锁、自动 GC 或统一任务硬 deadline。文件先落盘、catalog 后发布，崩溃可能留下未发布文件；重启不自动重放任务或写操作。

没有实现 EPICS 写入。未来独立 Gateway 的最低契约边界已写入 API 手册。
