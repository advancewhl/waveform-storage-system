"""Scalable experimental operator window for beamline-area selection."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from PyQt5.QtCore import QDate, QModelIndex, Qt, QThreadPool, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QFileDialog,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from waveform_storage.config.registry import DeviceRegistry
from waveform_storage.gui.beamline_selection import (
    BeamlineArea,
    build_beamline_areas,
    selected_device_ids,
)
from waveform_storage.gui.error_dialogs import (
    OperationError,
    show_operation_error,
)
from waveform_storage.gui.operator_backend import OperatorBackend, OperatorMode
from waveform_storage.gui.plotting import (
    plot_alignment_result,
    plot_device_waveform,
)
from waveform_storage.gui.workers import BackgroundWorker


class BeamlineAreaModel(QStandardItemModel):
    selection_changed = pyqtSignal()

    AREA_ROLE = Qt.UserRole + 1
    DEVICE_ID_ROLE = Qt.UserRole + 2

    def __init__(
        self,
        registry: DeviceRegistry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.areas = build_beamline_areas(registry)
        self.setHorizontalHeaderLabels(("束线区域 / BPM设备", "设备数", "采集PV", "状态"))
        devices = {device.id: device for device in registry.devices}

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
        area_id = item.data(self.AREA_ROLE)
        if not area_id:
            return
        selected = item.checkState() == Qt.Checked
        self.item(item.row(), 3).setText("整组已选择" if selected else "整组未选择")
        self.item(item.row(), 3).setForeground(
            QColor("#047857" if selected else "#64748b")
        )
        self.selection_changed.emit()

    def selected_area_ids(self) -> frozenset[str]:
        return frozenset(
            item.data(self.AREA_ROLE)
            for row in range(self.rowCount())
            if (item := self.item(row, 0)).checkState() == Qt.Checked
        )

    def selected_area_ids_ordered(self) -> tuple[str, ...]:
        selected = self.selected_area_ids()
        return tuple(
            area.area_id for area in self.areas if area.area_id in selected
        )

    def selected_devices(self) -> tuple[str, ...]:
        return selected_device_ids(self.areas, self.selected_area_ids())

    def set_all(self, selected: bool) -> None:
        state = Qt.Checked if selected else Qt.Unchecked
        for row in range(self.rowCount()):
            self.item(row, 0).setCheckState(state)


class ExperimentalMainWindow(QMainWindow):
    """Scalable operator UI backed by demo or explicit connected adapters."""

    def __init__(
        self,
        registry: DeviceRegistry,
        *,
        backend: OperatorBackend,
        journal_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.backend = backend
        self.journal_path = Path(journal_path) if journal_path else None
        self.journal_write_error: str | None = None
        self.area_model = BeamlineAreaModel(registry, self)
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers: set[BackgroundWorker] = set()
        self._busy = False
        self.alignment_result = None
        self.device_waveform_result = None
        self.device_waveform_key = None
        self.plot_canvas = None
        self.setWindowTitle("SHINE Waveform Console")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._apply_style()
        self._update_selection_summary()

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
        self.tasks_nav = self._nav_button("任务与错误", False)
        side.addWidget(self.capture_nav)
        side.addWidget(self.history_nav)
        side.addWidget(self.tasks_nav)
        side.addStretch(1)
        mode_text = {
            OperatorMode.DEMO: "DEMO\n离线安全演示",
            OperatorMode.LEGACY: "LEGACY\n现场兼容模式",
            OperatorMode.CANDIDATE_V2: "CANDIDATE V2\n结构化协议",
        }[self.backend.mode]
        environment = QLabel(mode_text)
        environment.setObjectName("environment")
        side.addWidget(environment)
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
        self.pages.addWidget(self._tasks_page())
        work.addWidget(self.pages, 1)
        work.addWidget(self._task_status_bar())
        root.addWidget(workspace, 1)

        self.capture_nav.clicked.connect(lambda: self._show_page(0))
        self.history_nav.clicked.connect(lambda: self._show_page(1))
        self.tasks_nav.clicked.connect(lambda: self._show_page(2))

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
        status = QLabel(
            {
                OperatorMode.DEMO: "●  DEMO 离线",
                OperatorMode.LEGACY: "●  LEGACY 现场",
                OperatorMode.CANDIDATE_V2: "●  CANDIDATE V2",
            }[self.backend.mode]
        )
        status.setObjectName("environmentPill")
        layout.addWidget(status)
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
        self.area_tree.expandAll()
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
        self.duration_combo.addItem("10 分钟", 10)
        self.duration_combo.addItem("30 分钟", 30)
        self.duration_combo.addItem("60 分钟", 60)
        control_layout.addWidget(self.duration_combo)
        control_layout.addWidget(self._field_label("频率来源"))
        frequency = QLineEdit("自动读取定时系统PV")
        frequency.setReadOnly(True)
        control_layout.addWidget(frequency)
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
        self.retrieve_button = QPushButton("检索数据")
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
                self.align_button,
                self.bunch_index,
                self.show_id_button,
            )
        ):
            form.addWidget(widget, 0, column)
        self.retrieve_button.clicked.connect(self._retrieve_times)
        self.align_button.clicked.connect(self._align_selected)
        self.bunch_index.valueChanged.connect(self._show_bunch_id)
        self.show_id_button.clicked.connect(self._locate_bunch)
        layout.addWidget(toolbar)

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
        plot_head.addStretch(1)
        self.plot_button = QPushButton("刷新绘图")
        self.export_button = QPushButton("导出对齐数据")
        self.raw_button = QPushButton("下载原始数据")
        plot_head.addWidget(self.plot_button)
        plot_head.addWidget(self.export_button)
        plot_head.addWidget(self.raw_button)
        self.plot_button.clicked.connect(self._render_selected_plot)
        self.export_button.clicked.connect(self._export_alignment)
        self.raw_button.clicked.connect(self._download_raw)
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
            "选择日期与采集时刻后，在这里显示 X / Y 随 BPM 设备的分布"
        )
        self.plot_placeholder.setObjectName("plotPlaceholder")
        self.plot_placeholder.setAlignment(Qt.AlignCenter)
        self.plot_layout.addWidget(self.plot_placeholder, 1)
        plot_layout.addWidget(self.plot_host, 1)
        layout.addWidget(plot, 1)
        return page

    def _tasks_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card = self._card()
        body = QVBoxLayout(card)
        body.setContentsMargins(22, 20, 22, 20)
        title = QLabel("任务与错误中心")
        title.setObjectName("cardTitle")
        description = QLabel(
            "任务与错误会记录阶段、错误码和原因；错误弹窗支持展开详情和复制。"
        )
        description.setObjectName("pageSubtitle")
        if self.journal_path is not None:
            description.setText(
                f"{description.text()} 持久日志：{self.journal_path}"
            )
            description.setWordWrap(True)
        actions = QHBoxLayout()
        demo = QPushButton("预览错误弹窗")
        demo.clicked.connect(self._show_error_demo)
        clear = QPushButton("清空当前列表")
        clear.clicked.connect(lambda: self.task_table.setRowCount(0))
        actions.addWidget(demo)
        actions.addWidget(clear)
        actions.addStretch(1)
        self.task_table = QTableWidget(0, 4)
        self.task_table.setObjectName("taskTable")
        self.task_table.setHorizontalHeaderLabels(
            ("时间", "操作阶段", "状态", "详情")
        )
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.task_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.task_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.task_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch
        )
        body.addWidget(title)
        body.addWidget(description)
        body.addSpacing(10)
        body.addLayout(actions)
        body.addWidget(self.task_table, 1)
        layout.addWidget(card)
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
        self.page_title.setText(("采集控制", "历史检索与绘图", "任务与错误")[index])

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

    def _request_start(self) -> None:
        area_ids = self.area_model.selected_area_ids_ordered()
        devices = self.area_model.selected_devices()
        if not devices:
            self._present_error(
                OperationError(
                    title="无法开始采集",
                    summary="尚未选择任何束线区域。",
                    stage="采集前校验",
                    code="CAPTURE_NO_AREA",
                    suggestion="请勾选至少一个束线区域；区域内BPM将作为整体加入任务。",
                )
            )
            return
        if self.backend.mode is not OperatorMode.DEMO:
            approved_by_area = {
                area_id: tuple(
                    device.id
                    for device in self.registry.devices
                    if device.section == area_id
                    and device.enabled
                    and device.acquisition_enabled
                )
                for area_id in area_ids
            }
            area_ids = tuple(
                area_id
                for area_id in area_ids
                if approved_by_area[area_id]
            )
            devices = tuple(
                device_id
                for area_id in area_ids
                for device_id in approved_by_area[area_id]
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
        if self.backend.mode is not OperatorMode.DEMO:
            reply = QMessageBox.question(
                self,
                "确认发送采集请求",
                (
                    f"将向 {self.backend.mode.value} 后台发送采集请求。\n"
                    f"{len(area_ids)} 个束线区域，{len(devices)} 台BPM，"
                    f"时长 {duration_seconds // 60} 分钟。\n\n"
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
                self._present_error(
                    OperationError(
                        title="采集请求未被接受",
                        summary=result.message,
                        stage="采集请求",
                        code=result.error_code or "CAPTURE_REJECTED",
                        suggestion=(
                            "先检查任务状态和服务端记录；若是 legacy "
                            "超时，不要直接重复提交。"
                        ),
                    )
                )
                return
            self._record_task(
                "采集请求",
                "成功",
                f"{result.mode.value} · {result.status} · {result.message}",
            )
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
                area_ids, devices, duration_seconds
            ),
            accepted,
        )

    def _retrieve_times(self) -> None:
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

    def _align_selected(self) -> None:
        time_sec = self._selected_time()
        if time_sec is None:
            self._show_validation_error(
                "无法开始对齐",
                "尚未选择有效的采集时刻。",
                "ALIGNMENT_NO_TIME",
                "请先检索数据并选择一个采集时刻。",
            )
            return
        selected_date = self._selected_date()
        devices = self._query_devices()

        def aligned(result) -> None:
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
            lambda: self.backend.align(
                selected_date, time_sec, devices
            ),
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
        self.bunch_index.setMaximum(max(0, result.bunch_ids.size - 1))
        self.bunch_index.setValue(0)
        self.plot_device.clear()
        for device_id in device_choices or result.device_ids:
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
                "尚未选择有效的采集时刻。",
                "PLOT_NO_TIME",
                "请先检索并选择采集时刻。",
            )
            return
        selected_date = self._selected_date()
        key = (selected_date.isoformat(), time_sec, device_id)
        if self.device_waveform_key == key and self.device_waveform_result is not None:
            self._install_figure(
                plot_device_waveform(self.device_waveform_result, device_id)
            )
            return

        def loaded(result) -> None:
            if result.bunch_ids.size == 0 or device_id not in result.device_ids:
                self._show_validation_error(
                    "单设备波形为空",
                    f"{device_id} 没有可绘制的 bunchID 数据。",
                    "PLOT_DEVICE_EMPTY",
                    "检查该设备在所选时刻是否存在有效 HDF5 文件。",
                )
                return
            self.device_waveform_result = result
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
            lambda: self.backend.align(
                selected_date,
                time_sec,
                (device_id,),
            ),
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

        def exported(path: Path) -> None:
            self._record_task("导出对齐数据", "成功", str(path))
            QMessageBox.information(self, "导出完成", f"已保存到：\n{path}")

        self._run_task(
            "导出对齐数据",
            lambda: self.backend.export_alignment(
                self.alignment_result,
                self._selected_date(),
                time_sec,
                destination,
            ),
            exported,
        )

    def _download_raw(self) -> None:
        time_sec = self._selected_time()
        if time_sec is None:
            self._show_validation_error(
                "无法下载原始数据",
                "尚未选择有效的采集时刻。",
                "RAW_NO_TIME",
                "请先检索并选择采集时刻。",
            )
            return
        destination = QFileDialog.getExistingDirectory(
            self, "选择原始数据保存目录"
        )
        if not destination:
            return
        devices = self._query_devices()

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
            lambda: self.backend.download_raw(
                self._selected_date(),
                time_sec,
                devices,
                destination,
            ),
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
        value = self.history_time.currentData()
        return str(value) if value else None

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
        worker.signals.completed.connect(on_success)
        worker.signals.completed.connect(
            lambda _result, current=worker: self._finish_worker(current)
        )
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
        self._set_busy(False, "就绪")

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
        for button in (
            self.start_button,
            self.retrieve_button,
            self.align_button,
            self.export_button,
            self.raw_button,
        ):
            button.setEnabled(not busy)

    def _record_task(self, stage: str, state: str, detail: str) -> None:
        timestamp = datetime.now().astimezone()
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        values = (
            timestamp.strftime("%H:%M:%S"),
            stage,
            state,
            detail,
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 2:
                item.setForeground(
                    QColor(
                        {
                            "成功": "#047857",
                            "完成": "#2563eb",
                            "失败": "#dc2626",
                            "已取消": "#64748b",
                        }.get(state, "#334155")
                    )
                )
            self.task_table.setItem(row, column, item)
        self.task_table.scrollToBottom()
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
            QFrame#sidebar { background: #0b1739; }
            QLabel#brand { color: #ffffff; font-size: 27px; font-weight: 800; }
            QLabel#product { color: #8fa7d4; font-size: 12px; }
            QLabel#environment {
                color: #9fb2d5; background: #12244f; border-radius: 9px;
                padding: 12px; line-height: 1.5;
            }
            QPushButton#navButton {
                text-align: left; color: #b8c7e3; border: 0; border-radius: 8px;
                padding: 12px 14px; background: transparent; font-weight: 600;
            }
            QPushButton#navButton:hover { background: #12244f; color: white; }
            QPushButton#navButton:checked { background: #2563eb; color: white; }
            QLabel#pageTitle { color: #0f172a; font-size: 24px; font-weight: 750; }
            QLabel#pageSubtitle { color: #64748b; }
            QLabel#environmentPill {
                color: #0f766e; background: #ccfbf1; border-radius: 12px;
                padding: 6px 12px; font-weight: 650;
            }
            QFrame#card {
                background: #ffffff; border: 1px solid #e3eaf3;
                border-radius: 12px;
            }
            QLabel#metricLabel { color: #64748b; font-size: 12px; }
            QLabel#metricValue { color: #0f172a; font-size: 23px; font-weight: 750; }
            QLabel#cardTitle { color: #172033; font-size: 16px; font-weight: 700; }
            QLabel#fieldLabel { color: #475569; font-weight: 650; }
            QLabel#selectionText { color: #475569; font-weight: 650; }
            QLineEdit, QComboBox, QDateEdit, QSpinBox {
                min-height: 36px; border: 1px solid #d5deea; border-radius: 7px;
                padding: 0 10px; background: white; color: #1e293b;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {
                border-color: #2563eb;
            }
            QPushButton {
                min-height: 36px; padding: 0 14px; color: #334155;
                border: 1px solid #d5deea; border-radius: 7px; background: white;
                font-weight: 600;
            }
            QPushButton:hover { background: #f1f5f9; border-color: #b9c5d5; }
            QPushButton#startButton {
                min-height: 46px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2563eb, stop:1 #4f46e5
                );
                color: white;
                border-color: #2563eb; font-size: 15px;
            }
            QPushButton#startButton:hover { background: #1d4ed8; }
            QTreeView#areaTree {
                border: 0; background: white; alternate-background-color: #f8fafc;
                color: #334155; selection-background-color: #dbeafe;
                selection-color: #1e3a8a;
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
                color: #4338ca; background: #eef2ff; border-radius: 10px;
                padding: 5px 10px; font-weight: 650;
            }
            QFrame#taskStatusBar {
                background: #ffffff; border: 1px solid #e3eaf3;
                border-radius: 10px;
            }
            QLabel#taskStateDot { color: #10b981; }
            QLabel#taskStateDot[busy="true"] { color: #f59e0b; }
            QLabel#taskStateText { color: #475569; font-weight: 600; }
            QProgressBar#taskProgress {
                max-height: 5px; border: 0; border-radius: 2px;
                background: #e2e8f0;
            }
            QProgressBar#taskProgress::chunk {
                border-radius: 2px; background: #2563eb;
            }
            QTableWidget#taskTable {
                border: 1px solid #e5eaf1; border-radius: 8px;
                gridline-color: #edf1f6; background: white;
                alternate-background-color: #f8fafc;
            }
            """
        )
