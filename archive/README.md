# 本地归档

- [build-artifacts/](build-artifacts/README.md)：中间修改包、历史构建与旧环境包，全部保留。
- `source_snapshot/`：原始 ZIP，仅本地。
- `legacy/`：旧采集脚本和旧服务端源码包，仅本地。
- `organization/20261007/`：本次整理前 Git 状态、工作区 diff、源码 hash 与完整迁移清单，仅本地；目录权限为 0700。

原始压缩包与旧脚本可能包含历史凭据，默认不进入 Git。需要恢复时从本地归档读取，不直接作为当前部署入口。
