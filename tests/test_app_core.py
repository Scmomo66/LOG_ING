import unittest
import tempfile
import json
from pathlib import Path


def rooted_path(*parts):
    return Path(Path.cwd().anchor).joinpath(*parts)

from app_core import (
    AppConfig,
    RollingLogBuffer,
    client_logging_remote_path,
    default_device_aliases_path,
    default_protocols_path,
    default_log_dir,
    default_workspace_dir,
    load_app_config,
)


class RollingLogBufferTest(unittest.TestCase):
    def test_keeps_latest_lines_and_tracks_original_line_numbers(self):
        buffer = RollingLogBuffer(limit=3)

        buffer.extend(["line 1", "line 2"])
        buffer.extend(["line 3", "line 4", "line 5"])

        self.assertEqual(buffer.lines, ["line 3", "line 4", "line 5"])
        self.assertEqual(buffer.offset, 2)
        self.assertEqual(buffer.total_seen, 5)
        self.assertEqual(buffer.original_index(0), 2)

    def test_clear_resets_lines_and_offset(self):
        buffer = RollingLogBuffer(limit=2)
        buffer.extend(["a", "b", "c"])

        buffer.clear()

        self.assertEqual(buffer.lines, [])
        self.assertEqual(buffer.offset, 0)
        self.assertEqual(buffer.total_seen, 0)


class LogPullPathTest(unittest.TestCase):
    def test_uses_script_directory_log_folder_when_not_frozen(self):
        app_file = rooted_path("repo", "main.py")

        self.assertEqual(
            default_log_dir(app_file=app_file, frozen=False),
            rooted_path("repo", "output", "log"),
        )

    def test_uses_executable_directory_log_folder_when_frozen(self):
        executable = rooted_path("Applications", "AndroidLogViewer", "AndroidLogViewer.exe")

        self.assertEqual(
            default_log_dir(executable=executable, frozen=True),
            rooted_path("Applications", "AndroidLogViewer", "output", "log"),
        )

    def test_uses_output_workspace_folder_for_workspace_exports(self):
        app_file = rooted_path("repo", "main.py")

        self.assertEqual(
            default_workspace_dir(app_file=app_file, frozen=False),
            rooted_path("repo", "output", "workspace"),
        )

    def test_uses_data_folder_for_protocols_and_device_aliases(self):
        app_file = rooted_path("repo", "main.py")

        self.assertEqual(
            default_protocols_path(app_file=app_file, frozen=False),
            rooted_path("repo", "data", "protocols.json"),
        )
        self.assertEqual(
            default_device_aliases_path(app_file=app_file, frozen=False),
            rooted_path("repo", "data", "device_aliases.json"),
        )

    def test_builds_remote_path_from_package_name(self):
        self.assertEqual(
            client_logging_remote_path("com.example.game"),
            "/sdcard/Android/data/com.example.game/files/client_logging_temp",
        )


class AppConfigTest(unittest.TestCase):
    def test_creates_default_config_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "data" / "config.json"

            config = load_app_config(config_path=config_path, app_dir=Path(tmp))

            self.assertTrue(config_path.exists())
            self.assertEqual(config.package_name, "com.bingo.cruise.free.best.top.game")
            self.assertEqual(config.log_dir(), Path(tmp) / "output" / "log")
            self.assertEqual(config.workspace_dir(), Path(tmp) / "output" / "workspace")

    def test_default_config_path_uses_supplied_app_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_app_config(app_dir=Path(tmp))

            self.assertEqual(config.config_path, Path(tmp) / "data" / "config.json")
            self.assertTrue(config.config_path.exists())

    def test_resolves_configured_paths_and_adb_connect_targets(self):
        config = AppConfig(
            app_dir=Path("/repo"),
            package_name="com.example.game",
            log_dir_value="logs",
            workspace_dir_value="/tmp/workspace",
            adb_connect_addresses=[" 127.0.0.1:5555 ", "", "127.0.0.1:5555", "127.0.0.1:7555"],
            client_logging_remote_template="/sdcard/Android/data/{package}/files/custom_logs",
        )

        self.assertEqual(config.log_dir(), Path("/repo/logs"))
        self.assertEqual(config.workspace_dir(), Path("/tmp/workspace"))
        self.assertEqual(config.adb_connect_targets(), ["127.0.0.1:5555", "127.0.0.1:7555"])
        self.assertEqual(
            config.client_logging_remote_path(),
            "/sdcard/Android/data/com.example.game/files/custom_logs",
        )

    def test_migrates_deprecated_adb_scan_config_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "data" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps({
                    "package_name": "com.example.game",
                    "output_dir": "output",
                    "log_dir": None,
                    "workspace_dir": None,
                    "adb_scan_hosts": ["127.0.0.1"],
                    "adb_scan_ports": [5555, 7555],
                    "client_logging_remote_template": "/sdcard/Android/data/{package}/files/client_logging_temp",
                }),
                encoding="utf-8",
            )

            config = load_app_config(config_path=config_path, app_dir=Path(tmp))
            saved = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(config.adb_connect_targets(), [])
            self.assertNotIn("adb_scan_hosts", saved)
            self.assertNotIn("adb_scan_ports", saved)
            self.assertEqual(saved["adb_connect_addresses"], [])


if __name__ == "__main__":
    unittest.main()
