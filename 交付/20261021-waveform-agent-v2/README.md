# Waveform Agent API v2 源码交付

交付日期：2026-10-21；加固更新：2026-10-22。状态：代码完成，尚未部署现场。

## IMPLEMENTED

read/control/data 已实现，v1 兼容保留。完整核查、接口和限制见 [Integration Handoff](../../docs/handoffs/waveform-agent-v2-integration-20261021.md)。

## API

[接口手册](../../docs/agent-api-v2.md)和 [OpenAPI](../../docs/agent-api-v2.openapi.json)覆盖历史、设备、段、raw、对齐、导出、频谱、任务、附件、采集控制和数据管理。

## AUTH / APPROVAL

三类 token 隔离。read POST 不要求人工审批，但需要 read service token 和幂等 key。control/data 默认关闭。data preview 只需 DataToken 与幂等 key，不要求或消耗审批。control POST 和 data mutation 强制 HMAC 签名审批，操作员在 preview 后只确认一次。签名绑定 action、完整 body、scope、operator、approval 和 TTL。data 修改绑定 preview、target_id、expected_version。审批 UI 和操作员认证由 accelerator-rag 负责。APPROVAL_SECRET_PREVIOUS 支持有期限的旧密钥验签窗口，新审批只用 current。

## DATA MODEL

新增独立 Agent 元数据版本目录；原 Mongo/HDF5 schema 和 protocol v2 不变。原始 HDF5 保留，新版本由现有 writer 生成，并带 manifest 和来源。catalog 写入 schema_version=1，兼容无版本旧目录并拒绝未知版本。preview、operation、versions 明确返回 effect_scope=agent-v2-overlay、source_modified=false、source_authoritative=true；overwrite 包含 supersedes_version。软删除只影响 v2；v1、原 catalog、GUI 和历史 artifact 保留原视图。恢复通过新版本执行。

成功 preview 后、首次 mutation 前，versions 可通过已绑定的身份读取当前原始对象并返回 version 0、checksum 和字节数。GET 不写 Mongo 或 mutation state，不新增 operation_id、审批占用或版本；已有 overlay history 时返回完整历史。

alignment 的算法源码 hash 对应实际 alignment 模块；spectrum 的版本来自实际分析 manifest，省略未定义的算法源码 hash。

## FILES_CHANGED

实现位于 `src/waveform_storage/application/agent_v2_*.py` 和 `metadata/agent_version_repository.py`。更新 Agent HTTP 装配、两个启动工具、v1 fixture、v2 测试与文档。逐项清单见 handoff；AOCI 托管资产单独收尾。

## TESTS

最新加固结果、下载中断与并发证据见[加固报告](evidence/harden-20261022/report.md)。以下结果按原验证日期保留。

实现前，允许 loopback socket 后的质量门通过 255 项。初次沙箱执行因禁止 socket 产生 9 项环境错误，未把该结果当作代码失败或通过。

2026-10-21 原交付质量门：272 项测试全部通过，包含当时新增的 v2 测试 17 项。compileall、注册表校验、源码质量、密钥启发式扫描、diff 检查和 import 检查通过。

- [实现前质量门日志](evidence/quality-baseline.log)
- [最终质量门日志](evidence/quality-final.log)
- [原交付 HTTP fixture（整改前历史证据）](evidence/http-fixture.json)
- [2026-10-22 审查整改证据](evidence/review-fix-20261022/)

原交付 fixture 的 spectrum 算法源码 hash 有误。本轮已移除该字段，新返回以整改证据为准；原日志和 JSON 保留为历史记录。

验证使用 `/home/whloong/miniconda3/envs/waveform-storage/bin/python`，环境为 `QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/waveform-mpl`。命令为 `python tools/run_quality.py`，通过 `WAVEFORM_V2_EVIDENCE_PATH` 保存 fixture 响应。

2026-10-22 小范围整改已完成：20 项 v2 定向测试通过，正式质量门运行一次、275 项通过。具体范围、起始工作树和 Git 状态见[整改报告](evidence/review-fix-20261022/report.md)。

## REAL HTTP E2E

实际运行 loopback HTTP、Candidate ZMQ、HDF5、alignment、legacy/v2 writer、AnalysisJobs 和统一频谱引擎。频谱验证已知 47.6 Hz 信号；下载验证字节数与 checksum。

control 使用同一持久任务执行器和合成 runner，验证 start/status/cancel、重复 request_id 只执行一次及重启中断后不重放。data 使用临时副本，验证 preview、注释更新、软删除、恢复、新文件版本覆盖、版本冲突与恢复版本 0 的原始字节。fixture 不是现场生产验证。

## COMPATIBILITY

v1 端点和响应保留，既有测试通过。v2 的日期区间、分页、错误信封、409 幂等冲突和 scope 认证须由客户端显式迁移。WAVEFORM_AGENT_V2_ONLY 默认 false，保留匿名 v1；true 时关闭 v1 路由，/health 与认证 v2 正常。生产推荐 v2-only 或独立端口；v1/v2 共用端口时启动日志警告必须以 ACL/代理限制 v1。

## SECURITY

新增对应 scope 的 operations 审计列表，支持安全字段、分页与筛选。每个 scope 默认 60 请求/min，control/data 写操作附加 10 次/min；超额为 429 RESOURCE_BUSY。preview 不占写配额。

下载开始响应后失败只断开连接，不追加 JSON。catalog 锁只覆盖状态读取和提交；文件读取在锁外，读取后验证版本与 checksum。preview/mutation 复用单次读取和解码记录，overwrite 的不同 target/source 最多读取源对象 4 次，另校验本地新版本 1 次。

无任意路径、URL、shell、Mongo 直写、物理删除和 EPICS 写入接口。raw 绑定完整对象身份；本机 no-follow 根目录读取、远端严格 SSH 主机信任、大小/时间限制、checksum 和 HDF5 外部链接拒绝均已实现。远端 SFTP 未做现场验证。签名和 token 不进入审计或响应。

## REMAINING LIMITATIONS

尚未部署现场，未构建新二进制，未切换 current，未核验现场运行版本。未测试真实设备、远端 SFTP、生产 Mongo、TLS 代理或生产负载。未实现 EPICS 写入。

`CAPABILITY_UNAVAILABLE` 用于关闭的写 scope 和浮点历史 raw 覆盖。物理删除、任意上传、科学元数据修改、跨系统依赖枚举及多主机一致性未开放。v2 软删除不修改 v1/GUI。本轮未实现 unified 视图；未来需独立 feature flag 与跨视图测试。限流是进程内配额，重启清零。没有自动 GC 或统一任务硬 deadline；重启不重放。

本次只计算接口要求的源、下载和算法 provenance 摘要，没有执行完整 manifest、发布二进制身份校验、真实桌面 smoke 或完整发布验收。

## 版本与回滚

HTTP API：v1 保留，v2 并行新增。下游采集协议：既有 protocol v2，未修改。GUI 和现场服务二进制：未构建或部署，现场版本未重新核验。

源码回滚时保留整个 output root。v1 可继续访问原 catalog；v2 的恢复必须保留原始文件、Agent catalog、任务状态、版本目录和分析附件。不要用删除 catalog 的方式解除幂等或审批占用。
