import os
import subprocess
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QListView

from main import DarkComboBox, DeviceRefreshWorker, MainWindow


class BrokenSignal:
    def disconnect(self, _slot):
        raise RuntimeError("wrapped C/C++ object has been deleted")


class BrokenDeviceRefreshWorker:
    devices_found = BrokenSignal()
    failed = BrokenSignal()
    finished = BrokenSignal()


class FakeRunningProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class MainWindowWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        if hasattr(self, "window"):
            self.window.close()

    def test_adds_log_line_to_workspace(self):
        self.window = MainWindow()

        self.window.add_log_line_to_workspace("first line")
        self.window.add_log_line_to_workspace("second line")

        self.assertEqual(
            self.window.workspace_text.toPlainText(),
            "first line\nsecond line",
        )

    def test_close_stops_refresh_timer(self):
        self.window = MainWindow()
        self.assertTrue(self.window.update_timer.isActive())

        self.window.close()
        self.app.processEvents()

        self.assertFalse(self.window.update_timer.isActive())

    def test_repeated_open_close_processes_pending_events(self):
        for _ in range(3):
            self.window = MainWindow()
            self.window.close()
            self.app.processEvents()
            self.assertTrue(self.window.is_closing)

    def test_disconnect_device_refresh_signals_ignores_deleted_qt_objects(self):
        self.window = MainWindow()
        self.window.shutdown_background_work()
        self.window.device_refresh_worker = BrokenDeviceRefreshWorker()

        self.window._disconnect_device_refresh_signals()

    def test_device_refresh_worker_stop_terminates_current_process(self):
        worker = DeviceRefreshWorker(connect_addresses=[])
        process = FakeRunningProcess()
        worker.process = process

        worker.stop()

        self.assertTrue(process.terminated)

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only subprocess flag")
    def test_device_refresh_worker_starts_subprocess_without_console_window(self):
        worker = DeviceRefreshWorker(connect_addresses=[])

        class FakeCompletedProcess:
            returncode = 0

            def communicate(self, timeout=None):
                return "", ""

            def poll(self):
                return self.returncode

        with patch("main.subprocess.Popen", return_value=FakeCompletedProcess()) as popen:
            result = worker.run_command(["adb", "devices", "-l"], timeout=3)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            popen.call_args.kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW,
            subprocess.CREATE_NO_WINDOW,
        )

    def test_combo_boxes_use_custom_dark_popup_views(self):
        self.window = MainWindow()

        for combo in (self.window.device_combo, self.window.level_combo):
            self.assertIsInstance(combo, DarkComboBox)
            self.assertIsInstance(combo.view(), QListView)
            self.assertEqual(combo.view().objectName(), "comboPopup")

    def test_device_combo_keeps_readable_width(self):
        self.window = MainWindow()

        self.assertGreaterEqual(self.window.device_combo.minimumWidth(), 220)
        self.assertEqual(
            self.window.device_combo.sizePolicy().horizontalPolicy(),
            self.window.device_combo.sizePolicy().Policy.MinimumExpanding,
        )
    def test_input_dialog_uses_readable_dark_style(self):
        self.window = MainWindow()
        style = self.window.styleSheet()

        self.assertIn("QInputDialog { background-color: #1f2328; }", style)
        self.assertIn("QInputDialog QLabel { color: #e6edf3;", style)
        self.assertIn("QInputDialog QLineEdit", style)

    def test_search_input_placeholder_is_readable_chinese(self):
        self.window = MainWindow()

        self.assertEqual(
            self.window.search_input.placeholderText(),
            "输入关键字或协议名（支持中英文，如 promo）...",
        )

    def test_protocol_completer_displays_and_parses_arrow_separator(self):
        self.window = MainWindow()
        self.window.protocol_manager.protocols = {"促销列表": "GetPromoList"}

        self.window.completer.update("促销")
        display_text = self.window.completer.model.stringList()[0]

        self.assertEqual(display_text, "促销列表 → GetPromoList")
        self.assertEqual(self.window.completer.get_english(display_text), "GetPromoList")
        self.window.on_protocol_selected(display_text)

    def test_device_refresh_worker_parses_only_ready_devices(self):
        worker = DeviceRefreshWorker(connect_addresses=[])
        output = """List of devices attached
127.0.0.1:5555 device product:sdk_gphone model:sdk_gphone64_x86_64 device:emu64x
emulator-5554 device product:sdk_gphone model:Pixel_8 device:emu64x
127.0.0.1:5557 offline product:sdk_gphone model:Pixel_7 device:emu64x
abc unauthorized product:sdk_gphone model:Pixel_6 device:emu64x
"""

        self.assertEqual(
            worker.parse_devices(output),
            ["127.0.0.1:5555", "emulator-5554"],
        )

    def test_device_refresh_worker_discovers_mumu_listening_ports_from_lsof(self):
        worker = DeviceRefreshWorker(connect_addresses=[])
        output = """COMMAND     PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
MuMuPlaye   447 u     4u  IPv4  0x00      0t0  TCP *:20001 (LISTEN)
MuMuEmula 10766 u    39u  IPv4  0x00      0t0  TCP *:5555 (LISTEN)
MuMuEmula 10766 u    43u  IPv4  0x00      0t0  TCP *:17600 (LISTEN)
MuMuEmula 50483 u     9u  IPv4  0x00      0t0  TCP 127.0.0.1:18432 (LISTEN)
MuMuEmula 50483 u    41u  IPv4  0x00      0t0  TCP *:17408 (LISTEN)
MuMuEmula 50483 u    43u  IPv4  0x00      0t0  TCP *:16384 (LISTEN)
Unity     51868 u     4u  IPv6  0x00      0t0  TCP *:7777 (LISTEN)
adb       62450 u     8u  IPv4  0x00      0t0  TCP 127.0.0.1:5037 (LISTEN)
"""

        self.assertEqual(
            worker.parse_lsof_listening_ports(output),
            ["127.0.0.1:5555", "127.0.0.1:16384"],
        )

    def test_device_refresh_worker_discovers_windows_emulator_ports(self):
        worker = DeviceRefreshWorker(connect_addresses=[])
        netstat = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:16384          0.0.0.0:0              LISTENING       50483
  TCP    0.0.0.0:17408          0.0.0.0:0              LISTENING       50483
  TCP    127.0.0.1:5037         0.0.0.0:0              LISTENING       62450
  TCP    127.0.0.1:7777         0.0.0.0:0              LISTENING       51868
"""
        tasklist = '"MuMuPlayer.exe","50483","Console","1","100 K"\n"adb.exe","62450","Console","1","100 K"\n'

        self.assertEqual(
            worker.parse_windows_netstat_ports(netstat, worker.parse_windows_tasklist(tasklist)),
            ["127.0.0.1:16384"],
        )


if __name__ == "__main__":
    unittest.main()

