# 工作目录地图

更新时间：2026-10-07。工作区根目录为 `/home/whloong/waveform-storage`；Git 仓库为其中的 `waveform-storage-system/`。

```text
waveform-storage/
├─ README.md -> waveform-storage-system/docs/WORKSPACE.md
├─ waveform-storage-system/       # 当前源码仓库
│  ├─ apps/ src/ config/ tools/ tests/ deploy/ packaging/
│  ├─ analysis/high_frequency/    # 高频分析输入和后续开发
│  ├─ data/samples/20251112/      # 旧采集原始样例
│  ├─ docs/                      # 当前说明与历史资料
│  │  ├─ audits/                 # 设备排查证据
│  │  ├─ deployment/             # 日期部署记录
│  │  ├─ handoffs/               # 历史任务交接
│  │  ├─ history/                # 旧 README 与 7 月过程记录
│  │  ├─ references/             # 论文资料，仅本地
│  │  └─ ui-preview/             # 界面截图与参考图片
│  ├─ 交付/                      # 当前前端与历史交付快照
│  ├─ outputs/                   # 必需基线和当前前端
│  ├─ dist/ build/               # 当前二进制和构建目录
│  ├─ offline-packages/          # 打包所需兼容依赖
│  ├─ archive/                   # 中间版本、旧源码和整理记录
│  └─ logs/ .env .venv/         # 本地运行状态和环境
└─ 高频数据分析 -> waveform-storage-system/analysis/high_frequency/
```

高频数据的旧文件路径仍可读取，实际文件集中在 `analysis/high_frequency/inputs/`。`handoffs/` 兼容链接指向 `docs/handoffs/`，已有交接提示中的绝对路径仍有效。

以后新数据放入对应分析目录的 `inputs/`；分析结果使用独立批次目录。新构建由现有打包脚本生成到 `outputs/`，明确交付后登记到 `交付/`；未使用中间版本归 `archive/build-artifacts/`。运行脚本和依赖路径应以当前源码为准。
