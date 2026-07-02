from pathlib import Path
from dataclasses import dataclass, field
import json
import sys
from typing import Optional

DEFAULT_PACKAGE_NAME = "com.bingo.cruise.free.best.top.game"
DEFAULT_CLIENT_LOGGING_REMOTE_TEMPLATE = "/sdcard/Android/data/{package}/files/client_logging_temp"
TEXT_ENCODING = "gb18030"
LEGACY_TEXT_ENCODINGS = ("utf-8-sig", "utf-8")


def read_text_file(path: Path) -> str:
    try:
        return Path(path).read_text(encoding=TEXT_ENCODING)
    except UnicodeDecodeError:
        for encoding in LEGACY_TEXT_ENCODINGS:
            try:
                return Path(path).read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise


def write_text_file(path: Path, text: str):
    Path(path).write_text(text, encoding=TEXT_ENCODING)


def load_json_file(path: Path):
    path = Path(path)
    last_error = None
    for encoding in (TEXT_ENCODING, *LEGACY_TEXT_ENCODINGS):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            last_error = error
    raise last_error


def save_json_file(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_file(path, json.dumps(data, ensure_ascii=False, indent=2))


class RollingLogBuffer:
    def __init__(self, limit: int):
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        self.limit = limit
        self.lines = []
        self.offset = 0

    @property
    def total_seen(self) -> int:
        return self.offset + len(self.lines)

    def extend(self, new_lines):
        if not new_lines:
            return

        self.lines.extend(new_lines)
        overflow = len(self.lines) - self.limit
        if overflow > 0:
            del self.lines[:overflow]
            self.offset += overflow

    def clear(self):
        self.lines.clear()
        self.offset = 0

    def original_index(self, visible_index: int) -> int:
        return self.offset + visible_index


def default_output_dir(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen:
        base = Path(executable or sys.executable).resolve().parent
    else:
        base = Path(app_file or __file__).resolve().parent
    return base / "output"


def default_log_dir(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    return default_output_dir(app_file=app_file, executable=executable, frozen=frozen) / "log"


def default_workspace_dir(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    return default_output_dir(app_file=app_file, executable=executable, frozen=frozen) / "workspace"


def default_data_dir(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen:
        base = Path(executable or sys.executable).resolve().parent
    else:
        base = Path(app_file or __file__).resolve().parent
    return base / "data"


def default_protocols_path(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    return default_data_dir(app_file=app_file, executable=executable, frozen=frozen) / "protocols.json"


def default_device_aliases_path(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    return default_data_dir(app_file=app_file, executable=executable, frozen=frozen) / "device_aliases.json"


def default_config_path(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    return default_data_dir(app_file=app_file, executable=executable, frozen=frozen) / "config.json"


@dataclass
class AppConfig:
    app_dir: Path
    package_name: str = DEFAULT_PACKAGE_NAME
    output_dir_value: str = "output"
    log_dir_value: Optional[str] = None
    workspace_dir_value: Optional[str] = None
    adb_connect_addresses: list = field(default_factory=list)
    client_logging_remote_template: str = DEFAULT_CLIENT_LOGGING_REMOTE_TEMPLATE
    config_path: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: dict, app_dir: Path, config_path: Optional[Path] = None):
        return cls(
            app_dir=Path(app_dir),
            package_name=data.get("package_name") or DEFAULT_PACKAGE_NAME,
            output_dir_value=data.get("output_dir") or "output",
            log_dir_value=data.get("log_dir"),
            workspace_dir_value=data.get("workspace_dir"),
            adb_connect_addresses=list(data.get("adb_connect_addresses") or []),
            client_logging_remote_template=data.get("client_logging_remote_template") or DEFAULT_CLIENT_LOGGING_REMOTE_TEMPLATE,
            config_path=config_path,
        )

    def to_dict(self) -> dict:
        return {
            "package_name": self.package_name,
            "output_dir": self.output_dir_value,
            "log_dir": self.log_dir_value,
            "workspace_dir": self.workspace_dir_value,
            "adb_connect_addresses": self.adb_connect_targets(),
            "client_logging_remote_template": self.client_logging_remote_template,
        }

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.app_dir / path

    def output_dir(self) -> Path:
        return self.resolve_path(self.output_dir_value)

    def log_dir(self) -> Path:
        return self.resolve_path(self.log_dir_value) if self.log_dir_value else self.output_dir() / "log"

    def workspace_dir(self) -> Path:
        return self.resolve_path(self.workspace_dir_value) if self.workspace_dir_value else self.output_dir() / "workspace"

    def adb_connect_targets(self) -> list:
        targets = []
        seen = set()
        for raw_address in self.adb_connect_addresses:
            address = str(raw_address).strip()
            if not address or address in seen:
                continue
            seen.add(address)
            targets.append(address)
        return targets

    def client_logging_remote_path(self, package_name: Optional[str] = None) -> str:
        package = (package_name or self.package_name).strip()
        if not package:
            raise ValueError("package_name is required")
        return self.client_logging_remote_template.format(package=package)

    def save(self):
        if not self.config_path:
            return
        save_json_file(self.config_path, self.to_dict())


def default_app_dir(
    app_file: Optional[Path] = None,
    executable: Optional[Path] = None,
    frozen: Optional[bool] = None,
) -> Path:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen:
        return Path(executable or sys.executable).resolve().parent
    return Path(app_file or __file__).resolve().parent


def load_app_config(config_path: Optional[Path] = None, app_dir: Optional[Path] = None) -> AppConfig:
    base_dir = Path(app_dir) if app_dir is not None else default_app_dir()
    path = Path(config_path) if config_path is not None else base_dir / "data" / "config.json"
    if path.exists():
        data = load_json_file(path)
        config = AppConfig.from_dict(data, app_dir=base_dir, config_path=path)
        if config.to_dict() != data:
            config.save()
        return config

    config = AppConfig(app_dir=base_dir, config_path=path)
    config.save()
    return config


def client_logging_remote_path(package_name: str) -> str:
    package = package_name.strip()
    if not package:
        raise ValueError("package_name is required")
    return DEFAULT_CLIENT_LOGGING_REMOTE_TEMPLATE.format(package=package)
