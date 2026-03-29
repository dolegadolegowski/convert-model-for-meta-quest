"""Desktop tray application for ConvertModelForMetaQuest remote worker."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import logging
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable
from urllib.parse import urlsplit

from .connection_code import ConnectionCodeError, connect_button_state, decode_connection_code
from .logging_utils import configure_logging
from .remote_client import RemoteWorkerClient
from .runner import detect_blender_executable
from .updater import (
    DEFAULT_GITHUB_REPO,
    UpdateInfo,
    UpdateInstallResult,
    check_for_updates,
    install_update,
)
from .version import read_version
from .worker_loop import LoopConfig, WorkerLoop, WorkerObserver
from .worker_processor import PipelineOptions, PipelineProcessor

APP_ORG = "ConvertModelForMetaQuest"
APP_NAME = "RemoteWorkerDesktop"
TOKEN_SERVICE = "ConvertModelForMetaQuestWorkerToken"
DEFAULT_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MIN_PYTHON_VERSION = (3, 10)


@dataclass
class DesktopSettings:
    server_url: str = ""
    worker_name: str = "Local Worker"
    worker_id: str = ""
    poll_wait: int = 30
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    work_dir: str = "worker_runtime"


@dataclass
class PrerequisiteResult:
    key: str
    name: str
    required: bool
    ok: bool
    details: str
    install_hint: str = ""


@dataclass
class PrerequisiteCheck:
    key: str
    name: str
    required: bool
    install_hint: str
    runner: Callable[[], tuple[bool, str]]


def _english_blender_install_hint() -> str:
    return (
        "Install Blender and ensure the executable is available.\n"
        "- macOS: brew install --cask blender\n"
        "- Windows: download installer from https://www.blender.org/download/\n"
        "- Ubuntu/Debian: sudo apt update && sudo apt install blender\n"
        "Then restart the app. You can also set BLENDER_EXECUTABLE to a custom path."
    )


def _resolve_executable(command_or_path: str) -> str | None:
    value = str(command_or_path or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if path.exists():
        return str(path)
    return shutil.which(value)


def _build_prerequisite_checks(args: argparse.Namespace) -> list[PrerequisiteCheck]:
    def check_python() -> tuple[bool, str]:
        current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ok = (sys.version_info.major, sys.version_info.minor) >= MIN_PYTHON_VERSION
        details = (
            f"Found Python {current}."
            if ok
            else f"Found Python {current}, but {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ is required."
        )
        return ok, details

    def check_ssl() -> tuple[bool, str]:
        try:
            import ssl  # noqa: PLC0415
        except Exception as exc:
            return False, f"Python SSL module is unavailable: {exc}"
        openssl = str(getattr(ssl, "OPENSSL_VERSION", "unknown OpenSSL")).strip()
        return True, f"SSL runtime ready ({openssl})."

    def check_keyring() -> tuple[bool, str]:
        if importlib.util.find_spec("keyring") is None:
            return False, "Python package 'keyring' is not installed. Secure token storage will be unavailable."
        return True, "keyring package detected for secure token storage."

    def check_blender() -> tuple[bool, str]:
        candidate = detect_blender_executable(getattr(args, "blender_exec", None))
        resolved = _resolve_executable(candidate)
        if not resolved:
            return False, f"Blender executable not found. Tried: {candidate!r}."
        try:
            proc = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:
            return False, f"Blender executable found at '{resolved}' but version check failed: {exc}"
        output = str((proc.stdout or proc.stderr or "").strip()).splitlines()
        first_line = output[0] if output else "Version output unavailable"
        if proc.returncode != 0:
            return False, f"Blender executable found at '{resolved}' but '--version' returned code {proc.returncode}."
        return True, f"{first_line} (path: {resolved})"

    def check_workdir() -> tuple[bool, str]:
        raw_work_dir = str(getattr(args, "work_dir", "worker_runtime") or "worker_runtime").strip() or "worker_runtime"
        work_root = Path(raw_work_dir).expanduser().resolve()
        try:
            work_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="cmq-preflight-", dir=work_root, delete=True):
                pass
        except Exception as exc:
            return False, f"Cannot write to worker runtime directory '{work_root}': {exc}"
        return True, f"Worker runtime directory is writable: {work_root}"

    return [
        PrerequisiteCheck(
            key="python_runtime",
            name="Python runtime",
            required=True,
            install_hint=(
                "Install Python 3.10+ from https://www.python.org/downloads/ "
                "and recreate the virtual environment."
            ),
            runner=check_python,
        ),
        PrerequisiteCheck(
            key="ssl_module",
            name="Python SSL support",
            required=True,
            install_hint=(
                "Install a Python build with OpenSSL support, then recreate the environment "
                "and reinstall dependencies."
            ),
            runner=check_ssl,
        ),
        PrerequisiteCheck(
            key="keyring_package",
            name="Secure token storage (keyring)",
            required=False,
            install_hint="Run: python3 -m pip install keyring",
            runner=check_keyring,
        ),
        PrerequisiteCheck(
            key="blender_executable",
            name="Blender executable",
            required=True,
            install_hint=_english_blender_install_hint(),
            runner=check_blender,
        ),
        PrerequisiteCheck(
            key="work_dir_writable",
            name="Worker runtime directory write access",
            required=True,
            install_hint=(
                "Choose a writable directory for work_dir (or fix filesystem permissions), "
                "then start the worker again."
            ),
            runner=check_workdir,
        ),
    ]


def evaluate_startup_prerequisites(args: argparse.Namespace) -> list[PrerequisiteResult]:
    results: list[PrerequisiteResult] = []
    for check in _build_prerequisite_checks(args):
        try:
            ok, details = check.runner()
        except Exception as exc:  # pragma: no cover - defensive safety net
            ok, details = False, f"Unexpected check failure: {exc}"
        results.append(
            PrerequisiteResult(
                key=check.key,
                name=check.name,
                required=check.required,
                ok=bool(ok),
                details=str(details or "").strip() or "No details provided.",
                install_hint=check.install_hint,
            )
        )
    return results


class TokenVault:
    def __init__(self) -> None:
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        self._keyring = keyring

    def _key(self, server_url: str) -> str:
        return str(server_url or "default").strip() or "default"

    def load(self, server_url: str) -> str:
        if self._keyring is None:
            return ""
        try:
            value = self._keyring.get_password(TOKEN_SERVICE, self._key(server_url))
        except Exception:
            return ""
        return str(value or "").strip()

    def save(self, server_url: str, token: str) -> None:
        if self._keyring is None:
            return
        try:
            self._keyring.set_password(TOKEN_SERVICE, self._key(server_url), str(token or ""))
        except Exception:
            return


def _auto_worker_name() -> str:
    hostname = (platform.node() or "").strip()
    return hostname or "cmq-worker"


def _slugify(name: str) -> str:
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in name.strip())
    collapsed = "-".join(part for part in normalized.split("-") if part)
    return collapsed.lower() or "cmq-worker"


def _auto_worker_id(worker_name: str) -> str:
    return f"worker-{_slugify(worker_name)}-{uuid.uuid4().hex[:8]}"


def normalize_server_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid server URL. Expected absolute URL, e.g. https://example.com")

    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"}:
        raise ValueError("Unsupported URL scheme")
    if scheme == "http":
        raise ValueError("Insecure http:// is blocked. Use https:// server URL.")

    base = f"{scheme}://{parsed.netloc}"
    if parsed.path and parsed.path != "/":
        base = f"{base}{parsed.path.rstrip('/')}"
    return base.rstrip("/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ConvertModelForMetaQuest desktop worker launcher",
    )
    parser.add_argument("--server-url", default="", help="Optional initial server URL")
    parser.add_argument("--token", default="", help="Optional initial worker token")
    parser.add_argument("--worker-name", default="Local Worker", help="Optional initial worker display name")
    parser.add_argument("--poll-wait", type=int, default=30, help="Initial long-poll wait seconds")
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="Initial max source model size accepted by worker",
    )
    parser.add_argument("--work-dir", default="worker_runtime", help="Initial worker runtime directory")
    parser.add_argument("--show-window", action="store_true", help="Keep window visible after successful connect")
    parser.add_argument("--blender-exec", default=None, help="Optional Blender executable path")
    parser.add_argument("--face-limit", type=int, default=400000, help="Face limit passed to optimization pipeline")
    parser.add_argument("--blender-timeout-seconds", type=int, default=1800, help="Timeout for one Blender process")
    parser.add_argument("--update-repo", default=DEFAULT_GITHUB_REPO, help="GitHub repo for desktop app self-update checks")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser


def run_desktop(args: argparse.Namespace) -> int:
    try:
        from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
        from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QMessageBox,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QSpinBox,
            QSystemTrayIcon,
            QTabWidget,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:
        print("PySide6 is required for desktop worker UI. Install: python3 -m pip install PySide6 keyring", file=sys.stderr)
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    class PreflightDialog(QDialog):
        def __init__(self, startup_args: argparse.Namespace) -> None:
            super().__init__()
            self.setWindowTitle("Worker Startup Checks")
            self.resize(760, 500)

            self._checks = _build_prerequisite_checks(startup_args)
            self.results: list[PrerequisiteResult] = []
            self._index = 0
            self._completed = False
            self._auto_accept_timer: QTimer | None = None

            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)

            self.title_label = QLabel("Checking worker prerequisites...")
            self.title_label.setStyleSheet("font-weight:600;font-size:15px;")
            layout.addWidget(self.title_label)

            self.status_label = QLabel("Starting checks...")
            self.status_label.setWordWrap(True)
            layout.addWidget(self.status_label)

            self.progress = QProgressBar()
            self.progress.setRange(0, max(1, len(self._checks)))
            self.progress.setValue(0)
            layout.addWidget(self.progress)

            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            layout.addWidget(self.log, 1)

            actions = QHBoxLayout()
            actions.addStretch(1)
            self.continue_btn = QPushButton("Open Worker")
            self.quit_btn = QPushButton("Quit")
            self.continue_btn.setEnabled(False)
            self.quit_btn.setEnabled(False)
            self.continue_btn.clicked.connect(self.accept)
            self.quit_btn.clicked.connect(self.reject)
            actions.addWidget(self.continue_btn)
            actions.addWidget(self.quit_btn)
            layout.addLayout(actions)

            QTimer.singleShot(30, self._run_next_check)

        def _append_result(self, result: PrerequisiteResult) -> None:
            level = "OK" if result.ok else "NOT FOUND"
            required = "required" if result.required else "optional"
            self.log.appendPlainText(f"[{level}] {result.name} ({required})")
            self.log.appendPlainText(f"  {result.details}")
            if not result.ok and result.install_hint:
                for line in str(result.install_hint).splitlines():
                    self.log.appendPlainText(f"  Install: {line}")
            self.log.appendPlainText("")

        def _run_next_check(self) -> None:
            if self._index >= len(self._checks):
                self._finish()
                return

            check = self._checks[self._index]
            self.status_label.setText(f"Checking: {check.name}...")
            QApplication.processEvents()
            try:
                ok, details = check.runner()
            except Exception as exc:  # pragma: no cover - defensive safety net
                ok, details = False, f"Unexpected check failure: {exc}"

            result = PrerequisiteResult(
                key=check.key,
                name=check.name,
                required=check.required,
                ok=bool(ok),
                details=str(details or "").strip() or "No details provided.",
                install_hint=check.install_hint,
            )
            self.results.append(result)
            self._append_result(result)
            self._index += 1
            self.progress.setValue(self._index)
            QTimer.singleShot(20, self._run_next_check)

        def _finish(self) -> None:
            if self._completed:
                return
            self._completed = True

            missing_required = [item for item in self.results if item.required and not item.ok]
            missing_optional = [item for item in self.results if not item.required and not item.ok]

            if missing_required:
                self.title_label.setText("Some required prerequisites are missing.")
                self.status_label.setText(
                    "Fix items marked NOT FOUND. You can still open the worker window, but processing will fail until requirements are installed."
                )
                self.continue_btn.setText("Open Worker Anyway")
            elif missing_optional:
                self.title_label.setText("Startup checks completed (optional items missing).")
                self.status_label.setText("Optional components are missing. Worker can run, but some convenience features may be disabled.")
            else:
                self.title_label.setText("All prerequisites are ready.")
                self.status_label.setText("Worker startup checks passed.")
                self._auto_accept_timer = QTimer(self)
                self._auto_accept_timer.setSingleShot(True)
                self._auto_accept_timer.timeout.connect(self.accept)
                self._auto_accept_timer.start(700)

            self.continue_btn.setEnabled(True)
            self.quit_btn.setEnabled(True)

    class EventBridge(QObject):
        log = Signal(str)
        state = Signal(str)
        update_check = Signal(object)
        update_install = Signal(object)

    class QtLogHandler(logging.Handler):
        def __init__(self, bridge: EventBridge) -> None:
            super().__init__()
            self._bridge = bridge
            self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

        def emit(self, record: logging.LogRecord) -> None:
            try:
                message = self.format(record)
            except Exception:
                message = str(record.getMessage())
            self._bridge.log.emit(message)
            normalized = message.lower()
            if "claimed job_id" in normalized:
                self._bridge.state.emit("processing")
            elif "job completed" in normalized:
                self._bridge.state.emit("connected")
            elif "worker loop error" in normalized or "network error" in normalized:
                self._bridge.state.emit("disconnected")

    class DesktopObserver(WorkerObserver):
        def __init__(self, bridge: EventBridge) -> None:
            self._bridge = bridge

        def set_connection_status(self, connected: bool) -> None:
            self._bridge.state.emit("connected" if connected else "disconnected")

        def set_last_download(self, message: str) -> None:
            self._bridge.log.emit(str(message or ""))
            self._bridge.state.emit("processing")

        def set_geometry_summary(self, message: str) -> None:
            self._bridge.log.emit(str(message or ""))
            self._bridge.state.emit("processing")

        def set_upload_status(self, message: str) -> None:
            line = str(message or "")
            self._bridge.log.emit(line)
            normalized = line.lower()
            if "success" in normalized:
                self._bridge.state.emit("connected")
            elif "failed" in normalized:
                self._bridge.state.emit("disconnected")
            else:
                self._bridge.state.emit("processing")

    class MainWindow(QMainWindow):
        def __init__(self, preflight_results: list[PrerequisiteResult] | None = None) -> None:
            super().__init__()
            self.setWindowTitle(f"ConvertModelForMetaQuest Worker v{read_version()}")
            self.resize(860, 600)

            self._bridge = EventBridge()
            self._bridge.log.connect(self._on_log)
            self._bridge.state.connect(self._on_state)
            self._bridge.update_check.connect(self._on_update_check_result)
            self._bridge.update_install.connect(self._on_update_install_result)
            self._observer = DesktopObserver(self._bridge)

            self._vault = TokenVault()
            self._settings = QSettings(APP_ORG, APP_NAME)
            self._thread: threading.Thread | None = None
            self._loop: WorkerLoop | None = None
            self._stop_event: threading.Event | None = None
            self._logger: logging.Logger | None = None
            self._hide_on_connect = not bool(args.show_window)
            self._state = "disconnected"
            self._latest_update_info: UpdateInfo | None = None
            self._update_check_in_progress = False
            self._update_install_in_progress = False

            self._build_ui()
            self._build_tray()
            self._load_settings()
            self._append_preflight_summary(preflight_results or [])
            QTimer.singleShot(1200, self._check_for_updates_silent)

        def _build_ui(self) -> None:
            root = QWidget(self)
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            status_row = QHBoxLayout()
            self.status_dot = QLabel("●")
            self.status_dot.setStyleSheet("color:#ff5c6c;font-size:20px;")
            self.status_label = QLabel("Disconnected")
            status_row.addWidget(self.status_dot)
            status_row.addWidget(self.status_label)
            status_row.addStretch(1)
            layout.addLayout(status_row)

            self.version_label = QLabel(f"Version {read_version()} | Checking for updates...")
            self.version_label.setStyleSheet("color:#d0d7de;")
            layout.addWidget(self.version_label)

            self.config_tabs = QTabWidget()

            code_tab = QWidget()
            code_layout = QVBoxLayout(code_tab)
            code_layout.setContentsMargins(8, 8, 8, 8)
            code_layout.setSpacing(8)
            code_layout.addWidget(QLabel("Paste encrypted worker connection code from the server admin panel:"))
            self.connection_code_input = QPlainTextEdit()
            self.connection_code_input.setPlaceholderText("Paste encrypted connection code here...")
            self.connection_code_input.setMaximumHeight(120)
            self.connection_code_input.textChanged.connect(self._schedule_decode_connection_code)
            code_layout.addWidget(self.connection_code_input)
            self.connection_code_status = QLabel("Waiting for connection code.")
            code_layout.addWidget(self.connection_code_status)
            code_layout.addStretch(1)
            self.config_tabs.addTab(code_tab, "Connection Code")

            manual_tab = QWidget()
            manual_layout = QVBoxLayout(manual_tab)
            manual_layout.setContentsMargins(8, 8, 8, 8)
            manual_layout.setSpacing(8)

            form = QFormLayout()
            self.server_input = QLineEdit()
            self.server_input.setPlaceholderText("https://your-server")
            self.server_input.editingFinished.connect(self._reload_token_for_server)
            self.server_input.textChanged.connect(self._refresh_connect_button_state)

            self.token_input = QLineEdit()
            self.token_input.setEchoMode(QLineEdit.Password)
            self.token_input.setPlaceholderText("Worker token")
            self.token_input.textChanged.connect(self._refresh_connect_button_state)

            self.worker_name_input = QLineEdit()
            self.worker_name_input.textChanged.connect(self._refresh_connect_button_state)

            self.poll_wait_input = QSpinBox()
            self.poll_wait_input.setRange(1, 120)
            self.poll_wait_input.valueChanged.connect(self._refresh_connect_button_state)

            self.max_download_input = QSpinBox()
            self.max_download_input.setRange(1, 10 * 1024)
            self.max_download_input.setSuffix(" MB")
            self.max_download_input.valueChanged.connect(self._refresh_connect_button_state)

            self.work_dir_input = QLineEdit()
            self.work_dir_input.setPlaceholderText("worker_runtime")
            self.work_dir_input.textChanged.connect(self._refresh_connect_button_state)

            form.addRow("Server URL", self.server_input)
            form.addRow("Token", self.token_input)
            form.addRow("Worker name", self.worker_name_input)
            form.addRow("Poll wait", self.poll_wait_input)
            form.addRow("Max download", self.max_download_input)
            form.addRow("Work dir", self.work_dir_input)
            manual_layout.addLayout(form)
            manual_layout.addStretch(1)
            self.config_tabs.addTab(manual_tab, "Manual Config")
            self.config_tabs.currentChanged.connect(self._refresh_connect_button_state)
            layout.addWidget(self.config_tabs)

            buttons = QHBoxLayout()
            self.connect_btn = QPushButton("Connect")
            self.connect_btn.clicked.connect(self._toggle_connection_clicked)
            buttons.addWidget(self.connect_btn)
            self.check_updates_btn = QPushButton("Check Updates")
            self.check_updates_btn.clicked.connect(self._check_for_updates_manual)
            buttons.addWidget(self.check_updates_btn)
            self.install_update_btn = QPushButton("Install Update")
            self.install_update_btn.clicked.connect(self._install_update_clicked)
            self.install_update_btn.setEnabled(False)
            buttons.addWidget(self.install_update_btn)
            buttons.addStretch(1)
            layout.addLayout(buttons)

            self.logs = QPlainTextEdit()
            self.logs.setReadOnly(True)
            layout.addWidget(self.logs, 1)

            self._decoded_connection_payload: dict[str, object] | None = None
            self._code_decode_timer = QTimer(self)
            self._code_decode_timer.setSingleShot(True)
            self._code_decode_timer.timeout.connect(self._decode_connection_code_now)
            self._refresh_connect_button_state()

        def _build_icon(self, color_hex: str) -> QIcon:
            pix = QPixmap(24, 24)
            pix.fill(Qt.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(QColor(color_hex))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(3, 3, 18, 18)
            painter.end()
            return QIcon(pix)

        def _build_tray(self) -> None:
            self.tray = QSystemTrayIcon(self)
            self.tray.setToolTip("ConvertModelForMetaQuest Worker")
            self.tray.activated.connect(self._on_tray_activated)

            menu = QMenu()
            self.action_reconnect = QAction("Reconnect", self)
            self.action_check_updates = QAction("Check Updates", self)
            self.action_install_update = QAction("Install Update", self)
            self.action_logs = QAction("Logs", self)
            self.action_quit = QAction("Quit", self)
            self.action_reconnect.triggered.connect(self._reconnect_clicked)
            self.action_check_updates.triggered.connect(self._check_for_updates_manual)
            self.action_install_update.triggered.connect(self._install_update_clicked)
            self.action_logs.triggered.connect(self._show_window)
            self.action_quit.triggered.connect(self._quit_app)
            menu.addAction(self.action_reconnect)
            menu.addAction(self.action_check_updates)
            menu.addAction(self.action_install_update)
            menu.addAction(self.action_logs)
            menu.addSeparator()
            menu.addAction(self.action_quit)
            self.tray.setContextMenu(menu)
            self.tray.setVisible(True)
            self._set_tray_state("disconnected")

        def _load_settings(self) -> None:
            default_worker_name = str(args.worker_name or "Local Worker").strip() or "Local Worker"
            defaults = DesktopSettings(
                server_url=str(args.server_url or "").strip(),
                worker_name=default_worker_name,
                worker_id="",
                poll_wait=max(1, int(args.poll_wait or 30)),
                max_download_bytes=max(1, int(args.max_download_bytes or DEFAULT_MAX_DOWNLOAD_BYTES)),
                work_dir=str(args.work_dir or "worker_runtime").strip() or "worker_runtime",
            )
            server_url = str(self._settings.value("server_url", defaults.server_url) or "").strip()
            worker_name = str(self._settings.value("worker_name", defaults.worker_name) or defaults.worker_name).strip() or default_worker_name
            worker_id = str(self._settings.value("worker_id", defaults.worker_id) or "").strip()
            poll_wait = int(self._settings.value("poll_wait", defaults.poll_wait) or defaults.poll_wait)
            max_download_bytes = int(self._settings.value("max_download_bytes", defaults.max_download_bytes) or defaults.max_download_bytes)
            work_dir = str(self._settings.value("work_dir", defaults.work_dir) or defaults.work_dir).strip() or "worker_runtime"
            connection_code = str(self._settings.value("connection_code", "") or "").strip()

            if defaults.server_url:
                server_url = defaults.server_url
            if str(args.token or "").strip():
                self._vault.save(server_url, str(args.token).strip())

            token_value = self._vault.load(server_url)

            self.server_input.setText(server_url)
            self.token_input.setText(token_value)
            self.worker_name_input.setText(worker_name)
            self.poll_wait_input.setValue(max(1, min(120, poll_wait)))
            self.max_download_input.setValue(max(1, min(10240, max_download_bytes // (1024 * 1024))))
            self.work_dir_input.setText(work_dir)
            self.connection_code_input.setPlainText(connection_code)

            self._worker_id = worker_id or _auto_worker_id(worker_name)
            if connection_code:
                self.config_tabs.setCurrentIndex(0)
            else:
                self.config_tabs.setCurrentIndex(1)
            self._decode_connection_code_now()
            self._refresh_connect_button_state()

        def _persist_settings(self) -> None:
            server_url = self.server_input.text().strip()
            self._settings.setValue("server_url", server_url)
            self._settings.setValue("worker_name", self.worker_name_input.text().strip() or "Local Worker")
            self._settings.setValue("worker_id", self._worker_id)
            self._settings.setValue("poll_wait", int(self.poll_wait_input.value()))
            self._settings.setValue("max_download_bytes", int(self.max_download_input.value()) * 1024 * 1024)
            self._settings.setValue("work_dir", self.work_dir_input.text().strip() or "worker_runtime")
            self._settings.setValue("connection_code", self.connection_code_input.toPlainText().strip())
            self._vault.save(server_url, self.token_input.text().strip())

        def _reload_token_for_server(self) -> None:
            server_url = self.server_input.text().strip()
            if not server_url:
                return
            token = self._vault.load(server_url)
            if token:
                self.token_input.setText(token)

        def _schedule_decode_connection_code(self) -> None:
            self.connection_code_status.setText("Decoding connection code...")
            self._code_decode_timer.start(140)

        def _decode_connection_code_now(self) -> None:
            code = self.connection_code_input.toPlainText().strip()
            if not code:
                self._decoded_connection_payload = None
                self.connection_code_status.setText("Waiting for connection code.")
                self._refresh_connect_button_state()
                return
            try:
                payload = decode_connection_code(code)
            except ConnectionCodeError as exc:
                self._decoded_connection_payload = None
                self.connection_code_status.setText(f"Invalid code: {exc}")
                self._refresh_connect_button_state()
                return
            self._decoded_connection_payload = payload
            self.connection_code_status.setText("Connection code valid. Connect is ready.")
            self._apply_decoded_payload_to_manual(payload)
            self._refresh_connect_button_state()

        def _apply_decoded_payload_to_manual(self, payload: dict[str, object]) -> None:
            server_url = str(payload.get("server_url", "") or "").strip()
            worker_token = str(payload.get("worker_token", "") or "").strip()
            worker_name = str(payload.get("worker_name", "") or "").strip() or "Local Worker"
            runtime_config = payload.get("runtime_config")
            runtime_map = runtime_config if isinstance(runtime_config, dict) else {}
            poll_wait = runtime_map.get("poll_wait_seconds")
            if server_url:
                self.server_input.setText(server_url)
            if worker_token:
                self.token_input.setText(worker_token)
            if worker_name:
                self.worker_name_input.setText(worker_name)
            if isinstance(poll_wait, int):
                self.poll_wait_input.setValue(max(1, min(120, int(poll_wait))))
            if server_url and worker_token:
                self._vault.save(server_url, worker_token)

        def _is_worker_running(self) -> bool:
            return bool(self._thread is not None and self._thread.is_alive())

        def _active_config_is_valid(self) -> bool:
            if self.config_tabs.currentIndex() == 0:
                return isinstance(self._decoded_connection_payload, dict)
            server_url = str(self.server_input.text() or "").strip()
            token = str(self.token_input.text() or "").strip()
            if not server_url or not token:
                return False
            try:
                normalize_server_url(server_url)
            except Exception:
                return False
            return True

        def _refresh_connect_button_state(self) -> None:
            can_connect, label = connect_button_state(
                connected=self._is_worker_running(),
                config_valid=self._active_config_is_valid(),
            )
            self.connect_btn.setEnabled(bool(can_connect))
            self.connect_btn.setText(label)
            can_install_update = bool(
                self._latest_update_info is not None
                and self._latest_update_info.available
                and not self._update_install_in_progress
            )
            if hasattr(self, "install_update_btn"):
                self.install_update_btn.setEnabled(can_install_update)
            if hasattr(self, "action_install_update"):
                self.action_install_update.setEnabled(can_install_update)

        def _set_tray_state(self, state_value: str) -> None:
            mapping = {
                "connected": ("#38d39f", "Connected"),
                "processing": ("#ffb547", "Processing"),
                "connecting": ("#ffb547", "Connecting"),
                "disconnected": ("#ff5c6c", "Disconnected"),
            }
            color, label = mapping.get(state_value, mapping["disconnected"])
            self.status_dot.setStyleSheet(f"color:{color};font-size:20px;")
            self.status_label.setText(label)
            self.tray.setIcon(self._build_icon(color))
            self.tray.setToolTip(f"ConvertModelForMetaQuest Worker - {label}")

        def _on_state(self, state_value: str) -> None:
            self._state = str(state_value or "disconnected")
            self._set_tray_state(self._state)
            self._refresh_connect_button_state()
            if self._state == "connected" and self._hide_on_connect:
                self.hide()

        def _on_log(self, message: str) -> None:
            line = str(message or "").strip()
            if line:
                self.logs.appendPlainText(line)

        def _append_preflight_summary(self, preflight_results: list[PrerequisiteResult]) -> None:
            if not preflight_results:
                return
            self._on_log("Startup prerequisite check summary:")
            for item in preflight_results:
                status_label = "OK" if item.ok else "NOT FOUND"
                required_label = "required" if item.required else "optional"
                self._on_log(f"- [{status_label}] {item.name} ({required_label}): {item.details}")
                if not item.ok and item.install_hint:
                    hint_lines = [line.strip() for line in str(item.install_hint).splitlines() if line.strip()]
                    for hint in hint_lines:
                        self._on_log(f"  Install: {hint}")

        def _check_for_updates_silent(self) -> None:
            self._check_for_updates(user_initiated=False)

        def _check_for_updates_manual(self) -> None:
            self._check_for_updates(user_initiated=True)

        def _check_for_updates(self, user_initiated: bool) -> None:
            if self._update_check_in_progress:
                if user_initiated:
                    self._on_log("Update check is already in progress.")
                return
            self._update_check_in_progress = True
            self.version_label.setText(f"Version {read_version()} | Checking for updates...")
            if user_initiated:
                self._on_log("Checking for updates on GitHub...")

            current_version = read_version()
            repo_full_name = str(args.update_repo or DEFAULT_GITHUB_REPO).strip() or DEFAULT_GITHUB_REPO

            def runner() -> None:
                info = check_for_updates(current_version=current_version, repo_full_name=repo_full_name)
                self._bridge.update_check.emit({"info": info, "user_initiated": user_initiated})

            threading.Thread(target=runner, daemon=True, name="cmq-update-check").start()

        def _on_update_check_result(self, payload: object) -> None:
            info: UpdateInfo
            user_initiated = False
            if isinstance(payload, dict):
                info = payload.get("info")  # type: ignore[assignment]
                user_initiated = bool(payload.get("user_initiated"))
            else:
                info = payload  # type: ignore[assignment]
            self._update_check_in_progress = False
            if not isinstance(info, UpdateInfo):
                self.version_label.setText(f"Version {read_version()} | Update status unavailable")
                return
            self._latest_update_info = info

            if info.error:
                self.version_label.setText(f"Version {info.current_version} | Update check failed")
                if user_initiated:
                    QMessageBox.warning(self, "Update check failed", str(info.error))
                self._on_log(f"Update check failed: {info.error}")
                self._refresh_connect_button_state()
                return

            if info.available and info.latest_version:
                release_label = info.release_name or info.latest_version
                self.version_label.setText(
                    f"Version {info.current_version} | Update available: {info.latest_version}"
                )
                self._on_log(f"Update available: {release_label} ({info.latest_version}).")
                self.tray.showMessage(
                    "ConvertModelForMetaQuest",
                    f"New version available: {info.latest_version}",
                    QSystemTrayIcon.Information,
                    3500,
                )
                if user_initiated:
                    QMessageBox.information(
                        self,
                        "Update available",
                        f"New version {info.latest_version} is available.\nUse 'Install Update' to apply it.",
                    )
            else:
                current = info.current_version or read_version()
                self.version_label.setText(f"Version {current} | Up to date")
                if user_initiated:
                    QMessageBox.information(self, "No updates", "You already have the latest version.")
            self._refresh_connect_button_state()

        def _install_update_clicked(self) -> None:
            if self._update_install_in_progress:
                self._on_log("Update installation is already in progress.")
                return
            if not self._latest_update_info or not self._latest_update_info.available:
                self._check_for_updates(user_initiated=True)
                return

            latest = self._latest_update_info.latest_version or "unknown"
            answer = QMessageBox.question(
                self,
                "Install update",
                f"Install update to version {latest} now?\n\nThe app will restart after successful update.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return

            self._update_install_in_progress = True
            self._refresh_connect_button_state()
            self.check_updates_btn.setEnabled(False)
            self._disconnect()
            self._on_log("Starting self-update...")

            project_root = Path(__file__).resolve().parents[2]
            update_info = self._latest_update_info

            def runner() -> None:
                result = install_update(
                    project_root=project_root,
                    update_info=update_info,
                    read_version_fn=read_version,
                )
                self._bridge.update_install.emit(result)

            threading.Thread(target=runner, daemon=True, name="cmq-update-install").start()

        def _restart_after_update(self) -> None:
            project_root = Path(__file__).resolve().parents[2]
            script_path = project_root / "scripts" / "worker_desktop_app.py"
            subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=str(project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            QApplication.quit()

        def _on_update_install_result(self, result: object) -> None:
            self._update_install_in_progress = False
            self.check_updates_btn.setEnabled(True)
            self._refresh_connect_button_state()
            if not isinstance(result, UpdateInstallResult):
                QMessageBox.warning(self, "Update failed", "Unknown updater response.")
                return
            if not result.ok:
                self._on_log(f"Self-update failed: {result.message}")
                QMessageBox.warning(self, "Update failed", result.message)
                return

            self._on_log(result.message)
            self.version_label.setText(f"Version {result.installed_version} | Update installed")
            self._latest_update_info = UpdateInfo(
                current_version=result.installed_version,
                latest_version=result.installed_version,
                available=False,
                html_url=None,
                download_url=None,
                release_name=result.installed_version,
                error=None,
            )
            QMessageBox.information(
                self,
                "Update installed",
                f"Updated from {result.previous_version} to {result.installed_version}.\nThe app will restart now.",
            )
            self._restart_after_update()

        def _build_loop(self) -> WorkerLoop:
            decoded_payload = self._decoded_connection_payload if self.config_tabs.currentIndex() == 0 else None
            runtime_map = decoded_payload.get("runtime_config") if isinstance(decoded_payload, dict) else None
            runtime_config = runtime_map if isinstance(runtime_map, dict) else {}

            server_raw = (
                str(decoded_payload.get("server_url", "")).strip()
                if isinstance(decoded_payload, dict)
                else self.server_input.text().strip()
            )
            token = (
                str(decoded_payload.get("worker_token", "")).strip()
                if isinstance(decoded_payload, dict)
                else self.token_input.text().strip()
            )
            worker_name = (
                str(decoded_payload.get("worker_name", "")).strip() or "Local Worker"
                if isinstance(decoded_payload, dict)
                else self.worker_name_input.text().strip() or "Local Worker"
            )
            poll_wait = int(runtime_config.get("poll_wait_seconds") or int(self.poll_wait_input.value()))

            server_url = normalize_server_url(server_raw)
            if not token:
                raise ValueError("Worker token is required.")
            if not str(self._worker_id or "").strip():
                self._worker_id = _auto_worker_id(worker_name)

            logger = configure_logging(args.log_level)
            logger.addHandler(QtLogHandler(self._bridge))
            self._logger = logger

            client = RemoteWorkerClient(
                server_url=server_url,
                worker_token=token,
                worker_name=worker_name,
                worker_id=self._worker_id,
                timeout=60,
                download_timeout=300,
                upload_timeout=600,
                allow_insecure_http=False,
            )

            processor = PipelineProcessor(
                blender_exec=args.blender_exec,
                options=PipelineOptions(
                    face_limit=max(1000, int(args.face_limit)),
                    blender_timeout_seconds=max(60, int(args.blender_timeout_seconds)),
                ),
            )

            work_root = Path(self.work_dir_input.text().strip() or "worker_runtime").expanduser().resolve()
            work_root.mkdir(parents=True, exist_ok=True)

            loop = WorkerLoop(
                client=client,
                processor=processor,
                work_root=work_root,
                logger=logger,
                observer=self._observer,
                config=LoopConfig(
                    poll_wait_seconds=max(1, int(poll_wait)),
                    max_download_bytes=max(1, int(self.max_download_input.value()) * 1024 * 1024),
                    reconnect_after_failures=3,
                    max_backoff_seconds=60,
                    once=False,
                ),
            )
            return loop

        def _start_worker(self) -> None:
            if self._thread is not None and self._thread.is_alive():
                self._on_log("Worker is already running.")
                return
            try:
                loop = self._build_loop()
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid configuration", str(exc))
                return
            except Exception as exc:
                QMessageBox.warning(self, "Worker initialization failed", str(exc))
                return

            self._persist_settings()
            self._loop = loop
            self._stop_event = loop.stop_event
            self._bridge.state.emit("connecting")

            def runner() -> None:
                code = 1
                try:
                    code = loop.run_forever()
                except Exception as exc:
                    self._bridge.log.emit(f"[{time.strftime('%H:%M:%S')}] Worker thread crashed: {exc}")
                finally:
                    self._bridge.state.emit("connected" if code == 0 else "disconnected")

            self._thread = threading.Thread(target=runner, daemon=True, name="cmq-worker-desktop")
            self._thread.start()
            self._refresh_connect_button_state()

        def _disconnect(self) -> None:
            if self._loop is not None:
                try:
                    self._loop.stop()
                except Exception:
                    pass
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            self._thread = None
            self._loop = None
            self._stop_event = None
            self._bridge.state.emit("disconnected")
            self._refresh_connect_button_state()

        def _toggle_connection_clicked(self) -> None:
            if self._is_worker_running():
                self._disconnect()
                return
            self._hide_on_connect = not bool(args.show_window)
            self._start_worker()

        def _reconnect_clicked(self) -> None:
            self._disconnect()
            self._start_worker()

        def _show_window(self) -> None:
            self.showNormal()
            self.activateWindow()
            self.raise_()

        def _on_tray_activated(self, reason) -> None:
            if reason == QSystemTrayIcon.Trigger:
                self._show_window()

        def _quit_app(self) -> None:
            self._disconnect()
            QApplication.quit()

        def closeEvent(self, event):  # noqa: N802
            if self.tray.isVisible():
                event.ignore()
                self.hide()
                return
            self._disconnect()
            event.accept()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    preflight = PreflightDialog(args)
    if preflight.exec() != QDialog.Accepted:
        return 3

    window = MainWindow(preflight_results=preflight.results)
    window.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
