"""
Android Log查看工具 - v12.0
修复：滚动条、状态管理、协议替换
新增：英文搜索协议
"""

import sys
import subprocess
import threading
import time
import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *


# ==================== 带行号的文本编辑器 ====================

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
    
    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)
    
    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class LogTextEdit(QPlainTextEdit):
    content_clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        
        self.update_line_number_area_width(0)
        
        self.highlighted_line = -1
        self.original_line_numbers = []
    
    def mousePressEvent(self, event):
        viewport_rect = self.viewport().geometry()
        # 只有左键点击才触发 content_clicked（右键是菜单，不触发）
        if viewport_rect.contains(event.pos()) and event.button() == Qt.MouseButton.LeftButton:
            self.content_clicked.emit()
        super().mousePressEvent(event)
    
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


# ==================== 协议库和设备管理 ====================

class ProtocolManager:
    def __init__(self):
        self.storage_path = Path.home() / ".android_log_viewer" / "protocols.json"
        self.protocols = {}
        self.load()
    
    def load(self):
        try:
            if self.storage_path.exists():
                self.protocols = json.loads(self.storage_path.read_text(encoding='utf-8'))
        except:
            self.protocols = {}
    
    def save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(self.protocols, ensure_ascii=False, indent=2), encoding='utf-8')
    
    def add(self, cn: str, en: str):
        if cn and en:
            self.protocols[cn] = en
            self.save()
    
    def delete(self, cn: str):
        if cn in self.protocols:
            del self.protocols[cn]
            self.save()
    
    def search(self, keyword: str) -> list:
        """搜索协议：同时匹配中文名和英文名（不区分大小写）"""
        kw = keyword.lower()
        results = []
        for cn, en in self.protocols.items():
            # 匹配中文名或英文名
            if kw in cn.lower() or kw in en.lower():
                results.append((cn, en))
        return results
    
    def get_all(self) -> dict:
        return self.protocols.copy()


class DeviceAliasManager:
    def __init__(self):
        self.storage_path = Path.home() / ".android_log_viewer" / "device_aliases.json"
        self.aliases = {}
        self.load()
    
    def load(self):
        try:
            if self.storage_path.exists():
                self.aliases = json.loads(self.storage_path.read_text(encoding='utf-8'))
        except:
            self.aliases = {}
    
    def save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(self.aliases, ensure_ascii=False, indent=2), encoding='utf-8')
    
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
        self.editing_row = -1  # 当前编辑的行
        self.editing_cn = ""   # 当前编辑的原中文名
        self.setWindowTitle("协议库管理")
        self.setMinimumSize(700, 500)
        self.init_ui()
        self.load_data()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 添加/编辑区域
        add_frame = QFrame()
        add_frame.setObjectName("addFrame")
        add_layout = QVBoxLayout(add_frame)
        add_layout.setContentsMargins(15, 15, 15, 15)
        add_layout.setSpacing(10)
        
        # 第一行：中文
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("中文名称"))
        self.cn_input = QLineEdit()
        self.cn_input.setPlaceholderText("如：促销列表")
        row1.addWidget(self.cn_input)
        add_layout.addLayout(row1)
        
        # 第二行：英文
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("英文协议"))
        self.en_input = QLineEdit()
        self.en_input.setPlaceholderText("如：GetPromoList")
        row2.addWidget(self.en_input)
        add_layout.addLayout(row2)
        
        # 第三行：按钮
        row3 = QHBoxLayout()
        row3.addStretch()
        self.cancel_btn = QPushButton("取消编辑")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_edit)
        row3.addWidget(self.cancel_btn)
        self.add_btn = QPushButton("添加")
        self.add_btn.setObjectName("primaryBtn")
        self.add_btn.clicked.connect(self.add_or_update_protocol)
        row3.addWidget(self.add_btn)
        add_layout.addLayout(row3)
        
        layout.addWidget(add_frame)
        
        # 提示
        tip = QLabel("💡 双击表格行可编辑")
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)
        
        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["中文名称", "英文协议", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 60)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self.on_double_click)
        layout.addWidget(self.table)
        
        # 底部
        btn_layout = QHBoxLayout()
        import_btn = QPushButton("导入")
        import_btn.clicked.connect(self.import_data)
        btn_layout.addWidget(import_btn)
        export_btn = QPushButton("导出")
        export_btn.clicked.connect(self.export_data)
        btn_layout.addWidget(export_btn)
        btn_layout.addStretch()
        count_label = QLabel()
        count_label.setObjectName("countLabel")
        self.count_label = count_label
        btn_layout.addWidget(count_label)
        btn_layout.addSpacing(20)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primaryBtn")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        self.setStyleSheet("""
            QDialog { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1a2e, stop:1 #16213e); }
            QFrame#addFrame { background-color: rgba(255,255,255,0.05); border-radius: 10px; }
            QLineEdit { background-color: rgba(255,255,255,0.08); color: #e0e0e0; border: 1px solid rgba(255,255,255,0.1); padding: 10px 14px; border-radius: 6px; font-size: 14px; }
            QLineEdit:focus { border: 1px solid #4a9eff; }
            QLabel { color: #a0a0a0; font-size: 13px; min-width: 60px; }
            QLabel#countLabel { color: #64b5f6; min-width: 0; }
            QPushButton { background-color: rgba(255,255,255,0.1); color: #e0e0e0; padding: 10px 24px; border: none; border-radius: 6px; font-size: 13px; }
            QPushButton:hover { background-color: rgba(255,255,255,0.15); }
            QPushButton#primaryBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2); color: white; }
            QPushButton#primaryBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7a8ff0, stop:1 #8a5fb8); }
            QPushButton#delBtn { background-color: rgba(255,82,82,0.2); color: #ff8a80; padding: 6px 12px; min-width: 40px; }
            QPushButton#delBtn:hover { background-color: rgba(255,82,82,0.35); }
            QTableWidget { background-color: rgba(0,0,0,0.2); color: #e0e0e0; border: none; border-radius: 8px; }
            QTableWidget::item { padding: 10px 8px; }
            QTableWidget::item:selected { background-color: rgba(74,158,255,0.25); }
            QHeaderView { background-color: transparent; }
            QHeaderView::section { background-color: rgba(255,255,255,0.03); color: #808090; padding: 12px 8px; border: none; font-weight: bold; }
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
            
            del_btn = QPushButton("删除")
            del_btn.setObjectName("delBtn")
            del_btn.clicked.connect(lambda _, c=cn: self.delete_protocol(c))
            self.table.setCellWidget(row, 2, del_btn)
        
        self.count_label.setText(f"共 {len(protocols)} 条")
    
    def on_double_click(self, index):
        """双击编辑"""
        row = index.row()
        cn_item = self.table.item(row, 0)
        en_item = self.table.item(row, 1)
        if cn_item and en_item:
            self.editing_row = row
            self.editing_cn = cn_item.text()
            self.cn_input.setText(cn_item.text())
            self.en_input.setText(en_item.text())
            self.add_btn.setText("保存修改")
            self.cancel_btn.setVisible(True)
    
    def cancel_edit(self):
        """取消编辑"""
        self.editing_row = -1
        self.editing_cn = ""
        self.cn_input.clear()
        self.en_input.clear()
        self.add_btn.setText("添加")
        self.cancel_btn.setVisible(False)
    
    def add_or_update_protocol(self):
        cn, en = self.cn_input.text().strip(), self.en_input.text().strip()
        if not cn or not en:
            return
        
        if self.editing_row >= 0 and self.editing_cn:
            # 编辑模式：先删除旧的，再添加新的
            self.manager.delete(self.editing_cn)
        
        self.manager.add(cn, en)
        self.cancel_edit()
        self.load_data()
    
    def delete_protocol(self, cn):
        self.manager.delete(cn)
        self.load_data()
    
    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入", "", "JSON (*.json)")
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
                for cn, en in data.items():
                    self.manager.add(cn, en)
                self.load_data()
            except Exception as e:
                QMessageBox.warning(self, "错误", str(e))
    
    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出", "protocols.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.manager.get_all(), ensure_ascii=False, indent=2), encoding='utf-8')


class ProtocolCompleter(QCompleter):
    def __init__(self, manager: ProtocolManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.model = QStringListModel()
        self.setModel(self.model)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchContains)
        # results: [(display_text, en), ...] display_text是下拉显示的，en是最终填入的
        self.results = []
        self._popup_visible = False
        QTimer.singleShot(100, self._install_popup_filter)
    
    def _install_popup_filter(self):
        try:
            self.popup().installEventFilter(self)
        except:
            pass
    
    def eventFilter(self, obj, event):
        try:
            if obj == self.popup():
                if event.type() == QEvent.Type.Show:
                    self._popup_visible = True
                elif event.type() == QEvent.Type.Hide:
                    self._popup_visible = False
        except:
            pass
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
                # 统一显示格式：中文名 → 英文名
                display_text = f"{cn} → {en}"
                self.results.append((display_text, en))
                display_list.append(display_text)
        
        self.model.setStringList(display_list)
    
    def get_english(self, display_text: str) -> str:
        """从下拉选项获取英文协议名"""
        for display, en in self.results:
            if display == display_text:
                return en
        # 兜底：尝试从 → 后面提取
        if " → " in display_text:
            return display_text.split(" → ")[-1]
        return display_text


# ==================== 日志抓取 ====================

class LogFetcher(QThread):
    log_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

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
                text=True, encoding='utf-8', errors='ignore', bufsize=1
            )
            self.start()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def stop_fetching(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
            except:
                pass
            self.process = None
        self.pid_filter = None

    def run(self):
        while self.running and self.process:
            try:
                line = self.process.stdout.readline()
                if line:
                    self.log_received.emit(line.strip())
                elif self.process.poll() is not None:
                    break
            except:
                break


# ==================== 主窗口 ====================

class MainWindow(QMainWindow):
    MAX_LOG_LINES = 20000
    
    def __init__(self):
        super().__init__()
        self.log_fetcher = LogFetcher()
        self.current_device = None
        self.log_lines = []
        self.search_results = []
        self.pending_lines = []
        self.pending_lock = threading.Lock()
        self.filtered_line_mapping = []
        
        # 简化状态：只有两个
        self.auto_scroll = True      # 是否自动滚动到底部
        self.freeze_display = False  # 是否冻结显示（暂停刷新内容）
        
        # 防止重复弹窗
        self.limit_dialog_shown = False
        
        # 日志起止时间
        self.log_start_time = None
        self.log_end_time = None
        
        self.protocol_manager = ProtocolManager()
        self.device_manager = DeviceAliasManager()
        
        self.update_timer = QTimer()
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
        layout.addWidget(self.create_filter_bar())
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

        # 设备选择
        device_group = QFrame()
        device_group.setObjectName("groupFrame")
        device_layout = QHBoxLayout(device_group)
        device_layout.setContentsMargins(8, 4, 8, 4)
        device_layout.setSpacing(8)
        
        device_layout.addWidget(QLabel("设备"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(200)
        self.device_combo.currentIndexChanged.connect(lambda i: setattr(self, 'current_device', self.device_combo.currentData() if i >= 0 else None))
        device_layout.addWidget(self.device_combo)
        
        edit_btn = QPushButton("备注")
        edit_btn.setObjectName("smallBtn")
        edit_btn.clicked.connect(self.edit_device_alias)
        device_layout.addWidget(edit_btn)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("smallBtn")
        refresh_btn.clicked.connect(self.refresh_devices)
        device_layout.addWidget(refresh_btn)
        
        toolbar.addWidget(device_group)

        # 过滤选项
        filter_group = QFrame()
        filter_group.setObjectName("groupFrame")
        filter_layout = QHBoxLayout(filter_group)
        filter_layout.setContentsMargins(8, 4, 8, 4)
        filter_layout.setSpacing(8)
        
        filter_layout.addWidget(QLabel("级别"))
        self.level_combo = QComboBox()
        self.level_combo.addItems(["ALL", "V", "D", "I", "W", "E", "F"])
        self.level_combo.setMaximumWidth(70)
        self.level_combo.currentTextChanged.connect(self.on_filter_changed)
        filter_layout.addWidget(self.level_combo)
        
        self.unity_check = QCheckBox("Unity")
        self.unity_check.toggled.connect(self.on_filter_changed)
        filter_layout.addWidget(self.unity_check)
        
        toolbar.addWidget(filter_group)

        toolbar.addStretch()

        protocol_btn = QPushButton("协议库")
        protocol_btn.clicked.connect(lambda: ProtocolDialog(self.protocol_manager, self).exec())
        toolbar.addWidget(protocol_btn)

        self.toggle_btn = QPushButton("开始抓取")
        self.toggle_btn.setObjectName("primaryBtn")
        self.toggle_btn.setMinimumWidth(100)
        self.toggle_btn.clicked.connect(self.toggle_logging)
        toolbar.addWidget(self.toggle_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("dangerBtn")
        clear_btn.clicked.connect(self.clear_log)
        toolbar.addWidget(clear_btn)

        save_btn = QPushButton("保存日志")
        save_btn.clicked.connect(self.save_log)
        toolbar.addWidget(save_btn)

        return frame

    def create_filter_bar(self):
        frame = QFrame()
        frame.setObjectName("filterFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        layout.addWidget(QLabel("包名"))
        self.package_input = QLineEdit()
        self.package_input.setText("com.bingo.cruise.free.best.top.game")
        self.package_input.setMinimumWidth(350)
        layout.addWidget(self.package_input)
        
        self.get_pid_btn = QPushButton("获取 PID")
        self.get_pid_btn.clicked.connect(self.get_package_pid)
        layout.addWidget(self.get_pid_btn)
        
        self.pid_label = QLabel("")
        self.pid_label.setObjectName("pidLabel")
        layout.addWidget(self.pid_label)
        
        layout.addStretch()
        
        return frame

    def get_package_pid(self):
        package = self.package_input.text().strip()
        if not package:
            QMessageBox.warning(self, "提示", "请输入包名")
            return
        
        if not self.current_device:
            QMessageBox.warning(self, "提示", "请选择设备")
            return
        
        try:
            result = subprocess.run(
                ['adb', '-s', self.current_device, 'shell', 'pidof', package],
                capture_output=True, text=True, timeout=5
            )
            pid = result.stdout.strip()
            if pid:
                self.pid_label.setText(f"PID: {pid}")
                self.status_label.setText(f"已获取 PID: {pid}")
            else:
                self.pid_label.setText("未找到进程")
                QMessageBox.warning(self, "提示", f"未找到包名 {package} 对应的进程\n请确认应用已启动")
        except Exception as e:
            self.pid_label.setText("获取失败")
            QMessageBox.warning(self, "错误", f"获取 PID 失败: {e}")

    def create_search_bar(self):
        frame = QFrame()
        frame.setObjectName("searchFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        layout.addWidget(QLabel("搜索"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字或协议名（支持中英文，如 promo）...")
        self.search_input.setMinimumWidth(300)
        self.search_input.returnPressed.connect(self.perform_search)
        layout.addWidget(self.search_input)
        
        self.completer = ProtocolCompleter(self.protocol_manager, self.search_input)
        self.completer.activated.connect(self.on_protocol_selected)
        self.search_input.setCompleter(self.completer)
        self.search_input.textChanged.connect(self.completer.update)

        search_btn = QPushButton("搜索")
        search_btn.setObjectName("primaryBtn")
        search_btn.clicked.connect(self.perform_search)
        layout.addWidget(search_btn)

        self.case_check = QCheckBox("区分大小写")
        layout.addWidget(self.case_check)
        self.regex_check = QCheckBox("正则表达式")
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
        header_layout.addWidget(QLabel("实时日志"))
        self.scroll_label = QLabel("")
        self.scroll_label.setObjectName("warningLabel")
        header_layout.addWidget(self.scroll_label)
        header_layout.addStretch()
        self.log_count_label = QLabel("0 行")
        self.log_count_label.setObjectName("countLabel")
        header_layout.addWidget(self.log_count_label)
        clear_hl_btn = QPushButton("清除高亮")
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
        layout.addWidget(self.log_text)

        return frame

    def on_log_content_clicked(self):
        """点击日志内容区域"""
        if self.freeze_display:
            # 解冻，恢复正常刷新
            self.freeze_display = False
            self.auto_scroll = True
            self.scroll_label.setText("")
            # 立即刷新一次
            self.force_update_display()
        elif self.log_fetcher.running:
            # 切换自动滚动
            self.auto_scroll = not self.auto_scroll
            if self.auto_scroll:
                self.scroll_label.setText("")
            else:
                # 暂停滚动但不冻结内容
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
        header_layout.addWidget(QLabel("工作区"))
        header_layout.addStretch()
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("smallBtn")
        clear_btn.clicked.connect(lambda: self.workspace_text.clear() if QMessageBox.question(self, "确认", "清空工作区？") == QMessageBox.StandardButton.Yes else None)
        header_layout.addWidget(clear_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("smallBtn")
        save_btn.clicked.connect(self.save_workspace)
        header_layout.addWidget(save_btn)
        layout.addWidget(header)

        self.workspace_text = QPlainTextEdit()
        self.workspace_text.setFont(QFont("Consolas", 10))
        self.workspace_text.setPlaceholderText("右键添加日志到这里...")
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
        header_layout.addWidget(QLabel("搜索结果"))
        self.search_count = QLabel("0 条")
        self.search_count.setObjectName("countLabel")
        header_layout.addWidget(self.search_count)
        header_layout.addStretch()
        clear_btn = QPushButton("清除")
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
        self.search_table.verticalHeader().setVisible(False)  # 隐藏行号
        self.search_table.setShowGrid(False)  # 隐藏网格线
        self.search_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_table.customContextMenuRequested.connect(self.show_search_context_menu)
        layout.addWidget(self.search_table)

        return frame

    def create_status_bar(self):
        self.status_bar = QStatusBar()
        self.status_bar.setObjectName("statusBar")
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("就绪")
        self.count_label = QLabel("日志: 0 / 20000")
        self.status_bar.addWidget(self.status_label)
        self.status_bar.addPermanentWidget(self.count_label)

    def setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0f0f1a, stop:1 #1a1a2e); }
            
            QFrame#toolbarFrame, QFrame#filterFrame, QFrame#searchFrame {
                background-color: rgba(255,255,255,0.03);
                border-radius: 10px;
            }
            QFrame#groupFrame {
                background-color: rgba(255,255,255,0.05);
                border-radius: 6px;
            }
            QFrame#logFrame, QFrame#workspaceFrame, QFrame#searchResultFrame {
                background-color: rgba(0,0,0,0.2);
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,0.05);
            }
            QFrame#sectionHeader {
                background-color: rgba(255,255,255,0.03);
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            
            QLabel { color: #a0a0b0; font-size: 12px; }
            QLabel#pidLabel { color: #4ecdc4; font-weight: bold; }
            QLabel#warningLabel { color: #ffa726; }
            QLabel#countLabel { color: #64b5f6; font-weight: bold; }
            
            QComboBox {
                background-color: rgba(255,255,255,0.08);
                color: #e0e0e0;
                border: 1px solid rgba(255,255,255,0.1);
                padding: 6px 12px;
                border-radius: 6px;
                min-width: 80px;
            }
            QComboBox:hover { border: 1px solid rgba(255,255,255,0.2); }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 6px solid #808090; }
            QComboBox QAbstractItemView {
                background-color: #2a2a3e;
                color: #e0e0e0;
                selection-background-color: #4a4a6e;
                border: 1px solid rgba(255,255,255,0.1);
            }
            
            QLineEdit {
                background-color: rgba(255,255,255,0.06);
                color: #e0e0e0;
                border: 1px solid rgba(255,255,255,0.08);
                padding: 8px 14px;
                border-radius: 8px;
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #4a9eff; background-color: rgba(255,255,255,0.08); }
            
            QCheckBox { color: #a0a0b0; spacing: 6px; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.2); background-color: rgba(255,255,255,0.05); }
            QCheckBox::indicator:checked { background-color: #4a9eff; border-color: #4a9eff; }
            
            QPushButton {
                background-color: rgba(255,255,255,0.08);
                color: #d0d0d0;
                border: none;
                padding: 8px 18px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: rgba(255,255,255,0.12); }
            QPushButton:pressed { background-color: rgba(255,255,255,0.06); }
            
            QPushButton#primaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667eea, stop:1 #764ba2);
                color: white;
            }
            QPushButton#primaryBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7a8ff0, stop:1 #8a5fb8); }
            
            QPushButton#dangerBtn { background-color: rgba(255,100,100,0.2); color: #ff8a80; }
            QPushButton#dangerBtn:hover { background-color: rgba(255,100,100,0.3); }
            
            QPushButton#smallBtn { padding: 5px 12px; font-size: 12px; }
            
            QPlainTextEdit {
                background-color: rgba(0,0,0,0.3);
                color: #d4d4d4;
                border: none;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                selection-background-color: rgba(74,158,255,0.3);
            }
            
            QTableWidget {
                background-color: transparent;
                color: #d0d0d0;
                border: none;
                gridline-color: transparent;
                outline: none;
            }
            QTableWidget::item { padding: 6px 8px; border: none; background-color: transparent; }
            QTableWidget::item:selected { background-color: rgba(74,158,255,0.25); }
            QHeaderView { background-color: transparent; border: none; }
            QHeaderView::section {
                background-color: transparent;
                color: #808090;
                padding: 8px;
                border: none;
                border-bottom: 1px solid rgba(255,255,255,0.08);
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
                background-color: rgba(255,255,255,0.15);
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background-color: rgba(255,255,255,0.25); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            
            QScrollBar:horizontal {
                background-color: transparent;
                height: 10px;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background-color: rgba(255,255,255,0.15);
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover { background-color: rgba(255,255,255,0.25); }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
            
            QStatusBar#statusBar {
                background-color: rgba(0,0,0,0.3);
                color: #808090;
                border-top: 1px solid rgba(255,255,255,0.05);
            }
            
            QMessageBox { background-color: #1a1a2e; }
            QMessageBox QLabel { color: #e0e0e0; }
        """)

    def connect_signals(self):
        self.log_fetcher.log_received.connect(self.on_log_received)
        self.log_fetcher.error_occurred.connect(lambda e: (QMessageBox.critical(self, "错误", e), setattr(self.toggle_btn, 'text', "开始抓取")))

    def refresh_devices(self):
        try:
            result = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=3)
            current = self.current_device
            self.device_combo.clear()
            
            for line in result.stdout.strip().split('\n')[1:]:
                if 'device' in line and 'offline' not in line:
                    serial = line.split()[0]
                    self.device_combo.addItem(self.device_manager.display_name(serial), serial)

            if self.device_combo.count() > 0:
                if current:
                    for i in range(self.device_combo.count()):
                        if self.device_combo.itemData(i) == current:
                            self.device_combo.setCurrentIndex(i)
                            break
                self.current_device = self.device_combo.currentData()
                alias = self.device_manager.get(self.current_device)
                self.status_label.setText(f"设备: {alias or self.current_device}")
            else:
                self.status_label.setText("未检测到设备")
        except Exception as e:
            self.status_label.setText(f"错误: {e}")

    def edit_device_alias(self):
        if not self.current_device:
            QMessageBox.warning(self, "提示", "请先选择设备")
            return
        text, ok = QInputDialog.getText(self, "设备备注", f"设备序列号: {self.current_device}\n\n请输入备注名:", text=self.device_manager.get(self.current_device))
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
            self.toggle_btn.setText("开始抓取")
            self.status_label.setText("已停止")
            self.scroll_label.setText("")
            self.auto_scroll = True
            self.freeze_display = False
            self.log_end_time = datetime.now()
            # 停止后定位到最后
            QTimer.singleShot(50, lambda: self.log_text.verticalScrollBar().setValue(
                self.log_text.verticalScrollBar().maximum()))
        else:
            if not self.current_device:
                QMessageBox.warning(self, "警告", "请选择设备")
                return
            
            pid = None
            pid_text = self.pid_label.text()
            if pid_text.startswith("PID: "):
                pid = pid_text.replace("PID: ", "").strip()
            
            with self.pending_lock:
                self.pending_lines.clear()
                self.log_lines.clear()
            self.log_text.clear()
            self.log_text.clear_highlight()
            self.auto_scroll = True
            self.freeze_display = False
            self.limit_dialog_shown = False  # 重置弹窗标志
            self.scroll_label.setText("")
            
            self.log_start_time = datetime.now()
            self.log_end_time = None
            
            self.log_fetcher.start_fetching(self.current_device, pid)
            self.toggle_btn.setText("停止抓取")
            
            if pid:
                self.status_label.setText(f"抓取中... (PID: {pid})")
            else:
                self.status_label.setText("抓取中...")

    def on_filter_changed(self):
        if not self.freeze_display:
            self.force_update_display()

    def on_log_received(self, line):
        with self.pending_lock:
            self.pending_lines.append(line)
            if len(self.pending_lines) > 500:
                self.pending_lines = self.pending_lines[-500:]

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

    def check_log_limit(self):
        """检查日志是否超限"""
        if len(self.log_lines) >= self.MAX_LOG_LINES and not self.limit_dialog_shown:
            self.limit_dialog_shown = True  # 防止重复弹窗
            self.log_end_time = datetime.now()
            
            # 后台保存
            filename = self.generate_log_filename()
            save_path = Path.home() / "Desktop" / filename
            
            lines_to_save = self.log_lines.copy()
            threading.Thread(target=self._save_log_async, args=(save_path, lines_to_save)).start()
            
            reply = QMessageBox.question(
                self, 
                "日志已满", 
                f"日志已达到 {self.MAX_LOG_LINES} 条上限\n\n已自动保存至:\n{save_path}\n\n是否清空面板继续抓取?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                with self.pending_lock:
                    self.log_lines.clear()
                    self.pending_lines.clear()
                self.log_text.clear()
                self.log_text.clear_highlight()
                self.clear_search()
                self.log_start_time = datetime.now()
                self.log_end_time = None
                self.limit_dialog_shown = False  # 重置，以便下次还能弹
            else:
                self.log_fetcher.stop_fetching()
                self.toggle_btn.setText("开始抓取")
                self.status_label.setText("已停止 - 可查看旧日志")

    def _save_log_async(self, path, lines):
        try:
            path.write_text('\n'.join(lines), encoding='utf-8')
        except Exception as e:
            print(f"保存失败: {e}")

    def force_update_display(self):
        """强制更新显示（忽略 freeze 状态）"""
        self._do_update_display(force=True)

    def update_display(self):
        """定时更新显示"""
        self._do_update_display(force=False)

    def _do_update_display(self, force=False):
        # 收集新日志
        with self.pending_lock:
            if self.pending_lines:
                self.log_lines.extend(self.pending_lines)
                self.pending_lines.clear()

        if not self.log_lines:
            self.log_count_label.setText("0 行")
            self.count_label.setText(f"日志: 0 / {self.MAX_LOG_LINES}")
            return

        # 检查是否超限
        if len(self.log_lines) >= self.MAX_LOG_LINES:
            QTimer.singleShot(0, self.check_log_limit)
            return

        # 如果冻结且不是强制刷新，只更新计数
        if self.freeze_display and not force:
            total = len(self.log_lines)
            self.count_label.setText(f"日志: {total} / {self.MAX_LOG_LINES}")
            return

        filtered, mapping = self.get_filtered()
        self.filtered_line_mapping = mapping

        if filtered:
            # 保存当前滚动位置
            scrollbar = self.log_text.verticalScrollBar()
            old_value = scrollbar.value()
            old_max = scrollbar.maximum()
            at_bottom = (old_value >= old_max - 10) if old_max > 0 else True
            
            highlighted = self.log_text.highlighted_line
            self.log_text.setPlainText('\n'.join(filtered))
            self.log_text.set_original_line_numbers(mapping)
            
            if highlighted >= 0 and highlighted < len(filtered):
                self.log_text.set_highlighted_line(highlighted)

            # 恢复滚动位置
            if self.auto_scroll and self.log_fetcher.running:
                scrollbar.setValue(scrollbar.maximum())
            elif not self.auto_scroll and not at_bottom:
                # 尝试保持相对位置
                if old_max > 0:
                    ratio = old_value / old_max
                    scrollbar.setValue(int(scrollbar.maximum() * ratio))

        total = len(self.log_lines)
        shown = len(mapping)
        self.log_count_label.setText(f"{shown} 行" if shown == total else f"{shown}/{total} 行")
        self.count_label.setText(f"日志: {total} / {self.MAX_LOG_LINES}")

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
            mapping.append(idx)
        
        return lines, mapping

    def on_protocol_selected(self, selected_text: str):
        """协议库选项被点击时，只填入英文协议名"""
        # selected_text 格式是 "中文名 → 英文名"
        if " → " in selected_text:
            en = selected_text.split(" → ")[-1]
        else:
            en = selected_text
        # 延迟设置（100ms），确保在 Qt 自动填充完成后覆盖
        QTimer.singleShot(100, lambda: self._set_search_text(en))
    
    def _set_search_text(self, text: str):
        """设置搜索框文本并执行搜索"""
        self.search_input.setText(text)
        self.perform_search()

    def perform_search(self):
        text = self.search_input.text()
        if not text:
            return

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
                except:
                    pass
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
        self.search_count.setText("0 条")

    def on_search_clicked(self, index):
        """双击搜索结果定位"""
        row = index.row()
        if row < len(self.search_results):
            filtered_idx, _, orig = self.search_results[row]
            block = self.log_text.document().findBlockByLineNumber(filtered_idx)
            if block.isValid():
                # 冻结显示，防止刷新打断定位
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
        
        copy = menu.addAction("复制")
        copy.setEnabled(cursor.hasSelection())
        copy.triggered.connect(lambda: QApplication.clipboard().setText(cursor.selectedText().replace('\u2029', '\n')))
        
        add = menu.addAction("添加到工作区")
        add.triggered.connect(self.add_log_to_workspace)
        
        menu.addSeparator()
        menu.addAction("清除高亮").triggered.connect(self.log_text.clear_highlight)
        
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
            
            copy = menu.addAction("复制内容")
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
        
        if text.strip():
            current = self.workspace_text.toPlainText()
            self.workspace_text.setPlainText((current + "\n" + text) if current else text)

    def save_workspace(self):
        text = self.workspace_text.toPlainText()
        if text:
            filename = f"workspace_{self.get_device_display_name()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path = Path.home() / "Desktop" / filename.replace(" ", "_").replace("/", "_")
            path.write_text(text, encoding='utf-8')
            QMessageBox.information(self, "保存成功", f"已保存至:\n{path}")

    def clear_log(self):
        if self.log_lines and QMessageBox.question(self, "确认", "确定要清空所有日志吗?") != QMessageBox.StandardButton.Yes:
            return
        
        with self.pending_lock:
            self.pending_lines.clear()
            self.log_lines.clear()
        self.log_text.clear()
        self.log_text.clear_highlight()
        self.clear_search()
        self.log_start_time = datetime.now() if self.log_fetcher.running else None
        self.log_end_time = None
        self.limit_dialog_shown = False

    def save_log(self):
        if not self.log_lines:
            QMessageBox.information(self, "提示", "没有日志可保存")
            return
        
        self.log_end_time = datetime.now()
        lines, _ = self.get_filtered()
        filename = self.generate_log_filename()
        path = Path.home() / "Desktop" / filename
        path.write_text('\n'.join(lines), encoding='utf-8')
        QMessageBox.information(self, "保存成功", f"已保存 {len(lines)} 行日志至:\n{path}")

    def closeEvent(self, event):
        self.log_fetcher.stop_fetching()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    font = app.font()
    font.setFamily("Microsoft YaHei" if sys.platform == "win32" else "PingFang SC")
    font.setPointSize(10)
    app.setFont(font)
    
    MainWindow().show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
