"""Small Tkinter window for worker status and timestamped logs."""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from .worker_loop import WorkerObserver


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class _QueueLogHandler(logging.Handler):
    def __init__(self, event_queue: "queue.Queue[dict[str, Any]]") -> None:
        super().__init__()
        self.event_queue = event_queue
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self.event_queue.put({"kind": "log", "message": message})
        except Exception:  # pragma: no cover - defensive logging code
            return


class GuiLogWindow(WorkerObserver):
    """GUI observer implementation receiving events from worker thread."""

    def __init__(self, title: str = "ConvertModelForMetaQuest Worker") -> None:
        import tkinter as tk
        from tkinter import scrolledtext

        self._tk = tk
        self._event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()

        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("860x420")

        self.connection_var = tk.StringVar(value="DISCONNECTED")
        self.last_download_var = tk.StringVar(value="-")
        self.geometry_var = tk.StringVar(value="-")
        self.upload_var = tk.StringVar(value="-")

        tk.Label(self.root, text="Connection:", anchor="w").pack(fill="x")
        tk.Label(self.root, textvariable=self.connection_var, anchor="w", fg="#004d00").pack(fill="x")

        tk.Label(self.root, text="Last Download:", anchor="w").pack(fill="x")
        tk.Label(self.root, textvariable=self.last_download_var, anchor="w").pack(fill="x")

        tk.Label(self.root, text="Geometry Summary:", anchor="w").pack(fill="x")
        tk.Label(self.root, textvariable=self.geometry_var, anchor="w").pack(fill="x")

        tk.Label(self.root, text="Upload Status:", anchor="w").pack(fill="x")
        tk.Label(self.root, textvariable=self.upload_var, anchor="w").pack(fill="x")

        tk.Label(self.root, text="Logs:", anchor="w").pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(self.root, height=14, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True)

        self._stop_event: threading.Event | None = None

    def attach_logger(self, logger: logging.Logger) -> None:
        logger.addHandler(_QueueLogHandler(self._event_queue))

    def bind_stop_event(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._drain_queue)
        self.root.mainloop()

    def set_connection_status(self, connected: bool) -> None:
        self._event_queue.put({"kind": "connection", "connected": connected, "ts": _now()})

    def set_last_download(self, message: str) -> None:
        self._event_queue.put({"kind": "download", "message": message})

    def set_geometry_summary(self, message: str) -> None:
        self._event_queue.put({"kind": "geometry", "message": message})

    def set_upload_status(self, message: str) -> None:
        self._event_queue.put({"kind": "upload", "message": message})

    def _drain_queue(self) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_event(event)
        self.root.after(150, self._drain_queue)

    def _apply_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "connection":
            connected = bool(event.get("connected"))
            ts = event.get("ts", _now())
            state = "CONNECTED" if connected else "DISCONNECTED"
            self.connection_var.set(f"{state} ({ts})")
            return
        if kind == "download":
            self.last_download_var.set(str(event.get("message", "-")))
            return
        if kind == "geometry":
            self.geometry_var.set(str(event.get("message", "-")))
            return
        if kind == "upload":
            self.upload_var.set(str(event.get("message", "-")))
            return
        if kind == "log":
            message = str(event.get("message", ""))
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

    def _on_close(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        self.root.destroy()
