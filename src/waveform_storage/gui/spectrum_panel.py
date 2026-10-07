"""The offline-capable third operator page using the shared analysis job API."""

from __future__ import annotations

from pathlib import Path
import shutil

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from waveform_storage.analysis.models import AnalysisConfig, AnalysisRequest
from waveform_storage.analysis.service import AnalysisJobs


LABELS = {"invalid": "无法分析", "stable_candidate": "重复候选",
          "observation": "观察项", "reject": "未通过门槛"}


class SpectrumPanel(QWidget):
    def __init__(self, output_root, parent=None):
        super().__init__(parent)
        self.output_root = Path(output_root)
        self.jobs = None
        self.job_id = None
        self.last_run = None
        self.alignment = None
        self._closed = False
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._poll)

    @staticmethod
    def _spin(value=0.0, *, maximum=1_000_000, decimals=5):
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(0, maximum)
        spin.setValue(value)
        spin.setMinimumWidth(90)
        return spin

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(16, 12, 16, 12)
        source_row = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem("波形存储 HDF5", "hdf5")
        self.source_combo.addItem("Archiver 导出", "archiver")
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择单台、合并 HDF5，或 Archiver 文件、ZIP、目录")
        self.file_button = QPushButton("选择文件")
        self.folder_button = QPushButton("选择目录")
        source_row.addWidget(self.source_combo)
        source_row.addWidget(self.path_edit, 1)
        source_row.addWidget(self.file_button)
        source_row.addWidget(self.folder_button)
        form.addLayout(source_row)
        self.input_note = QLabel("采样率使用该次采集的记录；旧 HDF5 缺失时须填写。")
        self.input_note.setWordWrap(True)
        form.addWidget(self.input_note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(7)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("高频频带（40 Hz～50 Hz）", "high_band")
        self.profile_combo.addItem("低频窄峰（V2）", "low_v2")
        self.profile_combo.addItem("低频窄峰（V1）", "low_v1")
        self.sample_rate = self._spin(decimals=3)
        self.sample_rate.setSuffix(" Hz")
        self.sample_rate.setSpecialValueText("读取文件记录")
        self.sample_rate.setToolTip("HDF5 缺失采样率时填写该次采集的实际 Hz；Archiver 使用时间戳估计")
        self.zero_combo = QComboBox()
        self.zero_combo.addItem("保留零值", "keep")
        self.zero_combo.addItem("排除 X/Y 同时为零", "mask_double_zero")
        self.low = self._spin()
        self.high = self._spin()
        self.window_seconds = self._spin(decimals=2)
        self.background_low = self._spin()
        self.background_high = self._spin()
        self.block_seconds = self._spin(decimals=2)
        self.threshold = self._spin(decimals=2)
        self.minimum_blocks = QSpinBox()
        self.minimum_blocks.setRange(1, 10000)
        self.block_ratio = self._spin(maximum=1, decimals=2)
        self.block_ratio.setSingleStep(0.1)
        self.peak_match = self._spin()
        self.minimum_seconds = self._spin(decimals=2)
        self.minimum_points = QSpinBox()
        self.minimum_points.setRange(4, 5_000_000)
        fields = [
            ("分析模板", self.profile_combo), ("采样率", self.sample_rate), ("零值策略", self.zero_combo),
            ("搜索下限（Hz）", self.low), ("搜索上限（Hz）", self.high), ("窗口（秒）", self.window_seconds),
            ("背景下限（Hz）", self.background_low), ("背景上限（Hz）", self.background_high), ("分块（秒）", self.block_seconds),
            ("峰背景比（dB）", self.threshold), ("最少命中块", self.minimum_blocks), ("命中比例", self.block_ratio),
            ("峰匹配（Hz）", self.peak_match),
            ("最短记录（秒）", self.minimum_seconds), ("最少有效点", self.minimum_points),
        ]
        for index, (label, widget) in enumerate(fields[:9]):
            row, pair = divmod(index, 3)
            grid.addWidget(QLabel(label), row, 2 * pair)
            grid.addWidget(widget, row, 2 * pair + 1)
        self.parameter_widgets = [widget for _label, widget in fields] + [
            self.source_combo, self.path_edit, self.file_button, self.folder_button]
        form.addLayout(grid)
        self.advanced_frame = QWidget()
        advanced_grid = QGridLayout(self.advanced_frame)
        advanced_grid.setContentsMargins(0, 0, 0, 0)
        for index, (label, widget) in enumerate(fields[9:]):
            row, pair = divmod(index, 3)
            advanced_grid.addWidget(QLabel(label), row, 2 * pair)
            advanced_grid.addWidget(widget, row, 2 * pair + 1)
        self.advanced_frame.hide()
        form.addWidget(self.advanced_frame)
        action = QHBoxLayout()
        self.run_button = QPushButton("开始分析")
        self.cancel_button = QPushButton("取消分析")
        self.cancel_button.setEnabled(False)
        self.export_button = QPushButton("保存结果包")
        self.export_button.setEnabled(False)
        self.plot_export_button = QPushButton("保存频谱图")
        self.plot_export_button.setEnabled(False)
        self.advanced_button = QPushButton("筛选参数")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self.advanced_frame.setVisible)
        for widget in (self.run_button, self.cancel_button, self.export_button,
                       self.plot_export_button, self.advanced_button):
            action.addWidget(widget)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        action.addWidget(self.progress, 1)
        form.addLayout(action)
        layout.addWidget(card)
        self.status_label = QLabel("选择数据后开始分析。结果候选属于统计筛选。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("通道", "状态", "峰值（Hz）", "峰背景比", "分块命中"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumWidth(300)
        splitter.addWidget(self.table)
        plot_host = QWidget()
        plot_layout = QVBoxLayout(plot_host)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(7, 4), facecolor="white", constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        plot_layout.addWidget(self.canvas, 1)
        self.detail_label = QLabel("选择一个结果通道查看频谱与质量信息。")
        self.detail_label.setWordWrap(True)
        plot_layout.addWidget(self.detail_label)
        splitter.addWidget(plot_host)
        splitter.setSizes([440, 650])
        layout.addWidget(splitter, 1)
        self.file_button.clicked.connect(self._choose_file)
        self.folder_button.clicked.connect(self._choose_folder)
        self.profile_combo.currentIndexChanged.connect(self._apply_profile)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.path_edit.textEdited.connect(self._clear_alignment)
        self.run_button.clicked.connect(self.start_analysis)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.export_button.clicked.connect(self._export)
        self.plot_export_button.clicked.connect(self._export_plot)
        self.table.itemSelectionChanged.connect(self._render_selection)
        self._apply_profile()

    def _apply_profile(self, *_args):
        config = AnalysisConfig.for_profile(self.profile_combo.currentData())
        for widget, value in [(self.low, config.search_low_hz), (self.high, config.search_high_hz),
                              (self.background_low, config.background_low_hz),
                              (self.background_high, config.background_high_hz),
                              (self.window_seconds, config.welch_seconds),
                              (self.block_seconds, config.block_seconds),
                              (self.threshold, config.threshold_db),
                              (self.minimum_blocks, config.minimum_blocks),
                              (self.block_ratio, config.minimum_block_ratio),
                              (self.peak_match, config.peak_match_hz),
                              (self.minimum_seconds, config.minimum_seconds),
                              (self.minimum_points, config.minimum_points)]:
            widget.setValue(value)

    def _source_changed(self, *_args):
        self.alignment = None
        self.path_edit.clear()
        self.sample_rate.setValue(0)
        archiver = self.source_combo.currentData() == "archiver"
        self.sample_rate.setEnabled(not archiver)
        self.zero_combo.setEnabled(not archiver)
        if archiver:
            self.zero_combo.setCurrentIndex(0)
        self.profile_combo.setCurrentIndex(1 if archiver else 0)
        self.input_note.setText("Archiver 使用真实时间戳；数值优先 float_val，缺失时取 num_val。"
                                if archiver else "采样率使用该次采集的记录；旧 HDF5 缺失时须填写。")

    def _clear_alignment(self, *_args):
        self.alignment = None
        self.sample_rate.setValue(0)

    def _choose_file(self):
        filters = ("HDF5 (*.h5 *.hdf5)" if self.source_combo.currentData() == "hdf5"
                   else "Archiver (*.csv *.tsv *.txt *.zip)")
        path, _ = QFileDialog.getOpenFileName(self, "选择分析文件", "", filters)
        if path:
            self._clear_alignment()
            self.path_edit.setText(path)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择分析输入目录")
        if path:
            self._clear_alignment()
            self.path_edit.setText(path)

    def use_alignment(self, result):
        if self.job_id:
            raise ValueError("当前分析仍在运行，请先等待完成或取消")
        self.source_combo.setCurrentIndex(0)
        self.alignment = result
        self.path_edit.setText(f"当前历史对齐结果：{len(result.device_ids)} 台 BPM")
        self.sample_rate.setValue(0)
        self.input_note.setText("已接收历史对齐结果。请填写该次历史采集的实际采样率。")

    def _config(self):
        return AnalysisConfig.for_profile(
            self.profile_combo.currentData(), search_low_hz=self.low.value(),
            search_high_hz=self.high.value(), background_low_hz=self.background_low.value(),
            background_high_hz=self.background_high.value(), welch_seconds=self.window_seconds.value(),
            block_seconds=self.block_seconds.value(), threshold_db=self.threshold.value(),
            minimum_blocks=self.minimum_blocks.value(), minimum_block_ratio=self.block_ratio.value(),
            peak_match_hz=self.peak_match.value(), zero_policy=self.zero_combo.currentData(),
            minimum_seconds=self.minimum_seconds.value(), minimum_points=self.minimum_points.value())

    def start_analysis(self):
        if self.job_id or self._closed:
            return
        try:
            source = "alignment" if self.alignment is not None else self.source_combo.currentData()
            paths = () if self.alignment is not None else (self.path_edit.text().strip(),)
            if paths == ("",):
                raise ValueError("请先选择输入文件或目录")
            request = AnalysisRequest(source, paths, self._config(),
                                      (self.sample_rate.value() or None) if source != "archiver" else None,
                                      self.alignment)
            if self.jobs is None:
                self.jobs = AnalysisJobs(self.output_root)
            self.job_id = self.jobs.submit(request)
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "分析参数无效", str(exc))
            return
        self.last_run = None
        self.table.setRowCount(0)
        self.figure.clear()
        self.canvas.draw_idle()
        self.plot_export_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.run_button.setEnabled(False)
        for widget in self.parameter_widgets:
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("正在读取数据并检查时间轴…")
        self.timer.start()

    def cancel_analysis(self):
        if self.job_id:
            self.jobs.cancel(self.job_id)
            self.status_label.setText("正在取消，将在当前通道处理结束后停止。")
            self.cancel_button.setEnabled(False)

    def _poll(self):
        if not self.job_id:
            return
        state = self.jobs.get(self.job_id)
        if state["total"]:
            self.progress.setRange(0, state["total"])
            self.progress.setValue(state["completed"])
            self.status_label.setText(f"已完成 {state['completed']}/{state['total']} 个通道：{state['channel']}")
        elif state["completed"]:
            self.status_label.setText(f"已分析 {state['completed']} 个通道：{state['channel']}；继续读取输入。")
        if state["status"] not in {"succeeded", "failed", "cancelled"}:
            return
        self.timer.stop()
        finished_id, self.job_id = self.job_id, None
        self.run_button.setEnabled(True)
        for widget in self.parameter_widgets:
            widget.setEnabled(True)
        archiver = self.source_combo.currentData() == "archiver"
        self.sample_rate.setEnabled(not archiver)
        self.zero_combo.setEnabled(not archiver)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if state["status"] == "succeeded" else 0)
        if state["status"] == "succeeded":
            self.last_run = self.jobs.result(finished_id)
            self._show_result()
        else:
            self.status_label.setText("分析已取消。" if state["status"] == "cancelled"
                                      else "分析失败：" + str(state["error"]))

    def _show_result(self):
        run = self.last_run
        self.table.setRowCount(len(run.channels))
        for row, channel in enumerate(run.channels):
            values = [channel.channel, LABELS[channel.classification],
                      "—" if channel.peak_hz is None else f"{channel.peak_hz:.5g}",
                      "—" if channel.peak_to_background_db is None else f"{channel.peak_to_background_db:.2f} dB",
                      f"{channel.block_hits}/{channel.block_count}"]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(channel.reason + "\n" + "\n".join(channel.warnings))
                self.table.setItem(row, col, item)
        valid = sum(c.classification != "invalid" for c in run.channels)
        candidates = sum(c.classification == "stable_candidate" for c in run.channels)
        self.status_label.setText(f"分析完成：{valid}/{len(run.channels)} 个通道可分析，{candidates} 个重复候选。可保存本次结果包。")
        self.status_label.setToolTip(str(run.directory))
        self.export_button.setEnabled(True)
        if run.channels:
            self.table.selectRow(0)

    def _render_selection(self):
        row = self.table.currentRow()
        if self.last_run is None or row < 0:
            return
        channel = self.last_run.channels[row]
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        if len(channel.frequency):
            axis.semilogy(channel.frequency, np.maximum(channel.psd, np.finfo(float).tiny),
                          color="#357fb5", linewidth=1.4)
            cfg = self.last_run.manifest["config"]
            axis.axvspan(cfg["search_low_hz"], cfg["search_high_hz"], color="#458cc4", alpha=0.12)
            axis.set_xlim(cfg["background_low_hz"], cfg["background_high_hz"])
            if channel.peak_hz is not None:
                axis.axvline(channel.peak_hz, color="#c78136", linewidth=1, linestyle="--")
            axis.set_xlabel("Frequency (Hz)", fontsize=10)
            axis.set_ylabel(r"$\mathrm{PSD}\;[\mathrm{raw}^{2}/\mathrm{Hz}]$", fontsize=10)
            axis.set_title(channel.channel, loc="left", fontsize=10)
            axis.grid(alpha=0.18)
            self.plot_export_button.setEnabled(True)
        else:
            axis.text(0.5, 0.5, "No valid spectrum", ha="center", va="center", transform=axis.transAxes)
            self.plot_export_button.setEnabled(False)
        self.canvas.draw_idle()
        metrics = ""
        if channel.sample_rate_hz is not None:
            duration = "—" if channel.duration_seconds is None else f"{channel.duration_seconds:.3f}"
            resolution = "—" if channel.resolution_hz is None else f"{channel.resolution_hz:.6g}"
            metrics = f"采样率 {channel.sample_rate_hz:g} Hz；记录跨度 {duration} 秒；分辨率 {resolution} Hz。\n"
        self.detail_label.setText(metrics + channel.reason)
        self.detail_label.setToolTip("\n".join(channel.warnings))

    def _export(self):
        if self.last_run is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存本次分析结果包", "spectrum-results.zip", "ZIP (*.zip)")
        if path:
            try:
                shutil.copy2(self.last_run.directory / "results.zip", path)
                self.status_label.setText("结果包已保存：" + path)
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    def _export_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存当前通道频谱图", "spectrum.png", "PNG (*.png)")
        if path:
            try:
                self.figure.savefig(path, dpi=180, bbox_inches="tight")
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    def shutdown(self):
        self._closed = True
        self.timer.stop()
        if self.jobs is not None:
            self.jobs.shutdown(wait=False)
