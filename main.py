'\nAndroid Log查看工具 - v12.0\n修复：滚动条、状态管理、协议替换\n新增：英文搜索协议\n'

import sys
import subprocess
import threading
import time
import json
import csv
import re
from datetime import datetime
from pathlib import Path

from app_core import (
    RollingLogBuffer,
    TEXT_ENCODING,
    default_device_aliases_path,
    default_protocols_path,
    load_json_file,
    load_app_config,
    save_json_file,
    write_text_file,
)

from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *


def no_console_subprocess_kwargs():
    """Hide child console windows when launching command-line tools from the GUI."""
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# ==================== ????????? ====================

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
    
    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)
    
    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class DarkComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def showPopup(self):
        super().showPopup()
        popup = self.view().window()
        palette = popup.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#20262d"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#20262d"))
        popup.setPalette(palette)
        popup.setAutoFillBackground(True)
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup.setStyleSheet("background-color: #20262d; border: 1px solid #6fc6b8;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(0, 0, -1, -1)
        focused = self.hasFocus()
        border = QColor("#6fc6b8") if focused or self._hovered else QColor("#46515c")
        background = QColor("#242b33") if self._hovered else QColor("#20262d")
        arrow_background = QColor("#343b44") if self._hovered else QColor("#2b3138")

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 6, 6)

        arrow_rect = QRect(rect.right() - 29, rect.top(), 29, rect.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(arrow_background)
        painter.drawRoundedRect(arrow_rect, 6, 6)
        painter.fillRect(arrow_rect.adjusted(0, 1, -5, -1), arrow_background)

        painter.setPen(QPen(QColor("#46515c"), 1))
        painter.drawLine(arrow_rect.left(), rect.top() + 4, arrow_rect.left(), rect.bottom() - 4)

        center = arrow_rect.center()
        points = QPolygonF([
            QPointF(center.x() - 5, center.y() - 2),
            QPointF(center.x() + 5, center.y() - 2),
            QPointF(center.x(), center.y() + 4),
        ])
        painter.setBrush(QColor("#c7d1d9"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(points)

        text_rect = rect.adjusted(12, 0, 36, 0)
        text = self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.setPen(QColor("#e6edf3"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class LogTextEdit(QPlainTextEdit):
    content_clicked = pyqtSignal()
    line_double_clicked = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        
        self.update_line_number_area_width(0)
        self.single_click_timer = QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(self.content_clicked.emit)
        
        self.highlighted_line = -1
        self.original_line_numbers = []
    
    def mousePressEvent(self, event):
        viewport_rect = self.viewport().geometry()
        # ??????????? content_clicked??????????
        if viewport_rect.contains(event.pos()) and event.button() == Qt.MouseButton.LeftButton:
            self.single_click_timer.start(220)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.single_click_timer.stop()
            cursor = self.cursorForPosition(event.pos())
            line = cursor.block().text().strip()
            if line:
                self.line_double_clicked.emit(line)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
    
    def set_original_line_numbers(self, mapping: list):
        self.original_line_numbers = mapping
        self.line_number_area.update()
    
    def line_number_area_width(self):
        digits = max(5, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance('9') * digits
    
    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)
    
    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))
    
    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor(25, 25, 30))
        
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if self.original_line_numbers and block_number < len(self.original_line_numbers):
                    number = str(self.original_line_numbers[block_number] + 1)
                else:
                    number = str(block_number + 1)
                
                if block_number == self.highlighted_line:
                    painter.setPen(QColor(255, 200, 50))
                    painter.fillRect(0, top, self.line_number_area.width(), 
                                     int(self.blockBoundingRect(block).height()),
                                     QColor(60, 50, 0))
                else:
                    painter.setPen(QColor(100, 100, 110))
                
                painter.drawText(0, top, self.line_number_area.width() - 5,
                                 int(self.blockBoundingRect(block).height()),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 number)
            
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1
    
    def set_highlighted_line(self, line_number):
        self.highlighted_line = line_number
        self.line_number_area.update()
        self.update_highlight()
    
    def clear_highlight(self):
        self.highlighted_line = -1
        self.setExtraSelections([])
        self.line_number_area.update()
    
    def update_highlight(self):
        if self.highlighted_line < 0:
            self.setExtraSelections([])
            return
        
        block = self.document().findBlockByLineNumber(self.highlighted_line)
        if block.isValid():
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(QColor(70, 60, 20))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QTextCursor(block)
            self.setExtraSelections([selection])


# ==================== ???????? ====================

class ProtocolManager:
    def __init__(self):
        self.storage_path = default_protocols_path()
        self.protocols = {}
        self.load()
    
    def load(self):
        try:
            if self.storage_path.exists():
                self.protocols = load_json_file(self.storage_path)
        except (OSError, json.JSONDecodeError):
            self.protocols = {}
    
    def save(self):
        save_json_file(self.storage_path, self.protocols)
    
    def add(self, cn: str, en: str):
        if cn and en:
            self.protocols[cn] = en
            self.save()
    
    def delete(self, cn: str):
        if cn in self.protocols:
            del self.protocols[cn]
            self.save()
    
    def search(self, keyword: str) -> list:
        '搜索协议：同时匹配中文名和英文名（不区分大小写）'
        kw = keyword.lower()
        results = []
        for cn, en in self.protocols.items():
            # ?????????????
            if kw in cn.lower() or kw in en.lower():
                results.append((cn, en))
        return results
    
    def get_all(self) -> dict:
        return self.protocols.copy()


class DeviceAliasManager:
    def __init__(self):
        self.storage_path = default_device_aliases_path()
        self.aliases = {}
        self.load()
    
    def load(self):
        try:
            if self.storage_path.exists():
                self.aliases = load_json_file(self.storage_path)
        except (OSError, json.JSONDecodeError):
            self.aliases = {}
    
    def save(self):
        save_json_file(self.storage_path, self.aliases)
    
    def get(self, serial: str) -> str:
        return self.aliases.get(serial, "")
    
    def set(self, serial: str, alias: str):
        if alias:
            self.aliases[serial] = alias
        elif serial in self.aliases:
            del self.aliases[serial]
        self.save()
    
    def display_name(self, serial: str) -> str:
        alias = self.get(serial)
        return f"{alias}" if alias else serial


class ProtocolDialog(QDialog):
    def __init__(self, manager: ProtocolManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.editing_row = -1  # ??????
        self.editing_cn = ""   # ?????????
        self.setWindowTitle('协议库管理')
        self.setMinimumSize(700, 500)
        self.init_ui()
        self.load_data()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 娣诲姞/缂栬緫鍖哄煙
        add_frame = QFrame()
        add_frame.setObjectName("addFrame")
        add_layout = QVBoxLayout(add_frame)
        add_layout.setContentsMargins(15, 15, 15, 15)
        add_layout.setSpacing(10)
        
        # ??????
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('中文名称'))
        self.cn_input = QLineEdit()
        self.cn_input.setPlaceholderText('如：促销列表')
        row1.addWidget(self.cn_input)
        add_layout.addLayout(row1)
        
        # ??????
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('英文协议'))
        self.en_input = QLineEdit()
        self.en_input.setPlaceholderText('如：GetPromoList')
        row2.addWidget(self.en_input)
        add_layout.addLayout(row2)
        
        # ??????
        row3 = QHBoxLayout()
        row3.addStretch()
        self.cancel_btn = QPushButton('取消编辑')
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_edit)
        row3.addWidget(self.cancel_btn)
        self.add_btn = QPushButton('添加')
        self.add_btn.setObjectName("primaryBtn")
        self.add_btn.clicked.connect(self.add_or_update_protocol)
        row3.addWidget(self.add_btn)
        add_layout.addLayout(row3)
        
        layout.addWidget(add_frame)
        
        # 琛ㄦ牸
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['中文名称', '英文协议', ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 60)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self.on_double_click)
        layout.addWidget(self.table)
        
        # 搴曢儴
        btn_layout = QHBoxLayout()
        import_btn = QPushButton('导入')
        import_btn.clicked.connect(self.import_data)
        btn_layout.addWidget(import_btn)
        export_btn = QPushButton('导出')
        export_btn.clicked.connect(self.export_data)
        btn_layout.addWidget(export_btn)
        btn_layout.addStretch()
        count_label = QLabel()
        count_label.setObjectName("countLabel")
        self.count_label = count_label
        btn_layout.addWidget(count_label)
        btn_layout.addSpacing(20)
        close_btn = QPushButton('关闭')
        close_btn.setObjectName("primaryBtn")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        self.setStyleSheet("""
            QDialog { background-color: #15171a; }
            QFrame#addFrame { background-color: #1f2328; border: 1px solid #30363d; border-radius: 8px; }
            QLineEdit { background-color: #20262d; color: #e6edf3; border: 1px solid #343b44; padding: 10px 14px; border-radius: 6px; font-size: 14px; }
            QLineEdit:focus { border: 1px solid #6fc6b8; background-color: #242b33; }
            QLabel { color: #c7cdd4; font-size: 13px; min-width: 60px; }
            QLabel#countLabel { color: #8ab4f8; min-width: 0; }
            QPushButton { background-color: #2b3138; color: #e6edf3; padding: 10px 24px; border: 1px solid #3a424c; border-radius: 6px; font-size: 13px; }
            QPushButton:hover { background-color: #343b44; border-color: #4b5561; }
            QPushButton#primaryBtn { background-color: #2f6f73; border-color: #4fa49b; color: #f3fffd; }
            QPushButton#primaryBtn:hover { background-color: #377f83; }
            QPushButton#delBtn { background-color: #3a2528; color: #ffb1aa; padding: 6px 12px; min-width: 40px; border-color: #674044; }
            QPushButton#delBtn:hover { background-color: #493034; }
            QTableWidget { background-color: #101418; color: #d7dde4; border: 1px solid #30363d; border-radius: 8px; }
            QTableWidget::item { padding: 10px 8px; }
            QTableWidget::item:selected { background-color: #293f4a; color: #f6fbff; }
            QHeaderView { background-color: transparent; }
            QHeaderView::section { background-color: #242a31; color: #9aa4af; padding: 12px 8px; border: none; font-weight: bold; }
        """)
    
    def load_data(self):
        self.table.setRowCount(0)
        protocols = self.manager.get_all()
        for cn, en in protocols.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            cn_item = QTableWidgetItem(cn)
            cn_item.setFlags(cn_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, cn_item)
            
            en_item = QTableWidgetItem(en)
            en_item.setFlags(en_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, en_item)
            
            del_btn = QPushButton('删除')
            del_btn.setObjectName("delBtn")
            del_btn.clicked.connect(lambda _, c=cn: self.delete_protocol(c))
            self.table.setCellWidget(row, 2, del_btn)
        
        self.count_label.setText(f"共 {len(protocols)} 条")
    
    def on_double_click(self, index):
        '双击编辑'
        row = index.row()
        cn_item = self.table.item(row, 0)
        en_item = self.table.item(row, 1)
        if cn_item and en_item:
            self.editing_row = row
            self.editing_cn = cn_item.text()
            self.cn_input.setText(cn_item.text())
            self.en_input.setText(en_item.text())
            self.add_btn.setText('保存修改')
            self.cancel_btn.setVisible(True)
    
    def cancel_edit(self):
        '取消编辑'
        self.editing_row = -1
        self.editing_cn = ""
        self.cn_input.clear()
        self.en_input.clear()
        self.add_btn.setText('添加')
        self.cancel_btn.setVisible(False)
    
    def add_or_update_protocol(self):
        cn, en = self.cn_input.text().strip(), self.en_input.text().strip()
        if not cn or not en:
            return
        
        if self.editing_row >= 0 and self.editing_cn:
            # 缂栬緫妯″紡锛氬厛鍒犻櫎鏃х殑锛屽啀娣诲姞鏂扮殑
            self.manager.delete(self.editing_cn)
        
        self.manager.add(cn, en)
        self.cancel_edit()
        self.load_data()
    
    def delete_protocol(self, cn):
        self.manager.delete(cn)
        self.load_data()
    
    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入', "", "JSON (*.json)")
        if path:
            try:
                data = load_json_file(Path(path))
                for cn, en in data.items():
                    self.manager.add(cn, en)
                self.load_data()
            except Exception as e:
                QMessageBox.warning(self, '错误', str(e))
    
    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出', "protocols.json", "JSON (*.json)")
        if path:
            save_json_file(Path(path), self.manager.get_all())


class ProtocolCompleter(QCompleter):
    def __init__(self, manager: ProtocolManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.model = QStringListModel()
        self.setModel(self.model)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchContains)
        # results: [(display_text, en), ...] display_text ??? ???? ? ????
        self.results = []
        self._popup_visible = False
        QTimer.singleShot(100, self._install_popup_filter)
    
    def _install_popup_filter(self):
        try:
            self.popup().installEventFilter(self)
        except RuntimeError:
            return
    
    def eventFilter(self, obj, event):
        try:
            if obj == self.popup():
                if event.type() == QEvent.Type.Show:
                    self._popup_visible = True
                elif event.type() == QEvent.Type.Hide:
                    self._popup_visible = False
        except RuntimeError:
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)
    
    def update(self, text: str):
        if self._popup_visible:
            return
        if not text:
            self.results = []
            self.model.setStringList([])
            return
        
        kw = text.lower()
        self.results = []
        display_list = []
        
        for cn, en in self.manager.get_all().items():
            cn_match = kw in cn.lower()
            en_match = kw in en.lower()
            
            if cn_match or en_match:
                # ?????????? -> ???
                display_text = f"{cn} → {en}"
                self.results.append((display_text, en))
                display_list.append(display_text)
        
        self.model.setStringList(display_list)
    
    def get_english(self, display_text: str) -> str:
        '从下拉选项获取英文协议名'
        display_text = display_text.strip()
        for display, en in self.results:
            if display == display_text:
                return en
        # ??????????????
        if "→" in display_text:
            return display_text.split("→", 1)[-1].strip()
        for separator in (" → ", " -> "):
            if separator in display_text:
                return display_text.split(separator)[-1].strip()


# ==================== 鏃ュ織鎶撳彇 ====================

class LogFetcher(QThread):
    log_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    fetch_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.running = False
        self.process = None
        self.pid_filter = None

    def start_fetching(self, device, pid=None):
        self.running = True
        self.pid_filter = pid
        try:
            cmd = ['adb', '-s', device, 'logcat', '-v', 'threadtime']
            if pid:
                cmd.extend(['--pid', str(pid)])
            
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding=TEXT_ENCODING, errors='replace', bufsize=1,
                **no_console_subprocess_kwargs(),
            )
            self.start()
        except Exception as e:
            self.running = False
            self.error_occurred.emit(str(e))

    def stop_fetching(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
            except Exception as e:
                self.error_occurred.emit(f"停止 adb logcat 失败: {e}")
        self.pid_filter = None

    def kill_process(self):
        if self.process and self.process.poll() is None:
            self.process.kill()

    def run(self):
        process = self.process
        try:
            while self.running and process:
                line = process.stdout.readline()
                if line:
                    self.log_received.emit(line.strip())
                elif process.poll() is not None:
                    if self.running and process.returncode:
                        stderr = process.stderr.read().strip() if process.stderr else ""
                        if stderr:
                            self.error_occurred.emit(stderr)
                    break
        except Exception as e:
            if self.running:
                self.error_occurred.emit(f"璇诲彇 adb logcat 澶辫触: {e}")
        finally:
            self.running = False
            self.process = None
            self.fetch_finished.emit()


class ClientLoggingPuller(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, device, remote_path, destination):
        super().__init__()
        self.device = device
        self.remote_path = remote_path
        self.destination = Path(destination)
        self.process = None

    def stop(self):
        self.requestInterruption()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def kill_process(self):
        if self.process and self.process.poll() is None:
            self.process.kill()

    def run(self):
        try:
            self.destination.mkdir(parents=True, exist_ok=True)
            self.process = subprocess.Popen(
                ['adb', '-s', self.device, 'pull', self.remote_path, str(self.destination)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding=TEXT_ENCODING,
                errors='replace',
                **no_console_subprocess_kwargs(),
            )
            stdout, stderr = self.process.communicate(timeout=60)
            if self.isInterruptionRequested():
                return
            if self.process.returncode != 0:
                message = (stderr or stdout or 'adb pull 执行失败').strip()
                self.failed.emit(message)
                return
            self.completed.emit(str(self.destination))
        except subprocess.TimeoutExpired:
            if self.process:
                self.process.terminate()
            if not self.isInterruptionRequested():
                self.failed.emit('adb pull 超时')
        except Exception as e:
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))
        finally:
            self.process = None


class DeviceRefreshWorker(QThread):
    devices_found = pyqtSignal(list, int)
    failed = pyqtSignal(str)
    EMULATOR_PROCESS_KEYWORDS = (
        "emula",
        "emulator",
        "mumuplayer",
        "qemu",
        "nox",
        "bluestacks",
        "hd-player",
        "ldplayer",
        "dnplayer",
        "nemu",
        "vbox",
    )

    def __init__(self, connect_addresses=None):
        super().__init__()
        self.connect_addresses = connect_addresses or []
        self.process = None
        self.process_lock = threading.Lock()

    def stop(self):
        self.requestInterruption()
        self.terminate_current_process()

    def kill_process(self):
        with self.process_lock:
            process = self.process
        if process and process.poll() is None:
            process.kill()

    def terminate_current_process(self):
        with self.process_lock:
            process = self.process
        if process and process.poll() is None:
            process.terminate()

    def run_command(self, args, timeout):
        if self.isInterruptionRequested():
            return None
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding=TEXT_ENCODING,
                errors='replace',
                **no_console_subprocess_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as e:
            return subprocess.CompletedProcess(args, 1, "", str(e))

        with self.process_lock:
            self.process = process

        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            self.terminate_current_process()
            try:
                stdout, stderr = process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.kill_process()
                stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(args, 124, stdout, stderr)
        finally:
            with self.process_lock:
                if self.process is process:
                    self.process = None

    def run(self):
        try:
            connected_count = self.connect_configured_devices()
            if self.isInterruptionRequested():
                return

            result = self.run_command(
                ['adb', 'devices', '-l'],
                timeout=3,
            )
            if self.isInterruptionRequested() or result is None:
                return
            if result.returncode != 0:
                message = (result.stderr or result.stdout or 'adb devices 执行失败').strip()
                self.failed.emit(message)
                return
            devices = self.parse_devices(result.stdout)
            self.devices_found.emit(devices, connected_count)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))

    def connect_configured_devices(self):
        connected_count = 0
        for address in self.adb_connect_candidates():
            if self.isInterruptionRequested():
                break
            if self.try_adb_connect(address):
                connected_count += 1
        return connected_count

    def adb_connect_candidates(self):
        candidates = []
        seen = set()
        for address in self.connect_addresses + self.discover_local_emulator_addresses():
            if address in seen:
                continue
            seen.add(address)
            candidates.append(address)
        return candidates

    def try_adb_connect(self, address):
        result = self.run_command(['adb', 'connect', address], timeout=3)
        if self.isInterruptionRequested() or result is None:
            return False

        output = f"{result.stdout}\n{result.stderr}".lower()
        connected = result.returncode == 0 and "failed" not in output and "cannot" not in output
        if not connected:
            self.disconnect_failed_address(address)
        return connected

    def disconnect_failed_address(self, address):
        self.run_command(['adb', 'disconnect', address], timeout=2)

    def discover_local_emulator_addresses(self):
        if sys.platform.startswith("win"):
            return self.discover_windows_emulator_addresses()
        return self.discover_lsof_emulator_addresses()

    def discover_lsof_emulator_addresses(self):
        result = self.run_command(['lsof', '-nP', '-iTCP', '-sTCP:LISTEN'], timeout=3)
        if self.isInterruptionRequested() or result is None:
            return []
        if result.returncode != 0:
            return []
        return self.parse_lsof_listening_ports(result.stdout)

    def discover_windows_emulator_addresses(self):
        netstat = self.run_command(['netstat', '-ano', '-p', 'tcp'], timeout=3)
        if self.isInterruptionRequested() or netstat is None:
            return []
        tasklist = self.run_command(['tasklist', '/FO', 'CSV', '/NH'], timeout=3)
        if self.isInterruptionRequested() or tasklist is None:
            return []
        if netstat.returncode != 0 or tasklist.returncode != 0:
            return []
        return self.parse_windows_netstat_ports(
            netstat.stdout,
            self.parse_windows_tasklist(tasklist.stdout),
        )

    def parse_lsof_listening_ports(self, output):
        ports_by_process = {}
        for raw_line in output.splitlines():
            parts = raw_line.split()
            if len(parts) < 2 or parts[0] == "COMMAND":
                continue
            if not self.is_emulator_process(parts[0]):
                continue
            port = self.local_port_from_endpoint(parts[-2] if parts[-1] == "(LISTEN)" else parts[-1])
            if port is None:
                continue
            process_key = (parts[0], parts[1])
            ports_by_process.setdefault(process_key, []).append(port)
        return self.adb_addresses_from_process_ports(ports_by_process)

    def parse_windows_tasklist(self, output):
        processes = {}
        for row in csv.reader(output.splitlines()):
            if len(row) < 2:
                continue
            processes[row[1]] = row[0]
        return processes

    def parse_windows_netstat_ports(self, output, process_names_by_pid):
        ports_by_process = {}
        for raw_line in output.splitlines():
            parts = raw_line.split()
            if len(parts) < 5 or parts[0].lower() != "tcp":
                continue
            if parts[-2].upper() != "LISTENING":
                continue
            process_name = process_names_by_pid.get(parts[-1], "")
            if not self.is_emulator_process(process_name):
                continue
            port = self.local_port_from_endpoint(parts[1])
            if port is None:
                continue
            process_key = (process_name, parts[-1])
            ports_by_process.setdefault(process_key, []).append(port)
        return self.adb_addresses_from_process_ports(ports_by_process)

    def is_emulator_process(self, process_name):
        process = process_name.lower()
        return any(keyword in process for keyword in self.EMULATOR_PROCESS_KEYWORDS)

    def local_port_from_endpoint(self, endpoint):
        match = re.search(r":(\d+)$", endpoint)
        if not match:
            return None
        port = int(match.group(1))
        if port in (5037, 5554):
            return None
        return port

    def adb_addresses_from_process_ports(self, ports_by_process):
        addresses = []
        seen = set()
        for ports in ports_by_process.values():
            port = self.select_adb_port(ports)
            if port is None:
                continue
            address = f"127.0.0.1:{port}"
            if address in seen:
                continue
            seen.add(address)
            addresses.append(address)
        return addresses

    def select_adb_port(self, ports):
        unique_ports = sorted(set(ports))
        if 5555 in unique_ports:
            return 5555
        high_ports = [port for port in unique_ports if port >= 10000]
        if not high_ports:
            return None
        return high_ports[0]

    def parse_devices(self, output):
        devices = []
        for raw_line in output.strip().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state == "device":
                devices.append(serial)
        return devices


# ==================== 涓荤獥鍙?====================

class MainWindow(QMainWindow):
    MAX_LOG_LINES = 20000
    
    def __init__(self):
        super().__init__()
        self.is_closing = False
        self.app_config = load_app_config()
        self.log_fetcher = LogFetcher()
        self.current_device = None
        self.log_buffer = RollingLogBuffer(self.MAX_LOG_LINES)
        self.log_lines = self.log_buffer.lines
        self.search_results = []
        self.pending_lines = []
        self.pending_lock = threading.Lock()
        self.filtered_line_mapping = []
        self.config_puller = None
        self.device_refresh_worker = None
        self.package_name = self.app_config.package_name
        
        # ???????
        self.auto_scroll = True      # ????????
        self.freeze_display = False  # ????????????????
        
        # ??????
        self.log_start_time = None
        self.log_end_time = None
        
        self.protocol_manager = ProtocolManager()
        self.device_manager = DeviceAliasManager()
        
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_display)
        self.update_timer.start(200)

        self.init_ui()
        self.connect_signals()
        self.refresh_devices()

    def init_ui(self):
        self.setWindowTitle("Android Log Viewer v12")
        self.setGeometry(150, 150, 1500, 950)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(self.create_toolbar())
        layout.addWidget(self.create_search_bar())

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(2)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.setHandleWidth(2)
        top.addWidget(self.create_log_area())
        top.addWidget(self.create_workspace_area())
        top.setSizes([900, 400])
        splitter.addWidget(top)
        splitter.addWidget(self.create_search_result_area())
        splitter.setSizes([650, 250])
        layout.addWidget(splitter, 1)

        self.create_status_bar()
        self.setup_style()

    def create_toolbar(self):
        frame = QFrame()
        frame.setObjectName("toolbarFrame")
        toolbar = QHBoxLayout(frame)
        toolbar.setSpacing(12)
        toolbar.setContentsMargins(12, 8, 12, 8)

        # ????
        device_group = QFrame()
        device_group.setObjectName("groupFrame")
        device_group.setMinimumWidth(360)
        device_group.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        device_layout = QHBoxLayout(device_group)
        device_layout.setContentsMargins(8, 4, 8, 4)
        device_layout.setSpacing(8)
        
        device_layout.addWidget(QLabel('设备'))
        self.device_combo = DarkComboBox()
        self.device_combo.setObjectName("deviceCombo")
        self.device_combo.setMinimumWidth(220)
        self.device_combo.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        self.configure_combo_box(self.device_combo, min_popup_width=220)
        self.device_combo.currentIndexChanged.connect(lambda i: setattr(self, 'current_device', self.device_combo.currentData() if i >= 0 else None))
        device_layout.addWidget(self.device_combo)
        
        edit_btn = QPushButton('备注')
        edit_btn.setObjectName("smallBtn")
        edit_btn.clicked.connect(self.edit_device_alias)
        device_layout.addWidget(edit_btn)
        
        self.refresh_btn = QPushButton('刷新')
        self.refresh_btn.setObjectName("smallBtn")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        device_layout.addWidget(self.refresh_btn)
        
        toolbar.addWidget(device_group)

        # 杩囨护閫夐」
        filter_group = QFrame()
        filter_group.setObjectName("groupFrame")
        filter_layout = QHBoxLayout(filter_group)
        filter_layout.setContentsMargins(8, 4, 8, 4)
        filter_layout.setSpacing(8)
        
        filter_layout.addWidget(QLabel('级别'))
        self.level_combo = DarkComboBox()
        self.level_combo.addItems(["ALL", "V", "D", "I", "W", "E", "F"])
        self.level_combo.setFixedWidth(86)
        self.configure_combo_box(self.level_combo, min_popup_width=86)
        self.level_combo.currentTextChanged.connect(self.on_filter_changed)
        filter_layout.addWidget(self.level_combo)
        
        self.unity_check = QCheckBox("Unity")
        self.unity_check.toggled.connect(self.on_filter_changed)
        filter_layout.addWidget(self.unity_check)
        
        toolbar.addWidget(filter_group)

        toolbar.addStretch()

        package_btn = QPushButton('包名')
        package_btn.setObjectName("smallBtn")
        package_btn.setToolTip(self.package_name)
        package_btn.clicked.connect(lambda: self.edit_package_name(package_btn))
        toolbar.addWidget(package_btn)

        self.get_pid_btn = QPushButton("PID")
        self.get_pid_btn.setObjectName("smallBtn")
        self.get_pid_btn.setToolTip("鑾峰彇褰撳墠鍖呭悕瀵瑰簲鐨?PID")
        self.get_pid_btn.clicked.connect(self.get_package_pid)
        toolbar.addWidget(self.get_pid_btn)

        self.pid_label = QLabel("")
        self.pid_label.setObjectName("pidLabel")
        toolbar.addWidget(self.pid_label)

        protocol_btn = QPushButton('协议库')
        protocol_btn.clicked.connect(lambda: ProtocolDialog(self.protocol_manager, self).exec())
        toolbar.addWidget(protocol_btn)

        self.pull_config_btn = QPushButton('拉取日志')
        self.pull_config_btn.clicked.connect(self.pull_client_logging_logs)
        toolbar.addWidget(self.pull_config_btn)

        self.toggle_btn = QPushButton('开始抓取')
        self.toggle_btn.setObjectName("primaryBtn")
        self.toggle_btn.setMinimumWidth(100)
        self.toggle_btn.clicked.connect(self.toggle_logging)
        toolbar.addWidget(self.toggle_btn)

        clear_btn = QPushButton('清空')
        clear_btn.setObjectName("dangerBtn")
        clear_btn.clicked.connect(self.clear_log)
        toolbar.addWidget(clear_btn)

        save_btn = QPushButton('保存日志')
        save_btn.clicked.connect(self.save_log)
        toolbar.addWidget(save_btn)

        return frame

    def configure_combo_box(self, combo, min_popup_width=None):
        view = QListView()
        view.setObjectName("comboPopup")
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setAutoFillBackground(True)
        view.viewport().setAutoFillBackground(True)
        view.viewport().setStyleSheet("background-color: #20262d;")
        view.setContentsMargins(0, 0, 0, 0)
        view.viewport().setContentsMargins(0, 0, 0, 0)
        view.setMinimumWidth(min_popup_width or combo.minimumWidth())
        view.setUniformItemSizes(True)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        palette = view.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor("#20262d"))
        palette.setColor(QPalette.ColorRole.Window, QColor("#20262d"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#e6edf3"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6f73"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#f3fffd"))
        view.setPalette(palette)
        view.setStyleSheet("""
            QListView#comboPopup {
                background-color: #20262d;
                color: #e6edf3;
                border: 1px solid #6fc6b8;
                outline: 0;
                padding: 0;
            }
            QListView#comboPopup::item {
                min-height: 28px;
                padding: 6px 12px;
                border: 0;
                background-color: #20262d;
            }
            QListView#comboPopup::item:hover {
                background-color: #2a3038;
            }
            QListView#comboPopup::item:selected {
                background-color: #2f6f73;
                color: #f3fffd;
            }
        """)
        combo.setView(view)
        combo.setMaxVisibleItems(10)
        combo.setStyleSheet("DarkComboBox { background: transparent; border: none; }")

    def create_filter_bar(self):
        frame = QFrame()
        frame.setObjectName("filterFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        layout.addWidget(QLabel('包名'))
        self.package_input = QLineEdit()
        self.package_input.setText(self.package_name)
        self.package_input.setMinimumWidth(350)
        layout.addWidget(self.package_input)
        
        self.get_pid_btn = QPushButton('获取 PID')
        self.get_pid_btn.clicked.connect(self.get_package_pid)
        layout.addWidget(self.get_pid_btn)
        
        self.pid_label = QLabel("")
        self.pid_label.setObjectName("pidLabel")
        layout.addWidget(self.pid_label)
        
        layout.addStretch()
        
        return frame

    def get_package_name(self):
        return self.package_name.strip()

    def edit_package_name(self, button=None):
        text, ok = QInputDialog.getText(
            self,
            '包名设置',
            "璇疯緭鍏?Android 鍖呭悕:",
            text=self.package_name,
        )
        if not ok:
            return
        package = text.strip()
        if not package:
            self.status_label.setText('包名不能为空')
            return
        self.package_name = package
        self.app_config.package_name = package
        self.app_config.save()
        if button:
            button.setToolTip(package)
        self.pid_label.setText("")
        self.status_label.setText('包名已更新')

    def get_package_pid(self):
        package = self.get_package_name()
        if not package:
            self.status_label.setText('请先设置包名')
            return
        
        if not self.current_device:
            QMessageBox.warning(self, '提示', '请选择设备')
            return
        
        try:
            result = subprocess.run(
                ['adb', '-s', self.current_device, 'shell', 'pidof', package],
                capture_output=True, text=True, encoding=TEXT_ENCODING, errors='replace', timeout=5,
                **no_console_subprocess_kwargs(),
            )
            pid = result.stdout.strip()
            if pid:
                self.pid_label.setText(f"PID: {pid}")
                self.status_label.setText(f"已获取 PID: {pid}")
            else:
                self.pid_label.setText('未找到进程')
                QMessageBox.warning(self, '提示', f"未找到包名 {package} 对应的进程\n请确认应用已启动")
        except Exception as e:
            self.pid_label.setText('获取失败')
            QMessageBox.warning(self, '错误', f"获取 PID 失败: {e}")

    def create_search_bar(self):
        frame = QFrame()
        frame.setObjectName("searchFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        layout.addWidget(QLabel('搜索'))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("\u8f93\u5165\u5173\u952e\u5b57\u6216\u534f\u8bae\u540d\uff08\u652f\u6301\u4e2d\u82f1\u6587\uff0c\u5982 promo\uff09...")
        self.search_input.setMinimumWidth(300)
        self.search_input.returnPressed.connect(self.perform_search)
        layout.addWidget(self.search_input)
        
        self.completer = ProtocolCompleter(self.protocol_manager, self.search_input)
        self.completer.activated.connect(self.on_protocol_selected)
        self.search_input.setCompleter(self.completer)
        self.search_input.textChanged.connect(self.completer.update)

        search_btn = QPushButton('搜索')
        search_btn.setObjectName("primaryBtn")
        search_btn.clicked.connect(self.perform_search)
        layout.addWidget(search_btn)

        self.case_check = QCheckBox('区分大小写')
        layout.addWidget(self.case_check)
        self.regex_check = QCheckBox('正则表达式')
        layout.addWidget(self.regex_check)

        layout.addStretch()
        return frame

    def create_log_area(self):
        frame = QFrame()
        frame.setObjectName("logFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("sectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.addWidget(QLabel('实时日志'))
        self.scroll_label = QLabel("")
        self.scroll_label.setObjectName("warningLabel")
        header_layout.addWidget(self.scroll_label)
        header_layout.addStretch()
        self.log_count_label = QLabel('0 行')
        self.log_count_label.setObjectName("countLabel")
        header_layout.addWidget(self.log_count_label)
        clear_hl_btn = QPushButton('清除高亮')
        clear_hl_btn.setObjectName("smallBtn")
        clear_hl_btn.clicked.connect(lambda: self.log_text.clear_highlight())
        header_layout.addWidget(clear_hl_btn)
        layout.addWidget(header)

        self.log_text = LogTextEdit()
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setReadOnly(True)
        self.log_text.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.log_text.customContextMenuRequested.connect(self.show_log_context_menu)
        self.log_text.content_clicked.connect(self.on_log_content_clicked)
        self.log_text.line_double_clicked.connect(self.add_log_line_to_workspace)
        layout.addWidget(self.log_text)

        return frame

    def on_log_content_clicked(self):
        '点击日志内容区域'
        if self.freeze_display:
            # ?????????
            self.freeze_display = False
            self.auto_scroll = True
            self.scroll_label.setText("")
            # ??????
            self.force_update_display()
        elif self.log_fetcher.running:
            # ??????
            self.auto_scroll = not self.auto_scroll
            if self.auto_scroll:
                self.scroll_label.setText("")
            else:
                # ??????????
                self.scroll_label.setText("已暂停滚动 · 点击恢复")

    def create_workspace_area(self):
        frame = QFrame()
        frame.setObjectName("workspaceFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("sectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.addWidget(QLabel('工作区'))
        header_layout.addStretch()
        clear_btn = QPushButton('清空')
        clear_btn.setObjectName("smallBtn")
        clear_btn.clicked.connect(lambda: self.workspace_text.clear() if QMessageBox.question(self, '确认', '清空工作区？') == QMessageBox.StandardButton.Yes else None)
        header_layout.addWidget(clear_btn)
        save_btn = QPushButton('保存')
        save_btn.setObjectName("smallBtn")
        save_btn.clicked.connect(self.save_workspace)
        header_layout.addWidget(save_btn)
        layout.addWidget(header)

        self.workspace_text = QPlainTextEdit()
        self.workspace_text.setFont(QFont("Consolas", 10))
        self.workspace_text.setPlaceholderText('日志片段')
        layout.addWidget(self.workspace_text)

        return frame

    def create_search_result_area(self):
        frame = QFrame()
        frame.setObjectName("searchResultFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("sectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.addWidget(QLabel('搜索结果'))
        self.search_count = QLabel('0 条')
        self.search_count.setObjectName("countLabel")
        header_layout.addWidget(self.search_count)
        header_layout.addStretch()
        clear_btn = QPushButton('清除')
        clear_btn.setObjectName("smallBtn")
        clear_btn.clicked.connect(self.clear_search)
        header_layout.addWidget(clear_btn)
        layout.addWidget(header)

        self.search_table = QTableWidget()
        self.search_table.setColumnCount(3)
        self.search_table.setHorizontalHeaderLabels(["行号", "时间", "内容"])
        self.search_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.search_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.search_table.doubleClicked.connect(self.on_search_clicked)
        self.search_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.search_table.setColumnWidth(0, 70)
        self.search_table.setColumnWidth(1, 140)
        self.search_table.verticalHeader().setVisible(False)  # 闅愯棌琛屽彿
        self.search_table.setShowGrid(False)  # 闅愯棌缃戞牸绾?
        self.search_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_table.customContextMenuRequested.connect(self.show_search_context_menu)
        layout.addWidget(self.search_table)

        return frame

    def create_status_bar(self):
        self.status_bar = QStatusBar()
        self.status_bar.setObjectName("statusBar")
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel('就绪')
        self.count_label = QLabel(f"日志: 0 / {self.MAX_LOG_LINES}")
        self.status_bar.addWidget(self.status_label)
        self.status_bar.addPermanentWidget(self.count_label)

    def setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #15171a; }
            
            QFrame#toolbarFrame, QFrame#filterFrame, QFrame#searchFrame {
                background-color: #1f2328;
                border: 1px solid #30363d;
                border-radius: 8px;
            }
            QFrame#groupFrame {
                background-color: #262b31;
                border: 1px solid #343a42;
                border-radius: 6px;
            }
            QFrame#logFrame, QFrame#workspaceFrame, QFrame#searchResultFrame {
                background-color: #1b1f24;
                border-radius: 8px;
                border: 1px solid #30363d;
            }
            QFrame#sectionHeader {
                background-color: #242a31;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            
            QLabel { color: #c7cdd4; font-size: 12px; }
            QLabel#pidLabel { color: #6fc6b8; font-weight: bold; }
            QLabel#warningLabel { color: #e2a84b; }
            QLabel#countLabel { color: #8ab4f8; font-weight: bold; }
            
            QComboBox { min-width: 80px; }
            QComboBox#deviceCombo { min-width: 220px; }
            QListView#comboPopup {
                background-color: #20262d;
                color: #e6edf3;
                border: 1px solid #6fc6b8;
                outline: none;
                padding: 4px 0;
                selection-background-color: #2f6f73;
                selection-color: #f3fffd;
            }
            QListView#comboPopup::item {
                min-height: 26px;
                padding: 6px 12px;
                border: none;
            }
            QListView#comboPopup::item:hover {
                background-color: #2a3038;
            }
            QListView#comboPopup::item:selected {
                background-color: #2f6f73;
                color: #f3fffd;
            }
            
            QLineEdit {
                background-color: #20262d;
                color: #e6edf3;
                border: 1px solid #343b44;
                padding: 8px 14px;
                border-radius: 6px;
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #6fc6b8; background-color: #242b33; }
            
            QCheckBox { color: #c7cdd4; spacing: 6px; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4b5561; background-color: #20262d; }
            QCheckBox::indicator:checked { background-color: #2f6f73; border-color: #6fc6b8; }
            
            QPushButton {
                background-color: #2b3138;
                color: #e6edf3;
                border: 1px solid #3a424c;
                padding: 8px 18px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #343b44; border-color: #4b5561; }
            QPushButton:pressed { background-color: #20262d; }
            
            QPushButton#primaryBtn {
                background-color: #2f6f73;
                border-color: #4fa49b;
                color: #f3fffd;
            }
            QPushButton#primaryBtn:hover { background-color: #377f83; }
            
            QPushButton#dangerBtn { background-color: #3a2528; color: #ffb1aa; border-color: #674044; }
            QPushButton#dangerBtn:hover { background-color: #493034; }
            
            QPushButton#smallBtn { padding: 5px 12px; font-size: 12px; }
            
            QPlainTextEdit {
                background-color: #101418;
                color: #d7dde4;
                border: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                selection-background-color: #315d66;
                selection-color: #f6fbff;
            }
            
            QTableWidget {
                background-color: transparent;
                color: #d0d0d0;
                border: none;
                gridline-color: transparent;
                outline: none;
            }
            QTableWidget::item { padding: 6px 8px; border: none; background-color: transparent; }
            QTableWidget::item:selected { background-color: #293f4a; color: #f6fbff; }
            QHeaderView { background-color: transparent; border: none; }
            QHeaderView::section {
                background-color: #242a31;
                color: #9aa4af;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #30363d;
                font-weight: bold;
            }
            QTableCornerButton::section { background-color: transparent; border: none; }
            
            QSplitter::handle { background-color: transparent; }
            QSplitter::handle:vertical { height: 6px; }
            QSplitter::handle:horizontal { width: 6px; }
            
            QScrollBar:vertical {
                background-color: transparent;
                width: 10px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #3a424c;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background-color: #4b5561; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            
            QScrollBar:horizontal {
                background-color: transparent;
                height: 10px;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background-color: #3a424c;
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover { background-color: #4b5561; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
            
            QStatusBar#statusBar {
                background-color: #11161b;
                color: #9aa4af;
                border-top: 1px solid #30363d;
            }
            
            QMessageBox { background-color: #1f2328; }
            QMessageBox QLabel { color: #e6edf3; }

            QInputDialog { background-color: #1f2328; }
            QInputDialog QLabel { color: #e6edf3; font-size: 13px; }
            QInputDialog QLineEdit {
                background-color: #20262d;
                color: #f3fffd;
                border: 1px solid #6fc6b8;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }
            QInputDialog QPushButton {
                min-width: 72px;
                padding: 8px 16px;
            }
        """)

    def connect_signals(self):
        self.log_fetcher.log_received.connect(self.on_log_received)
        self.log_fetcher.error_occurred.connect(self.on_fetch_error)
        self.log_fetcher.fetch_finished.connect(self.on_fetch_finished)

    def refresh_devices(self):
        if self.device_refresh_worker and self.device_refresh_worker.isRunning():
            self.status_label.setText('正在刷新 ADB 设备...')
            return

        self.refresh_btn.setEnabled(False)
        self.status_label.setText('正在刷新 ADB 设备...')
        self.device_refresh_worker = DeviceRefreshWorker(connect_addresses=self.app_config.adb_connect_targets())
        self.device_refresh_worker.devices_found.connect(self.on_devices_refreshed)
        self.device_refresh_worker.failed.connect(self.on_device_refresh_failed)
        self.device_refresh_worker.finished.connect(self.on_device_refresh_finished)
        self.device_refresh_worker.start()

    def on_devices_refreshed(self, devices, connected_count):
        if self.is_closing:
            return

        current = self.current_device
        self.device_combo.clear()

        for serial in devices:
            self.device_combo.addItem(self.device_manager.display_name(serial), serial)

        if self.device_combo.count() > 0:
            if current:
                for i in range(self.device_combo.count()):
                    if self.device_combo.itemData(i) == current:
                        self.device_combo.setCurrentIndex(i)
                        break
            self.current_device = self.device_combo.currentData()
            alias = self.device_manager.get(self.current_device)
            suffix = f" · 已连接 {connected_count} 个配置地址" if connected_count else ""
            self.status_label.setText(f"设备: {alias or self.current_device}{suffix}")
            return

        self.current_device = None
        suffix = f"（已连接 {connected_count} 个配置地址）" if connected_count else ""
        self.status_label.setText(f"未检测到设备{suffix}")

    def on_device_refresh_failed(self, message):
        if self.is_closing:
            return
        self.status_label.setText(f"刷新设备失败: {message}")

    def on_device_refresh_finished(self):
        if self.is_closing:
            return
        self.refresh_btn.setEnabled(True)

    def edit_device_alias(self):
        if not self.current_device:
            QMessageBox.warning(self, '提示', '请先选择设备')
            return
        text, ok = QInputDialog.getText(self, '设备备注', f"设备序列号: {self.current_device}\n\n请输入备注名:", text=self.device_manager.get(self.current_device))
        if ok:
            self.device_manager.set(self.current_device, text.strip())
            self.refresh_devices()

    def get_device_display_name(self):
        if self.current_device:
            alias = self.device_manager.get(self.current_device)
            return alias if alias else self.current_device
        return "unknown"

    def get_log_time_range(self):
        start = self.log_start_time or datetime.now()
        end = self.log_end_time or datetime.now()
        return f"{start.strftime('%H%M%S')}-{end.strftime('%H%M%S')}"

    def generate_log_filename(self):
        device_name = self.get_device_display_name().replace(" ", "_").replace("/", "_")
        date_str = datetime.now().strftime('%Y%m%d')
        time_range = self.get_log_time_range()
        return f"{device_name}_{date_str}_{time_range}.txt"

    def toggle_logging(self):
        if self.log_fetcher.running:
            self.log_fetcher.stop_fetching()
            self.toggle_btn.setText('开始抓取')
            self.status_label.setText('已停止')
            self.scroll_label.setText("")
            self.auto_scroll = True
            self.freeze_display = False
            self.log_end_time = datetime.now()
            # ????????
            QTimer.singleShot(50, self.scroll_log_to_bottom)
        else:
            if not self.current_device:
                self.status_label.setText('请选择设备后再开始抓取')
                return
            
            pid = None
            pid_text = self.pid_label.text()
            if pid_text.startswith("PID: "):
                pid = pid_text.replace("PID: ", "").strip()
            
            with self.pending_lock:
                self.pending_lines.clear()
                self.log_buffer.clear()
            self.log_text.clear()
            self.log_text.clear_highlight()
            self.auto_scroll = True
            self.freeze_display = False
            self.scroll_label.setText("")
            
            self.log_start_time = datetime.now()
            self.log_end_time = None
            
            self.log_fetcher.start_fetching(self.current_device, pid)
            self.toggle_btn.setText('停止抓取')
            
            if pid:
                self.status_label.setText(f"抓取中... (PID: {pid})")
            else:
                self.status_label.setText("抓取中...")

    def on_fetch_error(self, message):
        if self.is_closing:
            return
        self.toggle_btn.setText('开始抓取')
        self.status_label.setText(f"抓取失败: {message}")

    def on_fetch_finished(self):
        if self.is_closing:
            return
        if self.toggle_btn.text() != '开始抓取':
            self.toggle_btn.setText('开始抓取')
            self.status_label.setText('抓取已结束')

    def on_filter_changed(self):
        if not self.freeze_display:
            self.force_update_display()

    def on_log_received(self, line):
        if self.is_closing:
            return
        with self.pending_lock:
            self.pending_lines.append(line)

    def parse_log_line(self, line: str) -> dict:
        parts = line.split(None, 5)
        if len(parts) >= 6:
            return {
                'date': parts[0],
                'time': parts[1],
                'pid': parts[2],
                'tid': parts[3],
                'level': parts[4],
                'tag_msg': parts[5]
            }
        return None

    def get_tag(self, line: str) -> str:
        parsed = self.parse_log_line(line)
        if parsed and ':' in parsed['tag_msg']:
            tag = parsed['tag_msg'].split(':')[0].strip()
            return tag
        return ""

    def _save_log_async(self, path, lines):
        try:
            write_text_file(path, '\n'.join(lines))
        except Exception as e:
            print(f"保存失败: {e}")

    def force_update_display(self):
        """强制更新显示（忽略 freeze 状态）"""
        self._do_update_display(force=True)

    def update_display(self):
        '定时更新显示'
        if self.is_closing:
            return
        self._do_update_display(force=False)

    def _do_update_display(self, force=False):
        # 鏀堕泦鏂版棩蹇?
        with self.pending_lock:
            if self.pending_lines:
                self.log_buffer.extend(self.pending_lines)
                self.pending_lines.clear()

        if not self.log_lines:
            self.log_count_label.setText('0 行')
            self.count_label.setText(f"日志: 0 / {self.MAX_LOG_LINES}")
            return

        # ???????????????
        if self.freeze_display and not force:
            self.update_count_labels()
            return

        filtered, mapping = self.get_filtered()
        self.filtered_line_mapping = mapping

        if filtered:
            # 淇濆瓨褰撳墠婊氬姩浣嶇疆
            scrollbar = self.log_text.verticalScrollBar()
            old_value = scrollbar.value()
            old_max = scrollbar.maximum()
            at_bottom = (old_value >= old_max - 10) if old_max > 0 else True
            
            highlighted = self.log_text.highlighted_line
            self.log_text.setPlainText('\n'.join(filtered))
            self.log_text.set_original_line_numbers(mapping)
            
            if highlighted >= 0 and highlighted < len(filtered):
                self.log_text.set_highlighted_line(highlighted)

            # ????????
            if self.auto_scroll and self.log_fetcher.running:
                scrollbar.setValue(scrollbar.maximum())
            elif not self.auto_scroll and not at_bottom:
                # ?????????
                if old_max > 0:
                    ratio = old_value / old_max
                    scrollbar.setValue(int(scrollbar.maximum() * ratio))
        else:
            self.log_text.clear()
            self.log_text.clear_highlight()
            self.log_text.set_original_line_numbers([])

        self.update_count_labels(len(mapping))

    def update_count_labels(self, shown=None):
        total_visible = len(self.log_lines)
        total_seen = self.log_buffer.total_seen
        dropped = self.log_buffer.offset
        shown = total_visible if shown is None else shown
        self.log_count_label.setText(f"{shown} 行" if shown == total_visible else f"{shown}/{total_visible} 行")
        if dropped:
            self.count_label.setText(f"日志: {total_visible} / {self.MAX_LOG_LINES} · 已接收 {total_seen} · 已丢弃 {dropped}")
        else:
            self.count_label.setText(f"日志: {total_visible} / {self.MAX_LOG_LINES}")

    def get_filtered(self):
        lines, mapping = [], []
        level = self.level_combo.currentText()
        unity_only = self.unity_check.isChecked()
        
        for idx, line in enumerate(self.log_lines):
            if level != "ALL":
                parsed = self.parse_log_line(line)
                if not parsed or parsed['level'] != level:
                    continue
            
            if unity_only:
                tag = self.get_tag(line)
                if tag != "Unity":
                    continue
            
            lines.append(line)
            mapping.append(self.log_buffer.original_index(idx))
        
        return lines, mapping

    def on_protocol_selected(self, selected_text: str):
        """Keep the protocol display text visible and search by its English name."""
        was_blocked = self.search_input.blockSignals(True)
        self.search_input.setText(selected_text)
        self.search_input.blockSignals(was_blocked)
        self.search_input.setCursorPosition(len(selected_text))
        self.perform_search()

    def _set_search_text(self, text: str):
        '设置搜索框文本并执行搜索'
        if self.is_closing:
            return
        self.search_input.setText(text)
        self.perform_search()

    def scroll_log_to_bottom(self):
        if self.is_closing:
            return
        try:
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except RuntimeError:
            return

    def perform_search(self):
        text = self.search_input.text().strip()
        if not text:
            return

        normalized_text = self.completer.get_english(text)
        if normalized_text and normalized_text != text:
            text = normalized_text

        case = self.case_check.isChecked()
        regex = self.regex_check.isChecked()
        lines, mapping = self.get_filtered()

        self.search_results = []
        self.search_table.setRowCount(0)

        for idx, line in enumerate(lines):
            search_in = line if case else line.lower()
            search_for = text if case else text.lower()
            
            found = False
            if regex:
                try:
                    import re
                    found = bool(re.search(search_for, search_in))
                except re.error as e:
                    self.status_label.setText(f"正则表达式无效: {e}")
                    return
            else:
                found = search_for in search_in

            if found:
                orig = mapping[idx] if idx < len(mapping) else idx
                self.search_results.append((idx, line, orig))
                
                row = self.search_table.rowCount()
                self.search_table.insertRow(row)
                
                parsed = self.parse_log_line(line)
                self.search_table.setItem(row, 0, QTableWidgetItem(str(orig + 1)))
                self.search_table.setItem(row, 1, QTableWidgetItem(f"{parsed['date']} {parsed['time']}" if parsed else ""))
                self.search_table.setItem(row, 2, QTableWidgetItem(line[:300]))

        self.search_count.setText(f"{len(self.search_results)} 条")

    def clear_search(self):
        self.search_results = []
        self.search_table.setRowCount(0)
        self.search_count.setText('0 条')

    def on_search_clicked(self, index):
        '双击搜索结果定位'
        row = index.row()
        if row < len(self.search_results):
            filtered_idx, _, orig = self.search_results[row]
            block = self.log_text.document().findBlockByLineNumber(filtered_idx)
            if block.isValid():
                # ????????????????
                self.freeze_display = True
                self.auto_scroll = False
                self.scroll_label.setText("已定位 · 点击日志区域恢复刷新")
                
                self.log_text.setTextCursor(QTextCursor(block))
                self.log_text.centerCursor()
                self.log_text.set_highlighted_line(filtered_idx)

    def show_log_context_menu(self, pos):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background-color: #2a2a3e; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 4px; }
            QMenu::item { color: #e0e0e0; padding: 8px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: rgba(74,158,255,0.3); }
            QMenu::separator { height: 1px; background-color: rgba(255,255,255,0.1); margin: 4px 8px; }
        """)
        
        cursor = self.log_text.textCursor()
        
        copy = menu.addAction('复制')
        copy.setEnabled(cursor.hasSelection())
        copy.triggered.connect(lambda: QApplication.clipboard().setText(cursor.selectedText().replace('\u2029', '\n')))
        
        add = menu.addAction('添加到工作区')
        add.triggered.connect(self.add_log_to_workspace)
        
        menu.addSeparator()
        menu.addAction('清除高亮').triggered.connect(self.log_text.clear_highlight)
        
        menu.exec(self.log_text.mapToGlobal(pos))

    def show_search_context_menu(self, pos):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background-color: #2a2a3e; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 4px; }
            QMenu::item { color: #e0e0e0; padding: 8px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: rgba(74,158,255,0.3); }
        """)
        
        selected_rows = set(idx.row() for idx in self.search_table.selectedIndexes())
        
        if selected_rows:
            add = menu.addAction(f"添加到工作区 ({len(selected_rows)} 条)")
            add.triggered.connect(lambda: self.add_search_results_to_workspace(selected_rows))
            
            copy = menu.addAction('复制内容')
            copy.triggered.connect(lambda: self.copy_search_results(selected_rows))
        
        menu.exec(self.search_table.mapToGlobal(pos))

    def add_search_results_to_workspace(self, rows: set):
        lines = []
        for row in sorted(rows):
            if row < len(self.search_results):
                _, line, _ = self.search_results[row]
                lines.append(line)
        
        if lines:
            current = self.workspace_text.toPlainText()
            text = '\n'.join(lines)
            self.workspace_text.setPlainText((current + "\n" + text) if current else text)

    def copy_search_results(self, rows: set):
        lines = []
        for row in sorted(rows):
            if row < len(self.search_results):
                _, line, _ = self.search_results[row]
                lines.append(line)
        
        if lines:
            QApplication.clipboard().setText('\n'.join(lines))

    def add_log_to_workspace(self):
        cursor = self.log_text.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText().replace('\u2029', '\n')
        else:
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            text = cursor.selectedText()
        
        self.add_log_line_to_workspace(text)

    def add_log_line_to_workspace(self, text):
        text = text.strip()
        if not text:
            return
        current = self.workspace_text.toPlainText()
        self.workspace_text.setPlainText((current + "\n" + text) if current else text)

    def save_workspace(self):
        text = self.workspace_text.toPlainText()
        if text:
            filename = f"workspace_{self.get_device_display_name()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            output_dir = self.app_config.workspace_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / filename.replace(" ", "_").replace("/", "_")
            write_text_file(path, text)
            QMessageBox.information(self, '保存成功', f"已保存至:\n{path}")

    def clear_log(self):
        if self.log_lines and QMessageBox.question(self, '确认', '确定要清空所有日志吗?') != QMessageBox.StandardButton.Yes:
            return
        
        with self.pending_lock:
            self.pending_lines.clear()
            self.log_buffer.clear()
        self.log_text.clear()
        self.log_text.clear_highlight()
        self.clear_search()
        self.log_start_time = datetime.now() if self.log_fetcher.running else None
        self.log_end_time = None
        self.update_count_labels()

    def pull_client_logging_logs(self):
        if not self.current_device:
            self.status_label.setText('请选择设备后再拉取日志')
            return
        if self.config_puller and self.config_puller.isRunning():
            self.status_label.setText("日志正在拉取中...")
            return

        try:
            remote_path = self.app_config.client_logging_remote_path(self.get_package_name())
        except ValueError:
            self.status_label.setText('请输入包名后再拉取日志')
            return

        destination = self.app_config.log_dir()
        self.pull_config_btn.setEnabled(False)
        self.status_label.setText(f"正在拉取日志到 {destination}")
        self.config_puller = ClientLoggingPuller(self.current_device, remote_path, destination)
        self.config_puller.completed.connect(self.on_config_pull_completed)
        self.config_puller.failed.connect(self.on_config_pull_failed)
        self.config_puller.finished.connect(self.on_config_pull_finished)
        self.config_puller.start()

    def on_config_pull_completed(self, destination):
        if self.is_closing:
            return
        self.status_label.setText(f"日志已拉取到 {destination}")

    def on_config_pull_failed(self, message):
        if self.is_closing:
            return
        self.status_label.setText(f"日志拉取失败: {message}")

    def on_config_pull_finished(self):
        if self.is_closing:
            return
        self.pull_config_btn.setEnabled(True)

    def save_log(self):
        if not self.log_lines:
            QMessageBox.information(self, '提示', '没有日志可保存')
            return
        
        self.log_end_time = datetime.now()
        lines, _ = self.get_filtered()
        filename = self.generate_log_filename()
        output_dir = self.app_config.log_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / filename
        write_text_file(path, '\n'.join(lines))
        QMessageBox.information(self, '保存成功', f"已保存 {len(lines)} 行日志至:\n{path}")

    def closeEvent(self, event):
        try:
            self.shutdown_background_work()
        except Exception as e:
            print(f"关闭清理失败: {e}")
        finally:
            event.accept()

    def shutdown_background_work(self):
        self.is_closing = True
        if self.update_timer.isActive():
            self.update_timer.stop()
        if hasattr(self, "log_text") and self.log_text.single_click_timer.isActive():
            self.log_text.single_click_timer.stop()

        self._disconnect_fetcher_signals()
        self.log_fetcher.stop_fetching()
        if self.log_fetcher.isRunning():
            if not self.log_fetcher.wait(1500):
                self.log_fetcher.kill_process()
                self.log_fetcher.wait(500)

        if self.config_puller and self.config_puller.isRunning():
            self._disconnect_config_puller_signals()
            self.config_puller.stop()
            if not self.config_puller.wait(1500):
                self.config_puller.kill_process()
                self.config_puller.wait(500)

        if (
            self.device_refresh_worker
            and hasattr(self.device_refresh_worker, "isRunning")
            and self.device_refresh_worker.isRunning()
        ):
            self._disconnect_device_refresh_signals()
            self.device_refresh_worker.stop()
            if not self.device_refresh_worker.wait(3000):
                self.device_refresh_worker.kill_process()
                self.device_refresh_worker.wait(500)

    def _disconnect_fetcher_signals(self):
        for signal, slot in (
            (self.log_fetcher.log_received, self.on_log_received),
            (self.log_fetcher.error_occurred, self.on_fetch_error),
            (self.log_fetcher.fetch_finished, self.on_fetch_finished),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _disconnect_config_puller_signals(self):
        for signal, slot in (
            (self.config_puller.completed, self.on_config_pull_completed),
            (self.config_puller.failed, self.on_config_pull_failed),
            (self.config_puller.finished, self.on_config_pull_finished),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _disconnect_device_refresh_signals(self):
        for signal, slot in (
            (self.device_refresh_worker.devices_found, self.on_devices_refreshed),
            (self.device_refresh_worker.failed, self.on_device_refresh_failed),
            (self.device_refresh_worker.finished, self.on_device_refresh_finished),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    font = app.font()
    font.setFamily("Microsoft YaHei" if sys.platform == "win32" else "PingFang SC")
    font.setPointSize(10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()


