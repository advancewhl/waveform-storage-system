"""Scalable experimental operator window for beamline-area selection."""

from __future__ import annotations

from collections.abc import Iterable

from PyQt5.QtCore import QDate, QModelIndex, Qt, pyqtSignal
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
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

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
                device_status = QStandardItem(
                    "已配置" if device.acquisition_enabled else "仅注册"
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

    def selected_devices(self) -> tuple[str, ...]:
        return selected_device_ids(self.areas, self.selected_area_ids())

    def set_all(self, selected: bool) -> None:
        state = Qt.Checked if selected else Qt.Unchecked
        for row in range(self.rowCount()):
            self.item(row, 0).setCheckState(state)


class ExperimentalMainWindow(QMainWindow):
    """A previewable UI that scales to hundreds of registered devices."""

    start_capture_requested = pyqtSignal(tuple, int)

    def __init__(self, registry: DeviceRegistry) -> None:
        super().__init__()
        self.registry = registry
        self.area_model = BeamlineAreaModel(registry, self)
        self.setWindowTitle("SHINE Waveform · Experimental UI")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._apply_style()
        self._update_selection_summary()
        self.start_capture_requested.connect(self._preview_capture_request)

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
        environment = QLabel("实验分支\nLegacy通信保持不变")
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
        root.addWidget(workspace, 1)

        self.capture_nav.clicked.connect(lambda: self._show_page(0))
        self.history_nav.clicked.connect(lambda: self._show_page(1))
        self.tasks_nav.clicked.connect(lambda: self._show_page(2))

    def _topbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        heading = QVBoxLayout()
        self.page_title = QLabel("采集控制")
        self.page_title.setObjectName("pageTitle")
        subtitle = QLabel("按束线区域整组选取设备，区域内BPM不可单独选择")
        subtitle.setObjectName("pageSubtitle")
        heading.addWidget(self.page_title)
        heading.addWidget(subtitle)
        layout.addLayout(heading)
        layout.addStretch(1)
        status = QLabel("●  实验环境")
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
        control_layout.addSpacing(6)
        rule = QLabel("选择规则\n一个束线区域是最小采集单元。区域内所有BPM同时加入或同时移除。")
        rule.setObjectName("ruleBox")
        rule.setWordWrap(True)
        control_layout.addWidget(rule)
        control_layout.addStretch(1)
        self.selection_text = QLabel()
        self.selection_text.setObjectName("selectionText")
        control_layout.addWidget(self.selection_text)
        self.start_button = QPushButton("开始采集")
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
        retrieve = QPushButton("检索数据")
        confirm = QPushButton("确认并对齐")
        self.bunch_index = QSpinBox()
        self.bunch_index.setMaximum(9999)
        show_id = QPushButton("显示 bunchID")
        for column, widget in enumerate(
            (
                self.history_date,
                retrieve,
                self.history_time,
                confirm,
                self.bunch_index,
                show_id,
            )
        ):
            form.addWidget(widget, 0, column)
        layout.addWidget(toolbar)

        plot = self._card()
        plot_layout = QVBoxLayout(plot)
        plot_layout.setContentsMargins(22, 18, 22, 18)
        plot_head = QHBoxLayout()
        title = QLabel("BPM位置分布")
        title.setObjectName("cardTitle")
        plot_head.addWidget(title)
        plot_head.addStretch(1)
        plot_head.addWidget(QPushButton("导出对齐数据"))
        plot_head.addWidget(QPushButton("下载原始数据"))
        plot_layout.addLayout(plot_head)
        placeholder = QLabel("选择日期与采集时刻后，在这里显示 X / Y 随 BPM 设备的分布")
        placeholder.setObjectName("plotPlaceholder")
        placeholder.setAlignment(Qt.AlignCenter)
        plot_layout.addWidget(placeholder, 1)
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
            "任务失败会记录阶段、错误码、原因与建议；错误弹窗支持展开详情和复制。"
        )
        description.setObjectName("pageSubtitle")
        demo = QPushButton("预览错误弹窗")
        demo.setFixedWidth(160)
        demo.clicked.connect(self._show_error_demo)
        body.addWidget(title)
        body.addWidget(description)
        body.addSpacing(10)
        body.addWidget(demo)
        body.addStretch(1)
        layout.addWidget(card)
        return page

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
        devices = self.area_model.selected_devices()
        if not devices:
            show_operation_error(
                self,
                OperationError(
                    title="无法开始采集",
                    summary="尚未选择任何束线区域。",
                    stage="采集前校验",
                    code="CAPTURE_NO_AREA",
                    suggestion="请勾选至少一个束线区域；区域内BPM将作为整体加入任务。",
                ),
            )
            return
        duration = int(self.duration_combo.currentData())
        self.start_capture_requested.emit(devices, duration * 60)

    def _show_error_demo(self) -> None:
        show_operation_error(
            self,
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
        )

    def _preview_capture_request(
        self,
        devices: tuple[str, ...],
        duration_seconds: int,
    ) -> None:
        QMessageBox.information(
            self,
            "实验UI预览",
            (
                f"已生成整组选择：{len(self.area_model.selected_area_ids())} "
                f"个束线区域，共 {len(devices)} 台BPM。\n"
                f"采集时长：{duration_seconds // 60} 分钟。\n\n"
                "当前实验入口不会发送网络请求，也不会触发生产采集。"
            ),
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
            QLabel#ruleBox {
                color: #334155; background: #eff6ff; border: 1px solid #bfdbfe;
                border-radius: 9px; padding: 13px;
            }
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
                min-height: 46px; background: #2563eb; color: white;
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
            """
        )
