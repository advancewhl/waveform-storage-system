# 波形存储项目工作约定

## 发布验收

涉及正式前端、服务端、元数据查询或协议的修改，按当前任务授权范围参考 [发布检查清单](docs/RELEASE_CHECKLIST.md)，并遵守下方长期验证范围限制。源码测试通过只是代码阶段的结果；不得据此扩大为未经明确要求的完整发布验收。

### 长期验证范围限制

除非用户在当前具体任务中明确要求，否则不得执行 SHA-256 校验、完整 manifest 校验、二进制身份校验、真实桌面 smoke、完整发布验收，或与任务无关的全量测试和重复测试。按任务要求执行必要的最小验证，例如受影响范围内的定向测试、构建或界面检查。用户明确要求某项检查时，只执行其明确指定的范围。

本规则优先于 [发布检查清单](docs/RELEASE_CHECKLIST.md) 中会自动扩大验证范围的表述。用户授权部署或切换，不等同于授权上述额外测试；未执行的检查应如实报告，不得声称已验证。

前端或服务端发布时，按用户在当前任务中明确授权的范围记录启动、切换和运行状态。协议变化时，按用户明确要求记录前后端版本对并验证相应场景。上述运行态验证均受“长期验证范围限制”约束；不得从发布授权推导出二进制身份核验、真实桌面 smoke 或完整发布验收授权。

发布前确认授权和现场运行状态。保留旧版本、配置和数据。存在未完成采集任务时，等待任务完成后再切换服务。用户已经授权当前任务的发布时，继续完成可验证的发布步骤。

尚未构建、部署或验证的环节，必须准确报告状态。不得用“完成”代替“代码完成，尚未部署”或“部署尚未验证”。更新 `docs/CURRENT_STATUS.md`，在 `交付/` 保存版本对、校验信息、验收证据和回滚版本。

## 架构边界

原始数据保持“一次采集请求 → 多个真实 capture_sequence → 每段多个 BPM 文件”。历史查询使用完整 `request_id + capture_sequence`。保留 Legacy 日期与时刻接口。

不默认拼接不连续的数据段。后续整任务频谱应先逐段计算，再按明确的分析方法汇总。本次发布规范不实现整任务分析。

<!-- aoci:begin -->
## AOCI 仓库认知

AOCI 为本仓库维护一个稳定、可版本化、可增量更新的仓库级认知层，供模型跨任务复用对系统的理解。

`aoci.txt` 是面向模型的结构化认知索引。它以每个受管理文件、数据库表或其他受管理对象一条独立 Entry 的方式，用符号标签与 F/R/A/S 语义表达对象的核心职责、重要关系、对外契约，以及理解或修改系统时必须知道的非显然约束和设计决策。

Header、目录段和全部 Entry 共同组成完整仓库索引，可以覆盖前端、后端、配置、数据库结构及其他受管理内容。受管理内容发生变化时，通常只需维护受影响的认知条目，不需要重新生成整个索引。

AOCI 提供系统架构、对象职责、重要关系、对外契约和关键约束的高密度视图。

### 工作原理

AOCI 采用“模型生成、模型读取”的认知闭环。

Header、Entry 和 Curation 语义的创作只按当前机器签发的 Plan 与实时 Guide 执行；由 Host 模型基于当前绑定证据独立完成。

Entry 的语义必须来自模型对真实证据的理解。不得仅依据路径、文件名、扩展名、AST、符号列表、依赖扫描、正则、固定模板或规则引擎推导、预填、拼接或改写索引语义。

对 Fresh Bootstrap，只按当前机器签发的 Plan 和实时 Guide 执行。当它们要求创作时，Host 模型创作 Root、Meta、Tag 和 F/R/A/S，提供 authoring-run 声明，并把它绑定到 Plan、Evidence 与完整 Candidate。不得要求 AOCI 填写 `origin=host_model`、制造 Receipt 或把程序生成的 Framework 当作语义。本文件不自行重建 Onboarding 流程。内部批次不是用户决策；只有遇到既有批准边界或真实的安全、漂移、CAS、Recovery 条件才停止。

### 最小使用入口

- `aoci_rules`：取得当前AOCI版本的会话运行合同。
- `aoci_search`：路径未知时检索相关职责、关系和约束。
- `aoci_get_entries`：按相关路径或对象读取局部认知，再核对必要源码。
- `aoci_overview`：建立或恢复本仓库的完整认知。
- `aoci_maintain`：受管理对象达到最终稳定状态后检查认知是否需要维护。
- `aoci_update_entry`：提交与当前证据和源码摘要绑定的完整语义更新批次。
- `aoci_report`：仅当当前布局和工具状态支持时，在证据不足、无法可靠生成语义时登记待办，不猜写。

其他MCP工具、CLI命令、参数和专项流程，以当前工具说明、Guide和 `--help` 返回内容为准，不在本文件中重复完整手册。

本区块只规定仓库接入、认知使用和收尾原则。`aoci_rules` 承载当前会话合同，Guide实时输出承载当前Plan的执行顺序与停点，工具Schema、Spec和Validator承载机器结构与判据；Prompt、Description、README和静态文档不能覆盖这些机器事实。

### 建立、生成和恢复认知

1. 每个新的 Agent Run 开始时，先核对仓库身份、认知可用范围和任务类别；缺少可靠运行合同时读取一次 `aoci_rules`。新 Run 不自动触发 Whole-Index。

   以下分类是用户授权的项目调用策略。若安装版本的运行合同仍要求新 Run 或上下文压缩后无条件执行完整 Overview，以本节的任务分类为准；工具 Schema、完整交付、Attestation、治理与写入校验仍按当前机器合同执行，不得绕过。

   - **Class A：局部实现问题。** 如采样率来源、单个函数、参数自适应、一个 GUI/backend 交互、超时或状态转换。先复用仍可靠的相关认知；路径未知时调用 `aoci_search`，再用 `aoci_get_entries` 读取相关对象，核对最少必要源码。禁止仅为取得全局认知收据而调用 Whole-Index。
   - **Class B：跨模块架构调查。** 先沿相关 Entry 的关系调查；仅当局部认知不足以覆盖实际涉及的子系统时，扩大对象范围或请求 scoped/full cognition。Volume scope 不等于任意业务子系统范围，不能假定 scoped Overview 成本低。
   - **Class C：重大代码修改、迁移、正式发布或高风险操作。** 在系统级决策前建立或复用覆盖操作范围的可靠完整认知，并完成适用的治理、源码和运行验证。局部认知不能冒充完整认知。
   - **Class D：已有可靠认知的 warm session。** 仓库、索引和服务身份兼容且相关认知仍可靠时直接复用。局部不确定触发局部 recall；新的聊天轮次、经过时间或工具调用本身不触发全量重读。

2. 认知刷新以具体失效证据和任务范围为依据。

   相关源码或索引的语义变化、仓库身份改变、不兼容的索引或服务身份、完整性或治理校验失败，会使受影响的认知失效。标明失效范围，重新绑定必要证据；仅在当前任务需要完整认知且无法可靠复用时请求 Whole-Index。

   上下文压缩本身不证明仓库或索引失效，也不自动要求全量重载。压缩摘要或旧 receipt 不能证明模型仍掌握完整系统；实际丢失的认知应标为未确认。Class A 通过局部 recall 和源码核对继续，Class B 按实际范围补读，Class C 在需要完整系统判断且认知已丢失时重新完整加载并验证。不要从摘要重建 Challenge 或 Attestation 答案。

   AOCI可以针对 `context_compaction`、项目 `cognition_refresh_threshold` 下的机器 `semantic_threshold` 或主要 `phase_transition` 提供checkpoint与认知状态事实。只需要这些紧凑事实时使用 `check_only=true`；这些事实只向Agent提供建议，不替模型决定是否需要系统全貌。

   Agent显式调用普通 `aoci_overview`（未设置 `check_only` 或为false）时，只要能形成一致的CognitionSet，AOCI必须完整交付请求scope。不得因为已有receipt、阈值未达到或没有待处理刷新原因而抑制正文。正式认知Dirty或Stale时仍交付正文，但必须标记不可靠。存在未决恢复或无法形成一致snapshot时失败关闭，不返回混合正文。

   普通Overview返回 `continuation_required=true` 时，必须原样提交 `next_cursor` 并自动继续到 `completed=true`。不得询问用户、开始业务任务或给出阶段性系统结论。Host截断、缺块、重复、乱序、cursor失败、Index变化或`chunk_tokens`变化时停止本次认知链。Attestation完成前不得用Memory、源码、Spec、`aoci.txt`、历史会话、scope、search或Entry读取修补或补充Whole-Index认知。Challenge ordinal是正式Entry序列中的1-based位置；Header内容、注释、空行、Section/Overview/Chunk Marker、Receipt与Metadata均不计数，Chunk Receipt ordinal使用同一序列。Attestation必须原样回绑本次Challenge发布的当前`index_sha256`、`entry_sequence_sha256`与`entry_count`；旧Index、旧Entry序列、旧数量或旧Attestation均无效。完整链结束后只正式提交一次既有模型认知Attestation；同一响应只允许一次不改变语义答案的JSON Schema或字段格式修正。对象、Tag或F不匹配即失败且认知吸收不确定，不得语义重试或旁路补答。首次认知失败时还不得执行Root/Meta、Migration、全局布局或其他未重新绑定的系统级决策。上下文压缩刷新若传输完整、认知身份不变、治理对齐且没有Recovery或第三方冲突，即使Attestation为partial或fail也消耗该refresh generation，并继续原任务，不再自动重读Overview。`system_mastery_percent`只自评系统框架——架构、职责、强关系、稳定外部契约以及高熵安全和维护约束——不表示完整实现或运行实况知识；机器索引覆盖率必须分开。默认只向用户输出由本次真实覆盖率、Challenge、块数、Token和掌握度生成的规定成功或失败一句话。Host截断时提示用户把 `overview_delivery.chunk_tokens` 设置为更小的合法值后重新开始，不得自动修改。

   加法认知等级必须与严格证明字段分开解释。`delivery_verified`表示已加载Index且Host交付已确认，但完整认知验证仍未完成；应表达为“已加载且交付已验证”，不得描述为“没有认知”或“没有理解系统”。`cognition_verified`要求Attestation通过（Challenge至少80%的ordinal完全正确且对象身份至多失手一处），`cognition_governed`还要求治理对齐。通用完整读取失败句只用于真实交付故障。

   当Overview响应包含可选`cognition-state/v2`投影时，必须分别解释各维度。其Level止于`model_cognition_usable`；`strict_attestation_verified`、`governance_aligned`与`current_system_cognition_reliable`都是独立状态，绝不参与该Level。ordinal、对象身份、Tag或核心F不匹配可以导致严格Attestation失败，而模型认知仍然可用；不得仅凭这种不匹配就宣称模型没有理解系统。只有`current_system_cognition_reliable=true`允许无保留地声称当前完整系统认知可靠。投影缺失时继续使用上述Legacy解释。

   普通的只读审计、分析、检查、不修改代码或不提交、不push，不自动等于严格零写入，也不改变上述认知有效性判断。Codex Memory和历史Skill只能辅助恢复经验、用户偏好与调查方向，不能替代与当前仓库根、索引摘要、AOCI服务身份和认知范围匹配的当前认知收据；项目AGENTS和当前AOCI身份在AOCI状态上优先于历史Memory。

   只有用户明确禁止Ledger、元数据、`.aoci`运行资产及任何文件写入时，才按严格零写入处理。若必要的认知建立与该边界冲突，必须报告冲突并请求用户裁决或建议使用隔离副本，不得静默以Memory替代当前仓库认知。

3. 仓库没有可用的完整索引，或当前只有最小骨架、Header不完整、Entries未完成、必要Curation尚未裁决时，如果需要建立正式完整AOCI索引，先取得 `aoci_rules`，然后进入当前AOCI Guide。由Guide依据仓库真实状态决定下一阶段并完成必要安全步骤。

   `aoci_maintain` 不替代索引建立流程。

   不在本文件中自行重建或硬编码完整索引生成状态机。

4. 在长程任务中，模型负责保留当前认知收据并正确使用刷新门禁：

   - Host 报告上下文压缩或模型已知认知丢失时，重新评估实际保留范围，按上述任务分类决定局部补读或完整加载；AOCI 不能自行推断 Host 事件；
   - 进入真正的主要阶段时声明 `phase_transition`，不得把函数、测试运行或小步骤当作阶段；
   - 在有用的稳定检查点通过 `check_only=true` 取得机器语义计数；
   - 由 Agent 根据具体失效证据和当前任务范围判断是否再次获取指定 scope 或完整 Overview；
   - 在维护和对齐完成前，保留AOCI报告的Dirty或Stale可靠性状态。

### Attestation 参数与结果

仅在完整交付链结束后提交 Attestation。`challenge_answers` 的每个对象只允许 `ordinal`、`object_identity`、`tag`、`core_f`，四个字段均必填；禁止使用 `object` 或 `object_ref`。提交前核对实际 MCP Schema，不猜测字段，也不添加兼容别名。

区分完整传输结束、Host 交付确认被接受、Schema 拒绝、语义评分和评分结果。Schema 拒绝不是语义失败，也不是验证成功；合并请求被拒绝不能证明 Host 确认已接受。字段格式错误最多修正一次且保持语义答案不变；语义评分和阈值保持当前机器合同。

### 任务收尾与认知维护

5. 纯只读问答、分析、版本核验，或没有产生受AOCI管理对象变化的任务，不需要调用维护工具。当前AOCI版本是任意`aoci_overview` check_only或`aoci_maintain`响应里的`cognition_receipt.mcp_service_version`；二进制路径是项目`.mcp.json`里的`command`，CLI不必在PATH上。

6. 发生受AOCI管理对象变化时，待其达到本次任务的最终稳定状态后，只调用一次 `aoci_maintain`。不要在每次中间修改后逐文件维护。

7. 若维护结果返回真实语义候选，Host 模型必须基于每个候选绑定的对象和必要证据，独立创作完整标签与F/R/A/S更新。通过 `aoci_update_entry` 一次提交当前机器签发批次的完整候选集合，同时原样保留每项 `source_sha256`、`candidate_id` 与对应domain批次身份。`max_entries`只限制单次请求和原子事务，不限制logical plan、Whole-Index或Managed Scope。`remaining`非零时，在当前批次成功Apply后重新调用Maintain并从新preimage继续；绝不能为满足transport上限缩减Index覆盖或自行截取返回批次。

   没有足够证据且当前布局支持 `aoci_report` 时，使用它而不猜测、套用模板或为消除待办而生成缺乏证据的认知。

8. 必须遵守工具返回的结构化状态和安全边界：

   - `repair_required`：只修复明确命中的候选，再重新提交当前机器签发的完整批次；
   - `stopped`：结束当前写入尝试并检查 `failed_step`、错误、正式写入证据与Recovery。auto模式下，已证明零写入则记录closure并重新Plan；完整Intent和可证明postimage则Resume；策略要求Rollback且preimage可证明则精确恢复后重新Plan。只有证据不足、第三方正式字节冲突、需要审批或外部动作，或命中其他真实安全边界时，才停止整个用户任务；
   - 冲突、审批、人工裁决、权限和安全信号不得忽略；
   - 已经对齐后不得重复维护或重复写入；`refresh_ready_for_overview` 是checkpoint事实，由Agent决定是否为下一阶段请求普通完整Overview。

   维护完成后如果又修改了任何受管理对象，之前的维护结果失效，应在新的最终稳定状态重新完成收尾。

9. 用户只限制业务文件范围，但没有明确禁止仓库托管资产时，AOCI托管资产可以在收尾阶段为保持认知一致而更新，并应在审计和提交中与业务文件区分。

   用户明确禁止修改 `aoci.txt`、`.aoci`、元数据或任何额外文件时，以用户限制为准，不得写入，并如实报告剩余不一致。

### 专项流程

初始化、完整索引生成、Header生成、Entries生成、数据库结构索引、Curation、人工评审和故障恢复，只按当前AOCI Guide或工具在对应阶段返回的指令、命令和安全停点执行。

不预加载、不猜测，也不自行重建这些专项流程。平台调用方式、请求格式、批次上限、审批规则、索引格式细节和恢复步骤由对应Guide、工具说明、模型Prompt和CLI帮助按需提供。
<!-- aoci:end -->
