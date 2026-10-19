"""Formal operator window for beamline-area selection and waveform work."""

from __future__ import annotations

import json
import re
from zoneinfo import ZoneInfo
from datetime import date, datetime
from pathlib import Path
from typing import cast
from time import monotonic
from uuid import UUID

from PyQt5.QtCore import QDate, QModelIndex, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QFileDialog,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from waveform_storage.config.registry import DeviceRegistry
from waveform_storage.gui.beamline_selection import (
    BeamlineArea,
    build_display_areas,
)
from waveform_storage.gui.error_dialogs import (
    OperationError,
    show_operation_error,
)
from waveform_storage.gui.operator_contract import OperatorBackend, OperatorMode, CandidateHistoryBackend
from waveform_storage.gui.plotting import (
    plot_alignment_result,
    plot_device_waveform,
)
from waveform_storage.gui.workers import BackgroundWorker
from waveform_storage.gui.spectrum_panel import SpectrumPanel


class BeamlineAreaModel(QStandardItemModel):
    selection_changed = pyqtSignal()

    AREA_ROLE = Qt.UserRole + 1
    DEVICE_ID_ROLE = Qt.UserRole + 2

    def __init__(
        self,
        registry: DeviceRegistry,
        parent: QWidget | None = None,
        capture_registry: DeviceRegistry | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.areas = build_display_areas(registry)
        self.setHorizontalHeaderLabels(("束线区域 / BPM设备", "设备数", "采集PV", "状态"))
        devices = {device.id: device for device in registry.devices}
        approved_ids = (
            {device.id for device in capture_registry.devices
             if device.enabled and device.acquisition_enabled}
            if capture_registry is not None else set(devices)
        )
        self._updating_checks = False

        for area in self.areas:
            area_name = QStandardItem(area.display_name)
            area_name.setData(area.area_id, self.AREA_ROLE)
            area_name.setCheckable(True)
            area_name.setCheckState(Qt.Unchecked)
            area_name.setEditable(False)
            area_name.setFont(QFont("", 10, QFont.DemiBold))
            count = QStandardItem(str(area.device_count))
            pv_count = QStandardItem(str(area.pv_count))
            status = QStandardItem("整组未选择")
            for item in (count, pv_count, status):
                item.setEditable(False)
            self.appendRow((area_name, count, pv_count, status))

            for device_id in area.device_ids:
                device = devices[device_id]
                name = QStandardItem(device.display_name.split("/", 1)[-1])
                name.setData(device.id, self.DEVICE_ID_ROLE)
                name.setCheckable(device.id in approved_ids)
                if device.id in approved_ids:
                    name.setCheckState(Qt.Unchecked)
                name.setEditable(False)
                device_count = QStandardItem("—")
                pv = QStandardItem(device.capture_pv)
                status_labels = {
                    "assumed": "端口待验证",
                    "configured": "已配置",
                    "verified": "端口已验证",
                    "failed": "端口失败",
                }
                device_status = QStandardItem(
                    status_labels.get(
                        device.epics_port_status,
                        device.epics_port_status,
                    )
                )
                if device.acquisition_enabled:
                    device_status.setText(
                        f"{device_status.text()} · 采集启用"
                    )
                for item in (device_count, pv, device_status):
                    item.setEditable(False)
                area_name.appendRow((name, device_count, pv, device_status))

        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._updating_checks:
            return
        area_id = item.data(self.AREA_ROLE)
        self._updating_checks = True
        try:
            if area_id:
                selected = item.checkState() == Qt.Checked
                for row in range(item.rowCount()):
                    child = item.child(row, 0)
                    if child.isCheckable():
                        child.setCheckState(Qt.Checked if selected else Qt.Unchecked)
                parent = item
            elif item.data(self.DEVICE_ID_ROLE):
                parent = item.parent()
            else:
                return
            children = [parent.child(row, 0) for row in range(parent.rowCount())
                        if parent.child(row, 0).isCheckable()]
            checked = sum(child.checkState() == Qt.Checked for child in children)
            state = (Qt.Checked if checked == len(children) and children
                     else Qt.PartiallyChecked if checked else Qt.Unchecked)
            parent.setCheckState(state)
            self.item(parent.row(), 3).setText(
                "已选全部" if state == Qt.Checked else
                f"已选 {checked}/{len(children)}" if checked else "未选择"
            )
            self.item(parent.row(), 3).setForeground(
                QColor("#458cc4" if checked else "#64748b")
            )
        finally:
            self._updating_checks = False
        self.selection_changed.emit()

    def selected_area_ids(self) -> frozenset[str]:
        return frozenset(
            item.data(self.AREA_ROLE)
            for row in range(self.rowCount())
            if (item := self.item(row, 0)).checkState() != Qt.Unchecked
        )

    def selected_area_ids_ordered(self) -> tuple[str, ...]:
        selected = self.selected_area_ids()
        return tuple(
            area.area_id for area in self.areas if area.area_id in selected
        )

    def selected_devices(self) -> tuple[str, ...]:
        return tuple(
            child.data(self.DEVICE_ID_ROLE)
            for area_row in range(self.rowCount())
            for child in (self.item(area_row, 0).child(device_row, 0)
                          for device_row in range(self.item(area_row, 0).rowCount()))
            if child.isCheckable() and child.checkState() == Qt.Checked
        )

    def set_all(self, selected: bool) -> None:
        state = Qt.Checked if selected else Qt.Unchecked
        for row in range(self.rowCount()):
            self.item(row, 0).setCheckState(state)


class BpmSelectionDialog(QDialog):
    """Choose only BPMs with files at the selected historical time."""

    def __init__(
        self,
        available_ids: tuple[str, ...],
        display_names: dict[str, str],
        selected_ids: tuple[str, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择参与对齐的 BPM")
        self.resize(520, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"该采集时刻有 {len(available_ids)} 台 BPM 的波形文件"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 BPM 名称或编号")
        layout.addWidget(self.search)
        self.list_widget = QListWidget()
        selected = set(selected_ids) if selected_ids else set(available_ids)
        for device_id in available_ids:
            item = QListWidgetItem(display_names.get(device_id, device_id))
            item.setData(Qt.UserRole, device_id)
            item.setToolTip(device_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if device_id in selected else Qt.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)
        actions = QHBoxLayout()
        select_all = QPushButton("全选")
        clear_all = QPushButton("清空")
        self.count_label = QLabel()
        actions.addWidget(select_all)
        actions.addWidget(clear_all)
        actions.addStretch(1)
        actions.addWidget(self.count_label)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.search.textChanged.connect(self._filter)
        self.list_widget.itemChanged.connect(self._update_count)
        select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        clear_all.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._update_count()

    def selected_ids(self) -> tuple[str, ...]:
        return tuple(
            item.data(Qt.UserRole)
            for index in range(self.list_widget.count())
            if (item := self.list_widget.item(index)).checkState() == Qt.Checked
        )

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(
                query not in item.text().casefold()
                and query not in item.data(Qt.UserRole).casefold()
            )

    def _set_all(self, state: Qt.CheckState) -> None:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if not item.isHidden():
                item.setCheckState(state)

    def _update_count(self, _item: QListWidgetItem | None = None) -> None:
        count = len(self.selected_ids())
        self.count_label.setText(f"已选 {count} 台")
        self.ok_button.setEnabled(count > 0)


class AlignmentQualityDialog(QDialog):
    """Show only quality facts carried by the current alignment result."""

    def __init__(self, result, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("对齐诊断详情")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"当前结果包含 {int(result.bunch_ids.size)} 个 bunchID，"
            f"涉及 {len(result.device_ids)} 台 BPM。"
        ))
        self.table = QTableWidget(len(result.quality_report), 6, self)
        self.table.setHorizontalHeaderLabels(
            ("BPM", "状态", "原始点数", "可用点数", "相邻编号间隔", "质量记录")
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        for row, quality in enumerate(result.quality_report):
            status = "参与对齐" if quality.status == "usable" else "已跳过"
            steps = ", ".join(str(value) for value in quality.step_values) or "—"
            notes = []
            if quality.zero_count:
                notes.append(f"零值 {quality.zero_count}")
            if quality.duplicate_count:
                notes.append(f"重复值 {quality.duplicate_count}")
            if not quality.was_strictly_increasing:
                notes.append("输入未严格递增（已排序）")
            if quality.reason:
                notes.append(quality.reason)
            values = (
                quality.device_id,
                status,
                str(quality.original_count),
                str(quality.usable_count),
                steps,
                "；".join(notes) if notes else "无异常记录",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class HistoryDiagnosticDialog(QDialog):
    """Show raw preflight evidence beside the strict alignment outcome."""

    def __init__(self, raw_report, result=None, analysis_preview=(),
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("历史数据质量诊断")
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        if raw_report is None:
            layout.addWidget(QLabel("当前后端未提供相邻数据编号的间隔检查。"))
            raw_profiles = ()
            skipped = ()
        else:
            layout.addWidget(QLabel(raw_report.summary))
            raw_profiles = raw_report.devices
            skipped = raw_report.failed_devices

        quality_by_id = {
            item.device_id: item for item in (result.quality_report if result else ())
        }
        if result is not None:
            layout.addWidget(QLabel(
                f"严格交集：{int(result.bunch_ids.size):,} 个共同 bunchID。"
            ))
        row_data = []
        for raw in raw_profiles:
            quality = quality_by_id.get(raw.device_id)
            range_text = (
                f"{raw.minimum_step}–{raw.maximum_step}"
                if raw.minimum_step is not None else "—"
            )
            mode_text = (
                f"{raw.modal_step} ({raw.modal_step_count}/{raw.usable_step_count})"
                if raw.modal_step is not None else "无法确定"
            )
            common = (
                str(int(result.bunch_ids.size))
                if result is not None and raw.device_id in result.device_ids
                else "0" if result is not None else "—"
            )
            notes = []
            if raw.differs_from_majority:
                notes.append("编号间隔与多数设备不一致")
            if raw.issue:
                notes.append(raw.issue)
            if quality and quality.status != "usable":
                notes.append(quality.reason or "未参与对齐")
            row_data.append((raw.device_id, str(raw.raw_count), range_text,
                             mode_text, common, "；".join(notes) or "编号间隔未见异常"))
        for failure in skipped:
            if failure.device_id not in {item[0] for item in row_data}:
                row_data.append((failure.device_id, "—", "—", "—", "—",
                                 f"{failure.reason}：{failure.detail}".rstrip("：")))
        if raw_report:
            for missing_id in raw_report.missing_devices:
                if missing_id not in {item[0] for item in row_data}:
                    row_data.append((missing_id, "—", "—", "—", "—", "未找到波形文件"))

        raw_table = QTableWidget(len(row_data), 6, self)
        raw_table.setHorizontalHeaderLabels(
            ("BPM", "原始点数", "相邻编号间隔范围", "最常见间隔（次数/总数）", "共同可用点数", "诊断")
        )
        raw_table.horizontalHeaderItem(2).setToolTip(
            "相邻采样点的 bunchID 差值，不是时间间隔"
        )
        raw_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        raw_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        raw_table.verticalHeader().setVisible(False)
        for index, mode in enumerate((QHeaderView.ResizeToContents,) * 5 + (QHeaderView.Stretch,)):
            raw_table.horizontalHeader().setSectionResizeMode(index, mode)
        for row, values in enumerate(row_data):
            for column, value in enumerate(values):
                raw_table.setItem(row, column, QTableWidgetItem(value))
        tabs = QTabWidget(self)
        tabs.addTab(raw_table, "编号间隔与共同数据")
        if analysis_preview:
            analysis_table = QTableWidget(len(analysis_preview), 8, self)
            analysis_table.setHorizontalHeaderLabels(
                ("通道", "有效点数", "最低点数", "分析器跨度", "最短时长",
                 "最大间隔", "允许缺口", "质量门")
            )
            analysis_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            analysis_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            analysis_table.verticalHeader().setVisible(False)
            for index in range(7):
                analysis_table.horizontalHeader().setSectionResizeMode(
                    index, QHeaderView.ResizeToContents
                )
            analysis_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
            for row, item in enumerate(analysis_preview):
                gates = getattr(item, "quality", {}).get("preview_gates", {})
                def show(value, unit=""):
                    return "未评估" if value is None else f"{value:g}{unit}"
                gate_status = (
                    "通过" if item.classification == "ready"
                    else f"未通过：{item.reason}" if item.reason else "未评估"
                )
                values = (
                    getattr(item, "channel", "历史对齐"),
                    show(gates.get("valid_points")),
                    show(gates.get("minimum_points")),
                    show(gates.get("span_seconds"), " s"),
                    show(gates.get("minimum_seconds"), " s"),
                    show(gates.get("maximum_gap_seconds"), " s"),
                    show(gates.get("maximum_gap_limit_seconds"), " s"),
                    gate_status,
                )
                for column, value in enumerate(values):
                    analysis_table.setItem(row, column, QTableWidgetItem(value))
            tabs.addTab(analysis_table, "分析器质量门")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class OperatorMainWindow(QMainWindow):
    """Formal operator UI backed by an explicit connected or read-only adapter."""

    def __init__(
        self,
        registry: DeviceRegistry,
        *,
        backend: OperatorBackend,
        capture_registry: DeviceRegistry | None = None,
        ui_only: bool = False,
        journal_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.capture_registry = capture_registry or registry
        visible_devices = {device.id: device for device in registry.devices}
        for device in self.capture_registry.devices:
            if not (device.enabled and device.acquisition_enabled):
                continue
            visible = visible_devices.get(device.id)
            if visible is None or (
                visible.capture_pv,
                visible.epics_host,
                visible.epics_port,
            ) != (
                device.capture_pv,
                device.epics_host,
                device.epics_port,
            ):
                raise ValueError(
                    f"capture device {device.id} is missing or differs "
                    "from the display registry"
                )
        self.backend = backend
        self.ui_only = ui_only
        self.journal_path = (
            Path(journal_path)
            if journal_path is not None
            else Path(__file__).resolve().parents[3] / "logs" / "operator-ui.jsonl"
        )
        self.capture_state_path = self.journal_path.with_name(
            "operator-active-capture.json"
        )
        self.journal_write_error: str | None = None
        self.area_model = BeamlineAreaModel(registry, self, capture_registry)
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers: set[BackgroundWorker] = set()
        self._busy = False
        self._capture_request_id: str | None = None
        self._capture_duration_seconds = 0
        self._capture_started_at = 0.0
        self._capture_poll_failures = 0
        self._capture_status_worker: BackgroundWorker | None = None
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(2000)
        self._capture_timer.timeout.connect(self._poll_capture_status)
        self.alignment_result = None
        self._quality_result = None
        self.history_selected_devices: tuple[str, ...] = ()
        self.history_selection_key: tuple | None = None
        self.history_runs = ()
        self.history_segments = ()
        self.current_history_run = None
        self.current_history_segment = None
        self._history_generation = 0
        self.device_waveform_result = None
        self.device_waveform_key = None
        self._plot_request_generation = 0
        self.plot_canvas = None
        self.setWindowTitle("SHINE Waveform Console")
        self.setMinimumSize(1120, 720)
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1440, 900)
        else:
            available = screen.availableGeometry()
            self.resize(
                min(available.width(), 2160, max(1120, int(available.width() * 0.84))),
                min(available.height(), 1320, max(720, int(available.height() * 0.88))),
            )
        self._build_ui()
        self._bind_analysis_preview_invalidation()
        self._apply_style()
        self._update_selection_summary()
        if self.ui_only:
            self._set_frequency_display("未连接", available=False)
            self.task_state_text.setText("离线查看 · 未连接服务")
            for button in (
                self.start_button,
                self.retrieve_button,
                self.select_bpm_button,
                self.align_button,
                self.show_id_button,
                self.plot_button,
                self.export_button,
                self.raw_button,
            ):
                button.setEnabled(False)
        elif self.backend.mode is OperatorMode.CANDIDATE_V2:
            QTimer.singleShot(0, self._load_initial_frequency)
            QTimer.singleShot(0, self._resume_capture_monitor)

    def _bind_analysis_preview_invalidation(self) -> None:
        panel = self.spectrum_panel
        widgets = (
            panel.source_combo,
            panel.profile_combo,
            panel.sample_rate,
            panel.zero_combo,
            panel.low,
            panel.high,
            panel.background_low,
            panel.background_high,
            panel.window_seconds,
            panel.block_seconds,
            panel.threshold,
            panel.minimum_blocks,
            panel.block_ratio,
            panel.peak_match,
            panel.minimum_seconds,
            panel.minimum_points,
        )
        for widget in widgets:
            signal = getattr(widget, "valueChanged", None)
            if signal is None:
                signal = getattr(widget, "currentIndexChanged", None)
            if signal is not None:
                signal.connect(self._invalidate_analysis_preview)

    def _invalidate_analysis_preview(self, *_args) -> None:
        if self.alignment_result is None:
            return
        self._analysis_preview_key = None
        self._analysis_quality_preview = ()
        self._refresh_history_diagnostic()
        self._update_spectrum_action(self.alignment_result)

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 22, 24)
        side.setSpacing(10)
        brand = QLabel("SHINE")
        brand.setObjectName("brand")
        product = QLabel("Waveform Console")
        product.setObjectName("product")
        side.addWidget(brand)
        side.addWidget(product)
        side.addSpacing(28)
        self.capture_nav = self._nav_button("采集控制", True)
        self.history_nav = self._nav_button("历史检索与绘图", False)
        self.spectrum_nav = self._nav_button("频谱分析", False)
        side.addWidget(self.capture_nav)
        side.addWidget(self.history_nav)
        side.addWidget(self.spectrum_nav)
        side.addStretch(1)
        root.addWidget(sidebar)

        workspace = QWidget()
        workspace.setObjectName("workspace")
        work = QVBoxLayout(workspace)
        work.setContentsMargins(30, 22, 30, 26)
        work.setSpacing(18)
        work.addLayout(self._topbar())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._capture_page())
        self.pages.addWidget(self._history_page())
        self.spectrum_panel = SpectrumPanel(self.journal_path.parent / "spectrum-runs", self)
        self.pages.addWidget(self.spectrum_panel)
        work.addWidget(self.pages, 1)
        work.addWidget(self._task_status_bar())
        root.addWidget(workspace, 1)

        self.capture_nav.clicked.connect(lambda: self._show_page(0))
        self.history_nav.clicked.connect(lambda: self._show_page(1))
        self.spectrum_nav.clicked.connect(lambda: self._show_page(2))

    def _topbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        heading = QVBoxLayout()
        self.page_title = QLabel("采集控制")
        self.page_title.setObjectName("pageTitle")
        subtitle = QLabel("面向大规模BPM设备的波形采集、对齐与分析")
        subtitle.setObjectName("pageSubtitle")
        heading.addWidget(self.page_title)
        heading.addWidget(subtitle)
        layout.addLayout(heading)
        layout.addStretch(1)
        return layout

    def _capture_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addLayout(self._metric_cards())

        content = QHBoxLayout()
        content.setSpacing(16)
        selector = self._card()
        selector_layout = QVBoxLayout(selector)
        selector_layout.setContentsMargins(18, 16, 18, 16)
        selector_layout.setSpacing(12)
        selector_layout.addLayout(self._selector_toolbar())
        self.area_tree = QTreeView()
        self.area_tree.setObjectName("areaTree")
        self.area_tree.setModel(self.area_model)
        self.area_tree.setRootIsDecorated(True)
        self.area_tree.setAlternatingRowColors(True)
        self.area_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.area_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.area_tree.setUniformRowHeights(True)
        self.area_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.area_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.area_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.area_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.area_tree.collapseAll()
        selector_layout.addWidget(self.area_tree, 1)
        content.addWidget(selector, 3)

        control = self._card()
        control.setFixedWidth(330)
        control_layout = QVBoxLayout(control)
        control_layout.setContentsMargins(22, 20, 22, 20)
        control_layout.setSpacing(14)
        title = QLabel("采集参数")
        title.setObjectName("cardTitle")
        control_layout.addWidget(title)
        control_layout.addWidget(self._field_label("采集时长"))
        self.duration_combo = QComboBox()
        for minutes in (1, 10, 30, 60, 70):
            self.duration_combo.addItem(f"{minutes} 分钟", minutes)
        self.duration_combo.setCurrentIndex(4)
        control_layout.addWidget(self.duration_combo)
        control_layout.addWidget(self._field_label("采集频率"))
        self.frequency_combo = QComboBox()
        self.frequency_combo.addItem("跟随当前频率 PV", None)
        for hz in (1000, 500, 100, 50, 5, 2):
            self.frequency_combo.addItem(f"{hz} Hz", float(hz))
        self.frequency_combo.setToolTip("指定频率须与服务端实时频率 PV 一致")
        control_layout.addWidget(self.frequency_combo)
        frequency_display = QFrame()
        frequency_display.setObjectName("frequencyDisplay")
        frequency_layout = QHBoxLayout(frequency_display)
        frequency_layout.setContentsMargins(14, 8, 14, 8)
        frequency_layout.addWidget(self._field_label("当前频率"))
        frequency_layout.addStretch(1)
        self.frequency_value = QLabel("读取中…")
        self.frequency_value.setObjectName("frequencyValue")
        self.frequency_value.setProperty("available", False)
        self.frequency_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.frequency_value.setToolTip("程序启动时读取；采集前会重新核对")
        frequency_layout.addWidget(self.frequency_value)
        control_layout.addWidget(frequency_display)
        control_layout.addStretch(1)
        self.selection_text = QLabel()
        self.selection_text.setObjectName("selectionText")
        control_layout.addWidget(self.selection_text)
        self.start_button = QPushButton(
            "运行演示采集"
            if self.backend.mode is OperatorMode.DEMO
            else "开始采集"
        )
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self._request_start)
        control_layout.addWidget(self.start_button)
        content.addWidget(control)
        layout.addLayout(content, 1)
        return page

    def _history_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("historyPage")
        self._raw_step_warning_dialog = None
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = self._card()
        toolbar.setFixedHeight(152)
        form = QVBoxLayout(toolbar)
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(8)

        task_row = QHBoxLayout()
        task_row.setContentsMargins(0, 0, 0, 0)
        task_row.setSpacing(10)
        date_search = QWidget(toolbar)
        date_search_layout = QHBoxLayout(date_search)
        date_search_layout.setContentsMargins(0, 0, 0, 0)
        date_search_layout.setSpacing(8)
        self.history_date = QDateEdit(QDate.currentDate())
        self.history_date.setCalendarPopup(True)
        self.history_date.setDisplayFormat("yyyy-MM-dd")
        self.history_date.setFixedWidth(156)
        self.history_time = QComboBox()
        self.history_time.setMinimumContentsLength(20)
        self.history_time.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.history_time.addItem("先检索可用时刻")
        self.history_run_combo = self.history_time
        date_search_layout.addWidget(self.history_date)
        self.segment_index = QSpinBox()
        self.segment_index.setRange(0, 0)
        self.segment_index.setFixedWidth(104)
        self.segment_index.setAlignment(Qt.AlignCenter)
        self.segment_index.setToolTip("可直接编辑当前段号并回车跳转")
        self.previous_segment_button = QPushButton("‹")
        self.previous_segment_button.setFixedWidth(48)
        self.previous_segment_button.setToolTip("上一段")
        self.next_segment_button = QPushButton("›")
        self.next_segment_button.setFixedWidth(48)
        self.next_segment_button.setToolTip("下一段")
        self.segment_label = QLabel("尚未选择数据段")
        self.segment_label.setObjectName("segmentSummary")
        self.previous_segment_button.setEnabled(False)
        self.next_segment_button.setEnabled(False)
        self.retrieve_button = QPushButton(
            "检索任务" if self.backend.mode is OperatorMode.CANDIDATE_V2 else "检索数据"
        )
        date_search_layout.addWidget(self.retrieve_button)
        task_row.addWidget(date_search)
        task_row.addWidget(self.history_time)
        task_row.addStretch(1)

        segment_row = QHBoxLayout()
        segment_row.setContentsMargins(0, 0, 0, 0)
        segment_row.setSpacing(12)
        segment_controls = QWidget(toolbar)
        segment_controls_layout = QHBoxLayout(segment_controls)
        segment_controls_layout.setContentsMargins(0, 0, 0, 0)
        segment_controls_layout.setSpacing(8)
        segment_controls_layout.addWidget(self.previous_segment_button)
        segment_controls_layout.addWidget(self.segment_index)
        segment_controls_layout.addWidget(self.next_segment_button)
        self.select_bpm_button = QPushButton("BPM 0/0")
        self.select_bpm_button.setObjectName("bpmSelectButton")
        self.select_bpm_button.setMinimumWidth(120)
        self.align_button = QPushButton("确认并对齐")
        self.align_button.setObjectName("primaryButton")
        self.align_button.setFixedWidth(148)
        bpm_controls = QWidget(toolbar)
        bpm_controls_layout = QHBoxLayout(bpm_controls)
        bpm_controls_layout.setContentsMargins(0, 0, 0, 0)
        bpm_controls_layout.setSpacing(8)
        bpm_controls_layout.addWidget(self.select_bpm_button)
        bpm_controls_layout.addWidget(self.align_button)
        segment_row.addWidget(segment_controls)
        segment_row.addWidget(bpm_controls)
        segment_row.addStretch(1)

        form.addLayout(task_row)
        form.addLayout(segment_row)
        form.addWidget(self.segment_label)

        self.bunch_index = QSpinBox()
        self.bunch_index.setMaximum(9999)
        self.bunch_index.setPrefix("定位序号 ")
        self.bunch_index.setFixedWidth(116)
        self.bunch_index.setToolTip(
            "对齐结果中的位置序号；0 表示第一个 bunchID"
        )
        self.show_id_button = QPushButton("定位")
        self.show_id_button.setMinimumWidth(60)
        self.retrieve_button.clicked.connect(self._retrieve_times)
        self.select_bpm_button.clicked.connect(self._select_history_devices)
        self.align_button.clicked.connect(self._align_selected)
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            self.history_time.setItemText(0, "先检索采集任务")
            self.history_date.dateChanged.connect(self._history_date_changed)
            self.history_time.currentIndexChanged.connect(self._history_run_changed)
            self.segment_index.valueChanged.connect(self._history_segment_changed)
            self.previous_segment_button.clicked.connect(
                lambda: self.segment_index.setValue(self.segment_index.value() - 1))
            self.next_segment_button.clicked.connect(
                lambda: self.segment_index.setValue(self.segment_index.value() + 1))
        else:
            for widget in (self.previous_segment_button, self.segment_index,
                           self.next_segment_button, self.segment_label):
                widget.hide()
            self.history_date.dateChanged.connect(self._clear_history_selection)
            self.history_time.currentIndexChanged.connect(self._clear_history_selection)
        self.bunch_index.valueChanged.connect(self._show_bunch_id)
        self.show_id_button.clicked.connect(self._locate_bunch)
        layout.addWidget(toolbar, 1)

        self.history_selection_text = QLabel("尚未选择参与对齐的 BPM")
        self.history_selection_text.setObjectName("selectionText")
        self._history_available_device_count = 0
        self._prepared_history_snapshot = None
        self._prepared_history_key = None
        self._raw_step_report = None
        self._preflight_confirmation_required = False
        self._analysis_quality_preview = ()
        self._analysis_preview_key = None
        self.history_diagnostic_bar = QFrame()
        self.history_diagnostic_bar.setObjectName("historyDiagnosticBar")
        diagnostic_layout = QVBoxLayout(self.history_diagnostic_bar)
        diagnostic_layout.setContentsMargins(12, 7, 12, 7)
        diagnostic_layout.setSpacing(2)
        diagnostic_main_row = QHBoxLayout()
        diagnostic_main_row.setContentsMargins(0, 0, 0, 0)
        diagnostic_main_row.setSpacing(8)
        self.history_diagnostic_label = QLabel("尚未检查历史数据")
        self.history_diagnostic_label.setObjectName("historyDiagnosticText")
        self.history_diagnostic_label.setWordWrap(False)
        self.history_diagnostic_button = QPushButton("设备详情")
        self.history_diagnostic_button.clicked.connect(self._show_history_diagnostics)
        diagnostic_main_row.addWidget(self.history_diagnostic_label, 1)
        self.history_diagnostic_evidence_label = QLabel("")
        self.history_diagnostic_evidence_label.setObjectName(
            "historyDiagnosticEvidence"
        )
        self.history_diagnostic_evidence_label.setWordWrap(False)
        diagnostic_layout.addLayout(diagnostic_main_row)
        diagnostic_layout.addWidget(self.history_diagnostic_evidence_label)
        self.history_diagnostic_bar.hide()
        layout.addWidget(self.history_diagnostic_bar)

        plot = self._card()
        plot.setMinimumHeight(300)
        plot_layout = QVBoxLayout(plot)
        plot_layout.setContentsMargins(18, 13, 18, 14)
        plot_layout.setSpacing(10)
        plot_head = QGridLayout()
        plot_head.setContentsMargins(0, 0, 0, 0)
        plot_head.setHorizontalSpacing(8)
        plot_head.setVerticalSpacing(8)
        plot_left = QHBoxLayout()
        plot_left.setContentsMargins(0, 0, 0, 0)
        plot_left.setSpacing(8)
        self.plot_title = QLabel("BPM位置分布")
        self.plot_title.setObjectName("cardTitle")
        plot_left.addWidget(self.plot_title)
        self.plot_mode = QComboBox()
        self.plot_mode.setObjectName("plotMode")
        self.plot_mode.addItem("束线分布", "distribution")
        self.plot_mode.addItem("单设备波形", "device")
        self.plot_mode.setFixedWidth(100)
        self.plot_device = QComboBox()
        self.plot_device.setObjectName("plotDevice")
        self.plot_device.setMinimumWidth(138)
        self.plot_device.setMaximumWidth(168)
        self.plot_device.setEnabled(False)
        self.plot_device.setVisible(False)
        self.plot_device.addItem("对齐后选择BPM", None)
        plot_left.addWidget(self.plot_mode)
        plot_left.addWidget(self.plot_device)
        self.bunch_id_label = QLabel("尚未选择 bunchID")
        self.bunch_id_label.setObjectName("bunchIdPill")
        self.data_duration_label = QLabel("数据时长：—")
        self.data_duration_label.setToolTip(
            "来自分析器建立的相对时间轴；不是 bunchID 数值换算结果"
        )
        self.alignment_status_label = QLabel("未对齐")
        self.alignment_status_label.setObjectName("alignmentStatus")
        self.quality_status_button = QPushButton("质量详情")
        self.quality_status_button.setObjectName("qualityStatus")
        self.quality_status_button.setToolTip("打开当前对齐结果的设备质量记录")
        self.quality_status_button.setEnabled(False)
        self.quality_status_button.setVisible(False)
        self.quality_status_button.clicked.connect(self._show_alignment_quality)
        plot_left.addWidget(self.alignment_status_label)
        plot_left.addWidget(self.bunch_index)
        plot_left.addWidget(self.show_id_button)
        plot_details = QHBoxLayout()
        plot_details.setContentsMargins(0, 0, 0, 0)
        plot_details.setSpacing(8)
        plot_details.addWidget(self.quality_status_button)
        self.history_diagnostic_button.setText("设备详情")
        self.history_diagnostic_button.setObjectName("quietAction")
        self.history_diagnostic_button.setToolTip("查看相邻编号间隔、共同数据和频谱预检查详情")
        self.history_diagnostic_button.setEnabled(False)
        plot_details.addWidget(self.history_diagnostic_button)
        plot_left.addStretch(1)
        plot_right = QHBoxLayout()
        plot_right.setContentsMargins(0, 0, 0, 0)
        plot_right.setSpacing(8)
        self.plot_button = QPushButton("刷新绘图")
        self.export_button = QPushButton("导出对齐数据")
        self.raw_button = QPushButton("下载原始数据")
        self.history_spectrum_button = QPushButton("频谱分析")
        self.plot_button.setToolTip("刷新绘图")
        self.export_button.setToolTip("导出对齐数据")
        self.raw_button.setToolTip("下载原始数据")
        self.history_spectrum_button.setToolTip("使用当前对齐数据进行频谱分析")
        self.history_spectrum_button.setObjectName("spectrumAction")
        plot_right.addWidget(self.plot_button)
        plot_right.addWidget(self.export_button)
        plot_right.addWidget(self.raw_button)
        plot_right.addWidget(self.history_spectrum_button)
        self._history_plot_header = plot_head
        self._history_plot_controls = plot_left
        self._history_plot_details = plot_details
        self._history_plot_actions = plot_right
        self.plot_button.clicked.connect(self._render_selected_plot)
        self.export_button.clicked.connect(self._export_alignment)
        self.raw_button.clicked.connect(self._download_raw)
        self.history_spectrum_button.clicked.connect(self._analyze_history_alignment)
        self.plot_mode.currentIndexChanged.connect(self._plot_mode_changed)
        self.plot_device.currentIndexChanged.connect(
            lambda _index: self._plot_device_changed()
        )
        plot_layout.addLayout(plot_head)
        self.plot_host = QWidget()
        self.plot_host.setObjectName("plotHost")
        self.plot_layout = QVBoxLayout(self.plot_host)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_placeholder = QLabel(
            "选择历史数据后，在这里显示 X / Y 随 BPM 设备的分布"
        )
        self.plot_placeholder.setObjectName("plotPlaceholder")
        self.plot_placeholder.setAlignment(Qt.AlignCenter)
        self.plot_layout.addWidget(self.plot_placeholder, 1)
        plot_layout.addWidget(self.plot_host, 1)
        layout.addWidget(plot, 3)
        self._set_compact_history_toolbar(self.width() <= 1920)
        return page

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "history_spectrum_button"):
            self._set_compact_history_toolbar(event.size().width() <= 1920)

    def _set_compact_history_toolbar(self, compact: bool) -> None:
        narrow = self.width() < 1280
        if (getattr(self, "_compact_history_toolbar", None) == compact
                and getattr(self, "_narrow_history_controls", None) == narrow):
            return
        self._compact_history_toolbar = compact
        self._narrow_history_controls = narrow
        self.history_date.setFixedWidth(136)
        self.segment_index.setFixedWidth(94)
        self.previous_segment_button.setFixedWidth(38)
        self.next_segment_button.setFixedWidth(38)
        self.select_bpm_button.setMinimumWidth(104)
        self.align_button.setFixedWidth(140)
        header = self._history_plot_header
        for group in (self._history_plot_controls, self._history_plot_details,
                      self._history_plot_actions):
            header.removeItem(group)
        if compact:
            header.addLayout(self._history_plot_controls, 0, 0, 1, 3)
            header.addLayout(self._history_plot_details, 1, 0)
            header.addLayout(self._history_plot_actions, 1, 1, 1, 2, Qt.AlignRight)
        else:
            header.addLayout(self._history_plot_controls, 0, 0)
            header.addLayout(self._history_plot_details, 0, 1)
            header.addLayout(self._history_plot_actions, 0, 2, Qt.AlignRight)
        header.setColumnStretch(2, 1)
        self.plot_title.setVisible(not narrow)
        self.plot_mode.setFixedWidth(116)
        self.plot_device.setFixedWidth(224)
        self.bunch_id_label.setMaximumWidth(138 if compact else 200)
        self.bunch_index.setPrefix("序号 " if compact else "定位序号 ")
        self.bunch_index.setFixedWidth(122 if compact else 150)
        self.show_id_button.setMinimumWidth(62)
        for button, full_text, compact_text, width in (
            (self.plot_button, "刷新绘图", "刷新", 64),
            (self.export_button, "导出对齐数据", "导出", 64),
            (self.raw_button, "下载原始数据", "原始数据", 92),
            (self.history_spectrum_button, "频谱分析", "频谱", 84),
        ):
            warning = full_text == "频谱分析" and "⚠" in button.text()
            button.setText((compact_text if compact else full_text) + (" ⚠" if warning else ""))
            if compact:
                button.setFixedWidth(width)
            else:
                button.setMinimumWidth(0)
                button.setMaximumWidth(16777215)
            button.setToolTip(full_text + ("；当前数据可能不足" if warning else ""))
        if self._quality_result is not None:
            self.quality_status_button.setText(
                self._quality_status_text(self._quality_result, compact=compact)
            )
        self.history_diagnostic_button.setText("设备详情")
        self.history_diagnostic_button.setMinimumWidth(84)
        self.alignment_status_label.setVisible(
            not (compact and self.plot_mode.currentData() == "device")
        )
        if self.alignment_result is not None:
            self._show_bunch_id()
        else:
            self.bunch_id_label.setText(
                "bunchID —" if compact else "尚未选择 bunchID"
            )
        self.data_duration_label.setText(self._duration_status_text())
        if self.plot_mode.currentData() == "device" and self.device_waveform_result is not None:
            count = int(self.device_waveform_result.bunch_ids.size)
            self.alignment_status_label.setText(
                f"单设备 {count:,} 点" if compact
                else f"单设备波形 · {count:,} 点"
            )
        elif self.alignment_result is not None:
            self.alignment_status_label.setText(
                self._alignment_status_text(self.alignment_result)
            )

    def _duration_status_text(self) -> str:
        preview = getattr(self, "_analysis_quality_preview", ())
        durations = [
            item.duration_seconds for item in preview
            if item.duration_seconds is not None
        ]
        if not durations:
            return "分析器推算跨度：—"
        low, high = min(durations), max(durations)
        if abs(high - low) < 1e-9:
            return f"分析器推算跨度：{low:.3f} s"
        return f"分析器推算跨度：{low:.3f}–{high:.3f} s"

    def _alignment_status_text(self, result) -> str:
        count = int(result.bunch_ids.size)
        return f"已对齐 · {count:,} 点"

    def _task_status_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("taskStatusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        self.task_state_dot = QLabel("●")
        self.task_state_dot.setObjectName("taskStateDot")
        self.task_state_text = QLabel("就绪")
        self.task_state_text.setObjectName("taskStateText")
        self.task_progress = QProgressBar()
        self.task_progress.setObjectName("taskProgress")
        self.task_progress.setTextVisible(False)
        self.task_progress.setFixedWidth(150)
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        layout.addWidget(self.task_state_dot)
        layout.addWidget(self.task_state_text)
        layout.addStretch(1)
        layout.addWidget(self.task_progress)
        return bar

    def _metric_cards(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)
        self.area_metric = self._metric("束线区域", str(len(self.area_model.areas)))
        self.device_metric = self._metric(
            "注册BPM", str(sum(area.device_count for area in self.area_model.areas))
        )
        self.selected_area_metric = self._metric("已选区域", "0")
        self.selected_device_metric = self._metric("已选BPM", "0")
        for card in (
            self.area_metric,
            self.device_metric,
            self.selected_area_metric,
            self.selected_device_metric,
        ):
            layout.addWidget(card)
        return layout

    def _selector_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        title = QLabel("束线区域与设备")
        title.setObjectName("cardTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索区域、设备或PV")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        all_button = QPushButton("全选区域")
        none_button = QPushButton("清空")
        all_button.clicked.connect(lambda: self.area_model.set_all(True))
        none_button.clicked.connect(lambda: self.area_model.set_all(False))
        self.search.textChanged.connect(self._filter_tree)
        self.area_model.selection_changed.connect(self._update_selection_summary)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(self.search)
        layout.addWidget(all_button)
        layout.addWidget(none_button)
        return layout

    def _metric(self, label: str, value: str) -> QFrame:
        card = self._card()
        card.setProperty("metric", True)
        body = QVBoxLayout(card)
        body.setContentsMargins(17, 13, 17, 13)
        name = QLabel(label)
        name.setObjectName("metricLabel")
        number = QLabel(value)
        number.setObjectName("metricValue")
        body.addWidget(name)
        body.addWidget(number)
        return card

    @staticmethod
    def _card() -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _nav_button(text: str, checked: bool) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setChecked(checked)
        return button

    def _show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.page_title.setText(("采集控制", "历史检索与绘图", "频谱分析")[index])
        self.page_title.setObjectName("historyPageTitle" if index == 1 else "pageTitle")
        self._refresh_widget_style(self.page_title)
        for page, button in enumerate((self.capture_nav, self.history_nav, self.spectrum_nav)):
            button.setChecked(page == index)

    def _analyze_history_alignment(self) -> None:
        try:
            if self.alignment_result is None or self.alignment_result.bunch_ids.size == 0:
                raise ValueError("请先选择 BPM 并生成历史对齐结果")
            self._refresh_current_analysis_preview()
            self._refresh_history_diagnostic()
            warning = self._spectrum_requirement_warning()
            if warning:
                message = QMessageBox(self)
                message.setIcon(QMessageBox.Warning)
                message.setWindowTitle("当前数据可能不足")
                message.setText(warning)
                details = QPushButton("查看诊断")
                proceed = QPushButton("继续到频谱")
                cancel = QPushButton("留在历史页")
                message.addButton(details, QMessageBox.ActionRole)
                message.addButton(proceed, QMessageBox.AcceptRole)
                message.addButton(cancel, QMessageBox.RejectRole)
                message.setEscapeButton(cancel)
                message.setAttribute(Qt.WA_DeleteOnClose)
                self._spectrum_warning_dialog = message
                message.finished.connect(
                    lambda _result, dialog=message:
                    setattr(self, "_spectrum_warning_dialog", None)
                    if getattr(self, "_spectrum_warning_dialog", None) is dialog
                    else None
                )
                details.clicked.connect(
                    lambda _checked=False: self._show_history_diagnostics()
                )
                result = self.alignment_result
                generation = self._history_generation
                proceed.clicked.connect(
                    lambda _checked=False, result=result, generation=generation:
                    self._continue_to_spectrum(result, generation)
                )
                message.open()
                return
            self._continue_to_spectrum(self.alignment_result, self._history_generation)
        except ValueError as exc:
            QMessageBox.warning(self, "尚无法分析", str(exc))

    def _continue_to_spectrum(self, result, generation: int) -> None:
        if generation != self._history_generation or result is not self.alignment_result:
            return
        try:
            run = self.current_history_run
            segment = self.current_history_segment
            matching_history = (run is not None and segment is not None
                                and segment.request_id == run.request_id)
            sample_rate_hz = run.frequency_hz if matching_history else None
            self.spectrum_panel.use_alignment(
                self.alignment_result, sample_rate_hz=sample_rate_hz,
                history_request_id=segment.request_id if matching_history else None,
                capture_sequence=segment.capture_sequence if matching_history else None,
            )
            self._refresh_current_analysis_preview()
            self._refresh_history_diagnostic()
            self._show_page(2)
        except ValueError as exc:
            QMessageBox.warning(self, "尚无法分析", str(exc))

    def _refresh_current_analysis_preview(self) -> None:
        result = self.alignment_result
        if result is None:
            return
        run = self.current_history_run
        segment = self.current_history_segment
        matching_history = (
            run is not None and segment is not None
            and segment.request_id == run.request_id
        )
        sample_rate_hz = run.frequency_hz if matching_history else None
        try:
            config = self.spectrum_panel.analysis_config_after_history_handoff()
            config_error = None
        except ValueError as exc:
            config = None
            config_error = f"频谱参数需修正：{exc}"
        key = self._analysis_preview_cache_key(
            result, sample_rate_hz, config, config_error
        )
        if key == self._analysis_preview_key:
            return
        if config_error is not None:
            preview = self._analysis_preview_failure(config_error)
        else:
            try:
                from waveform_storage.analysis.engine import assess_request_quality
                from waveform_storage.analysis.models import AnalysisRequest

                preview = assess_request_quality(
                    AnalysisRequest(
                        "alignment", config=config, sample_rate_hz=sample_rate_hz,
                        alignment=result,
                    )
                )
            except ImportError as exc:
                preview = self._analysis_preview_failure(
                    f"分析器质量门依赖不可用：{exc}"
                )
            except ValueError as exc:
                preview = self._analysis_preview_failure(
                    f"频谱参数需修正：{exc}"
                )
        self._analysis_quality_preview = tuple(preview)
        self._analysis_preview_key = key

    @staticmethod
    def _analysis_preview_failure(reason: str):
        from types import SimpleNamespace

        return (SimpleNamespace(
            channel="历史对齐",
            classification="invalid",
            reason=reason,
            duration_seconds=None,
            sample_rate_hz=None,
            quality={},
        ),)

    def _analysis_preview_cache_key(self, result, sample_rate_hz, config, config_error):
        return (
            self._history_key(), tuple(result.device_ids), sample_rate_hz,
            config, config_error, self._history_generation,
        )

    def closeEvent(self, event) -> None:
        self.spectrum_panel.shutdown()
        super().closeEvent(event)

    def _update_selection_summary(self) -> None:
        area_count = len(self.area_model.selected_area_ids())
        device_count = len(self.area_model.selected_devices())
        self.selected_area_metric.findChild(QLabel, "metricValue").setText(
            str(area_count)
        )
        self.selected_device_metric.findChild(QLabel, "metricValue").setText(
            str(device_count)
        )
        self.selection_text.setText(
            f"已选择 {area_count} 个区域 · {device_count} 台BPM"
        )

    def _load_initial_frequency(self) -> None:
        query = getattr(self.backend, "get_timing_frequency", None)
        if self.ui_only:
            return
        if not callable(query):
            self._set_frequency_display("暂不可用", available=False)
            return
        worker = BackgroundWorker(query)
        self._active_workers.add(worker)
        worker.signals.completed.connect(self._initial_frequency_received)
        worker.signals.failed.connect(self._initial_frequency_failed)
        worker.signals.completed.connect(
            lambda _value, current=worker: self._finish_worker(current)
        )
        worker.signals.failed.connect(
            lambda _error, current=worker: self._finish_worker(current)
        )
        self.thread_pool.start(worker)

    def _initial_frequency_received(self, frequency: float) -> None:
        self._set_frequency_display(f"{frequency:g} Hz", available=True)

    def _initial_frequency_failed(self, exc: Exception) -> None:
        self._set_frequency_display("暂不可用", available=False)
        self.frequency_value.setToolTip(str(exc) or "服务器频率读取失败")

    def _set_frequency_display(self, text: str, *, available: bool) -> None:
        self.frequency_value.setText(text)
        self.frequency_value.setProperty("available", available)
        self.frequency_value.style().unpolish(self.frequency_value)
        self.frequency_value.style().polish(self.frequency_value)

    def _request_start(self) -> None:
        if self.ui_only:
            return
        if self._capture_request_id is not None:
            title = "采集中" if self._capture_poll_failures == 0 else "采集状态待确认"
            QMessageBox.information(
                self,
                title,
                f"服务器已有采集任务，当前不会再次发送。\n请求ID：{self._capture_request_id}",
            )
            return
        devices = self.area_model.selected_devices()
        selected_device_set = set(devices)
        selected_sections = {
            device.section for device in self.registry.devices
            if device.id in selected_device_set
        }
        area_ids = tuple(
            dict.fromkeys(
                device.section for device in self.registry.devices
                if device.section in selected_sections
            )
        )
        if not devices:
            self._present_error(
                OperationError(
                    title="无法开始采集",
                    summary="尚未选择任何 BPM。",
                    stage="采集前校验",
                    code="CAPTURE_NO_AREA",
                    suggestion="请展开区域并勾选至少一台已启用采集的 BPM。",
                )
            )
            return
        if self.backend.mode is not OperatorMode.DEMO:
            selected_devices = set(devices)
            approved_by_area: dict[str, list[str]] = {}
            for device in self.capture_registry.devices:
                if device.enabled and device.acquisition_enabled:
                    approved_by_area.setdefault(device.section, []).append(device.id)
            area_ids = tuple(
                area_id
                for area_id, approved in approved_by_area.items()
                if selected_devices.intersection(approved)
            )
            devices = tuple(
                device_id
                for area_id in area_ids
                for device_id in approved_by_area[area_id]
                if device_id in selected_devices
            )
            if not devices:
                self._present_error(
                    OperationError(
                        title="设备尚未启用现场采集",
                        summary="所选区域没有已批准用于采集的设备。",
                        stage="采集前注册表校验",
                        code="CAPTURE_DEVICES_NOT_ENABLED",
                        suggestion=(
                            "先完成端口和业务语义验证，再在受审注册表中逐台启用；"
                            "不要绕过该状态直接发送。"
                        ),
                    )
                )
                return
        duration_seconds = int(self.duration_combo.currentData()) * 60
        requested_frequency = self.frequency_combo.currentData()
        if self.backend.mode is not OperatorMode.DEMO:
            frequency_text = (
                f"{requested_frequency:g} Hz" if requested_frequency is not None
                else "跟随频率 PV"
            )
            reply = QMessageBox.question(
                self,
                "确认发送采集请求",
                (
                    f"将向 {self.backend.mode.value} 后台发送采集请求。\n"
                    f"{len(area_ids)} 个束线区域，{len(devices)} 台BPM，"
                    f"时长 {duration_seconds // 60} 分钟，频率 {frequency_text}。\n\n"
                    "是否继续？"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._record_task("采集请求", "已取消", "操作员未确认发送")
                return

        def accepted(result) -> None:
            if not result.accepted:
                if result.error_code == "SERVICE_BUSY":
                    active = re.fullmatch(
                        r"capture ([0-9a-f-]{36}) is still active",
                        result.message,
                    )
                    if active is not None:
                        request_id = str(UUID(active.group(1)))
                        self._record_task(
                            "采集监控", "已接入", f"现有任务 {request_id}"
                        )
                        QMessageBox.information(
                            self,
                            "采集中",
                            f"服务器已有采集任务，当前不会再次发送。\n请求ID：{request_id}",
                        )
                        QTimer.singleShot(
                            0,
                            lambda: self._begin_capture_monitor(request_id, 0),
                        )
                        return
                self._present_error(
                    OperationError(
                        title="采集请求未被接受",
                        summary=result.message,
                        stage="采集请求",
                        code=result.error_code or "CAPTURE_REJECTED",
                        suggestion=(
                            "已有采集任务在运行，请等待它完成后再提交。"
                            if result.error_code == "SERVICE_BUSY"
                            else "先检查任务状态和服务端记录；不要直接重复提交。"
                        ),
                    )
                )
                return
            self._record_task(
                "采集请求",
                "已接受",
                f"{result.mode.value} · {result.status} · {result.message}",
            )
            if (
                self.backend.mode is OperatorMode.CANDIDATE_V2
                and result.status not in {"succeeded", "failed", "cancelled", "rejected"}
            ):
                QTimer.singleShot(
                    0,
                    lambda: self._begin_capture_monitor(
                        result.request_id, duration_seconds
                    ),
                )
            else:
                QMessageBox.information(
                    self,
                    "采集请求已处理",
                    (
                        f"状态：{result.status}\n"
                        f"请求ID：{result.request_id}\n"
                        f"{result.message}"
                    ),
                )

        self._run_task(
            "采集请求",
            lambda: self.backend.start_capture(
                area_ids, devices, duration_seconds, requested_frequency
            ),
            accepted,
        )

    def _begin_capture_monitor(
        self, request_id: str, duration_seconds: int, *, started_at: datetime | None = None
    ) -> None:
        started_at = started_at or datetime.now().astimezone()
        self._capture_request_id = request_id
        self._capture_duration_seconds = duration_seconds
        elapsed = max(0, (datetime.now().astimezone() - started_at).total_seconds())
        self._capture_started_at = monotonic() - elapsed
        self._capture_poll_failures = 0
        self._capture_timer.setInterval(2000)
        self._save_capture_state(request_id, duration_seconds, started_at)
        self._set_busy(True, f"采集状态核对中 · 请求 {request_id[:8]}…")
        self.start_button.setText("采集中")
        self.start_button.setEnabled(True)
        if duration_seconds:
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(1)
        self._capture_timer.start()
        self._poll_capture_status()

    def _save_capture_state(
        self, request_id: str, duration_seconds: int, started_at: datetime
    ) -> None:
        try:
            self.capture_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.capture_state_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "request_id": request_id,
                        "duration_seconds": duration_seconds,
                        "started_at": started_at.isoformat(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.capture_state_path)
        except OSError as exc:
            self.journal_write_error = str(exc)

    def _resume_capture_monitor(self) -> None:
        if self._busy or self._capture_request_id is not None:
            return
        try:
            if self.capture_state_path.stat().st_size > 4096:
                return
            saved = json.loads(self.capture_state_path.read_text(encoding="utf-8"))
            request_id = str(UUID(saved["request_id"]))
            duration = int(saved["duration_seconds"])
            started_at = datetime.fromisoformat(saved["started_at"])
            if duration < 0 or started_at.tzinfo is None:
                return
        except (OSError, ValueError, TypeError, KeyError):
            return
        self._begin_capture_monitor(request_id, duration, started_at=started_at)

    def _poll_capture_status(self) -> None:
        request_id = self._capture_request_id
        if request_id is None or self._capture_status_worker is not None:
            return
        worker = BackgroundWorker(
            lambda: self.backend.get_capture_status(request_id)
        )
        self._capture_status_worker = worker
        worker.signals.completed.connect(
            lambda response, current=worker: self._capture_status_received(
                current, response
            )
        )
        worker.signals.failed.connect(
            lambda exc, current=worker: self._capture_status_failed(current, exc)
        )
        worker.signals.cancelled.connect(
            lambda current=worker: self._clear_capture_status_worker(current)
        )
        self.thread_pool.start(worker)

    def _capture_status_received(self, worker, response) -> None:
        self._clear_capture_status_worker(worker)
        self._capture_poll_failures = 0
        self._capture_timer.setInterval(2000)
        if response.request_id != self._capture_request_id:
            self._finish_capture_monitor(
                "failed",
                "状态响应的请求ID不匹配",
                "STATUS_REQUEST_ID_MISMATCH",
            )
            return
        if response.status in {"succeeded", "failed", "cancelled", "rejected"}:
            self._finish_capture_monitor(
                response.status, response.message, response.error_code
            )
            return

        if self._capture_duration_seconds:
            elapsed = max(0, int(monotonic() - self._capture_started_at))
            duration = self._capture_duration_seconds
            progress = min(95, max(1, int(elapsed / duration * 90)))
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(progress)
            if elapsed < duration:
                detail = f"{elapsed}/{duration} 秒"
            else:
                detail = f"已采集 {duration} 秒，正在收集并校验文件"
        else:
            detail = "服务器正在采集"
            self.task_progress.setRange(0, 0)
        self.task_state_text.setText(
            f"采集中 · {detail} · 请求 {response.request_id[:8]}…"
        )
        self.start_button.setText("采集中")

    def _capture_status_failed(self, worker, exc: Exception) -> None:
        self._clear_capture_status_worker(worker)
        self._capture_poll_failures += 1
        if self._capture_poll_failures == 3:
            self._record_task("采集状态", "待确认", str(exc) or repr(exc))
            self._capture_timer.setInterval(10000)
        self.task_state_text.setText(
            f"采集状态待确认 · 自动重连中 · 请求 {self._capture_request_id[:8]}…"
        )
        self.start_button.setText("状态待确认")

    def _clear_capture_status_worker(self, worker) -> None:
        if self._capture_status_worker is worker:
            self._capture_status_worker = None

    def _finish_capture_monitor(
        self, status: str, message: str, error_code: str | None
    ) -> None:
        request_id = self._capture_request_id or "unknown"
        self._capture_timer.stop()
        self._capture_request_id = None
        self.start_button.setText("开始采集")
        try:
            saved = json.loads(self.capture_state_path.read_text(encoding="utf-8"))
            if saved.get("request_id") == request_id:
                self.capture_state_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
        succeeded = status == "succeeded"
        partial = error_code == "PARTIAL_CAPTURE"
        self._set_busy(False, "部分数据已保存" if partial else "采集完成" if succeeded else "采集失败")
        self.task_progress.setValue(100 if succeeded or partial else 0)
        detail = f"{status} · {message} · 请求 {request_id}"
        self._record_task("采集任务", "部分成功" if partial else "成功" if succeeded else "失败", detail)
        if succeeded or partial:
            QMessageBox.information(
                self,
                "部分数据已保存" if partial else "采集完成",
                f"请求ID：{request_id}\n{message}",
            )
            return
        self._present_error(
            OperationError(
                title="采集任务未成功完成",
                summary=message,
                stage="设备采集与文件校验",
                code=error_code or "CAPTURE_FAILED",
                suggestion="查看“任务与错误”及服务器逐设备结果后处理；不要盲目重复提交。",
                details=f"request_id: {request_id}\nstatus: {status}",
            )
        )

    def _candidate_history_backend(self) -> CandidateHistoryBackend:
        return cast(CandidateHistoryBackend, self.backend)

    def _history_key(self):
        segment = self.current_history_segment
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            return (("segment", segment.request_id, segment.capture_sequence)
                    if segment is not None else None)
        return ("time", self._selected_date(), self._selected_time())

    def _history_date_changed(self, *_args):
        self.history_runs = ()
        self.current_history_run = None
        self.history_time.blockSignals(True)
        self.history_time.clear()
        self.history_time.addItem("先检索采集任务")
        self.history_time.blockSignals(False)
        self._reset_history_segments()

    def _reset_history_segments(self):
        self.history_segments = ()
        self.current_history_segment = None
        self.segment_index.blockSignals(True)
        self.segment_index.setRange(0, 0)
        self.segment_index.blockSignals(False)
        self.segment_index.setSuffix(" / 0")
        self.segment_label.setText("尚未选择数据段")
        self.previous_segment_button.setEnabled(False)
        self.next_segment_button.setEnabled(False)
        self._clear_history_selection()

    def _retrieve_runs(self):
        selected_date = self._selected_date()
        self._history_date_changed()
        generation = self._history_generation

        def loaded(runs):
            if generation != self._history_generation or selected_date != self._selected_date():
                return
            self.history_runs = tuple(runs)
            self.history_time.blockSignals(True)
            self.history_time.clear()
            for run in runs:
                clock = run.first_capture_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")
                if run.has_task_metadata:
                    frequency = f"{run.frequency_hz:g} Hz" if run.frequency_hz is not None else "频率未知"
                    seconds = run.duration_seconds
                    duration = (f"{seconds // 60} 分钟" if seconds and seconds % 60 == 0
                                else f"{seconds} 秒" if seconds is not None else "时长未知")
                    text = f"{clock} | {frequency} | {duration} | 当日 {run.segment_count} 段"
                else:
                    text = f"{clock} | 历史数据 | 当日 {run.segment_count} 段"
                self.history_time.addItem(text, run)
                self.history_time.setItemData(self.history_time.count() - 1, text, Qt.ToolTipRole)
            if not runs:
                self.history_time.addItem("该日期没有可用数据")
            self.history_time.blockSignals(False)
            self._history_run_changed()

        self._run_task("历史任务检索", lambda: self._candidate_history_backend().retrieve_runs(selected_date), loaded)

    def _history_run_changed(self, *_args):
        self.current_history_run = self.history_time.currentData()
        self._reset_history_segments()
        run = self.current_history_run
        if run is None:
            return
        generation = self._history_generation

        def loaded(segments):
            if generation != self._history_generation or run != self.current_history_run:
                return
            self.history_segments = tuple(segments)
            self.segment_index.blockSignals(True)
            if segments:
                self.segment_index.setRange(1, len(segments))
            else:
                self.segment_index.setRange(0, 0)
            self.segment_index.setEnabled(bool(segments))
            self.segment_index.setValue(1 if segments else 0)
            self.segment_index.blockSignals(False)
            self.segment_index.setSuffix(f" / {len(segments)}")
            self._history_segment_changed()

        self._run_task("任务数据段检索", lambda: self._candidate_history_backend().retrieve_segments(run.request_id), loaded)

    def _history_segment_changed(self, *_args):
        position = self.segment_index.value()
        segment = self.history_segments[position - 1] if self.history_segments and position else None
        self.current_history_segment = segment
        self._clear_history_selection()
        count = len(self.history_segments)
        self.previous_segment_button.setEnabled(position > 1)
        self.next_segment_button.setEnabled(0 < position < count)
        if segment is None:
            self.segment_label.setText("尚未选择数据段")
            return
        run = self.current_history_run
        requested = run.requested_device_count
        bpm_count = (f"{len(segment.device_ids)}/{requested}" if requested is not None
                     else str(len(segment.device_ids)))
        clock = segment.captured_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        self.segment_label.setText(
            f"S{segment.capture_sequence:05d} · {clock} · {bpm_count} BPM"
        )
        self._history_available_device_count = len(segment.device_ids)
        self._update_bpm_button()

    def _retrieve_times(self) -> None:
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            self._retrieve_runs()
            return
        selected_date = self._selected_date()

        def loaded(times: tuple[str, ...]) -> None:
            self.history_time.clear()
            if not times:
                self.history_time.addItem("该日期没有可用数据", None)
                self._record_task("历史数据检索", "完成", "未发现采集时刻")
                return
            for value in times:
                normalized = str(value).replace(":", "")
                display = (
                    f"{normalized[0:2]}:{normalized[2:4]}:{normalized[4:6]}"
                    if len(normalized) == 6
                    else str(value)
                )
                self.history_time.addItem(display, normalized)
            self._record_task(
                "历史数据检索", "成功", f"发现 {len(times)} 个采集时刻"
            )

        self._run_task(
            "历史数据检索",
            lambda: self.backend.retrieve_times(selected_date),
            loaded,
        )

    def _clear_history_selection(self, *_args) -> None:
        self._history_generation += 1
        self.history_selected_devices = ()
        self.history_selection_key = None
        self._clear_alignment_view()
        self.history_selection_text.setText("尚未选择参与对齐的 BPM")
        if self.backend.mode is not OperatorMode.CANDIDATE_V2:
            self._history_available_device_count = 0
        self._update_bpm_button()

    def _update_bpm_button(self) -> None:
        self.select_bpm_button.setText(
            f"BPM {len(self.history_selected_devices)}/{self._history_available_device_count}"
        )

    def _select_history_devices(self) -> None:
        time_sec = self._selected_time()
        if time_sec is None:
            self._show_validation_error(
                "无法选择 BPM",
                ("请先检索采集任务并选择数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "请先检索并选择采集时刻。"),
                "BPM_SELECTION_NO_TIME",
                ("选择日期，点击“检索任务”，然后选择任务和数据段。"
                 if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "选择日期，点击“检索数据”，然后选取采集时刻。"),
            )
            return
        selected_date = self._selected_date()
        all_ids = tuple(device.id for device in self.capture_registry.devices)
        display_names = {
            device.id: device.display_name for device in self.registry.devices
        }
        key = self._history_key()
        generation = self._history_generation

        def show_choices(available_ids: tuple[str, ...]) -> None:
            if key != self._history_key() or generation != self._history_generation:
                return
            if not available_ids:
                self._show_validation_error(
                    "没有可选 BPM",
                    "该采集时刻没有可用的波形文件。",
                    "BPM_SELECTION_NO_FILES",
                    "检查采集日期和时刻，或查看服务端设备结果。",
                )
                return
            self._history_available_device_count = len(available_ids)
            self._update_bpm_button()
            previous = (
                self.history_selected_devices
                if self.history_selection_key == key else ()
            )
            dialog = BpmSelectionDialog(
                available_ids, display_names, previous, self
            )
            if dialog.exec_() != QDialog.Accepted:
                return
            self.history_selected_devices = dialog.selected_ids()
            self.history_selection_key = key
            self._history_generation += 1
            self._clear_alignment_view()
            self.history_selection_text.setText(
                f"已选择 {len(self.history_selected_devices)} / "
                f"{len(available_ids)} 台可用 BPM"
            )
            self._update_bpm_button()
            self._record_task(
                "历史BPM选择", "完成",
                f"{selected_date} {time_sec} · {len(self.history_selected_devices)} 台",
            )

        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            segment = self.current_history_segment
            show_choices(tuple(item for item in all_ids if item in segment.device_ids))
        elif self.backend.mode is OperatorMode.DEMO:
            show_choices(all_ids)
        else:
            self._run_task(
                "查询可用BPM",
                lambda: self.backend.available_devices(
                    selected_date, time_sec, all_ids
                ),
                show_choices,
            )

    def _align_selected(self) -> None:
        time_sec = self._selected_time()
        if time_sec is None:
            self._show_validation_error(
                "无法开始对齐",
                ("尚未选择有效的数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "尚未选择有效的采集时刻。"),
                "ALIGNMENT_NO_TIME",
                ("请先检索任务并选择一个数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "请先检索数据并选择一个采集时刻。"),
            )
            return
        selected_date = self._selected_date()
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            devices = self._selected_history_devices()
            if not devices:
                return
        else:
            devices = self.history_selected_devices or self._query_devices()

        segment = self.current_history_segment
        generation = self._history_generation
        if (self.backend.mode is OperatorMode.CANDIDATE_V2
                and callable(getattr(self.backend, "prepare_segment", None))
                and callable(getattr(self.backend, "align_prepared_segment", None))):
            prepared_key = (self._history_key(), devices)
            if (self._preflight_confirmation_required
                    and self._prepared_history_key == prepared_key
                    and self._prepared_history_snapshot is not None):
                self._confirm_raw_step_alignment(segment, devices, generation)
                return
            if (self._prepared_history_key == prepared_key
                    and self._prepared_history_snapshot is not None):
                self._execute_history_alignment(
                    segment, devices, generation,
                    prepared=self._prepared_history_snapshot,
                )
                return

            def prepared_loaded(snapshot) -> None:
                if (generation != self._history_generation
                        or prepared_key != (self._history_key(), devices)):
                    return
                self._prepared_history_snapshot = snapshot
                self._prepared_history_key = prepared_key
                self._raw_step_report = snapshot.raw_step_report
                self._show_raw_step_diagnostic()
                if not snapshot.raw_step_report.raw_step_compatible:
                    self._preflight_confirmation_required = True
                    self.align_button.setText("继续严格对齐")
                    self._record_task(
                        "数据编号间隔检查", "需核实", snapshot.raw_step_report.summary
                    )
                    self._confirm_raw_step_alignment(segment, devices, generation)
                    return
                self._preflight_confirmation_required = False
                self._execute_history_alignment(
                    segment, devices, generation, prepared=snapshot
                )

            self._run_task(
                "数据编号间隔检查",
                lambda: self._candidate_history_backend().prepare_segment(segment, devices),
                prepared_loaded,
            )
            return

        self._execute_history_alignment(segment, devices, generation)

    def _confirm_raw_step_alignment(self, segment, devices, generation) -> None:
        if self._raw_step_warning_dialog is not None:
            self._raw_step_warning_dialog.raise_()
            self._raw_step_warning_dialog.activateWindow()
            return
        snapshot = self._prepared_history_snapshot
        if snapshot is None:
            return
        report = snapshot.raw_step_report
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Warning)
        message.setWindowTitle("数据编号间隔需要确认")
        message.setText(report.summary)
        issues = []
        for item in report.devices:
            if item.issue or item.differs_from_majority:
                step = (f"{item.minimum_step}～{item.maximum_step}"
                        if item.minimum_step is not None else "无法确定")
                issues.append(f"{item.device_id}：相邻编号相差 {step}；{item.issue or '与多数设备不一致'}")
        issues.extend(f"{item.device_id}：{item.reason}" for item in report.failed_devices)
        issues.extend(f"{device_id}：未找到波形文件" for device_id in report.missing_devices)
        if not report.has_clear_majority:
            issues.append("无法确定多数设备的常见编号间隔。")
        reference = (
            f"多数可比较设备的相邻编号通常相差 {report.majority_step:,}。\n\n"
            if report.majority_step is not None else ""
        )
        message.setInformativeText(
            "这里的编号间隔指相邻采样点的 bunchID 差值，不是时间间隔。\n\n"
            + reference
            + "\n".join(issues[:4])
            + "\n\n尚未执行对齐。继续将保留全部所选 BPM，仅使用共同 bunchID；"
              "不会自动剔除设备或修改原始数据编号。请确认后继续。"
        )
        message.setDetailedText("\n".join(issues) or report.summary)
        proceed = message.addButton("继续严格对齐", QMessageBox.AcceptRole)
        cancel = message.addButton("取消", QMessageBox.RejectRole)
        message.setDefaultButton(cancel)
        message.setEscapeButton(cancel)
        message.setTextFormat(Qt.PlainText)
        font_size = 14
        message.setStyleSheet(
            f"QLabel {{ font-size: {font_size}px; }}"
            f"QPushButton {{ font-size: {font_size}px; min-height: 36px; padding: 0 14px; }}"
        )
        for button in message.buttons():
            button.setStyleSheet(
                f"font-size: {font_size}px; min-height: 36px; padding: 0 14px;"
            )
        details_button = next(button for button in message.buttons()
                              if button not in (proceed, cancel))
        details_button.setText("查看详情")
        details_expanded = False

        def toggle_details_label():
            nonlocal details_expanded
            details_expanded = not details_expanded
            details_button.setText("收起详情" if details_expanded else "查看详情")

        details_button.clicked.connect(toggle_details_label)
        message.setAttribute(Qt.WA_DeleteOnClose)
        self._raw_step_warning_dialog = message

        def finished(_result):
            if self._raw_step_warning_dialog is message:
                self._raw_step_warning_dialog = None
            if (message.clickedButton() is not proceed
                    or generation != self._history_generation
                    or snapshot is not self._prepared_history_snapshot
                    or self._prepared_history_key != (self._history_key(), devices)):
                return
            self._preflight_confirmation_required = False
            self.align_button.setText("检查并对齐")
            self._execute_history_alignment(segment, devices, generation, prepared=snapshot)

        message.finished.connect(finished)
        message.open()

    def _execute_history_alignment(self, segment, devices, generation, *, prepared=None):
        run = self.current_history_run
        matching_history = (
            run is not None and segment is not None
            and segment.request_id == run.request_id
        )
        sample_rate_hz = run.frequency_hz if matching_history else None
        selected_date = self._selected_date()
        selected_time = self._selected_time()
        try:
            config = self.spectrum_panel.analysis_config_after_history_handoff()
            config_error = None
        except ValueError as exc:
            config = None
            config_error = f"频谱参数需修正：{exc}"
            self._analysis_quality_preview = self._analysis_preview_failure(config_error)
            self._analysis_preview_key = None

        def align_and_assess():
            if prepared is not None:
                result = self._candidate_history_backend().align_prepared_segment(prepared)
            elif self.backend.mode is OperatorMode.CANDIDATE_V2:
                result = self._candidate_history_backend().align_segment(segment, devices)
            else:
                result = self.backend.align(selected_date, selected_time, devices)
            if config_error is not None:
                preview = self._analysis_preview_failure(config_error)
            else:
                try:
                    from waveform_storage.analysis.engine import assess_request_quality
                    from waveform_storage.analysis.models import AnalysisRequest

                    preview = assess_request_quality(
                        AnalysisRequest(
                            "alignment", config=config, sample_rate_hz=sample_rate_hz,
                            alignment=result,
                        )
                    )
                except ImportError as exc:
                    preview = self._analysis_preview_failure(
                        f"分析器质量门依赖不可用：{exc}"
                    )
                except ValueError as exc:
                    preview = self._analysis_preview_failure(
                        f"频谱参数需修正：{exc}"
                    )
            return result, preview

        def aligned(payload) -> None:
            if generation != self._history_generation:
                return
            result, preview = payload
            self._analysis_quality_preview = tuple(preview)
            self._analysis_preview_key = self._analysis_preview_cache_key(
                result, sample_rate_hz, config, config_error
            )
            if not self.load_alignment_result(result, devices):
                return
            self._record_task(
                "波形对齐",
                "成功" if result.bunch_ids.size else "无共同点",
                (
                    f"{len(result.device_ids)} 台BPM · "
                    f"{result.bunch_ids.size} 个共同 bunchID · "
                    f"跳过 {len(result.skipped_devices)} 台"
                ),
            )

        self._run_task("波形对齐", align_and_assess, aligned)

    def _show_raw_step_diagnostic(self) -> None:
        self._refresh_history_diagnostic()

    def _show_history_diagnostics(self) -> None:
        if self._raw_step_report is None and self.alignment_result is None:
            return
        HistoryDiagnosticDialog(
            self._raw_step_report, self.alignment_result,
            self._analysis_quality_preview, self,
        ).exec_()

    def _refresh_history_diagnostic(self) -> None:
        report = self._raw_step_report
        result = self.alignment_result
        self.history_diagnostic_button.setEnabled(
            report is not None or result is not None
        )
        if report is None and result is None:
            self.history_diagnostic_bar.hide()
            return
        preview = self._analysis_quality_preview
        raw_issues = []
        if report is not None:
            raw_issues = [
                item for item in report.devices
                if item.issue or item.differs_from_majority
            ]
            affected_devices = {
                item.device_id for item in raw_issues if item.device_id
            }
            affected_devices.update(
                item.device_id for item in report.failed_devices if item.device_id
            )
            affected_devices.update(
                device_id for device_id in report.missing_devices if device_id
            )
            anonymous_issues = sum(
                not item.device_id for item in raw_issues
            ) + sum(
                not item.device_id for item in report.failed_devices
            )
            issue_count = len(affected_devices) + anonymous_issues
            if issue_count:
                raw_status = f"编号间隔疑点 {issue_count} 台"
            elif not report.has_clear_majority:
                raw_status = "编号间隔待核实"
            else:
                raw_status = "编号间隔未见异常"
        else:
            raw_status = "编号间隔未检查"

        if result is None:
            common = None
            retention_text = None
            common_status = "等待严格对齐（共同点数尚未计算）"
        else:
            common = int(result.bunch_ids.size)
            raw_counts = [item.raw_count for item in report.devices] if report else []
            if raw_counts:
                denominator = min(raw_counts)
                retention = common / denominator * 100 if denominator else 0.0
                retention_text = f"（保留 {retention:.2f}%）"
            else:
                retention_text = ""
            common_status = f"共同 {common:,} 点{retention_text}"

        spectrum_status = self._compact_analysis_preview_status(preview)
        self.history_diagnostic_label.setText(
            f"{raw_status}｜{common_status}｜频谱：{spectrum_status}"
        )

        evidence = []
        if raw_issues:
            shown = raw_issues[:1]
            for item in shown:
                name = item.device_id.rsplit("_", 1)[-1] or item.device_id
                if item.minimum_step is not None:
                    evidence.append(
                        f"{name} 相邻编号相差 {item.minimum_step}～{item.maximum_step}"
                    )
                else:
                    evidence.append(f"{name}：相邻编号间隔无法确定")
            if len(raw_issues) > len(shown):
                evidence.append(f"等 {len(raw_issues)} 台")
        elif report is not None and report.majority_step is not None:
            evidence.append(f"多数设备相邻编号通常相差 {report.majority_step}")
        elif report is not None:
            observed = next(
                (item for item in report.devices if item.minimum_step is not None),
                None,
            )
            if observed is not None:
                name = observed.device_id.rsplit("_", 1)[-1] or observed.device_id
                evidence.append(
                    f"{name} 相邻编号相差 {observed.minimum_step}～{observed.maximum_step}"
                )
            evidence.append("多数参考无法确定")

        if raw_issues and report.majority_step is not None:
            evidence.append(f"多数设备通常相差 {report.majority_step:,}")

        run = self.current_history_run
        segment = self.current_history_segment
        matching_history = (
            run is not None and segment is not None
            and segment.request_id == run.request_id
        )
        if matching_history and run.frequency_hz is not None:
            evidence.append(f"采样率 {run.frequency_hz:g} Hz（历史任务记录）")
        else:
            evidence.append("采样率未提供")
        self.history_diagnostic_evidence_label.setText("｜".join(evidence))

        warning = (
            report is None or not report.raw_step_compatible
            or (result is not None and (
                not preview or any(
                    item.classification != "ready" for item in preview
                ) or result.bunch_ids.size == 0
                or self._quality_issue_count(result) > 0
            ))
        )
        self.history_diagnostic_bar.setProperty(
            "level", "warning" if warning else "normal"
        )
        full_details = []
        if report is not None:
            full_details.append(report.summary)
            if report.majority_step is not None:
                full_details.append(
                    f"多数设备的常见编号间隔：{report.majority_step}"
                    f"（{report.majority_count}/{report.majority_denominator} 台）"
                )
            full_details.extend(
                f"{item.device_id}：{item.issue or '编号间隔与多数设备不一致'}"
                for item in raw_issues
            )
        else:
            full_details.append("未检查相邻数据编号的间隔；共同 ID 不能证明数据在时间上连续。")
        if result is not None:
            full_details.append(
                "分析器根据对齐后的 bunchID 和当前采样率建立相对时间轴；"
                "这不确认原始设备物理时钟一致性。"
            )
            full_details.extend(
                item.reason for item in preview if getattr(item, "reason", None)
            )
        self.history_diagnostic_bar.setToolTip("\n".join(full_details))
        self.history_diagnostic_bar.setVisible(warning)
        if warning:
            self._refresh_widget_style(self.history_diagnostic_bar)

    @staticmethod
    def _compact_analysis_preview_status(preview) -> str:
        if not preview:
            return "待检查"
        gates = [
            getattr(item, "quality", {}).get("preview_gates", {})
            for item in preview
        ]
        shortfalls = [
            gate for gate in gates
            if gate.get("minimum_seconds_passed") is False
            and gate.get("span_seconds") is not None
            and gate.get("minimum_seconds") is not None
        ]
        if shortfalls:
            shortest = min(item["span_seconds"] for item in shortfalls)
            required = max(item["minimum_seconds"] for item in shortfalls)
            return f"推算跨度 {shortest:.3f} s / 至少 {required:g} s"
        if all(item.classification == "ready" for item in preview):
            return "预检查通过"
        reasons = " ".join(
            getattr(item, "reason", "") or "" for item in preview
        )
        if "参数" in reasons or "频带" in reasons:
            return "参数需修正"
        if "依赖不可用" in reasons or "ModuleNotFoundError" in reasons:
            return "质量门未检查"
        return "质量门未通过"

    def load_alignment_result(
        self,
        result,
        device_choices: tuple[str, ...] | None = None,
    ) -> bool:
        """Load one result into both plot modes; useful for demo rendering."""

        if result.bunch_ids.size == 0:
            self._clear_alignment_view(preserve_raw_diagnostics=True)
            self.alignment_result = result
            self._set_data_duration(result)
            self.alignment_status_label.setText("严格交集为空")
            self._set_quality_status(result)
            self._update_spectrum_action(result)
            self._refresh_history_diagnostic()
            self.task_state_text.setText("所选设备没有共同 bunchID")
            return True
        self.alignment_result = result
        self._set_data_duration(result)
        self.alignment_status_label.setText(self._alignment_status_text(result))
        self._set_quality_status(result)
        self._update_spectrum_action(result)
        self.bunch_index.setMaximum(max(0, result.bunch_ids.size - 1))
        self.bunch_index.setValue(0)
        self.plot_device.clear()
        for device_id in result.device_ids:
            self.plot_device.addItem(device_id, device_id)
        self.plot_device.setEnabled(self.plot_device.count() > 0)
        self.device_waveform_result = None
        self.device_waveform_key = None
        self._show_bunch_id()
        self._refresh_history_diagnostic()
        self._render_selected_plot()
        return True

    def _show_bunch_id(self) -> None:
        result = self.alignment_result
        if result is None or result.bunch_ids.size == 0:
            self.bunch_id_label.setText(
                "bunchID —" if self._compact_history_toolbar else "尚未加载对齐数据"
            )
            return
        index = min(self.bunch_index.value(), result.bunch_ids.size - 1)
        self.bunch_index.setValue(index)
        text = f"Index {index}  ·  bunchID {int(result.bunch_ids[index])}"
        self.bunch_id_label.setText(
            f"bunchID {int(result.bunch_ids[index])}"
            if self._compact_history_toolbar else text
        )
        self.bunch_id_label.setToolTip(text)

    def _set_data_duration(self, result) -> None:
        self._duration_result = result
        ids = result.bunch_ids
        if ids.size == 0:
            self.data_duration_label.setText(self._duration_status_text())
        else:
            self.data_duration_label.setText(self._duration_status_text())

    @staticmethod
    def _quality_issue_count(result) -> int:
        return sum(
            quality.status != "usable"
            or quality.zero_count > 0
            or quality.duplicate_count > 0
            or not quality.was_strictly_increasing
            for quality in result.quality_report
        )

    @classmethod
    def _quality_status_text(cls, result, *, compact: bool = False) -> str:
        issue_count = cls._quality_issue_count(result)
        if compact:
            return f"质量 ⚠{issue_count}" if issue_count else "质量详情"
        return f"质量详情 ⚠ {issue_count} 台" if issue_count else "质量详情"

    def _show_alignment_quality(self) -> None:
        result = self._quality_result
        if result is not None:
            AlignmentQualityDialog(result, self).exec_()

    def _set_quality_status(self, result) -> None:
        if result is not self.alignment_result:
            return
        self._quality_result = result
        issue_count = self._quality_issue_count(result)
        self.quality_status_button.setText(
            self._quality_status_text(
                result, compact=self._compact_history_toolbar
            )
        )
        self.quality_status_button.setProperty(
            "level", "warning" if issue_count else "normal"
        )
        self.quality_status_button.setVisible(True)
        self.quality_status_button.setEnabled(True)
        self._refresh_widget_style(self.quality_status_button)

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _spectrum_requirement_warning(self) -> str | None:
        result = self.alignment_result
        if result is None or result.bunch_ids.size == 0:
            return None
        problems = []
        if self._raw_step_report is None:
            problems.append(
                "当前后端未提供相邻数据编号的间隔检查；时间基准与连续性无法确认。"
            )
        elif not self._raw_step_report.raw_step_compatible:
            problems.append(
                self._raw_step_report.summary
                + "。数据编号间隔、设备完整性或多数参考仍需核实；共同点数不能证明连续记录。"
            )
        preview = self._analysis_quality_preview
        if not preview or any(item.classification != "ready" for item in preview):
            reasons = list(dict.fromkeys(
                item.reason for item in preview if item.reason
            ))
            problems.append(
                "分析器当前质量门未通过或无法确认。"
                + (" " + reasons[0] if reasons else "")
            )
        if not problems:
            return None
        details = self._duration_status_text()
        return (
            "进入频谱前请确认以下诊断：\n"
            + "\n".join(problems)
            + f"\n{details}。分析器质量门与数据编号间隔检查分别显示；前者通过也不会消除编号间隔告警。"
        )

    def _update_spectrum_action(self, result) -> None:
        warning = self._spectrum_requirement_warning()
        compact = self._compact_history_toolbar
        title = "频谱" if compact else "频谱分析"
        self.history_spectrum_button.setText(title + (" ⚠" if warning else ""))
        self.history_spectrum_button.setToolTip(
            warning or "使用当前对齐数据进行频谱分析"
        )

    def _locate_bunch(self) -> None:
        self._render_selected_plot()

    def _plot_mode_changed(self) -> None:
        self._plot_request_generation += 1
        distribution = self.plot_mode.currentData() == "distribution"
        self.alignment_status_label.setVisible(
            not (self._compact_history_toolbar and not distribution)
        )
        self.bunch_index.setEnabled(distribution)
        self.show_id_button.setEnabled(distribution)
        self.plot_device.setVisible(not distribution)
        self.plot_title.setText(
            "BPM位置分布" if distribution else "单设备波形"
        )
        if distribution:
            if self.alignment_result is not None:
                self.alignment_status_label.setText(
                    self._alignment_status_text(self.alignment_result)
                )
                self._set_quality_status(self.alignment_result)
            self._show_bunch_id()
        else:
            self._plot_device_changed()

    def _plot_device_changed(self) -> None:
        self._plot_request_generation += 1
        if self.plot_mode.currentData() != "device":
            return
        device_id = self.plot_device.currentData()
        result = self.alignment_result
        if device_id and result is not None:
            row = result.device_ids.index(device_id)
            sample_count = int(result.valid_mask[row].sum())
            self.bunch_id_label.setText(
                (f"{sample_count} 个 ID" if self._compact_history_toolbar
                 else f"{device_id}  ·  {sample_count} 个bunchID")
            )

    def _render_selected_plot(self) -> None:
        result = self.alignment_result
        if result is None or result.bunch_ids.size == 0:
            self._show_validation_error(
                "无法绘图",
                "尚未加载有效的对齐结果。",
                "PLOT_NO_ALIGNMENT",
                "请先选择采集时刻并执行对齐。",
            )
            return
        if self.plot_mode.currentData() == "device":
            device_id = self.plot_device.currentData()
            if not device_id:
                self._show_validation_error(
                    "无法绘制单设备波形",
                    "尚未选择 BPM 设备。",
                    "PLOT_NO_DEVICE",
                    "请在绘图模式旁选择一台 BPM。",
                )
                return
            self._plot_device_changed()
            self._request_device_waveform(device_id)
            return
        else:
            self._show_bunch_id()
            figure = plot_alignment_result(result, self.bunch_index.value())
        self._install_figure(figure)

    def _request_device_waveform(self, device_id: str) -> None:
        time_sec = self._selected_time()
        if time_sec is None:
            self._show_validation_error(
                "无法绘制单设备波形",
                ("尚未选择有效的数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "尚未选择有效的采集时刻。"),
                "PLOT_NO_TIME",
                ("请先检索采集任务并选择数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "请先检索并选择采集时刻。"),
            )
            return
        selected_date = self._selected_date()
        key = (self._history_key(), device_id)
        segment = self.current_history_segment
        generation = self._history_generation
        plot_generation = self._plot_request_generation
        if self.device_waveform_key == key and self.device_waveform_result is not None:
            self._set_data_duration(self.device_waveform_result)
            self.alignment_status_label.setText(
                (f"单设备 {int(self.device_waveform_result.bunch_ids.size):,} 点"
                 if self._compact_history_toolbar else
                 f"单设备波形 · {int(self.device_waveform_result.bunch_ids.size):,} 点")
            )
            self._set_quality_status(self.device_waveform_result)
            self._install_figure(
                plot_device_waveform(self.device_waveform_result, device_id)
            )
            return

        def loaded(result) -> None:
            if (
                generation != self._history_generation
                or plot_generation != self._plot_request_generation
                or key != (self._history_key(), self.plot_device.currentData())
                or self.plot_mode.currentData() != "device"
            ):
                return
            if result.bunch_ids.size == 0 or device_id not in result.device_ids:
                self._show_validation_error(
                    "单设备波形为空",
                    f"{device_id} 没有可绘制的 bunchID 数据。",
                    "PLOT_DEVICE_EMPTY",
                    "检查该设备在所选时刻是否存在有效 HDF5 文件。",
                )
                return
            self.device_waveform_result = result
            self._set_data_duration(result)
            self.alignment_status_label.setText(
                (f"单设备 {int(result.bunch_ids.size):,} 点"
                 if self._compact_history_toolbar else
                 f"单设备波形 · {int(result.bunch_ids.size):,} 点")
            )
            self._set_quality_status(result)
            self.device_waveform_key = key
            self._plot_device_changed()
            self._install_figure(plot_device_waveform(result, device_id))
            self._record_task(
                "单设备波形",
                "成功",
                f"{device_id} · {result.bunch_ids.size} 个bunchID",
            )

        self._run_task(
            "单设备波形",
            (lambda: self._candidate_history_backend().align_segment(segment, (device_id,)))
            if self.backend.mode is OperatorMode.CANDIDATE_V2
            else (lambda: self.backend.align(selected_date, time_sec, (device_id,))),
            loaded,
        )

    def _install_figure(self, figure) -> None:
        if self.plot_canvas is not None:
            self.plot_layout.removeWidget(self.plot_canvas)
            self.plot_canvas.figure.clear()
            self.plot_canvas.deleteLater()
        if self.plot_placeholder is not None:
            self.plot_layout.removeWidget(self.plot_placeholder)
            self.plot_placeholder.deleteLater()
            self.plot_placeholder = None
        self.plot_canvas = FigureCanvas(figure)
        self.plot_layout.addWidget(self.plot_canvas, 1)
        self.plot_canvas.draw_idle()

    def _clear_alignment_view(self, *, preserve_raw_diagnostics: bool = False) -> None:
        if self._raw_step_warning_dialog is not None:
            self._raw_step_warning_dialog.reject()
        self.alignment_result = None
        if hasattr(self, "spectrum_panel"):
            self.spectrum_panel.reset_history_context()
        self._duration_result = None
        if not preserve_raw_diagnostics:
            self._prepared_history_snapshot = None
            self._prepared_history_key = None
            self._raw_step_report = None
            self._preflight_confirmation_required = False
            self._analysis_quality_preview = ()
            self._analysis_preview_key = None
            if hasattr(self, "history_diagnostic_bar"):
                self.history_diagnostic_bar.hide()
                self.history_diagnostic_label.setText("尚未检查历史数据")
                self.history_diagnostic_button.setEnabled(False)
            if hasattr(self, "align_button"):
                self.align_button.setText("检查并对齐")
        self.data_duration_label.setText(self._duration_status_text())
        self.alignment_status_label.setText("未对齐")
        self._quality_result = None
        self.quality_status_button.setText("质量详情")
        self.quality_status_button.setProperty("level", "idle")
        self.quality_status_button.setEnabled(False)
        self.quality_status_button.setVisible(False)
        self._refresh_widget_style(self.quality_status_button)
        self.history_spectrum_button.setText(
            "频谱" if self._compact_history_toolbar else "频谱分析"
        )
        self.history_spectrum_button.setToolTip("使用当前对齐数据进行频谱分析")
        self.device_waveform_result = None
        self.device_waveform_key = None
        self.plot_device.clear()
        self.plot_device.setEnabled(False)
        self.bunch_id_label.setText(
            "bunchID —" if self._compact_history_toolbar else "尚未选择 bunchID"
        )
        if self.plot_canvas is not None:
            self.plot_layout.removeWidget(self.plot_canvas)
            self.plot_canvas.figure.clear()
            self.plot_canvas.deleteLater()
            self.plot_canvas = None
        if self.plot_placeholder is None:
            self.plot_placeholder = QLabel(
                "选择历史数据后，在这里显示 X / Y 随 BPM 设备的分布"
            )
            self.plot_placeholder.setObjectName("plotPlaceholder")
            self.plot_placeholder.setAlignment(Qt.AlignCenter)
            self.plot_layout.addWidget(self.plot_placeholder, 1)

    def _export_alignment(self) -> None:
        if self.alignment_result is None:
            self._show_validation_error(
                "无法导出",
                "尚未生成对齐结果。",
                "EXPORT_NO_ALIGNMENT",
                "请先执行对齐。",
            )
            return
        time_sec = self._selected_time()
        if time_sec is None:
            return
        destination = QFileDialog.getExistingDirectory(
            self, "选择对齐数据导出目录"
        )
        if not destination:
            return

        result = self.alignment_result
        selected_date = self._selected_date()
        segment = self.current_history_segment

        def exported(path: Path) -> None:
            self._record_task("导出对齐数据", "成功", str(path))
            QMessageBox.information(self, "导出完成", f"已保存到：\n{path}")

        self._run_task(
            "导出对齐数据",
            (lambda: self._candidate_history_backend().export_segment_alignment(result, segment, destination))
            if self.backend.mode is OperatorMode.CANDIDATE_V2
            else (lambda: self.backend.export_alignment(result, selected_date, time_sec, destination)),
            exported,
        )

    def _download_raw(self) -> None:
        time_sec = self._selected_time()
        if time_sec is None:
            self._show_validation_error(
                "无法下载原始数据",
                ("尚未选择有效的数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "尚未选择有效的采集时刻。"),
                "RAW_NO_TIME",
                ("请先检索采集任务并选择数据段。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "请先检索并选择采集时刻。"),
            )
            return
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            devices = self._selected_history_devices()
            if not devices:
                return
        else:
            devices = self.history_selected_devices or self._query_devices()
        destination = QFileDialog.getExistingDirectory(
            self, "选择原始数据保存目录"
        )
        if not destination:
            return

        selected_date = self._selected_date()
        segment = self.current_history_segment

        def downloaded(paths: tuple[Path, ...]) -> None:
            detail = (
                f"已保存 {len(paths)} 个文件"
                if paths
                else "后台未返回文件列表"
            )
            self._record_task("下载原始数据", "成功", detail)
            QMessageBox.information(self, "下载完成", detail)

        self._run_task(
            "下载原始数据",
            (lambda: self._candidate_history_backend().download_segment_raw(segment, devices, destination))
            if self.backend.mode is OperatorMode.CANDIDATE_V2
            else (lambda: self.backend.download_raw(selected_date, time_sec, devices, destination)),
            downloaded,
        )

    def _show_error_demo(self) -> None:
        self._present_error(
            OperationError(
                title="数据检索失败",
                summary="无法连接波形存储服务。",
                stage="历史数据检索 / SSH",
                code="STORAGE_CONNECTION_FAILED",
                suggestion="检查存储服务地址、网络和凭据配置后重试。",
                details=(
                    "host: configured storage host\n"
                    "operation: list remote capture times\n"
                    "timeout: 10 s\n"
                    "credentials: [redacted]"
                ),
            ),
            record=False,
        )

    def _selected_date(self) -> date:
        return self.history_date.date().toPyDate()

    def _selected_time(self) -> str | None:
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            return self.current_history_segment.time_sec if self.current_history_segment else None
        value = self.history_time.currentData()
        return str(value) if value else None

    def _selected_history_devices(self) -> tuple[str, ...]:
        if self.history_selection_key != self._history_key() or not self.history_selected_devices:
            self._show_validation_error(
                "尚未选择 BPM",
                "请先在历史页点击“选择BPM”，勾选参与处理的设备。",
                "BPM_SELECTION_REQUIRED",
                ("列表只显示所选数据段有波形文件的 BPM。" if self.backend.mode is OperatorMode.CANDIDATE_V2
                 else "列表只显示所选采集时刻有波形文件的 BPM。"),
            )
            return ()
        return self.history_selected_devices

    def _query_devices(self) -> tuple[str, ...]:
        selected = self.area_model.selected_devices()
        if selected:
            return selected
        return tuple(
            device.id
            for device in self.registry.devices
            if device.acquisition_enabled
        )

    def _run_task(self, stage: str, operation, on_success) -> None:
        if self._busy:
            self._show_validation_error(
                "已有任务正在运行",
                "请等待当前后台任务结束。",
                "TASK_BUSY",
                "完成后可继续操作；界面底部会显示当前状态。",
            )
            return
        worker = BackgroundWorker(operation)
        self._active_workers.add(worker)
        # Lock the UI before handing work to the thread pool. Waiting for the
        # worker's queued ``started`` signal leaves a short double-click race.
        self._set_busy(True, f"{stage}处理中…")
        def completed(result):
            self._finish_worker(worker)
            try:
                on_success(result)
            except Exception as exc:
                self._task_failed(worker, stage, exc)

        worker.signals.completed.connect(completed)
        worker.signals.failed.connect(
            lambda exc, current=worker: self._task_failed(
                current, stage, exc
            )
        )
        worker.signals.cancelled.connect(
            lambda current=worker: self._finish_worker(current)
        )
        self.thread_pool.start(worker)

    def _task_failed(
        self,
        worker: BackgroundWorker,
        stage: str,
        exc: Exception,
    ) -> None:
        details = getattr(exc, "worker_traceback", repr(exc))
        self._finish_worker(worker)
        self._present_error(
            OperationError(
                title=f"{stage}失败",
                summary=str(exc) or exc.__class__.__name__,
                stage=stage,
                code=f"{stage.upper().replace(' ', '_')}_FAILED",
                suggestion=(
                    "检查输入、连接和后台配置后再试。若发送状态不明确，"
                    "先核对服务端任务记录，避免重复执行。"
                ),
                details=details,
            )
        )

    def _finish_worker(self, worker: BackgroundWorker) -> None:
        self._active_workers.discard(worker)
        self._set_busy(bool(self._active_workers), "处理中" if self._active_workers else "就绪")

    def _set_busy(self, busy: bool, message: str) -> None:
        self._busy = busy
        self.task_state_text.setText(message)
        self.task_state_dot.setProperty("busy", busy)
        self.task_state_dot.style().unpolish(self.task_state_dot)
        self.task_state_dot.style().polish(self.task_state_dot)
        if busy:
            self.task_progress.setRange(0, 0)
        else:
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(0)
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            self.history_time.setEnabled(not busy)
            self.segment_index.setEnabled(not busy and bool(self.history_segments))
            position = self.segment_index.value()
            self.previous_segment_button.setEnabled(not busy and position > 1)
            self.next_segment_button.setEnabled(not busy and 0 < position < len(self.history_segments))
        for button in (
            self.start_button,
            self.retrieve_button,
            self.select_bpm_button,
            self.align_button,
            self.export_button,
            self.raw_button,
        ):
            button.setEnabled(not busy and not self.ui_only)

    def _record_task(self, stage: str, state: str, detail: str) -> None:
        timestamp = datetime.now().astimezone()
        self._append_journal(
            {
                "timestamp": timestamp.isoformat(),
                "kind": "task",
                "stage": stage,
                "state": state,
                "detail": detail,
                "mode": self.backend.mode.value,
            }
        )

    def _append_journal(self, payload: dict[str, object]) -> None:
        if self.journal_path is None:
            return
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open(
                "a", encoding="utf-8", newline="\n"
            ) as stream:
                stream.write(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                    + "\n"
                )
            self.journal_write_error = None
        except OSError as exc:
            self.journal_write_error = str(exc)

    def _present_error(
        self,
        error: OperationError,
        *,
        record: bool = True,
    ) -> None:
        if record:
            self._record_task(
                error.stage,
                "失败",
                f"{error.code} · {error.summary}",
            )
            self._append_journal(
                {
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "kind": "error",
                    "stage": error.stage,
                    "state": "失败",
                    "code": error.code,
                    "summary": error.summary,
                    "suggestion": error.suggestion,
                    "details": error.details,
                    "mode": self.backend.mode.value,
                }
            )
        show_operation_error(self, error)

    def _show_validation_error(
        self,
        title: str,
        summary: str,
        code: str,
        suggestion: str,
    ) -> None:
        self._present_error(
            OperationError(
                title=title,
                summary=summary,
                stage="界面校验",
                code=code,
                suggestion=suggestion,
            )
        )

    def _filter_tree(self, text: str) -> None:
        needle = text.strip().lower()
        for area_row in range(self.area_model.rowCount()):
            area_item = self.area_model.item(area_row, 0)
            area_match = needle in area_item.text().lower()
            child_matches = False
            for child_row in range(area_item.rowCount()):
                values = [
                    area_item.child(child_row, column).text()
                    for column in range(self.area_model.columnCount())
                ]
                match = not needle or any(needle in value.lower() for value in values)
                self.area_tree.setRowHidden(
                    child_row,
                    area_item.index(),
                    not match,
                )
                child_matches = child_matches or match
            self.area_tree.setRowHidden(
                area_row,
                QModelIndex(),
                bool(needle) and not area_match and not child_matches,
            )

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            * { font-family: "Microsoft YaHei UI"; font-size: 13px; }
            QWidget#shell, QWidget#workspace { background: #f4f7fb; }
            QFrame#sidebar { background: #458cc4; }
            QLabel#brand { color: #ffffff; font-size: 27px; font-weight: 800; }
            QLabel#product { color: #ffffff; font-size: 12px; }
            QPushButton#navButton {
                text-align: left; color: #ffffff; border: 0; border-radius: 8px;
                padding: 12px 14px; background: transparent; font-weight: 600;
            }
            QPushButton#navButton:hover { background: #357fb5; color: white; }
            QPushButton#navButton:checked { background: #357fb5; color: white; }
            QLabel#pageTitle { color: #0f172a; font-size: 24px; font-weight: 750; }
            QLabel#historyPageTitle { color: #0f172a; font-size: 20px; font-weight: 750; }
            QLabel#pageSubtitle { color: #64748b; }
            QWidget#historyPage QPushButton,
            QWidget#historyPage QComboBox,
            QWidget#historyPage QDateEdit,
            QWidget#historyPage QSpinBox { font-size: 14px; min-height: 36px; }
            QWidget#historyPage QComboBox,
            QWidget#historyPage QDateEdit,
            QWidget#historyPage QSpinBox {
                border-color: #c7d3df; border-radius: 6px; padding: 0 8px;
            }
            QWidget#historyPage QComboBox QAbstractItemView { font-size: 14px; }
            QWidget#historyPage QPushButton {
                padding: 0 12px; border: 1px solid #c7d3df;
                border-bottom: 2px solid #aebdcd; border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:1 #edf2f7);
            }
            QWidget#historyPage QPushButton:hover {
                color: #245d88; border-color: #9dbbd4;
                border-bottom-color: #7d9fba;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:1 #e4eef7);
            }
            QWidget#historyPage QPushButton:pressed {
                border-color: #9eb2c6; border-bottom-width: 1px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #dde7f0, stop:1 #edf3f8);
            }
            QWidget#historyPage QPushButton:focus { border-color: #5c9aca; }
            QWidget#historyPage QPushButton:disabled {
                color: #94a3b5; border-color: #dce4ec;
                background: #f2f5f8;
            }
            QWidget#historyPage QPushButton#primaryButton {
                color: white; border-color: #397fb4; border-bottom-color: #245f8c;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5c9fd2, stop:1 #357fb8);
            }
            QWidget#historyPage QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #6baddd, stop:1 #418dc7);
            }
            QWidget#historyPage QPushButton#primaryButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2f73a7, stop:1 #438bc0);
            }
            QWidget#historyPage QPushButton#bpmSelectButton {
                color: #2f6f9e; border-color: #bfd6e8; border-bottom-color: #98b9d3;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f8fcff, stop:1 #e3eff8);
            }
            QWidget#historyPage QPushButton#bpmSelectButton:hover { background: #dcecf8; }
            QWidget#historyPage QPushButton#bpmSelectButton:pressed { background: #c9dfef; }
            QWidget#historyPage QPushButton#primaryButton:disabled,
            QWidget#historyPage QPushButton#bpmSelectButton:disabled {
                color: #94a3b5; border-color: #dce4ec; background: #f2f5f8;
            }
            QWidget#historyPage QLabel#segmentSummary {
                color: #64748b; font-size: 13px;
            }
            QWidget#historyPage QLabel#alignmentStatus {
                color: #475569; font-size: 13px; font-weight: 650;
            }
            QFrame#card {
                background: #ffffff; border: 1px solid #e3eaf3;
                border-radius: 12px;
            }
            QFrame#historyDiagnosticBar {
                background: #f1f7fc; border: 1px solid #d8e7f2;
                border-radius: 8px;
            }
            QFrame#historyDiagnosticBar[level="warning"] {
                background: #fff7e6; border-color: #f0d9a8;
            }
            QFrame#historyDiagnosticBar[level="normal"] {
                background: #f0f7f1; border-color: #d8e7dc;
            }
            QLabel#historyDiagnosticText {
                color: #172033; font-weight: 700; font-size: 13px;
            }
            QLabel#historyDiagnosticEvidence {
                color: #64748b; font-size: 12px;
            }
            QLabel#metricLabel { color: #64748b; font-size: 12px; }
            QLabel#metricValue { color: #0f172a; font-size: 23px; font-weight: 750; }
            QLabel#cardTitle { color: #172033; font-size: 16px; font-weight: 700; }
            QLabel#fieldLabel { color: #475569; font-weight: 650; }
            QFrame#frequencyDisplay {
                background: #f4f8fc; border: 1px solid #e3eaf3;
                border-radius: 8px;
            }
            QLabel#frequencyValue {
                color: #64748b; font-size: 13px; font-weight: 650;
            }
            QLabel#frequencyValue[available="true"] { color: #458cc4; }
            QLabel#selectionText { color: #475569; font-weight: 650; }
            QLabel#segmentSummary { color: #64748b; font-size: 13px; }
            QLabel#alignmentStatus { color: #475569; font-size: 13px; font-weight: 650; }
            QPushButton#primaryButton {
                color: white; background: #458cc4; border-color: #458cc4;
            }
            QPushButton#primaryButton:hover { background: #357fb5; }
            QPushButton#bpmSelectButton {
                color: #357fb5; background: #eef6fb; border-color: #d4e6f2;
            }
            QWidget#historyPage QPushButton#qualityStatus {
                min-height: 34px; padding: 0 8px; border: 1px solid #dce5ee;
                color: #64748b; background: #f7f9fc; border-radius: 6px;
                font-weight: 500;
            }
            QWidget#historyPage QPushButton#qualityStatus[level="warning"] {
                color: #8a6420; background: #fff7e6; border-color: #f0dfb4;
            }
            QWidget#historyPage QPushButton#qualityStatus:hover,
            QWidget#historyPage QPushButton#quietAction:hover {
                color: #235f8a; background: #eef6fb;
                border-color: #d4e6f2;
            }
            QWidget#historyPage QPushButton#quietAction {
                min-height: 34px; padding: 0 10px; border: 1px solid #dce5ee;
                color: #64748b; background: #f7f9fc;
            }
            QWidget#historyPage QPushButton#qualityStatus:disabled,
            QWidget#historyPage QPushButton#quietAction:disabled {
                color: #94a3b5; background: #f7f9fc; border-color: #e4eaf1;
            }
            QDialog { background: #f4f7fb; color: #334155; }
            QListWidget {
                background: #ffffff; color: #334155;
                border: 1px solid #d5deea; border-radius: 7px;
                selection-background-color: #dcecf7;
            }
            QListWidget::item { min-height: 32px; }
            QLineEdit, QComboBox, QDateEdit, QSpinBox {
                min-height: 36px; border: 1px solid #d5deea; border-radius: 7px;
                padding: 0 10px; background: white; color: #1e293b;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {
                border-color: #458cc4;
            }
            QPushButton {
                min-height: 36px; padding: 0 14px; color: #334155;
                border: 1px solid #d5deea; border-radius: 7px; background: white;
                font-weight: 600;
            }
            QPushButton:hover { background: #f1f5f9; border-color: #b9c5d5; }
            QPushButton#startButton {
                min-height: 46px;
                background: #458cc4;
                color: white;
                border-color: #458cc4; font-size: 15px;
            }
            QPushButton#startButton:hover { background: #357fb5; }
            QTreeView#areaTree {
                border: 0; background: white; alternate-background-color: #f8fafc;
                color: #334155; selection-background-color: #dcecf7;
                selection-color: #235f8a;
            }
            QTreeView#areaTree::item { min-height: 34px; }
            QHeaderView::section {
                background: #f8fafc; color: #64748b; border: 0;
                border-bottom: 1px solid #e2e8f0; padding: 9px;
                font-weight: 650;
            }
            QLabel#plotPlaceholder {
                color: #7890ad; background: #f8fafc;
                border: 1px dashed #cbd5e1; border-radius: 10px;
                font-size: 14px;
            }
            QWidget#plotHost { background: #ffffff; }
            QLabel#bunchIdPill {
                color: #357fb5; background: #eaf3f9; border-radius: 10px;
                padding: 5px 10px; font-weight: 650;
            }
            QFrame#taskStatusBar {
                background: #ffffff; border: 1px solid #e3eaf3;
                border-radius: 10px;
            }
            QLabel#taskStateDot { color: #458cc4; }
            QLabel#taskStateDot[busy="true"] { color: #64748b; }
            QLabel#taskStateText { color: #475569; font-weight: 600; }
            QProgressBar#taskProgress {
                max-height: 5px; border: 0; border-radius: 2px;
                background: #e2e8f0;
            }
            QProgressBar#taskProgress::chunk {
                border-radius: 2px; background: #458cc4;
            }
            """
        )
