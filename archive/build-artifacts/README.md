# 中间构建版本归档

本目录于 2026-10-07 集中保存中间修改版本、历史可执行包和旧环境包。全部保留，没有删除或去重。按原输出日期与用途分目录，已有 SHA-256 文件与压缩包成对移动。

这些包不作为当前交付入口。2026-10-07 整理时记录的前端基线为 `0643957c5c96`，位于 `交付/前端更新-20261005/`；当前源码和部署状态见 `docs/CURRENT_STATUS.md`。现有打包脚本所需基线保留在 `outputs/`，服务端 retry 候选集中在 `server-candidates/20261005/`，部署身份待核实。

| 文件 | 字节数 | 原校验文件 |
| --- | ---: | --- |
| `dist/deepin20.9/waveform-acquisition-v2-323121c-deepin20.9-x86_64.tar.gz` | 48142841 | 有 |
| `dist/deepin20.9/waveform-acquisition-v2-716f91b-deepin20.9-x86_64.tar.gz` | 48138332 | 有 |
| `dist/deepin20.9/waveform-acquisition-v2-8c14845-deepin20.9-x86_64.tar.gz` | 48138698 | 有 |
| `dist/deepin20.9/waveform-acquisition-v2-b3f18a2-dirty-deepin20.9-x86_64.tar.gz` | 48145376 | 有 |
| `dist/deepin20.9/waveform-acquisition-v2-bce4d94-deepin20.9-x86_64.tar.gz` | 48143360 | 有 |
| `dist/deepin20.9/waveform-acquisition-v2-cbc622b-deepin20.9-x86_64.tar.gz` | 48140613 | 有 |
| `environment/waveform-storage-conda-linux-x86_64-py310-20260929.tar.gz` | 235154218 | 有 |
| `outputs/deployment-pair-20261003/waveform-operator-direct-20261003-1977b23987d4.tar.gz` | 64168 | 未附原校验文件 |
| `outputs/deployment-pair-20261003/waveform-operator-direct-offline-20261003-30b044eaac6c.tar.gz` | 117306869 | 有 |
| `outputs/deployment-pair-20261003/waveform-operator-direct-offline-20261003-d5a080b3cf82.tar.gz` | 117307347 | 有 |
| `outputs/deployment-pair-20261003/waveform-operator-direct-offline-20261003-e8fd0990bd07.tar.gz` | 117307508 | 有 |
| `outputs/deployment-pair-20261004/waveform-operator-direct-offline-20261004-10dcaa24dde7.tar.gz` | 117369150 | 有 |
| `outputs/deployment-pair-20261004/waveform-operator-direct-offline-20261004-8085d42f6d3c.tar.gz` | 117300374 | 有 |
| `outputs/deployment-pair-20261004/waveform-operator-direct-offline-20261004-dd008611f13c.tar.gz` | 117300344 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-0f6b275d7dc5.tar.gz` | 198951545 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-2150af699d39.tar.gz` | 198954043 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-30003665c8ad.tar.gz` | 198950800 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-355cdbbe645e.tar.gz` | 198954739 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-3f5821373602.tar.gz` | 198950750 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-584c34e8c8e2.tar.gz` | 198952960 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-ebee7866c9bb.tar.gz` | 198954000 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-f1793e5faf12.tar.gz` | 198952952 | 有 |
| `outputs/deployment-pair-20261005/waveform-operator-direct-offline-20261004-executable-fbd004116acb.tar.gz` | 198950588 | 有 |
| `outputs/operator-formal/waveform-operator-formal-093dea5c752e.tar.gz` | 61636 | 有 |
| `outputs/operator-formal/waveform-operator-formal-838f10e27044.tar.gz` | 61162 | 有 |
| `outputs/operator-formal/waveform-operator-formal-8d1b985da2d9.tar.gz` | 61668 | 有 |
| `outputs/operator-formal/waveform-operator-formal-cd030bc8401a.tar.gz` | 62368 | 有 |
| `outputs/operator-formal/waveform-operator-formal-e074bdd48923.tar.gz` | 60458 | 有 |
| `outputs/server-formal/waveform-server-formal-17fdf816a3cc.tar.gz` | 44937 | 有 |
| `outputs/server-formal/waveform-server-formal-88455f6a1652.tar.gz` | 44947 | 有 |
| `server-candidates/20261005/waveform-server-retry-20261005-0a9866da327e.tar.gz` | 59107230 | 有 |
| `server-candidates/20261005/waveform-server-retry-20261005-2f3038f9c6a7.tar.gz` | 59107487 | 有 |
| `server-candidates/20261005/waveform-server-retry-20261005-a357f2c3efe5.tar.gz` | 59106765 | 有 |

频谱分析的中间源码包 `waveform-operator-formal-d0c190be8d87.tar.gz` 与校验文件保存在 `spectrum-preview/20261007/`。当前本机预览及验证说明见 `docs/spectrum-analysis-validation.md`，原桌面交付入口仍按既有记录使用。

本机启动和排版修复过程产生的源码包 `cf99935148d0`、`2ffd065849d1`、`31c8046573a3`、`2279ca4bf622`，以及三次尚未采用的可执行目录，也保存在同一目录。2026-10-07 整理记录显示，当时的本机桌面入口已接入新的本机包；另一台桌面机的既有包按当时部署记录使用。

结果浏览改进前的源码包 `fff64e6b7f72` 与校验文件保存在同一归档目录。本机此前使用的可执行目录 `outputs/operator-local/waveform-operator/` 原地保留，新版本经 `outputs/operator-local/current/` 指向独立版本目录。
