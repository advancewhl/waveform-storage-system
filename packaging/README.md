# 打包脚本导航

| 工作 | 入口 |
| --- | --- |
| 正式前端源码包 | `build-operator-formal.py` 与 `operator-formal-files.txt` |
| 正式服务端源码包 | `build-server-formal.py` 与 `server-formal-files.txt` |
| 直接连接离线前端 | `build-direct-operator-offline.py` |
| 前端可执行构建 | `build-operator-executable-debian10.sh` |
| 服务端可执行构建 | `build-acquisition-v2-debian10.sh` |
| 前端可执行交付包 | `package-operator-executable.py` |
| 服务端更新包 | `build-live-server-pv-package.py`、`package-server-20261005.py` |

`dist/` 和 `outputs/deployment-pair-20261003/`、`outputs/deployment-pair-20261004/` 的基础包是现有脚本输入；`offline-packages/glibc228-crypto-compat-20261004.tar.gz` 是兼容依赖输入。这些路径保留。打包脚本默认输出仍写到原目录，本次未运行重打包。

中间包集中保存在 [构建归档](../archive/build-artifacts/README.md)。明确交付版本见 [交付索引](../交付/README_先看.md)。历史环境包只用于恢复或核对，不能直接视为当前交付包。
