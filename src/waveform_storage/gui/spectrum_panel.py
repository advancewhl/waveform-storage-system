"""The offline-capable third operator page using the shared analysis job API."""

from __future__ import annotations

import math
from numbers import Real
from pathlib import Path
import re
import shutil

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from waveform_storage.analysis.models import AnalysisConfig, AnalysisRequest, format_peak_frequency
from waveform_storage.analysis.service import AnalysisJobs
from waveform_storage.analysis.status import DISPLAY_STATUS_LABELS


LABELS = DISPLAY_STATUS_LABELS


class NumericResultItem(QTableWidgetItem):
    def __init__(self, text, value):
        super().__init__(text)
        self.sort_key = (0, ()) if value is None else (1, value if isinstance(value, tuple) else (value,))

    def __lt__(self, other):
        if isinstance(other, NumericResultItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)


class SpectrumPanel(QWidget):
    def __init__(self, output_root, parent=None):
        super().__init__(parent)
        self.output_root = Path(output_root)
        self.jobs = None
        self.job_id = None
        self.last_run = None
        self.alignment = None
        self.history_request_id = None
        self.history_capture_sequence = None
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

    @staticmethod
    def _parameter_field(label, widget):
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        text = QLabel(label)
        text.setBuddy(widget)
        widget.setFixedWidth(180 if isinstance(widget, QComboBox) else 145)
        row.addWidget(text)
        row.addWidget(widget)
        row.addStretch(1)
        return field

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
        self.input_note = QLabel("文件未记录采样率时，请在“本次采样率”填入该次采集值。")
        self.input_note.setWordWrap(True)
        form.addWidget(self.input_note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(7)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("高频（40 Hz～50 Hz）", "high_band")
        self.profile_combo.addItem("低频 0.152 Hz（V2）", "low_v2")
        self.profile_combo.addItem("低频 0.152 Hz（V1）", "low_v1")
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
            ("分析模板", self.profile_combo), ("本次采样率", self.sample_rate), ("零值策略", self.zero_combo),
            ("搜索下限（Hz）", self.low), ("搜索上限（Hz）", self.high), ("窗口（秒）", self.window_seconds),
            ("背景下限（Hz）", self.background_low), ("背景上限（Hz）", self.background_high), ("分块（秒）", self.block_seconds),
            ("谱峰背景比（dB）", self.threshold), ("最少命中块", self.minimum_blocks), ("命中比例", self.block_ratio),
            ("谱峰匹配（Hz）", self.peak_match),
            ("最短记录（秒）", self.minimum_seconds), ("最少有效点", self.minimum_points),
        ]
        for index, (label, widget) in enumerate(fields[:2]):
            row, pair = divmod(index, 3)
            grid.addWidget(self._parameter_field(label, widget), row, pair)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        self.parameter_widgets = [widget for _label, widget in fields] + [
            self.source_combo, self.path_edit, self.file_button, self.folder_button]
        form.addLayout(grid)
        self.advanced_frame = QWidget()
        advanced_grid = QGridLayout(self.advanced_frame)
        advanced_grid.setContentsMargins(0, 0, 0, 0)
        advanced_grid.setHorizontalSpacing(20)
        for index, (label, widget) in enumerate(fields[2:]):
            row, pair = divmod(index, 3)
            advanced_grid.addWidget(self._parameter_field(label, widget), row, pair)
        for column in range(3):
            advanced_grid.setColumnStretch(column, 1)
        self.show_block_hits = QCheckBox("显示命中块列")
        advanced_grid.addWidget(self.show_block_hits, advanced_grid.rowCount(), 0)
        self.welch_diagnostic = QLabel("Welch 信息：—")
        self.welch_diagnostic.setWordWrap(True)
        advanced_grid.addWidget(self.welch_diagnostic, advanced_grid.rowCount(), 0, 1, 3)
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
        self.advanced_button = QPushButton("高级分析参数")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self.advanced_frame.setVisible)
        self.parameter_widgets.append(self.advanced_button)
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
        table_host = QWidget()
        table_layout = QVBoxLayout(table_host)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)
        filters = QHBoxLayout()
        self.plane_filter = QComboBox()
        for label, value in (("全部", ""), ("X", "X"), ("Y", "Y")):
            self.plane_filter.addItem(label, value)
        self.plane_filter.setFixedWidth(75)
        self.state_filter = QComboBox()
        self.state_filter.addItem("全部状态", "")
        for value in DISPLAY_STATUS_LABELS:
            self.state_filter.addItem(LABELS[value], value)
        self.state_filter.setFixedWidth(130)
        self.result_count = QLabel("显示 0 个")
        filters.addWidget(QLabel("方向"))
        filters.addWidget(self.plane_filter)
        filters.addSpacing(8)
        filters.addWidget(QLabel("状态"))
        filters.addWidget(self.state_filter)
        filters.addStretch(1)
        filters.addWidget(self.result_count)
        table_layout.addLayout(filters)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("通道", "状态", "谱峰（Hz）", "谱峰背景比", "命中块"))
        self.table.setColumnHidden(4, True)
        self.show_block_hits.toggled.connect(lambda checked: self.table.setColumnHidden(4, not checked))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setToolTip("点击表头切换升序或降序；谱峰、谱峰背景比和命中块按数值排序")
        self.table.setMinimumWidth(300)
        table_layout.addWidget(self.table, 1)
        splitter.addWidget(table_host)
        plot_host = QWidget()
        plot_layout = QVBoxLayout(plot_host)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_controls = QHBoxLayout()
        self.display_mode = QComboBox()
        self.display_mode.addItem("原始", "linear")
        self.display_mode.addItem("Log", "log")
        self.display_mode.setToolTip(
            "原始：PSD 原始数值，使用线性 Y 轴；不是原始时间域波形。\n"
            "Log：同一份 PSD 数据，使用对数 Y 轴。"
        )
        plot_controls.addWidget(QLabel("显示模式"))
        plot_controls.addWidget(self.display_mode)
        plot_controls.addStretch(1)
        plot_layout.addLayout(plot_controls)
        self.figure = Figure(figsize=(7, 4), facecolor="white", constrained_layout=True)
        self.canvas = FigureCanvas(self.figure)
        plot_layout.addWidget(self.canvas, 1)
        self.detail_label = QLabel("选择一个结果通道查看频谱与质量信息。")
        self.detail_label.setWordWrap(True)
        plot_layout.addWidget(self.detail_label)
        splitter.addWidget(plot_host)
        splitter.setSizes([640, 650])
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
        self.display_mode.currentIndexChanged.connect(self._render_selection)
        self.plane_filter.currentIndexChanged.connect(self._refresh_result_table)
        self.state_filter.currentIndexChanged.connect(self._refresh_result_table)
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
        self.history_request_id = None
        self.history_capture_sequence = None
        self.path_edit.clear()
        self.sample_rate.setValue(0)
        self.sample_rate.setSpecialValueText("读取文件记录")
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
        self.history_request_id = None
        self.history_capture_sequence = None
        self.sample_rate.setValue(0)
        self.sample_rate.setSpecialValueText("读取文件记录")

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

    def reset_history_context(self):
        """Discard history analysis, including late results of a cancelled job."""
        if self.alignment is None:
            self.history_request_id = None
            self.history_capture_sequence = None
            return
        if self.job_id:
            self.jobs.cancel(self.job_id)
            self.job_id = None
            self.timer.stop()
        self.alignment = None
        self.history_request_id = None
        self.history_capture_sequence = None
        self.last_run = None
        self.path_edit.clear()
        self.sample_rate.setValue(0)
        self.input_note.setText("历史数据段已切换，请重新接收当前历史对齐结果。")
        self.table.setRowCount(0)
        self.result_count.setText("显示 0 个")
        self.figure.clear()
        self.canvas.draw_idle()
        self.export_button.setEnabled(False)
        self.plot_export_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        for widget in self.parameter_widgets:
            widget.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("历史数据段已切换，请重新选择分析输入。")

    def use_alignment(self, result, *, sample_rate_hz=None, history_request_id=None,
                      capture_sequence=None):
        if self.job_id:
            raise ValueError("当前分析仍在运行，请先等待完成或取消")
        self.source_combo.setCurrentIndex(0)
        self.alignment = result
        self.history_request_id = history_request_id
        self.history_capture_sequence = capture_sequence
        self.path_edit.setText(f"当前历史对齐结果：{len(result.device_ids)} 台 BPM")
        self.sample_rate.setSpecialValueText("未提供")
        if (isinstance(sample_rate_hz, Real) and not isinstance(sample_rate_hz, bool)
                and math.isfinite(sample_rate_hz) and sample_rate_hz > 0):
            self.sample_rate.setMaximum(max(self.sample_rate.maximum(), sample_rate_hz))
            self.sample_rate.setValue(sample_rate_hz)
            self.input_note.setText(
                f"已接收当前历史对齐结果；本次采样率已从历史任务记录读取：{sample_rate_hz:g} Hz。")
        else:
            self.sample_rate.setValue(0)
            self.input_note.setText(
                "已接收当前历史对齐结果；历史记录没有可用采样率，请填写该次采集的实际采样率。")

    def _config(self):
        return AnalysisConfig.for_profile(
            self.profile_combo.currentData(), search_low_hz=self.low.value(),
            search_high_hz=self.high.value(), background_low_hz=self.background_low.value(),
            background_high_hz=self.background_high.value(), welch_seconds=self.window_seconds.value(),
            block_seconds=self.block_seconds.value(), threshold_db=self.threshold.value(),
            minimum_blocks=self.minimum_blocks.value(), minimum_block_ratio=self.block_ratio.value(),
            peak_match_hz=self.peak_match.value(), zero_policy=self.zero_combo.currentData(),
            minimum_seconds=self.minimum_seconds.value(), minimum_points=self.minimum_points.value())

    def analysis_config_after_history_handoff(self):
        """Return the config that will be active after history selects HDF5."""
        if self.source_combo.currentData() == "archiver":
            # Switching from Archiver to HDF5 selects the high-band profile.
            # If it is already selected, _source_changed will not reapply the
            # profile, so preserve the user's current parameter edits.
            if self.profile_combo.currentData() != "high_band":
                return AnalysisConfig.for_profile(
                    "high_band", zero_policy=self.zero_combo.currentData()
                )
        return self._config()

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
        self.result_count.setText("显示 0 个")
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
        self._refresh_result_table()
        valid = sum(c.classification != "invalid" for c in run.channels)
        clear_peaks = sum(c.display_status == "clear_peak" for c in run.channels)
        probable_peaks = sum(c.display_status == "probable_peak" for c in run.channels)
        self.status_label.setText(
            f"分析完成：{valid}/{len(run.channels)} 个通道可分析，其中 {clear_peaks} 个谱峰明显，"
            f"{probable_peaks} 个谱峰较明显。可保存本次结果包。"
        )
        self.status_label.setToolTip(str(run.directory))
        self.export_button.setEnabled(True)

    @staticmethod
    def _plane(channel):
        plane = str(channel.metadata.get("plane", "")).upper()
        if plane in {"X", "Y"}:
            return plane
        name = channel.channel.upper()
        return next((plane for plane in ("X", "Y") if name.endswith(("_" + plane, ":" + plane))), "")

    def _refresh_result_table(self, *_args):
        if self.last_run is None:
            return
        current = self.table.item(self.table.currentRow(), 0)
        selected = current.data(Qt.UserRole) if current is not None else None
        plane, state = self.plane_filter.currentData(), self.state_filter.currentData()
        rows = [(index, c) for index, c in enumerate(self.last_run.channels)
                if (not plane or self._plane(c) == plane)
                and (not state or c.display_status == state)]
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for row, (index, channel) in enumerate(rows):
            values = [channel.channel, LABELS[channel.display_status],
                      format_peak_frequency(channel.peak_hz, channel.resolution_hz),
                      "—" if channel.peak_to_background_db is None else f"{channel.peak_to_background_db:.2f} dB",
                      f"{channel.block_hits}/{channel.block_count}"]
            keys = [None, None, channel.peak_hz, channel.peak_to_background_db,
                    (channel.block_hits, channel.block_ratio or 0, channel.block_count)]
            for col, text in enumerate(values):
                item = NumericResultItem(text, keys[col]) if col >= 2 else QTableWidgetItem(text)
                item.setData(Qt.UserRole, index)
                item.setToolTip(channel.reason + "\n" + "\n".join(channel.warnings))
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.result_count.setText(f"显示 {len(rows)}/{len(self.last_run.channels)} 个")
        selected_row = next((row for row in range(len(rows))
                             if self.table.item(row, 0).data(Qt.UserRole) == selected), 0)
        if rows:
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self._render_selection()

    def _render_selection(self):
        row = self.table.currentRow()
        if self.last_run is None:
            return
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            self._refresh_welch_diagnostic(None)
            self.figure.clear()
            self.canvas.draw_idle()
            self.detail_label.setText("当前筛选条件下没有通道。")
            self.plot_export_button.setEnabled(False)
            return
        channel = self.last_run.channels[item.data(Qt.UserRole)]
        self._refresh_welch_diagnostic(channel)
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        if len(channel.frequency):
            psd = np.asarray(channel.psd)
            logarithmic = self.display_mode.currentData() == "log"
            if logarithmic:
                axis.set_yscale("log")
                plot_psd = np.ma.masked_where(~np.isfinite(psd) | (psd <= 0), psd)
            else:
                plot_psd = psd
            axis.plot(channel.frequency, plot_psd, color="#357fb5", linewidth=1.4)
            cfg = self.last_run.manifest["config"]
            axis.axvspan(cfg["search_low_hz"], cfg["search_high_hz"], color="#458cc4", alpha=0.12)
            axis.set_xlim(cfg["background_low_hz"], cfg["background_high_hz"])
            if channel.peak_hz is not None:
                axis.axvline(channel.peak_hz, color="#c78136", linewidth=1, linestyle="--")
                peak_power = float(np.interp(channel.peak_hz, channel.frequency, channel.psd))
                if not logarithmic or (np.isfinite(peak_power) and peak_power > 0):
                    axis.scatter([channel.peak_hz], [peak_power],
                                 color="#c78136", s=22, zorder=4)
                left = channel.peak_hz <= (cfg["background_low_hz"] + cfg["background_high_hz"]) / 2
                peak_display = format_peak_frequency(channel.peak_hz, channel.resolution_hz)
                axis.annotate(rf"$f_{{\mathrm{{peak}}}}={peak_display}\;\mathrm{{Hz}}$",
                              xy=(channel.peak_hz, .97), xycoords=("data", "axes fraction"),
                              xytext=(6 if left else -6, 0), textcoords="offset points",
                              ha="left" if left else "right", va="top", fontsize=10, color="#a96822",
                              bbox={"facecolor": "white", "edgecolor": "none", "alpha": .9, "pad": 2})
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
            metrics = f"采样率 {channel.sample_rate_hz:g} Hz；记录跨度 {duration} 秒。\n"
        self.detail_label.setText(metrics + channel.reason)
        self.detail_label.setToolTip("\n".join(channel.warnings))

    @staticmethod
    def _diagnostic_number(value, *, decimals=6):
        if isinstance(value, bool):
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return "—"
        if not math.isfinite(number) or number <= 0:
            return "—"
        return f"{number:.{decimals}f}".rstrip("0").rstrip(".")

    def _refresh_welch_diagnostic(self, channel):
        config = self.last_run.manifest.get("config", {}) if self.last_run else {}
        requested = self._diagnostic_number(config.get("welch_seconds"))
        details = (channel.details if channel is not None
                   and isinstance(channel.details, dict) else {})
        actual = self._diagnostic_number(details.get("actual_welch_seconds"))
        resolution = self._diagnostic_number(channel.resolution_hz if channel is not None else None)
        self.welch_diagnostic.setText(
            f"设置 Welch 窗口：{requested} s；实际 Welch 窗口：{actual} s；"
            f"频率分辨率：{resolution} Hz"
        )

    def _export(self):
        if self.last_run is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存本次分析结果包", self._default_results_filename(), "ZIP (*.zip)"
        )
        if path:
            try:
                shutil.copy2(self.last_run.directory / "results.zip", path)
                self.status_label.setText("结果包已保存：" + path)
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    @staticmethod
    def _safe_export_stem(value):
        safe = re.sub(r'[\x00-\x1f\x7f-\x9f\\/:*?"<>|]', "_", str(value).strip())
        safe = safe.rstrip(" .")
        return "" if not safe or safe in {".", ".."} else safe

    def _default_results_filename(self):
        fallback = "spectrum-results.zip"
        if self.last_run is None:
            return fallback
        request_id = self.history_request_id
        sequence = self.history_capture_sequence
        if (self.alignment is not None and request_id is not None and str(request_id).strip()
                and isinstance(sequence, int) and not isinstance(sequence, bool) and sequence >= 0):
            stem = self._safe_export_stem(f"{request_id}_S{sequence:05d}")
        else:
            raw_path = self.path_edit.text().strip()
            if not raw_path:
                return fallback
            source = Path(raw_path).expanduser()
            try:
                if source.is_file():
                    stem = self._safe_export_stem(source.stem)
                elif source.is_dir():
                    stem = self._safe_export_stem(source.name)
                else:
                    return fallback
            except OSError:
                return fallback
        return f"{stem}_频谱结果.zip" if stem else fallback

    def _export_plot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存当前通道频谱图", self._default_plot_filename(), "PNG (*.png)"
        )
        if path:
            try:
                self.figure.savefig(path, dpi=180, bbox_inches="tight")
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    def _default_plot_filename(self):
        if self.last_run is None:
            return "spectrum.png"
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            return "spectrum.png"
        index = item.data(Qt.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self.last_run.channels):
            return "spectrum.png"
        channel = self.last_run.channels[index].channel
        if channel is None:
            return "spectrum.png"
        safe_name = re.sub(r'[\x00-\x1f\\/:*?"<>|]', "_", str(channel).strip())
        safe_name = safe_name.rstrip(" .")
        if not safe_name or safe_name in {".", ".."}:
            return "spectrum.png"
        suffix = "_Log" if self.display_mode.currentData() == "log" else "_原始"
        return f"{safe_name}{suffix}.png"

    def shutdown(self):
        self._closed = True
        self.timer.stop()
        if self.jobs is not None:
            self.jobs.shutdown(wait=False)
