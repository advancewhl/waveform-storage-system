# Waveform Agent API v2 加固交付

日期：2026-10-22。任务：`C2C: HARDEN_WAVEFORM_AGENT_API_V2`。状态：源码与规定验证完成，尚未部署。

## FIXED

核查确认了原实现的 6 项现状：匿名 v1 与认证 v2 共用监听端口；data preview 要求签名审批；下载开始后异常可能追加 JSON；执行和 POST 的 catalog 锁覆盖文件或网络读取；data 修改只影响 v2 overlay；基线为 v1 12 项、v2 20 项测试。

本轮完成以下修复：

| 范围 | 最终行为 |
| --- | --- |
| 网络认证边界 | `WAVEFORM_AGENT_V2_ONLY` 默认 `false`；`true` 要求启用 v2，拒绝 v1 路径，保留 `/health`。双版本启动明确警告匿名 v1 风险 |
| 单次审批 | preview 只需 DataToken 与幂等 key，不占用审批，不改变对象或版本；仍持久化审计、幂等和 preview 绑定。mutation 要求一次签名确认，完整 body 参与 action_hash |
| 下载错误 | 标记 headers_sent；响应开始后只写安全日志并断开连接，不追加 JSON 或第二个响应；发送前错误保留结构化信封 |
| 锁与一致性 | 文件和网络 I/O 移出 catalog 锁。data writer 使用独立串行锁；任务读取后重新检查源 checksum 和 overlay generation，变化则 VERSION_CONFLICT，不进入算法 |
| overlay 表达 | preview、operation、versions 及其版本项显式声明 effect_scope、source_modified、source_authoritative；overwrite 包含 supersedes_version |
| 审计列表 | data/control 按 scope 提供分页和筛选，只输出安全摘要。失败取消也能按路径中的 request_id 查询，无效 body 不会阻断列表 |
| 限流 | 每个 scope token 默认 60 请求/min，control/data 写操作另限 10 次/min；两个配额分别可设为 0。preview 不占写配额，超限为 429 RESOURCE_BUSY |
| catalog 版本 | 独立 Agent catalog 写入 schema_version=1；兼容无版本旧目录，拒绝未知版本且保留原文件 |
| 密钥轮换 | 先用 current 验签，再试 previous；移除 previous 后拒绝旧密钥。新审批由独立签发端只用 current 签发 |
| 下载去重 | preview 按对象身份去重；mutation 每个对象只重新读取校验一次，overwrite 复用解码结果。不同 target/source 共 4 次源读取，另 1 次本地新版本校验；相同对象为 2 次源读取加 1 次本地校验 |

## API CHANGES

新增两个 GET：

- `/api/agent/v2/data/operations?limit=&cursor=&operator_id=&action=&target_id=`
- `/api/agent/v2/control/operations?limit=&cursor=&operator_id=&action=&request_id=`

列表使用 total/truncated/next_cursor；筛选按 AND 组合。action 精确匹配 `POST /api/agent/v2/...`。仅输出 operation_id、action、status、operator_id、approval_id、target_id、created_at、updated_at 和可选 error.code。data 单条 operation 拒绝其他 scope 的 ID。

DataToken 的 preview 不再要求任何审批 header。mutation 仍要求 Idempotency-Key、X-Operation-Id、X-Approval-Id、X-Operator-Id、X-Approval-Token。签名覆盖 preview_id、confirm_target、expected_version 和其他全部 body 字段。缺失、过期、错误 scope/hash 和 replay 仍拒绝。

新增或补充配置：

- `WAVEFORM_AGENT_V2_ONLY=false|true`，默认 false。
- `WAVEFORM_AGENT_RATE_LIMIT_PER_MINUTE=60`。
- `WAVEFORM_AGENT_WRITE_RATE_LIMIT_PER_MINUTE=10`。
- `WAVEFORM_AGENT_APPROVAL_SECRET_PREVIOUS`，默认空，只在轮换窗口使用。

生产推荐 v2-only 或独立进程、端口与代理 ACL。三个 token 继续隔离，control/data 默认关闭。轮换、迁移、启动和错误契约已同步到 API 手册、OpenAPI、Integration Handoff、交付 README 和 CURRENT_STATUS。

## SECURITY

所有原始对象与 artifact 仍按受信 catalog、精确身份和允许根目录读取。正常下载继续校验 Content-Length 与 X-Content-SHA256。源 checksum 校验没有为去重而省略。

preview、operation、versions 返回 `effect_scope="agent-v2-overlay"`、`source_modified=false`、`source_authoritative=true`。软删除是 v2 视图隐藏；overwrite 发布 v2 新版本目录，原始 Mongo/HDF5 保留。v1 和 GUI 继续读取原始视图。本轮未实现 unified；未来须独立 feature flag、接入 v1/GUI 并单独测试。

限流使用进程内固定 60 s 窗口，按 scope 分开计数，不存储 token 或 Authorization；重启清零。没有多实例统一配额。审批 current/previous 不进入审计。下载失败日志不包含异常详情或路径。

## FILES_CHANGED

本轮相对起始工作树修改 12 个业务文件，完整清单见 [files-changed.json](files-changed.json)，差异见 [business-changes.diff](business-changes.diff)。既有 v1/v2 未提交实现没有被当作本轮新增代码重新统计。

实现文件为 agent_http_api.py、agent_v2_contract.py、agent_v2_http.py、agent_v2_service.py、metadata/agent_version_repository.py 和生产启动入口；另更新 v2 测试和 5 份文档/OpenAPI。agent_v2_storage.py 与 v1 测试文件和任务起始副本字节一致。

未修改 analysis/alignment/retrieval 科学实现、Mongo/HDF5 schema 或 acquisition protocol v2 语义。AOCI 的 11 条语义更新和观察记录单独维护，见 [aoci-changes.diff](aoci-changes.diff) 与 [aoci-results.json](aoci-results.json)。Verify、Check、Guide 均通过；Guide 为 aligned、complete=true、next_action=none。此结果表示治理对齐，不声称完成新的完整系统认知验证。

## TESTS

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| 首次沙箱基线 | socket 被禁止，32 项中 28 项环境错误，不作为通过 | [baseline-sandbox.log](baseline-sandbox.log) |
| 获准 loopback 后基线 | v1 12 项与 v2 20 项，共 32 项通过 | [baseline-loopback.log](baseline-loopback.log) |
| 首轮加固回归 | 42 项中 2 项文档断言失败，随后同步文档/OpenAPI | [focused-before-doc-sync.log](focused-before-doc-sync.log) |
| 加固回归 | v1 12 项与 v2 31 项，共 43 项通过 | [focused.log](focused.log) |
| 完整质量门 | 本轮运行一次，286 项通过；compileall、注册表、源码质量、密钥启发式扫描、diff 与 import 检查通过 | [quality.log](quality.log) |
| 最终审计修复 | 质量门后发现失败取消筛选和无效目标 body 的边界，修复后只补测受影响的 1 项 HTTP 测试，通过 | [audit-final.log](audit-final.log) |

完整质量门之后没有再跑全量测试。最终审计小补丁的验证范围如表所示，未把前一次全量运行描述成对该补丁的再次验证。最终 `git diff --check` 通过。

定向命令：

```bash
QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/waveform-mpl \
  PYTHONPATH=src:tests \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python \
  -m unittest test_waveform_agent_api test_waveform_agent_v2 -v
```

完整命令：

```bash
QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/waveform-mpl \
  /home/whloong/miniconda3/envs/waveform-storage/bin/python tools/run_quality.py
```

最终补测：`python -m unittest test_waveform_agent_v2.AgentV2Tests.test_operation_lists_filter_page_sanitize_and_scope -v`，使用相同解释器、offscreen 环境及 `PYTHONPATH=src:tests`。

## REAL HTTP FIXTURE

真实 loopback HTTP、Candidate ZMQ、HDF5、对齐、legacy/v2 writer、统一频谱和 artifact 链路继续通过。control 使用同一 protocol/executor 和合成 runner；data 使用临时文件副本。响应摘要见 [http-fixture.json](http-fixture.json) 和 [quality-http-fixture.json](quality-http-fixture.json)。既有交付和前轮审查证据保留。

本轮新增断言覆盖 v2-only、preview 无审批、mutation 一次审批、scope 配额、previous 密钥、catalog 版本、overlay 字段、审计分页/过滤、下载中断和读取期间的版本或源变化。慢读测试中两次状态/operation HTTP 查询合计约 0.0022 s，阈值为 0.75 s；这只是本机并发回归，不是性能基准。

中断下载和慢 reader 使用故障注入，仍通过真实 HTTP 验证响应行为。它们不替代实际 HDF5/算法链路的 fixture E2E，也不代表真实远端 SFTP 故障测试。

## COMPATIBILITY

v1 默认仍匿名启用，没有增加 token 要求。V2_ONLY 为 true 时明确关闭 v1，迁移前应先更新客户端或隔离端口。v1 的请求/响应和原始数据视图保持兼容。read POST 不需要人工审批。

旧 preview 客户端若仍发送合法审批 header，服务不占用其 approval；客户端应迁移到无审批 preview，并为完整 mutation body 签发一次确认。原有无版本 Agent catalog 自动补齐版本字段；未知未来版本失败关闭。

保留起始 301 个未跟踪路径以及既有未提交工作。分支仍为 `ui/history-retrieval-compact-v2`，HEAD 仍为 `25e6b73985082bb5e2157b77114949597994301f`。未 reset、clean、commit、push、创建或切换分支。起始和最终 Git 状态分别见 git-initial.txt、git-final.txt；最终范围核对见 [scope-review.json](scope-review.json)。

## REMAINING LIMITATIONS

尚未部署；未连接真实设备，未验证真实远端 SFTP、生产 Mongo、TLS 代理或生产负载。fixture 通过不是现场生产验证。

overlay 仍仅影响 v2。未提供统一 v1/GUI 可见性、物理删除、任意上传覆盖、分布式幂等或自动 GC。浮点历史 raw 的无损版本覆盖仍为 CAPABILITY_UNAVAILABLE。

未实现 EPICS 写入、Agent 编排、审批 UI、操作员登录或 RBAC。没有执行发布二进制身份、完整 manifest、桌面 smoke 或完整发布验收。
