"""Desktop tray application for ConvertModelForMetaQuest remote worker."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import platform
from pathlib import Path
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from .logging_utils import configure_logging
from .remote_client import RemoteWorkerClient
from .version import read_version
from .worker_loop import LoopConfig, WorkerLoop, WorkerObserver
from .worker_processor import PipelineOptions, PipelineProcessor

APP_ORG = "ConvertModelForMetaQuest"
APP_NAME = "RemoteWorkerDesktop"
TOKEN_SERVICE = "ConvertModelForMetaQuestWorkerToken"
DEFAULT_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024


@dataclass
class DesktopSettings:
    server_url: str = ""
    worker_name: str = "Local Worker"
    worker_id: str = ""
    poll_wait: int = 30
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    work_dir: str = "worker_runtime"


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
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser


def run_desktop(args: argparse.Namespace) -> int:
    try:
        from PySide6.QtCore import QObject, QSettings, Qt, Signal
        from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QMessageBox,
            QPlainTextEdit,
            QPushButton,
            QSpinBox,
            QSystemTrayIcon,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:
        print("PySide6 is required for desktop worker UI. Install: python3 -m pip install PySide6 keyring", file=sys.stderr)
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    class EventBridge(QObject):
        log = Signal(str)
        state = Signal(str)

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
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"ConvertModelForMetaQuest Worker v{read_version()}")
            self.resize(860, 600)

            self._bridge = EventBridge()
            self._bridge.log.connect(self._on_log)
            self._bridge.state.connect(self._on_state)
            self._observer = DesktopObserver(self._bridge)

            self._vault = TokenVault()
            self._settings = QSettings(APP_ORG, APP_NAME)
            self._thread: threading.Thread | None = None
            self._loop: WorkerLoop | None = None
            self._stop_event: threading.Event | None = None
            self._logger: logging.Logger | None = None
            self._hide_on_connect = not bool(args.show_window)
            self._state = "disconnected"

            self._build_ui()
            self._build_tray()
            self._load_settings()

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

            form = QFormLayout()
            self.server_input = QLineEdit()
            self.server_input.setPlaceholderText("https://your-server")
            self.server_input.editingFinished.connect(self._reload_token_for_server)

            self.token_input = QLineEdit()
            self.token_input.setEchoMode(QLineEdit.Password)
            self.token_input.setPlaceholderText("Worker token")

            self.worker_name_input = QLineEdit()

            self.poll_wait_input = QSpinBox()
            self.poll_wait_input.setRange(1, 120)

            self.max_download_input = QSpinBox()
            self.max_download_input.setRange(1, 10 * 1024)
            self.max_download_input.setSuffix(" MB")

            self.work_dir_input = QLineEdit()
            self.work_dir_input.setPlaceholderText("worker_runtime")

            form.addRow("Server URL", self.server_input)
            form.addRow("Token", self.token_input)
            form.addRow("Worker name", self.worker_name_input)
            form.addRow("Poll wait", self.poll_wait_input)
            form.addRow("Max download", self.max_download_input)
            form.addRow("Work dir", self.work_dir_input)
            layout.addLayout(form)

            buttons = QHBoxLayout()
            self.connect_btn = QPushButton("Connect")
            self.reconnect_btn = QPushButton("Reconnect")
            self.disconnect_btn = QPushButton("Disconnect")
            self.connect_btn.clicked.connect(self._connect_clicked)
            self.reconnect_btn.clicked.connect(self._reconnect_clicked)
            self.disconnect_btn.clicked.connect(self._disconnect)
            buttons.addWidget(self.connect_btn)
            buttons.addWidget(self.reconnect_btn)
            buttons.addWidget(self.disconnect_btn)
            buttons.addStretch(1)
            layout.addLayout(buttons)

            self.logs = QPlainTextEdit()
            self.logs.setReadOnly(True)
            layout.addWidget(self.logs, 1)

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
            self.action_logs = QAction("Logs", self)
            self.action_quit = QAction("Quit", self)
            self.action_reconnect.triggered.connect(self._reconnect_clicked)
            self.action_logs.triggered.connect(self._show_window)
            self.action_quit.triggered.connect(self._quit_app)
            menu.addAction(self.action_reconnect)
            menu.addAction(self.action_logs)
            menu.addSeparator()
            menu.addAction(self.action_quit)
            self.tray.setContextMenu(menu)
            self.tray.setVisible(True)
            self._set_tray_state("disconnected")

        def _settings_bool(self, key: str, default: bool) -> bool:
            raw = self._settings.value(key, default)
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            return text in {"1", "true", "yes", "on"}

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

            self._worker_id = worker_id or _auto_worker_id(worker_name)

        def _persist_settings(self) -> None:
            server_url = self.server_input.text().strip()
            self._settings.setValue("server_url", server_url)
            self._settings.setValue("worker_name", self.worker_name_input.text().strip() or "Local Worker")
            self._settings.setValue("worker_id", self._worker_id)
            self._settings.setValue("poll_wait", int(self.poll_wait_input.value()))
            self._settings.setValue("max_download_bytes", int(self.max_download_input.value()) * 1024 * 1024)
            self._settings.setValue("work_dir", self.work_dir_input.text().strip() or "worker_runtime")
            self._vault.save(server_url, self.token_input.text().strip())

        def _reload_token_for_server(self) -> None:
            server_url = self.server_input.text().strip()
            if not server_url:
                return
            token = self._vault.load(server_url)
            if token:
                self.token_input.setText(token)

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
            if self._state == "connected" and self._hide_on_connect:
                self.hide()

        def _on_log(self, message: str) -> None:
            line = str(message or "").strip()
            if line:
                self.logs.appendPlainText(line)

        def _build_loop(self) -> WorkerLoop:
            server_url = normalize_server_url(self.server_input.text().strip())
            token = self.token_input.text().strip()
            if not token:
                raise ValueError("Worker token is required.")

            worker_name = self.worker_name_input.text().strip() or "Local Worker"
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
                    poll_wait_seconds=max(1, int(self.poll_wait_input.value())),
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

        def _connect_clicked(self) -> None:
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
    window = MainWindow()
    window.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
