# 频谱分析使用说明

软件新增第三个侧栏入口“频谱分析”。Archiver 导出和波形存储导出共用任务服务、频谱图、结果表与下载格式。

## 快速上手

在项目根目录启动本地源码预览：

```bash
bash tools/run_spectrum_preview.sh
```

该入口默认打开第三栏。频谱分析读取本地文件，离线查看模式也可使用。

启动脚本优先使用 `outputs/operator-local/current/` 指向的本机版本，其次使用既有本机包或预览；否则使用本机 Python 源码。桌面图标也使用同一版本入口。需要接入已有采集与历史服务时，运行 `bash tools/run_spectrum_preview.sh --connected`，沿用当前 SSH 连接配置。

1. 选择“波形存储 HDF5”或“Archiver 导出”。
2. 选择文件或目录。Archiver 支持 CSV、TSV、TXT 和 ZIP。
3. 选择模板，核对搜索频带、背景频带、窗口与分块。
4. 旧 HDF5 没有采样率时，填写该次采集的实际采样率。
5. 点击“开始分析”，选择结果通道查看频谱。
6. 使用“保存结果包”或“保存频谱图”导出结果。

历史检索完成对齐后，也可点击历史页面的“频谱分析”。界面接收当前对齐结果，并要求填写历史采集频率。

## 结果浏览

结果表上方的“方向”可选全部、X 或 Y；“状态”可选全部状态、重复候选、观察项、未通过门槛或无法分析。两个筛选条件同时生效，右侧显示当前通道数。

点击峰值、峰背景比或命中块的表头，切换升序和降序。数值列按实际数值排序；命中块先比较命中数量，再比较命中比例和总块数。缺失值显示为“—”。筛选后没有通道时，右侧清空频谱。

选择表格中的通道，右侧显示其频谱、峰点与峰频率。频率标签使用 LaTeX 兼容数学渲染，保存频谱图时一并导出。筛选和排序用于浏览，结果包保留本次所有通道。

## 启动环境故障

若提示 `ModuleNotFoundError: No module named 'scipy'`，说明正在运行的包或 Python 环境缺少分析依赖。工作目录中的新源码曾被旧可执行文件加载，而旧包未包含 SciPy。本机桌面入口已调整为使用包含依赖的新包；更新后关闭原窗口，重新打开桌面图标。

打包后的程序使用包内代码，源码运行使用工作目录代码。HDF5 未记录采样率时，界面的“读取文件记录”须改成该次采集的实际 Hz。

## 参数模板

| 模板 | 搜索频带 | 窗口与独立分块 | 初始门槛 |
| --- | --- | --- | --- |
| 低频 V1 | 目标附近，中心 0.152 Hz | 300 秒 | 沿用原局部背景比和稳定性规则 |
| 低频 V2 | 目标附近，中心 0.152 Hz | 300 秒 | 沿用原去趋势、全局显著性、峰宽和稳定性规则 |
| 高频频带 | 40 Hz～50 Hz | 5 秒 | 峰背景比 6 dB；至少 3 个块命中；比例至少 0.6 |

低频参数来自原分析项目。高频门槛是探索性模板，不能作为故障标准。“筛选参数”可调整门槛、命中块数、命中比例、峰匹配范围、最短记录和最少有效点。

500 Hz 和 1,000 Hz 使用同一脚本。窗口与分块按秒设置，样本数随采样率变化。频谱图标明实际分辨率。

## 数据要求

HDF5 支持单台原始格式、交错 X/Y 合并格式和带有效掩码的 v2 格式。文件采样率与填写值冲突时，程序要求核对。

bunchID 步长用于保留相对采样位置。程序结合采样率建立相对时间轴，不将轮次开始时间标成逐点 UTC 时间。

Archiver 优先读取 `float_val`，缺失时回退 `num_val`。程序兼容 UTF-8 BOM，使用时间戳计算采样间隔。无时区的时间戳按 UTC 解释，并输出提示。

零值默认保留。“排除 X/Y 同时为零”是显式选项。程序记录删除点、插值点和零值数量。不同文件独立分析，当前版本不会自动拼接缺少共同时间依据的采集。

长缺口、常值、点数不足、时长不足和 Nyquist 覆盖不足都输出具体原因。“重复候选”仅表示本次记录内重复出现，束流状态和物理原因须另行验证。当前版本不自动生成相位或相干性结论。

## 结果文件

| 文件 | 内容 |
| --- | --- |
| `run-manifest.json` | 配置、来源 hash、采样率依据、算法版本和解释范围 |
| `spectrum-summary.csv` | 通道、峰值、背景比和分块命中 |
| `data-quality.csv` | 点数、时间间隔、缺口和无效原因 |
| `spectrum-blocks.csv` | 每个独立分块的结果 |
| `results.json` | 完整数值结果与原低频指标 |
| `spectra.npz` | 频率与功率谱密度数组 |
| `report.md` | 本次结果说明 |
| `results.zip` | 完整结果包 |

GUI 将运行保存到日志目录下的 `spectrum-runs/`。CLI 可通过 `--output` 指定目录。任务状态保存在输出目录的 `jobs/`；重启后，未完成任务标记失败，已有结果可重新读取。

## 命令行

```bash
# 高频 HDF5；采样率须与该次采集一致
python tools/run_spectrum_analysis.py \
  --source hdf5 \
  --input analysis/high_frequency/inputs/20261006_155512_BPMdata.h5 \
  --sample-rate-hz 1000 --profile high_band

# Archiver 低频 V2
python tools/run_spectrum_analysis.py \
  --source archiver --input /path/to/archiver-export --profile low_v2
```

使用已安装项目依赖的 Python。`--config` 可传入 JSON 参数覆盖文件，字段与 `AnalysisConfig` 一致。

## 统一任务接口

服务默认监听本机回环地址：

```bash
python tools/run_spectrum_service.py \
  --allow-root /path/to/analysis-inputs --output logs/spectrum-api
```

| 接口 | 用途 |
| --- | --- |
| `POST /api/analysis/jobs` | 提交任务，返回任务 ID |
| `GET /api/analysis/jobs/{id}` | 查询状态和进度 |
| `POST /api/analysis/jobs/{id}/cancel` | 在通道边界取消任务 |
| `GET /api/analysis/jobs/{id}/artifacts/results.zip` | 下载结果包 |

提交示例：

```json
{
  "source_type": "hdf5",
  "paths": ["/path/to/analysis-inputs/capture.h5"],
  "profile": "high_band",
  "sample_rate_hz": 500,
  "config": {"welch_seconds": 5, "block_seconds": 5}
}
```

接口只读取启动时允许的根目录，默认用于本机或受控环境。GUI 本地分析无需启动 HTTP 服务。

## 构建与安装

前端允许清单、离线依赖列表和 PyInstaller 配置已包含新模块。新增运行依赖为 pandas 和 SciPy。

预览产物放在 `outputs/spectrum-preview/`。桌面机安装需使用目标系统兼容的构建脚本。本次未自动更新已部署的桌面机。
