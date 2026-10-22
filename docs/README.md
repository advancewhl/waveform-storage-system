# 文档索引

- [当前阶段状态](CURRENT_STATUS.md)、[工作区地图](WORKSPACE.md)、[目录整理记录](工作目录整理_2026-10-07.md)
- [日志前端使用说明](日志前端使用说明.md)
- [频谱分析架构](spectrum-analysis-design.md)、[频谱分析使用说明](spectrum-analysis-usage.md)、[频谱分析验证记录](spectrum-analysis-validation.md)
- `deployment/`：有明确日期的部署与更新记录
- `audits/`：设备排查及未部署候选证据
- `handoffs/`：集成交接与历史任务记录；Agent 当前契约见下方入口
- `history/`：历史 README、7 月重构、现场调查和过程文档
- `ui-preview/`：界面预览；`ui-preview/references/` 为参考照片
- `references/`：论文 DOC 等原始资料，仅本地保存

设计与协议文档目前保留现有文件名：数据对齐、设备注册表、MongoDB 元数据、采集协议、通信调查和候选服务部署。历史文档中的旧状态应结合当前状态核对。

## Agent API 接入

- [v2 接口手册](agent-api-v2.md)：read/control/data、认证、审批、限流、版本与迁移。
- [v2 OpenAPI](agent-api-v2.openapi.json)：机器可读契约。
- [v1 兼容契约](waveform-agent-api.md)：旧客户端的端点、响应和重试语义。
- [v2 Integration Handoff](handoffs/waveform-agent-v2-integration-20261021.md)、[源码交付与验证证据](../交付/20261021-waveform-agent-v2/README.md)。

v2 已完成源码与本机 fixture 验证，尚未部署现场。生产接入推荐 `WAVEFORM_AGENT_V2_ONLY=true`；保留 v1 时须独立施加代理或网络 ACL。data preview 无需审批，mutation 需一次签名确认。数据修改仅影响 v2 overlay，原 Mongo/HDF5 和 v1/GUI 视图保留。
