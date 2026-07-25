
import functools
import time
import numpy as np
import json
import os
import PyQt5
from PyQt5.QtCore import (
    pyqtSignal, Qt, QObject, QSize, QTimer, QTextStream, QSharedMemory, QDate,
    QThreadPool,
)
from PyQt5.QtNetwork import QLocalSocket, QLocalServer
from PyQt5.QtWidgets import QMainWindow, QHeaderView, QWidget, QHBoxLayout, QTableWidgetItem, QLabel, QPushButton, \
    QListWidget, QListWidgetItem, QGraphicsScene, QGraphicsPixmapItem, QMessageBox, QFileDialog
from PyQt5 import QtWidgets
from PyQt5.QtGui import QColor, QFont, QTextCursor, QPixmap, QBrush
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from waveform_storage.config.registry import load_registry
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.device_selection import build_button_specs
from waveform_storage.gui.controllers import (
    GuiController,
    QueryParameters,
    TaskState,
)
from waveform_storage.gui.workers import BackgroundWorker
from waveform_storage.legacy.gui_capture_adapter import (
    read_legacy_frequency,
    send_legacy_message,
)

from zmq_client_dealer import send_start
from mainWindow import Ui_StreamData
# from mainWindow_update251105 import Ui_StreamData
from caproto.threading.client import Context
# from h5_data_process import search_time_sec,get_para,main_dataprocess,remote_search_time_sec,remote_find_h5_files
# from overlap import get_para,main_dataprocess,remote_search_time_sec,remote_find_h5_files,remote_save_timesec_h5_files,save_newfile
from overlap import (
    AlignmentResultCache,
    alignment_bunch_id,
    alignment_summary,
    clear_retrieval_cache,
    get_para,
    main_alignment_process,
    plot_alignment_result,
    remote_save_timesec_h5_files,
    remote_search_time_sec,
    save_alignment_result,
)
import matplotlib
matplotlib.use('Qt5Agg')  # 必须在导入 pyplot 之前设置
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
# pyuic5 mainWindow.ui -o mainWindow.py
import re
import fcntl
import struct
from datetime import datetime

APP_SETTINGS = AppSettings.from_env(project_root=PROJECT_ROOT)
CONFIG_DIR = APP_SETTINGS.config_dir

class MyCommunicate(QObject):
    update_table_signal = pyqtSignal(str)
    ok_signal = pyqtSignal(str)

class LockManager:
    """锁管理类：自动检测并生成所需文件，支持状态恢复"""
# path = '/home/desktop-user-a/Desktop/AlarmProject/20251011'  home/site-user/work/StreamDataPro
    def __init__(self):
        self.lock_file = "/home/site-user/waveform_data/lock/button_lock.lock"  # 锁文件路径
        self.status_file = "/home/site-user/waveform_data/lock/lock_status.json"  # 状态文件路径
        self.fd = None  # 锁文件描述符
        self.lock_duration = 600
        self._init_files()
        self._load_lock_duration()  # 锁定时长（秒）)

        # 初始化：自动检测并生成所需文件


    def _init_files(self):
        """自动检测并生成lock_file和status_file（若不存在）"""
        # 生成锁文件（若不存在）
        if not os.path.exists(self.lock_file):
            with open(self.lock_file, "w") as f:
                pass  # 创建空文件startButton
            # os.chmod(self.lock_file, 0o777)
            print(f"已自动生成锁文件：{self.lock_file}")

        # 生成状态文件（若不存在）
        if not os.path.exists(self.status_file):
            # 初始化状态为未锁定
            init_status = {
                "is_locked": False,
                "start_time": None,
                # "pid": None,
                "duration": self.lock_duration,
                "last_save": datetime.now().isoformat()
            }
            with open(self.status_file, "w") as f:
                json.dump(init_status, f, indent=2)
            # os.chmod(self.status_file,0o777)
            print(f"已自动生成状态文件：{self.status_file}")
    def _load_lock_duration(self):

        try:
            # os.chmod(self.status_file, 0o777)
            with open(self.status_file, "r") as f:
                status = json.load(f)
            # os.chmod(self.status_file, 0o777)
            self.lock_duration = status.get("duration",self.lock_duration)
        except  Exception as e:
            print('get lock_duration from file failed')
    def _load_status(self):
        """加载状态文件（确保文件已存在）"""
        try:
            current = oct(os.stat(self.status_file).st_mode)[-3:]
            print(current)
            # os.chmod(self.status_file, 0o777)
            with open(self.status_file, "r") as f:
                status = json.load(f)
            # os.chmod(self.status_file, 0o777)
            return {
                "is_locked": status.get("is_locked"),
                "start_time": status.get("start_time")
            }
        except Exception as e:
            print(f"加载状态文件失败，重置为初始状态：{e}")
            # 若文件损坏，重置状态文件
            self._init_files()
            return {"is_locked": False, "start_time": None}

    def _save_status(self, is_locked, start_time=None):
        """保存状态到文件"""
        status = {
            "is_locked": is_locked,
            "start_time": start_time,
            # "pid": os.getpid(),
            "duration": self.lock_duration,
            "last_save": datetime.now().isoformat()
        }
        # os.chmod(self.status_file, 0o777)
        with open(self.status_file, "w") as f:
            json.dump(status, f, indent=2)


    def get_remaining_time(self):
        """计算剩余时间：总时长 -（当前时间 - 开始时间）"""
        status = self._load_status()
        if not status["is_locked"] or not status["start_time"]:
            return 0

        try:
            start = datetime.fromisoformat(status["start_time"])
            elapsed = (datetime.now() - start).total_seconds()
            # print(f"self.lock_duration - elapsed{self.lock_duration - elapsed}")
            return max(0, self.lock_duration - elapsed)
        except Exception:
            return 0

    def acquire_lock(self):
        """获取锁并记录开始时间"""
        try:
            # print('acquire_lock')
            self.fd = open(self.lock_file, "a+")

            # os.chmod(self.lock_file, 0o777)
            lock_struct = struct.pack('hhllh', fcntl.F_WRLCK, 0, 0, 0, 0)
            fcntl.fcntl(self.fd, fcntl.F_SETLK, lock_struct)

            start_time = datetime.now().isoformat()
            self._save_status(is_locked=True, start_time=start_time)
            return True
        except BlockingIOError:
            if self.fd:
                self.fd.close()
            self.fd = None
            return False
        except Exception as e:
            print(f"获取锁失败：{e}")
            if self.fd:
                self.fd.close()
            self.fd = None
            return False

    def release_lock(self):
        """时间到后释放锁"""
        if self.fd:
            try:
                unlock_struct = struct.pack('hhllh', fcntl.F_UNLCK, 0, 0, 0, 0)
                fcntl.fcntl(self.fd, fcntl.F_SETLK, unlock_struct)
                self.fd.close()
            except Exception as e:
                print(f"释放锁失败：{e}")
        self.fd = None
        self._save_status(is_locked=False)

    def try_restore(self):
        """恢复状态（依赖自动生成的文件）"""
        status = self._load_status()
        if not status["is_locked"] or not status["start_time"]:
            return False

        remaining = self.get_remaining_time()
        if remaining <= 0:
            self._save_status(is_locked=False)
            return False

        try:
            self.fd = open(self.lock_file, "a+")
            lock_struct = struct.pack('hhllh', fcntl.F_WRLCK, 0, 0, 0, 0)
            fcntl.fcntl(self.fd, fcntl.F_SETLK, lock_struct)
            return True
        except BlockingIOError:
            if self.fd:
                self.fd.close()
            self.fd = None
            self._save_status(is_locked=False)
            return False
        except Exception as e:
            print(f"恢复锁失败：{e}")
            if self.fd:
                self.fd.close()
            self.fd = None
            return False

    def get_status_text(self):
        """生成状态显示文本"""
        status = self._load_status()
        remaining = self.get_remaining_time()

        if status["is_locked"] and remaining > 0:
            return (f"锁定中 | 剩余: {int(remaining)}秒 "
                    f"开始于: {status['start_time'].split('.')[0]}")
        else:
            return "未锁定 | 可点击操作"

#appimagekit-gqrx.svg
class MainWindow(QMainWindow, Ui_StreamData):
    # updateSignal = pyqtSignal(deque)
    updateSignal = pyqtSignal()

    def __init__(self):
        super(MainWindow, self).__init__()
        self.settings = APP_SETTINGS
        self.setupUi(self)
        self.timer = QTimer(self)
        self.interval = 10
        self.controller = GuiController()
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers = set()
        self.alignment_cache = AlignmentResultCache()
        self.device_registry = load_registry(CONFIG_DIR / "devices.json")
        self.device_button_specs = build_button_specs(self.device_registry)
        self.device_buttons = {}
        # LockManager
        self.lock_manager = LockManager()
        self.is_locked = False
        self.timer.timeout.connect(self.update_state)
        self.restore_state()




        self.progressBar.setVisible(False)
        self.setPVButtons()
        self._connect_actions()
        self.dateEdit.setDate(QDate.currentDate())
        with open(CONFIG_DIR / 'pv_list_ALL.txt', 'r', encoding='utf-8') as file:
            raw_data = file.read().strip()
            raw_doc_data = "[" + raw_data + "]"
        pv_data_list = json.loads(raw_doc_data)
        self.pv_list = [item['pv'].replace('-E:RESULT_CAPTURE', '').replace('-', '_').replace(':', '_') for item in
                             pv_data_list]

    def restore_state(self):
        if self.lock_manager.try_restore():
            self.is_locked = True
            self.startButton.setEnabled(False)
            self.startButton.setText("锁定中...")
            self.timer.start(1000)
        self.update_state()
    def _connect_actions(self):
        action_bindings = (
            (self.startButton, self.startButton_click),
            (self.retrievalButton, self.retrievalButton_click),
            (self.confirmButton, self.confirmButton_click),
            (self.showidButton, self.showidButton_click),
            (self.plotButton, self.plotButton_click),
            (self.downloadButton, self.downloadButton_click),
            (self.downloadRawDataButton, self.rawDataDownload_click),
        )
        for button, handler in action_bindings:
            button.clicked.connect(handler)

    def setPVButtons(self):
        missing_widgets = []
        for spec in self.device_button_specs:
            button = self.findChild(QPushButton, spec.widget_name)
            if button is None:
                missing_widgets.append(spec.widget_name)
                continue
            button.setChecked(spec.default_selected)
            self.device_buttons[spec.device_id] = button
        if missing_widgets:
            print(
                "registry devices without current static GUI buttons:",
                missing_widgets,
            )

    def _selected_capture_devices(self):
        return [
            spec.device_id
            for spec in self.device_button_specs
            if spec.device_id in self.device_buttons
            and self.device_buttons[spec.device_id].isChecked()
        ]
    def update_state(self):
        remaining = self.lock_manager.get_remaining_time()
        # print(f'remaining--------->{remaining}')
        if remaining <= 0 and self.is_locked:
            self.lock_manager.release_lock()
            self.is_locked = False
            self.startButton.setEnabled(True)
            self.startButton.setText(f"Start Capture")
            self.timer.stop()
        self.label_9.setText(self.lock_manager.get_status_text())

    def closeEvent(self, event):
        for worker in tuple(self._active_workers):
            worker.cancel()
        if self.lock_manager.fd:
            try:
                self.lock_manager.fd.close()
            except Exception:
                pass
            self.lock_manager.fd = None
        event.accept()

    def _set_data_buttons_enabled(self, enabled):
        for button in (
            self.retrievalButton,
            self.confirmButton,
            self.showidButton,
            self.plotButton,
            self.downloadButton,
            self.downloadRawDataButton,
        ):
            button.setEnabled(enabled)

    def _start_background(
        self,
        state,
        operation,
        on_completed,
        *,
        query=None,
        ready=False,
        store_result=False,
    ):
        try:
            self.controller.begin(state, query)
        except RuntimeError:
            return None
        worker = BackgroundWorker(operation)
        self._active_workers.add(worker)
        self._set_data_buttons_enabled(False)
        worker.signals.progress.connect(self.progressBar.setValue)

        def completed(value):
            self.controller.complete(
                value if store_result else None,
                ready=ready,
            )
            self._active_workers.discard(worker)
            self._set_data_buttons_enabled(True)
            on_completed(value)

        def failed(error):
            self.controller.fail(error)
            self._active_workers.discard(worker)
            self._set_data_buttons_enabled(True)
            QMessageBox.warning(self, "Task failed", str(error))

        def cancelled():
            self.controller.cancel()
            self._active_workers.discard(worker)
            self._set_data_buttons_enabled(True)

        worker.signals.completed.connect(completed)
        worker.signals.failed.connect(failed)
        worker.signals.cancelled.connect(cancelled)
        self.thread_pool.start(worker)
        return worker

    def startButton_click(self):

        interval_origin = self.interval_comboBox.currentText()
        print(f'interval_origin:{interval_origin}')
        self.interval = re.sub(r'[^0-9]', '', interval_origin)
        print(f'interval:{self.interval}')
        self.lock_manager.lock_duration = int(self.interval)*60
        if not self.is_locked and self.lock_manager.acquire_lock():
            self.is_locked = True
            self.startButton.setEnabled(False)
            self.startButton.setText("锁定中...")
            self.timer.start(1000)
        self.update_state()

        selected_devices = self._selected_capture_devices()
        print(f"selected capture devices: {selected_devices}")

        frequency = read_legacy_frequency(
            Context,
            timeout_address="192.0.2.1:6310",
        )
        print(frequency)
        interval_origin = self.interval_comboBox.currentText()
        print(f'interval_origin:{interval_origin}')
        interval = re.sub(r'[^0-9]','',interval_origin)
        print(f'interval:{interval}')
        #
        # #if frequency==100 or frequency==1000 or frequency==2 or frequency==10:
        if frequency and selected_devices:
            ###########################ZMQ stop send message###########
            print("find frequency and BPMs names")
            message = f"{frequency},{interval},{selected_devices}"
            # print(message)

            try:
                return_message = send_legacy_message(
                    send_start,
                    server_host=self.settings.zmq_server_host,
                    server_port=self.settings.zmq_server_port,
                    message=message,
                )
                print("response from server:", return_message)
            except:
                print('something wrong in connecting serve')
        else:
            msg = QMessageBox()
            msg.setWindowTitle("Reminder")
            msg.setText("Please set the frequency!")
            font = QFont()
            font.setPointSize(12)
            msg.setFont(font)
            msg.exec_()

        # os.environ['EPICS_CA_ADDR_LIST'] = "192.0.2.1:6310"
        # ctx = Context()
        # fre_pv = ctx.get_pvs("AC-LA-INJ1-TIM:WRN1:MCFQ8")[0]
        # frequency = fre_pv.read().data[0]
        # print(frequency)
        #
        # #if frequency==100 or frequency==1000 or frequency==2 or frequency==10:
        # if frequency:
        #     #send_start(server_host="192.0.2.4", message=str(frequency))   #gai cheng 8.30   fu wu qi de  ip
        #     send_start(server_host="192.0.2.2", message=str(frequency))
        #     self.progressBar.setVisible(True)
        #     self.progressBar.setValue(100)
        #     self.progressBar.setFormat("Fetching waveform data. The next acquisition can be started in 10 minitues")
        #     # self.startButton.setEnabled(False)
        #     # self.retrievalButton.setEnabled(False)
        #     # self.downloadButton.setEnabled(False)
        #     # self.progressBar.setValue(10)
        #     # time.sleep(150)             #5min
        #     # self.progressBar.setValue(50)
        #     # time.sleep(150)            #5min
        #     # self.progressBar.setValue(100)
        #     # self.progressBar.setFormat("Finished")
        #     # self.startButton.setEnabled(True)
        #     # self.retrievalButton.setEnabled(True)
        #     # self.downloadButton.setEnabled(True)
        #     #time.sleep(1)
        #     #self.startBPMs()
        # else:
        #     msg = QMessageBox()
        #     msg.setWindowTitle("Reminder")
        #     msg.setText("Please set the frequency!")
        #     font = QFont()
        #     font.setPointSize(12)
        #     msg.setFont(font)
        #     msg.exec_()

    def retrievalButton_click(self):
        self.alignment_cache.clear()
        clear_retrieval_cache()
        self.hostname = self.settings.storage_host
        self.username = self.settings.storage_user
        self.password = self.settings.storage_password

        date = self.dateEdit.date().toPyDate()    #2025-09-30  xu yao cheng wei (quan ju bian liang)  fa song gei shu ju chu li
        print(str(date))
        self.runID = str(date).replace('-', '')

        # remote_path = os.path.join("/home/desktop-user-a/Desktop/SHINE_DATA/AC/LA/",runID,"raw/BIData/")
        self.remote_path = os.path.join("/SHINE_Data/AC/LA/",self.runID,"raw/BIData/")

        query = QueryParameters(
            self.remote_path,
            self.runID,
            "",
            tuple(self.pv_list),
        )

        def completed(test):
            format_test = [f"{t[0:2]}:{t[2:4]}:{t[4:6]}" for t in test]
            print(f'test---->{format_test}')
            self.comboBox.clear()
            self.comboBox.addItems(format_test)
            self.comboBox.setCurrentIndex(0)

        self._start_background(
            TaskState.RETRIEVING,
            lambda: remote_search_time_sec(
                hostname=self.hostname,
                username=self.username,
                remote_path=self.remote_path,
                password=self.password,
            ),
            completed,
            query=query,
        )

    def _ensure_alignment_result(self, time_sec):
        key = (
            self.remote_path,
            self.runID,
            time_sec,
            tuple(self.pv_list),
        )
        result = self.alignment_cache.get_or_load(
            key,
            lambda: main_alignment_process(
                self.remote_path,
                self.runID,
                time_sec,
                self.pv_list,
            ),
        )
        self.alignment_result = result
        return result

    def _require_aligned_bunch_ids(self, result):
        if result.bunch_ids.size:
            return True
        skipped = [
            f"{item.device_id or '<unknown>'}: {item.reason}"
            for item in result.skipped_devices
        ]
        detail = "\n".join(skipped[:10]) or "No usable device data were found."
        QMessageBox.warning(
            self,
            "No aligned data",
            f"No common bunchID is available for this query.\n\n{detail}",
        )
        return False

    def _run_alignment(self, time_sec, callback):
        query = QueryParameters(
            self.remote_path,
            self.runID,
            time_sec,
            tuple(self.pv_list),
        )

        def completed(result):
            self.alignment_result = result
            callback(result)

        return self._start_background(
            TaskState.ALIGNING,
            lambda: self._ensure_alignment_result(time_sec),
            completed,
            query=query,
            ready=True,
            store_result=True,
        )

    def confirmButton_click(self):
        # self.progressBar.setVisible(True)
        text = self.comboBox.currentText()
        time_sec = get_para(text)
        def completed(result):
            indexrange = alignment_summary(result)
            self.index_spinBox.setMaximum(max(0, result.bunch_ids.size - 1))
            print(indexrange)
            self.rangelabel.setText(indexrange)
            self._require_aligned_bunch_ids(result)

        self._run_alignment(time_sec, completed)

    def showidButton_click(self):
        # self.index_spinBox.setValue(0)  ###########设置默认值0

        self.selectedIDindex=self.index_spinBox.value()
        text = self.comboBox.currentText()
        time_sec = get_para(text)
        def completed(result):
            if not self._require_aligned_bunch_ids(result):
                return
            current_bunchID = alignment_bunch_id(
                result, self.selectedIDindex
            )
            self.current_bunchID = str(current_bunchID)
            self.select_bunchID_show.setText(self.current_bunchID)
            print('show bunchID', self.current_bunchID)

        self._run_alignment(time_sec, completed)

    def plotButton_click(self):

        text = self.comboBox.currentText()
        time_sec = get_para(text)
        print("plot user select time_sec:", time_sec)
        def completed(result):
            if not self._require_aligned_bunch_ids(result):
                return
            self.selectedIDindex = self.index_spinBox.value()
            current_bunchID = alignment_bunch_id(
                result, self.selectedIDindex
            )
            self.current_bunchID = str(current_bunchID)
            self.select_bunchID_show.setText(self.current_bunchID)
            self.fig = plot_alignment_result(result, self.selectedIDindex)
            self.canvas = FigureCanvas(self.fig)
            if self.groupBox.layout():
                QtWidgets.QWidget().setLayout(self.groupBox.layout())
            self.gridlayout = QtWidgets.QGridLayout(self.groupBox)
            self.gridlayout.addWidget(self.canvas)

        self._run_alignment(time_sec, completed)

    def downloadButton_click(self):
        self.progressBar.setVisible(True)
        text = self.comboBox.currentText()
        #print(type(text))
        ######################edit251010-start##########
        time_sec = get_para(text)
        #print("user select time_sec:", time_sec)
        def alignment_completed(result):
            if not self._require_aligned_bunch_ids(result):
                return
            directory = QFileDialog.getExistingDirectory(
                self,
                "Please choose the directory",
                os.path.join(os.path.expanduser("~"), "Downloads")
            )
            if not directory:
                return
            self.progressBar.setFormat("Data is downloading")
            self.progressBar.setValue(10)

            def export_completed(new_h5_filename):
                self.progressBar.setFormat("Finished")
                self.progressBar.setValue(100)
                msg = QMessageBox()
                msg.setWindowTitle("Reminder")
                skipped_names = [
                    item.device_id for item in result.skipped_devices
                ]
                msg.setText(
                    f"The data have been stored in {new_h5_filename}\n\n"
                    f"{len(result.device_ids)} BPMs can be matched.\n\n"
                    f"The number of overlapping bunchID is "
                    f"{result.bunch_ids.size}.\n\n"
                    f"The skipped BPMs names are {skipped_names}"
                )
                font = QFont()
                font.setPointSize(14)
                msg.setFont(font)
                msg.exec_()

            self._start_background(
                TaskState.EXPORTING,
                lambda: save_alignment_result(
                    result,
                    self.runID,
                    time_sec,
                    directory,
                    export_format=self.settings.alignment_export_format,
                ),
                export_completed,
                ready=True,
            )

        self._run_alignment(time_sec, alignment_completed)

    def rawDataDownload_click(self):
        self.hostname = self.settings.storage_host
        self.username = self.settings.storage_user
        self.password = self.settings.storage_password
        date = self.dateEdit.date().toPyDate()
        #print(str(date))
        self.runID = str(date).replace('-', '')
        self.remote_path = os.path.join("/SHINE_Data/AC/LA/", self.runID, "raw/BIData/")

        directory = QFileDialog.getExistingDirectory(
            self,
            "Please choose the directory",
            os.path.join(os.path.expanduser("~"), "Downloads")
        )
        if directory:
            self.progressBar.setFormat("Data is downloading")
            self.startButton.setEnabled(False)
            self.retrievalButton.setEnabled(False)
            self.downloadButton.setEnabled(False)
            self.progressBar.setValue(10)

            local_path = os.path.join(directory, self.runID)

            text = self.comboBox.currentText()
            #print(type(text))
            time_sec = get_para(text)
            if time_sec is None:
                print('please retrieval time_sec')

            def completed(_saved):
                self.progressBar.setFormat("Finished")
                self.progressBar.setValue(100)
                msg = QMessageBox()
                msg.setWindowTitle("Reminder")
                msg.setText(f"The data have been stored in {local_path}")
                font = QFont()
                font.setPointSize(14)
                msg.setFont(font)
                msg.exec_()

            self._start_background(
                TaskState.DOWNLOADING,
                lambda: remote_save_timesec_h5_files(
                    hostname=self.hostname,
                    username=self.username,
                    password=self.password,
                    remote_path=self.remote_path,
                    local_path=local_path,
                    time_sec=time_sec,
                    port=self.settings.storage_port,
                ),
                completed,
            )

if __name__ == '__main__':

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
