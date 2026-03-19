"""CLI app for running ConvertModelForMetaQuest as a remote worker."""

from __future__ import annotations

import argparse
import os
import platform
import threading
from pathlib import Path

from .logging_utils import configure_logging
from .remote_client import RemoteWorkerClient
from .version import read_version
from .worker_loop import LoopConfig, NullWorkerObserver, WorkerLoop
from .worker_processor import PipelineOptions, PipelineProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ConvertModelForMetaQuest as remote worker")
    parser.add_argument("--server-url", default=os.getenv("SERVER_URL"), help="HTTPS URL of Medical 3D Models API")
    parser.add_argument("--token", default=os.getenv("WORKER_TOKEN"), help="Worker auth token")
    parser.add_argument(
        "--worker-id",
        default=os.getenv("WORKER_ID") or f"worker-{platform.node()}",
        help="Stable worker identifier used during register/heartbeat/claim",
    )
    parser.add_argument(
        "--worker-name",
        default=os.getenv("WORKER_NAME") or platform.node() or "cmq-worker",
        help="Human-readable worker name",
    )

    gui_group = parser.add_mutually_exclusive_group()
    gui_group.add_argument("--with-gui", "--gui", dest="with_gui", action="store_true", help="Run with small GUI status window")
    gui_group.add_argument("--no-gui", action="store_true", help="Run headless (console logs only)")

    parser.add_argument("--work-dir", default="worker_runtime", help="Directory for downloaded/processed files")
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Allow http:// server URL (for local development only)",
    )
    parser.add_argument("--poll-wait", "--claim-wait", dest="poll_wait", type=int, default=30, help="Long-poll wait seconds")
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=None,
        help="Optional register hint for server-preferred heartbeat interval (compat flag)",
    )
    parser.add_argument(
        "--lease-timeout",
        type=int,
        default=None,
        help="Optional register hint for server lease timeout (compat flag)",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=1024 * 1024 * 1024,
        help="Maximum allowed downloaded input size in bytes",
    )
    parser.add_argument(
        "--http-timeout-seconds",
        type=int,
        default=60,
        help="Base HTTP timeout for API calls (register/claim/heartbeat)",
    )
    parser.add_argument(
        "--download-timeout-seconds",
        type=int,
        default=180,
        help="Timeout for model download requests",
    )
    parser.add_argument(
        "--upload-timeout-seconds",
        type=int,
        default=600,
        help="Timeout for result upload requests",
    )
    parser.add_argument(
        "--reconnect-after-failures",
        type=int,
        default=3,
        help="Force worker re-register after this many consecutive loop failures",
    )
    parser.add_argument("--once", action="store_true", help="Run single claim cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and exit without network calls")

    parser.add_argument("--blender-exec", help="Optional Blender executable path")
    parser.add_argument("--face-limit", type=int, default=300000)
    parser.add_argument("--blender-timeout-seconds", type=int, default=1800)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="store_true", help="Print worker version and exit")
    return parser


def _resolve_gui_mode(args: argparse.Namespace) -> bool:
    if args.with_gui:
        return True
    if args.no_gui:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(read_version())
        return 0

    logger = configure_logging(args.log_level)

    if args.dry_run:
        logger.info("Dry run complete. version=%s worker_name=%s", read_version(), args.worker_name)
        return 0

    if not args.server_url:
        logger.error("Missing --server-url or SERVER_URL")
        return 2
    if not args.token:
        logger.error("Missing --token or WORKER_TOKEN")
        return 2

    use_gui = _resolve_gui_mode(args)

    try:
        client = RemoteWorkerClient(
            server_url=args.server_url,
            worker_token=args.token,
            worker_name=args.worker_name,
            worker_id=args.worker_id,
            timeout=max(10, int(args.http_timeout_seconds)),
            download_timeout=max(10, int(args.download_timeout_seconds)),
            upload_timeout=max(10, int(args.upload_timeout_seconds)),
            allow_insecure_http=bool(args.allow_insecure_http),
            heartbeat_interval_hint=args.heartbeat_interval,
            lease_timeout_hint=args.lease_timeout,
        )
    except ValueError as exc:
        logger.error("Invalid worker configuration: %s", exc)
        return 1

    processor = PipelineProcessor(
        blender_exec=args.blender_exec,
        options=PipelineOptions(
            face_limit=args.face_limit,
            blender_timeout_seconds=args.blender_timeout_seconds,
        ),
    )

    loop = WorkerLoop(
        client=client,
        processor=processor,
        work_root=Path(args.work_dir).expanduser().resolve(),
        logger=logger,
        observer=NullWorkerObserver(),
        config=LoopConfig(
            poll_wait_seconds=args.poll_wait,
            once=args.once,
            max_download_bytes=args.max_download_bytes,
            reconnect_after_failures=max(1, int(args.reconnect_after_failures)),
        ),
    )

    if not use_gui:
        return loop.run_forever()

    try:
        from .gui_log_window import GuiLogWindow
    except Exception as exc:
        logger.warning("GUI import unavailable: %s. Falling back to --no-gui.", exc)
        return loop.run_forever()

    try:
        gui = GuiLogWindow(app_version=read_version())
        gui.attach_logger(logger)
        gui.bind_stop_event(loop.stop_event)
        loop.observer = gui
    except Exception as exc:
        logger.warning("GUI initialization failed: %s. Falling back to --no-gui.", exc)
        return loop.run_forever()

    result = {"code": 0}

    def _worker_thread() -> None:
        result["code"] = loop.run_forever()

    thread = threading.Thread(target=_worker_thread, daemon=True)
    thread.start()
    try:
        gui.run()
    finally:
        loop.stop()
        thread.join(timeout=5)

    return int(result["code"])


if __name__ == "__main__":
    raise SystemExit(main())
