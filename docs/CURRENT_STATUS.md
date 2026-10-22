# 波形存储当前状态（2026-10-22）

## 2026-10-22 Agent API v2 Git 源码归档

本轮审阅并归档自 v1 冻结提交 `25e6b73` 之后的 Agent API v2 实现、审查整改和加固变更。统一 README 与文档索引中的 v1/v2 接入入口，保留各轮交付报告与验证证据。下方“未 commit、push”的表述记录各轮交付当时状态，本轮 Git 归档另行完成；提交 ID 与推送结果以 Git 历史及本次任务回复为准。

本轮仅做源码审阅、文档整理、提交范围和敏感内容检查，不修改 API 实现，也不重复运行全量测试。复用此前 43 项 v1/v2 定向回归、286 项完整质量门，以及最后审计边界修复的 1 项定向通过记录。验证日期和修复顺序见[加固报告](../交付/20261021-waveform-agent-v2/evidence/harden-20261022/report.md)。

本次归档不是正式 release，没有构建、切换或部署现场服务。真实远端 SFTP、生产 Mongo、TLS 和设备仍未验证；v2 overlay 不改变 v1/GUI，未实现 EPICS 写入。本地数据、构建和界面验证产物、备份及无关历史交付文件保留在原位置，不纳入本次源码提交。

## 2026-10-22 Agent API v2 加固

已完成本轮源码加固，尚未部署。新增 `WAVEFORM_AGENT_V2_ONLY`，默认保留 v1；启用后 v1 返回 404，认证 v2 与 `/health` 可用。同端口启用 v1/v2 时明确警告匿名 v1 的网络边界。

data preview 改为无审批预览，只保留 DataToken、幂等和审计。mutation 仍绑定完整 body、preview_id、confirm_target 和 expected_version，操作员确认一次。新增 previous 审批密钥轮换、scope 请求和写操作限流、catalog schema_version=1，以及带安全投影和筛选分页的 data/control operations 列表。

下载开始响应后失败只关闭连接。文件读取移出 catalog 锁，读取后校验 checksum 与 overlay 版本；中途变更使任务以 VERSION_CONFLICT 失败。preview/mutation 复用读取记录，减少 overwrite 重复下载。preview、operation、versions 明确声明只影响 Agent v2 overlay，source_modified=false；v1/GUI 仍使用原视图，未实现 unified。

实现前 v1/v2 基线 32 项通过；加固定向回归 43 项通过。完整质量门运行一次，286 项通过。最终审阅补齐失败取消与无效 body 的审计边界，并单独通过对应 HTTP 测试。详情见[加固报告](../交付/20261021-waveform-agent-v2/evidence/harden-20261022/report.md)。此前“所有 control/data POST 审批”的记录为历史状态，当前 preview 规则以本节和 v2 手册为准。

本轮保留既有未提交文件，未 commit、push 或部署。科学实现、Mongo/HDF5 schema、protocol v2 均未修改。真实 loopback fixture 通过，未验证远端 SFTP、生产 Mongo、TLS 或设备；未实现 EPICS 写入。

## 2026-10-22 Agent API v2 小范围审查整改

本轮修复 3 项已确认问题。spectrum 省略误用的 alignment 算法源码 hash，算法版本仍来自真实分析 manifest。审批文档明确区分 read POST 与 control/data POST。成功 preview 后、首次 mutation 前，versions 返回当前原始对象的 version 0、checksum 和字节数；GET 不创建 mutation state、operation_id、审批占用或版本。

先用真实 HTTP fixture 确认原行为为 404，再完成修复。v2 定向测试 20 项通过；本轮正式质量门仅运行一次，275 项全部通过。科学算法、审批实现、HTTP 路由、metadata repository 和 protocol v2 未修改，v1 回归通过。证据见[审查整改记录](../交付/20261021-waveform-agent-v2/evidence/review-fix-20261022/report.md)。本轮未 commit、push 或现场部署；fixture 不代表生产验证。

## 2026-10-21 Agent HTTP API v2 源码交付

v2 已实现 read/control/data 三个命名空间。v1 保留原端点和只读语义。v2 使用独立 service token；control/data 默认关闭，所有 control/data POST 强制签名审批；read POST 不要求人工审批，但需要 read service token 和幂等 key。data 修改要求 preview、显式目标确认和预期版本。数据版本目录保留原始 HDF5，通过现有 writer 生成新版本与 manifest；软删除只影响 v2，原 Mongo/v1/GUI 视图保持原状。

实现前质量门 255 项通过；最终 `tools/run_quality.py` 运行 272 项测试并通过，其中新增 v2 测试 17 项。真实 loopback HTTP、Candidate ZMQ 和 HDF5 fixture 覆盖 read、对齐、legacy/v2 导出、47.6 Hz 频谱、附件、合成采集 start/status/cancel、重启不重放，以及临时副本上的软删除、恢复和版本化覆盖。证据见[本次交付记录](../交付/20261021-waveform-agent-v2/README.md)。

本轮为源码交付，尚未部署现场。没有重建二进制、切换 current、重启现场服务或连接真实设备。没有实现 EPICS 写入，也没有修改 analysis/alignment/retrieval 科学实现、Mongo/HDF5 schema 或 protocol v2。关闭的写 scope 和浮点历史 raw 的版本覆盖仍返回 `CAPABILITY_UNAVAILABLE`；物理删除和任意上传未开放。接口、环境变量、签名规则和迁移说明见 [v2 手册](agent-api-v2.md)及 [Integration Handoff](handoffs/waveform-agent-v2-integration-20261021.md)。

## 2026-10-20 Agent HTTP API v1 冻结整改

源码已完成 v1 整改。正式路由为 `/api/agent/v1/waveform`，新增 `/health`，POST 先持久化 Agent job 再后台执行 catalog、retrieval、alignment 和分析；UUID `idempotency_key` 与任务状态一同持久化，支持相同 output root 下的重启恢复。result 明确返回历史采样率来源、captured_at、storage_date_local 和实际 algorithm_version。派生产物 URI 同步使用 v1，白名单及 traversal 防护已验证。

本轮定向测试 35 项通过；完整 `python tools/run_quality.py` 本次运行 255 项测试并通过。完整 HTTP fixture 验证真实段 3、47.6 Hz 谱峰、0.05 Hz 分辨率、4/4 分块、幂等 retry/conflict 和三个下载。真实命令及日志见[本轮交付记录](../交付/20261020-waveform-agent-v1/README.md)，客户端接口见 [Integration Handoff](handoffs/waveform-agent-v1-integration-20261020.md)。此前 247 项是上一轮结果，不作为本轮验证依据。

未修改 Mongo/HDF5 schema、采集 protocol、retrieval/alignment 算法、Welch 或科学参数。当前工作树源码尚未部署，未切换已有前端或服务端版本。服务没有内建 auth、TLS、tenant isolation 或自动 artifact GC，跨主机生产接入须经批准的安全网关/隧道并配置 TLS、认证和 ACL。后台操作没有新增统一执行 deadline。accelerator-rag、Qwen 和 RAG 均未集成。

## 2026-10-19 Agent-facing Waveform API

已完成源码实现。`WaveformAgentService` 复用 Candidate 后端、严格对齐和现有 AnalysisJobs，提供历史 run/段查询、单段单设备 X/Y 频谱分析、任务轮询和白名单产物下载。采样率来自历史 `CaptureRunSummary.frequency_hz`；缺失或与 HDF5 metadata 冲突时拒绝分析。不接受存储路径，不提供采集和原始数据修改接口。

定向测试 27 项通过；`python tools/run_quality.py` 本次运行 247 项测试并通过完整离线质量门。真实 catalog → 跳号段 3 → 可用设备 → alignment → ChannelResult 回归得到 47.6 Hz 谱峰。证据、运行命令和 accelerator-rag Integration Handoff 见[本轮交付记录](../交付/20261019-waveform-agent-api/README.md)及[接口文档](waveform-agent-api.md)。

本次没有部署现场服务、切换已有版本或构建桌面二进制，现场运行状态未核验。HTTP 启动器固定绑定 loopback，目前没有内建认证；accelerator-rag/Qwen 集成尚未实现。未执行 SHA-256、完整 manifest、二进制身份、真实桌面 smoke 或完整发布验收。

## 2026-10-19 Git 源码 checkpoint

用户已确认当前历史页界面。本次只做源码审阅、文档更新和 Git 存档，不再调整历史页布局、字体、尺寸、比例、样式或交互逻辑。当前源码包含 2026-10-17 r6 之后的源码调整。

当前历史检索区固定为 152 px，检索区与绘图区按 1:3 比例布局，历史控件字号为 14 px。正常严格对齐成功时，诊断提示栏保持隐藏；出现数据编号间隔异常或其他告警时，诊断区域仍会显示。相关文案使用“相邻数据编号的间隔”。

本次目标是保存当前 Git 源码和文档状态，不是创建新的正式 release。最新有明确本机 release 记录的仍是 `20261017-history-wording-r6-local`。`outputs/operator-local/current` 当前指向该 release；Git 源码比该 release 更新，本次没有重建 PyInstaller 二进制，也不声称该 release 包含当前源码。

本次没有部署现场 operator 或 acquisition server，也没有重新核验现场运行状态。验证范围为源码与文档审阅及 `git diff --check`；没有运行测试、构建或完整发布验收。

## 历史发布与验收记录

以下 2026-10-17 及更早的内容保留各次发布和验收时的事实。它们不代表本次检查后的现场运行状态。

### 2026-10-17 数据编号间隔提示，本机审阅版

`20261017-history-wording-r6-local` 已激活到本机 `outputs/operator-local/current`。历史页、异常弹窗和诊断详情中的“步进”改为“相邻数据编号的间隔”，例如“相邻编号相差 28～76”。弹窗明确说明这是 bunchID 差值，不是时间间隔，并在可用时显示多数设备的常见间隔。本轮只改显示文字，诊断与对齐规则保持原有行为；字体、紧凑布局和按钮样式沿用 r5。

原始编号诊断和异常确认共 5 项定向测试通过。离线示例检查覆盖 1280×800、2485×1376 两种尺寸、两种绘图模式及异常弹窗和详情表。PyInstaller 构建、打包程序离线启动与 current 路径确认通过。真实桌面效果等待用户关闭旧窗口后重新打开审阅。

本轮未执行 SHA-256、完整 manifest、二进制身份校验、真实桌面 smoke、完整发布验收或全量测试。服务端和现场前端未部署，现场运行状态未重新核验。回滚目标为已保留的 `20261017-history-compact-r5-local`。详情见[本轮交付记录](../交付/20261017-history-wording-r6-local/README.md)。

### 2026-10-17 历史页恢复字号与紧凑布局，本机审阅版

`20261017-history-compact-r5-local` 已激活到本机 `outputs/operator-local/current`。历史页控件与异常弹窗恢复为 14 px 字体，取消随窗口宽度放大字体的规则。检索区域固定为 152 px 高，输入框和按钮恢复紧凑尺寸，按钮增加轻微渐变、底边层次、悬停及按下反馈。原始步进异常确认弹窗继续保留。

历史布局和异常确认两项定向测试通过。离线示例界面检查覆盖 1280×800、2485×1376 两种尺寸和两种绘图模式，控件边界及尺寸检查通过。PyInstaller 构建、打包程序的离线历史页启动与 current 路径确认通过。真实桌面效果等待用户关闭旧窗口后重新打开审阅。

本轮未执行 SHA-256、完整 manifest、二进制身份校验、真实桌面 smoke、完整发布验收或全量测试。服务端和现场前端未部署，现场运行状态未重新核验。回滚目标为已保留的 `20261017-history-display-r4-local`。详情见[本轮交付记录](../交付/20261017-history-compact-r5-local/README.md)。

### 2026-10-17 外接屏历史页与异常确认弹窗，本机审阅版

`20261017-history-display-r4-local` 已激活到本机 `outputs/operator-local/current`。历史页原始步进异常改为立即弹出确认窗口，默认取消，只有明确确认后才执行严格对齐；任务或数据段变化后，旧确认失效。

窗口宽度达到 1920 px 时，历史控件使用 24 px 字体和 64 px 最小内容高度；较窄窗口使用 20 px 和 52 px。日期、段号、BPM 选择、对齐及定位控件同步加宽，诊断文字和弹窗操作按钮同步放大。窗口宽度不超过 2400 px 时，绘图工具栏采用两行布局。

定向测试 6 项通过，离线界面检查包含用户截图的 2485×1376 尺寸和异常弹窗。PyInstaller 构建、打包程序的离线历史页启动与 current 路径确认通过。旧窗口保留，等待用户关闭后重新打开桌面“波形存储”审阅。回滚目标为已保留的 `20261017-history-controls-r3-local`。

本轮未执行 SHA-256、完整 manifest、二进制身份校验、真实桌面 smoke、完整发布验收或全量测试。服务端和现场前端未修改或部署，现场运行状态未重新核验。详情见[本轮交付记录](../交付/20261017-history-display-r4-local/README.md)。

### 2026-10-17 历史页按钮和字体放大，本机审阅版

`20261017-history-controls-r3-local` 已于 12:45（北京时间）激活到本机 `outputs/operator-local/current`。历史页按钮和输入控件的字体从 14 px 调整为 16 px，最小内容高度从 36 px 调整为 44 px。日期、段号、BPM 选择、对齐和定位等控件同步加宽。绘图工具栏在窗口宽度不超过 1920 px 时使用两行布局，更宽窗口保持一行。

历史页布局定向测试 1 项通过。离线示例数据检查覆盖 1120、1280、1600、1920、1921 和 2262 px 窗口宽度及两种绘图模式，控件尺寸检查通过。PyInstaller 前端构建和打包程序的离线历史页启动通过，`current` 解析路径已确认。重新打开桌面“波形存储”可审阅；本轮未启动真实桌面窗口，真实桌面效果等待用户确认。

本轮未执行 SHA-256、完整 manifest、二进制身份校验、真实桌面 smoke、完整发布验收或全量测试。服务端、现场前端、systemd、MongoDB 和 dashboard 本轮未修改或部署。协议沿用既有版本，现场服务端版本与运行状态未重新核验。回滚目标为已保留的 `20261017-history-ui-layout-r2-local`。详情见[本轮交付记录](../交付/20261017-history-controls-r3-local/README.md)。

### 2026-10-17 历史检索布局优化，本机前端发布

`20261017-history-ui-layout-r2-local` 已激活到本机 `outputs/operator-local/current`。历史页首行的检索时间下拉框按内容宽度显示，不再占满整行；本轮只调整前端布局，没有修改服务端协议或数据格式。

聚焦 GUI 测试 11 项通过，完整质量门通过 242 项测试。隔离 operator 构建、`MANIFEST.sha256` 校验和打包二进制历史页启动检查通过。源码状态为 `working-tree`，`source_revision` 为 `368a9aa48a3e042307a23f86fceec752a4665598`，源码清单 SHA-256 为 `49b74541cb42dabcbd4f66bd83a71e57f886483cd53aef1360aa9f74a26b30f6`。

本机真实桌面 smoke 从 `/home/whloong/桌面/波形存储.desktop` 经正式 launcher 启动，于 2026-10-17 通过。报告确认历史任务加载、数据段切换、BPM 选择、原始步进预检查、对齐、原始数据下载和 V2 导出均完成，`capture_requests=0`。验收任务为 `22833275-5e77-41c5-b703-f306accde164`，`capture_sequence=0`；页面显示 5 段和 25 个可用 BPM，smoke 选择 1 个 BPM 并对齐 20,000 点。由于只选择 1 台设备，预检查要求确认步进参考不明确后继续；此结果不代表多设备步进异常验收。

本机 `current` 已从 `20261016-history-quality-r1` 切换至 `20261017-history-ui-layout-r2-local`。二进制 SHA-256 为 `85301cacfd1793e93edaabda441cd69535c5aa559c8cc9d860593bbdb90569e5`；桌面文件和 launcher SHA-256 分别为 `a7e9f4e010216037d43daa0f6cbee711a6d64a5dcf21121f0832f86b240a1888` 和 `5870593351bfa8c05dec4557fa1f6a9012bc38c4d0b0c261e6fc6b93a18c8174`。真实桌面验收进程 `/proc/4001920/exe` 指向本次二进制，哈希与 `BUILD-INFO` 一致。

回滚版本为 `outputs/operator-local/releases/20261016-history-quality-r1/waveform-operator/`，其二进制 SHA-256 为 `c88b8213e97eff8d601b0154ed31f3432541bf87a2066d669058ae121b7c4beb`。本任务未修改或部署现场 operator、acquisition server、systemd、MongoDB 或 dashboard；服务端候选包只构建，现场版本未重新核验。构建、版本对和桌面验收材料见[本次交付记录](../交付/20261017-history-ui-layout-r2-local/README.md)。

### 2026-10-16 历史检索与绘图质量改进

`20261016-history-quality-r1` 已完成源码实现、独立审查、完整质量门和 operator-only 构建。历史检索新增原始步进预检查，并复用已读取记录执行严格对齐。界面分开显示原始设备步进、共同点保留率、分析器推算跨度和频谱质量门。步进异常时，用户必须明确确认后才能继续；系统保留所有设备，不自动剔除或缩放数据。

真实数据核验覆盖任务 `22833275-5e77-41c5-b703-f306accde164` 的 5 个数据段。每段包含 25 台可读设备，每台有 20,000 个原始点。目标设备 `AC_LA_L1_BI_COLDBPM05` 的相邻 bunchID 差为 28～76，其他 24 台观测步进为 2,000。5 段严格共同点数为 422、621、455、375 和 288。任务记录采样率为 500 Hz；分析器根据共同 ID 推算的跨度为 0.574～1.240 s，均未达到 5 s 频谱准入时长。该推算不把 bunchID 当作时钟，也不把点数除以采样率当作连续时长。统计依据和逐段报告见[交付记录](../交付/20261016-history-quality-r1/README.md)。

原始步进、检索服务、Candidate backend、历史 GUI、部分成功段集成及 smoke 回归均通过。完整 `tools/run_quality.py` 质量门通过 241 项测试。Qt offscreen 验收覆盖正常、预检异常、对齐后异常、共同交集为空四种状态，以及 1280×800 和 1600×1000 两种尺寸；另验证单设备详情不会覆盖严格交集点数。绘图最多显示 6 个均匀分布的两行设备标签，曲线仍绘制全部数据点。operator-only 隔离构建、8,368 项 manifest 校验和打包二进制历史页面启动检查均通过。构建及截图证据见[交付记录](../交付/20261016-history-quality-r1/README.md)。

2026-10-16 20:22，本机 `outputs/operator-local/current` 已从 r6 原子切换到 r1。锁内重验确认 previous 路径仍为 r6，并校验了 r1 manifest、二进制、桌面文件和 launcher。20:22:51 的真实桌面历史 smoke 通过，实际 executable path、二进制哈希及查询/导出身份均与 r1 一致。受控 smoke 保持采集按钮禁用，测试 backend 禁止 `start_capture`；harness 报告 `capture_requests=0`。现场 operator、服务端、systemd、MongoDB 和 dashboard 均未由本任务修改或部署。本轮通过正式 launcher 查询本地 endpoint 的历史数据，但未重新核验现场部署版本、systemd 进程和 MongoDB/dashboard 部署状态。构建记录中的 `current_activated=false`、`deployed=false` 是构建时快照，不表示后续的本机激活状态。

Production desktop frontend releases apply to both desktop-user-a and desktop-user-b user environments on AC-GT-CN-S2-OPI21. The shared frontend release is incomplete until both accounts are validated.

真实桌面 smoke 只选择 `AC_LA_INJ1_BI_SBPM01`，预检因只有 1 台设备而报告多数步进参考无法确定；该 smoke 不证明 25 台设备的异常步进。25 台设备的 bunchID 统计和严格交集使用真实 HDF5 证据与 ID fixture 核验；r5 GUI 截图使用真实 bunchID 和示例 X/Y。按用户要求，本轮没有执行 25 台真实 X/Y 的联合 GUI 补测。

桌面 smoke 进程 PID 为 `3120745`，运行时二进制 SHA-256 为 `c88b8213e97eff8d601b0154ed31f3432541bf87a2066d669058ae121b7c4beb`。验收查询任务 `22833275-5e77-41c5-b703-f306accde164` 的段 0，显示 500 Hz、5 段和 25 个可用 BPM；所选设备为 `AC_LA_INJ1_BI_SBPM01`，单设备严格对齐 20,000 点，完成原始 HDF5 下载与 V2 导出。导出身份与所选任务和段一致。进程已退出；桌面文件 SHA-256、原始和导出文件 SHA-256 见交付记录 `evidence/gui-history.json`。

#### AOCI 收尾状态（2026-10-17）

发布启动时，AOCI 阻止了 scope acknowledge，原因是事务 `scope-d1b9042675acd186cdc024bc.json` 等待恢复。用户确认已审阅并授权记录后，按机器返回的事务身份 `d1b9042675acd186cdc024bc` 恢复，回执为 `status=applied`、`recovery_available=false`，7 个待审 observe 路径已清零。Safe Inventory 的 `requires_human_review=13` 是人工可见项计数，用户已确认审阅；范围复核事务回执见本次交付证据。此前完整交付 10/10 块，严格 Attestation 为 partial 7/10；未重试语义答案。


### 更早的现场与服务端记录

2026-10-09 现场桌面机公共 launcher 已激活 r6。desktop-user-a 的桌面入口、GUI、二进制哈希、SSH identity、隧道、ZMQ health 和只读历史查询通过；用户现场确认 desktop-user-a、desktop-user-b 两个账号均可使用 r6，并确认真实 HDF5 频谱功能验收通过。desktop-user-a 的技术验证有机器记录；desktop-user-b 使用情况和 HDF5 功能验收属于用户现场确认，没有对应的机器端输出。本次部署没有发送采集请求，采集服务器未修改。详见[现场桌面机 r6 发布记录](../交付/20261009-spectrum-ui-r6-desktop/README.md)。

2026-10-09 已构建并激活本机频谱 UI operator `20261009-spectrum-ui-r6`。`outputs/operator-local/current` 已切换至 r6，正式桌面 launcher 解析到 r6 二进制；offscreen 打包验收和完整性清单校验通过。

峰值频率按实际 `resolution_hz` 决定显示小数位，表格、图注和 `report.md` 共用同一格式化规则，原始峰值及 JSON/CSV 数值不变。高频模板使用 20 秒整体 Welch 窗口和 5 秒重复性分块；实际窗口与分辨率只显示在默认折叠的高级参数区域。

频谱 focused 35 项、历史 focused 6 项和全量质量门 225 项测试通过。Codex 未启动真实桌面 GUI；本机 r6 等待用户手动验证。激活前 `current` 指向 r5，保留已人工验证的 r3 作为回滚目标。

本机 r6 构建阶段没有更新现场 server、systemd、MongoDB 或 dashboard；现场桌面机后来单独部署 r6，详情见[现场桌面机 r6 发布记录](../交付/20261009-spectrum-ui-r6-desktop/README.md)。本次源码保存与远端同步记录见 Git 提交历史。交付记录见[本机频谱 UI r6](../交付/20261009-spectrum-ui-r6/README.md)。

按 2026-10-08 发布记录，现场 acquisition server 使用 `20261008-history-release-r4`。当日的本机前端、server、真实桌面入口、systemd 实际进程和历史数据处理链路已按该版本对完成验证。本次未检查或修改现场 server。

2026-10-08 曾构建本机频谱 UI operator `20261008-spectrum-ui-r1`。源码清单、二进制校验和、offscreen 页面启动及本地 HDF5 分析均通过，但真实桌面入口未能发现对应进程或窗口，未通过桌面验收。随后已将本机 `current` 原子回滚至 `20261008-history-release-r4`；r1 release 保留但未激活。现场 acquisition server、systemd、现场 operator 均未修改。验收记录见[频谱 UI r1 回滚记录](../交付/20261008-spectrum-ui-r1/README.md)。

2026-10-09 已构建并激活本机频谱 UI operator `20261009-spectrum-ui-r2`。`outputs/operator-local/current` 已指向 r2；桌面入口与 launcher 解析路径已核实，Codex 未尝试打开 GUI。当前等待用户从桌面“波形存储”图标手动验证。现场 acquisition server、systemd、MongoDB、dashboard 和现场 operator 均未修改。构建与激活记录见[频谱 UI r2 交付记录](../交付/20261009-spectrum-ui-r2/README.md)。

2026-10-09 本机 operator 已更新为 `20261009-spectrum-ui-r3`，`current` 已原子切换至 r3。结果表默认隐藏“命中块”列，显示选项位于折叠的高级分析参数区域。历史频谱入口从当前任务的 `CaptureRunSummary.frequency_hz` 读取采样率；缺失时仍需填写，切换任务或数据段时清空旧值。完整质量门 216 项测试、release 清单校验和二进制 offscreen 页面检查均通过。同日，用户确认 r3 已完成人工桌面验证。现场 server、systemd、MongoDB、dashboard 和现场 operator 本轮均未修改。记录见[频谱 UI r3 交付记录](../交付/20261009-spectrum-ui-r3/README.md)。

同日短暂构建并激活 r4。打包验收发现，清理历史 metadata 时可能影响独立文件分析结果。r4 未供用户手动验证，随后由修正后的 r5 替代。r4 release 保留，但不作为回滚目标。

2026-10-09 本机 r6 已激活，仍等待用户对本机开发环境进行人工桌面验收。本段状态仅指本机开发环境；现场桌面机验收状态见下表和 r6 现场发布记录。

## 版本与部署状态

| 组件 | 最近记录的 release | current 或部署位置 | 状态 | 验证日期 |
| --- | --- | --- | --- | --- |
| 当前 Git 源码 | Agent API v2 加固与源码归档（无二进制 release） | 当前分支 | 43 项定向、286 项质量门及最终审计边界补测通过；本轮仅文档与 Git 归档，尚未部署 | 2026-10-22 |
| 本机最近已记录 release | `20261017-history-wording-r6-local` | `outputs/operator-local/current → releases/20261017-history-wording-r6-local/waveform-operator` | 本机激活入口仍指向 r6；本次未重建二进制，当前源码比该 release 更新 | 2026-10-19（仅检查链接） |
| 现场 operator（desktop-user-a、desktop-user-b） | `waveform-operator-r6-20261009` | `/home/site-user/work/StreamDataPro_new/waveform-operator-r6-20261009` | 2026-10-09 历史记录为双账号验收通过；本轮未连接复核，未由本任务部署 | 2026-10-09（历史记录） |
| 现场 acquisition server | `20261008-history-release-r4` | `/home/storage-user/waveform-storage-v2/current → releases/20261008-history-release-r4` | 2026-10-08 历史记录为 packaged/current 和 systemd active；本轮未连接复核 | 2026-10-08（历史记录） |
| 日志 dashboard | `20261008-history-release-r4` | `/home/storage-user/waveform-storage-v2/dashboard/tools/` | 2026-10-08 历史记录中的 HTTP/Mongo 验收通过；本轮未连接复核 | 2026-10-08（历史记录） |
| MongoDB | `mongo:8.0.29` | 现有 `waveform-mongodb` 容器 | 2026-10-08 历史记录为运行且查询正常；本轮未连接复核 | 2026-10-08（历史记录） |

旧窗口继续使用启动时加载的版本。激活前未发现本机 operator 进程。desktop-user-a 从原桌面入口启动 r6 并保持运行、以及用户确认 desktop-user-b 可使用共享前端，均属于 2026-10-09 的历史记录；本轮没有连接现场账号复核这些情况。

## 当前入口

| 工作 | 入口 |
| --- | --- |
| 本机正式桌面前端 | `/home/whloong/桌面/波形存储.desktop → tools/launch_operator_desktop.sh → current` |
| 前端源码入口 | `apps/operator_gui/main.py` |
| 采集服务 | `waveform-acquisition-v2.service` |
| 服务 ExecStart | `/home/storage-user/waveform-storage-v2/current/waveform-acquisition-v2/waveform-acquisition-v2` |
| 日志页 | `http://192.0.2.2:8765/` |
| 只读元数据查询 | `tools/query_waveform_mongodb.py` |
| 正式交付 | [20261008-history-release-r4](../交付/20261008-history-release-r4/README.md) |
| 固定发布规范 | [RELEASE_CHECKLIST](RELEASE_CHECKLIST.md) |

## 版本对与验收证据

2026-10-08 发布的 r4 版本对使用 `v2 + history-run-segment extension`。该 operator 需要同时支持下列命令的 server。前后端必须按版本对部署并验证，不能只更新源码或其中一端。

- `list_capture_runs`
- `list_capture_segments`
- `find_waveform_files_by_segment`

两端 BUILD-INFO 包含源码 revision、工作目录状态、源码清单哈希、二进制 SHA-256、Python 和关键依赖版本、协议能力。完整版本对见[release-pair.json](../交付/20261008-history-release-r4/release-pair.json)。

2026-10-06 的真实协议查询返回 3 个有数据任务、87 个数据段和 1740 个精确匹配文件。旧 list_capture_times 接口仍工作。日志页列出该日创建的全部 5 个任务，其中 2 个没有波形文件；这与历史页的 3 个可用任务口径不同。

真实桌面验收加载“1000 Hz｜10 分钟｜21 段”的任务，完成切段、BPM 选择、20000 点对齐、原始下载和 V2 导出。实际使用完整 request_id 与真实 capture_sequence，查询和导出身份一致。验收未发送采集请求。

现场所选任务没有部分成功段。整链路 integration fixture 验证了请求 2 台、实际 1 台、sequence 0/3 跳号，并经真实 ZMQ、正式 backend 和 GUI 完成对齐、下载、导出。

服务 MainPID 为 1129649，实际进程路径与 r4 current 一致。SSH 主进程 PID 为 145015，监听本地 127.0.0.1:5001 并转发至远端 127.0.0.1:5001。启动日志确认 47 台设备全部启用、MongoDB connected，未发现启动错误。PID 是本次验收快照，重启后会变化。

完整证据位于交付目录的 [evidence/](../交付/20261008-history-release-r4/evidence/)。构建前质量门通过 205 项测试；补齐部分成功段整链路测试后，最终质量门通过 206 项测试。

## 回滚版本

- 当前本机审阅版 r6 的回滚目标：`outputs/operator-local/releases/20261017-history-compact-r5-local/waveform-operator/`。本轮未执行校验和检查；r5 与 r6 均保留。
- 上一轮本机审阅版 r5 的回滚目标：`outputs/operator-local/releases/20261017-history-display-r4-local/waveform-operator/`。本轮未执行校验和检查；r4 与 r5 均保留。
- 上一轮本机审阅版 r4 的回滚目标：`outputs/operator-local/releases/20261017-history-controls-r3-local/waveform-operator/`。本轮未执行校验和检查；r3 与 r4 均保留。
- 上一轮本机审阅版 r3 的回滚目标：`outputs/operator-local/releases/20261017-history-ui-layout-r2-local/waveform-operator/`。本轮未执行校验和检查；r2 与 r3 均保留。
- 本机 r2 的 `previous_operator_release` 与回滚目标：`outputs/operator-local/releases/20261016-history-quality-r1/waveform-operator/`。r1 二进制 SHA-256 为 `c88b8213e97eff8d601b0154ed31f3432541bf87a2066d669058ae121b7c4beb`。r1 和 r2 均保留。
- r1 发布时的历史回滚目标为 `outputs/operator-local/releases/20261009-spectrum-ui-r6/waveform-operator/`，二进制 SHA-256 为 `fe98830a1a806c95e088d7070b86f97c444d35ff8dfbc1abe43aa11ee2ada6bb`；r3 是更早的人工验证版本，r4 不作为回滚目标。
- previous_server_release：`/home/storage-user/waveform-storage-v2/releases/20261008-history-release-r3/`。
- 日志页旧 HTML：`dashboard/release-backups/20261008-history-release-r4/`。
- 日志页旧查询脚本：`dashboard/release-backups/20261008-history-release-r2/`。

本机 r2 如需回滚，可将 `current` 原子切回 r1。现场 acquisition server 最近的文档记录为 r4；此前 r4 发布的服务端回滚目标为 r3。本轮没有检查、切换或回滚 server。各本机 release 均保留。

## 架构与范围

原始数据保持“一次请求 → 多个 capture_sequence → 每段多个 BPM 文件”。历史处理一次选择一个段。不修改采集 interval、运行时核心循环或原始 HDF5 格式，不自动拼接不连续波形。

整任务分析留待后续。整任务频谱应先逐段计算，再按明确方法汇总。现有单段频谱功能继续使用当前对齐结果，日志页继续直接只读 MongoDB。

用户已确认本机 r3 的人工桌面验证通过。现场桌面机 r6 于 2026-10-09 激活，仍属于当日历史记录；本轮未连接核对现场状态。2026-10-16 本机 `current` 已切换至 r1，真实桌面历史 smoke 通过。本任务未修改现场 acquisition server；2026-10-08 的 server 验收记录只说明当日状态。

## 历史记录

- [2026-10-05 现场前端记录](deployment/20261005/前端更新记录-20261005.md)
- [独立日志前端说明](日志前端使用说明.md)
- [单段频谱分析说明](spectrum-analysis-usage.md)
- [目录整理与迁移说明](工作目录整理_2026-10-07.md)
- [7 月历史文档](history/2026-07/)
- [8 月历史交接](handoffs/)
