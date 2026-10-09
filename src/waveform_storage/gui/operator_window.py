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
        self.history_selected_devices: tuple[str, ...] = ()
        self.history_selection_key: tuple | None = None
        self.history_runs = ()
        self.history_segments = ()
        self.current_history_run = None
        self.current_history_segment = None
        self._history_generation = 0
        self.device_waveform_result = None
        self.device_waveform_key = None
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
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        toolbar = self._card()
        form = QGridLayout(toolbar)
        form.setContentsMargins(20, 16, 20, 16)
        form.setHorizontalSpacing(12)
        self.history_date = QDateEdit(QDate.currentDate())
        self.history_date.setCalendarPopup(True)
        self.history_time = QComboBox()
        self.history_time.addItem("先检索可用时刻")
        self.history_run_combo = self.history_time
        self.segment_index = QSpinBox()
        self.segment_index.setRange(0, 0)
        self.previous_segment_button = QPushButton("上一段")
        self.next_segment_button = QPushButton("下一段")
        self.segment_label = QLabel("尚未选择数据段")
        self.previous_segment_button.setEnabled(False)
        self.next_segment_button.setEnabled(False)
        self.retrieve_button = QPushButton(
            "检索任务" if self.backend.mode is OperatorMode.CANDIDATE_V2 else "检索数据"
        )
        self.select_bpm_button = QPushButton("选择BPM")
        self.align_button = QPushButton("确认并对齐")
        self.bunch_index = QSpinBox()
        self.bunch_index.setMaximum(9999)
        self.bunch_index.setPrefix("序号 ")
        self.bunch_index.setToolTip(
            "对齐结果中的位置序号；0 表示第一个 bunchID"
        )
        self.show_id_button = QPushButton("定位并绘图")
        for column, widget in enumerate(
            (
                self.history_date,
                self.retrieve_button,
                self.history_time,
                self.select_bpm_button,
                self.align_button,
                self.bunch_index,
                self.show_id_button,
            )
        ):
            form.addWidget(widget, 0, column)
        self.retrieve_button.clicked.connect(self._retrieve_times)
        self.select_bpm_button.clicked.connect(self._select_history_devices)
        self.align_button.clicked.connect(self._align_selected)
        if self.backend.mode is OperatorMode.CANDIDATE_V2:
            self.history_time.setItemText(0, "先检索采集任务")
            form.removeWidget(self.history_time)
            form.addWidget(self.history_time, 0, 2, 1, 5)
            for widget in (self.select_bpm_button, self.align_button, self.bunch_index,
                           self.show_id_button):
                form.removeWidget(widget)
            for column, widget in enumerate((self.previous_segment_button, self.segment_index,
                                              self.next_segment_button, self.select_bpm_button,
                                              self.align_button, self.bunch_index, self.show_id_button)):
                form.addWidget(widget, 1, column)
            form.addWidget(self.segment_label, 2, 0, 1, 7)
            self.history_date.dateChanged.connect(self._history_date_changed)
            self.history_time.currentIndexChanged.connect(self._history_run_changed)
            self.segment_index.valueChanged.connect(self._history_segment_changed)
            self.previous_segment_button.clicked.connect(
                lambda: self.segment_index.setValue(self.segment_index.value() - 1))
            self.next_segment_button.clicked.connect(
                lambda: self.segment_index.setValue(self.segment_index.value() + 1))
        else:
            self.history_date.dateChanged.connect(self._clear_history_selection)
            self.history_time.currentIndexChanged.connect(self._clear_history_selection)
        self.bunch_index.valueChanged.connect(self._show_bunch_id)
        self.show_id_button.clicked.connect(self._locate_bunch)
        layout.addWidget(toolbar)

        self.history_selection_text = QLabel("尚未选择参与对齐的 BPM")
        self.history_selection_text.setObjectName("selectionText")
        layout.addWidget(self.history_selection_text)

        plot = self._card()
        plot_layout = QVBoxLayout(plot)
        plot_layout.setContentsMargins(22, 18, 22, 18)
        plot_head = QHBoxLayout()
        self.plot_title = QLabel("BPM位置分布")
        self.plot_title.setObjectName("cardTitle")
        plot_head.addWidget(self.plot_title)
        self.plot_mode = QComboBox()
        self.plot_mode.setObjectName("plotMode")
        self.plot_mode.addItem("束线分布", "distribution")
        self.plot_mode.addItem("单设备波形", "device")
        self.plot_mode.setFixedWidth(132)
        self.plot_device = QComboBox()
        self.plot_device.setObjectName("plotDevice")
        self.plot_device.setMinimumWidth(210)
        self.plot_device.setEnabled(False)
        self.plot_device.setVisible(False)
        self.plot_device.addItem("对齐后选择BPM", None)
        plot_head.addWidget(self.plot_mode)
        plot_head.addWidget(self.plot_device)
        self.bunch_id_label = QLabel("尚未选择 bunchID")
        self.bunch_id_label.setObjectName("bunchIdPill")
        plot_head.addWidget(self.bunch_id_label)
        self.data_duration_label = QLabel("数据时长：—")
        self.data_duration_label.setToolTip("最后一个 bunch ID 减第一个 bunch ID，换算为秒")
        plot_head.addWidget(self.data_duration_label)
        plot_head.addStretch(1)
        self.plot_button = QPushButton("刷新绘图")
        self.export_button = QPushButton("导出对齐数据")
        self.raw_button = QPushButton("下载原始数据")
        self.history_spectrum_button = QPushButton("频谱分析")
        plot_head.addWidget(self.plot_button)
        plot_head.addWidget(self.export_button)
        plot_head.addWidget(self.raw_button)
        plot_head.addWidget(self.history_spectrum_button)
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
        layout.addWidget(plot, 1)
        return page

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
        for page, button in enumerate((self.capture_nav, self.history_nav, self.spectrum_nav)):
            button.setChecked(page == index)

    def _analyze_history_alignment(self) -> None:
        try:
            if self.alignment_result is None:
                raise ValueError("请先选择 BPM 并生成历史对齐结果")
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
            self._show_page(2)
        except ValueError as exc:
            QMessageBox.warning(self, "尚无法分析", str(exc))

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
            f"第 {position} / {count} 段（全任务） · S{segment.capture_sequence:05d} · {clock} · {bpm_count} BPM"
        )

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
            self._clear_alignment_view()
            self.history_selection_text.setText(
                f"已选择 {len(self.history_selected_devices)} / "
                f"{len(available_ids)} 台可用 BPM"
            )
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

        def aligned(result) -> None:
            if generation != self._history_generation:
                return
            if not self.load_alignment_result(result, devices):
                return
            self._record_task(
                "波形对齐",
                "成功",
                (
                    f"{len(result.device_ids)} 台BPM · "
                    f"{result.bunch_ids.size} 个bunchID · "
                    f"跳过 {len(result.skipped_devices)} 台"
                ),
            )

        self._run_task(
            "波形对齐",
            (lambda: self._candidate_history_backend().align_segment(segment, devices))
            if self.backend.mode is OperatorMode.CANDIDATE_V2
            else (lambda: self.backend.align(selected_date, time_sec, devices)),
            aligned,
        )

    def load_alignment_result(
        self,
        result,
        device_choices: tuple[str, ...] | None = None,
    ) -> bool:
        """Load one result into both plot modes; useful for demo rendering."""

        if result.bunch_ids.size == 0:
            self._show_validation_error(
                "对齐结果为空",
                "所选设备之间没有可绘制的共同 bunchID。",
                "ALIGNMENT_EMPTY",
                "查看任务详情及设备质量报告，或缩小设备范围后重试。",
            )
            return False
        self.alignment_result = result
        self._set_data_duration(result)
        self.bunch_index.setMaximum(max(0, result.bunch_ids.size - 1))
        self.bunch_index.setValue(0)
        self.plot_device.clear()
        for device_id in result.device_ids:
            self.plot_device.addItem(device_id, device_id)
        self.plot_device.setEnabled(self.plot_device.count() > 0)
        self.device_waveform_result = None
        self.device_waveform_key = None
        self._show_bunch_id()
        self._render_selected_plot()
        return True

    def _show_bunch_id(self) -> None:
        result = self.alignment_result
        if result is None or result.bunch_ids.size == 0:
            self.bunch_id_label.setText("尚未加载对齐数据")
            return
        index = min(self.bunch_index.value(), result.bunch_ids.size - 1)
        self.bunch_index.setValue(index)
        self.bunch_id_label.setText(
            f"Index {index}  ·  bunchID {int(result.bunch_ids[index])}"
        )

    def _set_data_duration(self, result) -> None:
        ids = result.bunch_ids
        if ids.size == 0:
            self.data_duration_label.setText("数据时长：—")
        else:
            seconds = (int(ids[-1]) - int(ids[0])) / 1_000_000
            self.data_duration_label.setText(f"数据时长：{seconds:.6f} s")

    def _locate_bunch(self) -> None:
        self._render_selected_plot()

    def _plot_mode_changed(self) -> None:
        distribution = self.plot_mode.currentData() == "distribution"
        self.bunch_index.setEnabled(distribution)
        self.show_id_button.setEnabled(distribution)
        self.plot_device.setVisible(not distribution)
        self.plot_title.setText(
            "BPM位置分布" if distribution else "单设备波形"
        )
        if distribution:
            self._show_bunch_id()
        else:
            self._plot_device_changed()

    def _plot_device_changed(self) -> None:
        if self.plot_mode.currentData() != "device":
            return
        device_id = self.plot_device.currentData()
        result = self.alignment_result
        if device_id and result is not None:
            row = result.device_ids.index(device_id)
            sample_count = int(result.valid_mask[row].sum())
            self.bunch_id_label.setText(
                f"{device_id}  ·  {sample_count} 个bunchID"
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
        if self.device_waveform_key == key and self.device_waveform_result is not None:
            self._set_data_duration(self.device_waveform_result)
            self._install_figure(
                plot_device_waveform(self.device_waveform_result, device_id)
            )
            return

        def loaded(result) -> None:
            if generation != self._history_generation:
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

    def _clear_alignment_view(self) -> None:
        self.alignment_result = None
        if hasattr(self, "spectrum_panel"):
            self.spectrum_panel.reset_history_context()
        self.data_duration_label.setText("数据时长：—")
        self.device_waveform_result = None
        self.device_waveform_key = None
        self.plot_device.clear()
        self.plot_device.setEnabled(False)
        self.bunch_id_label.setText("尚未选择 bunchID")
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
            QLabel#pageSubtitle { color: #64748b; }
            QFrame#card {
                background: #ffffff; border: 1px solid #e3eaf3;
                border-radius: 12px;
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
