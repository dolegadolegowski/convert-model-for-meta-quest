# Changelog

## 1.4

- Added worker task dispatcher to support multiple remote job types from server claims (`convert`, `reduce_size`).
- Worker now reads `task_type` / `task_params` aliases from claim payload and routes processing accordingly.
- `reduce_size` jobs now override Blender pipeline face limit per task request, clamped to safe max (400000), and keep GLB output flow unchanged.
- Added unit coverage for dispatcher parsing/routing and worker-loop handling of supported/unsupported task types.

## 1.3

- Fixed connection-code decode failures on fresh machines when no local secret env was configured.
- Added compatibility fallback secret (`medical3d-worker-code-v1`) to match current `Medical 3D Models` server default.
- Updated desktop preflight and launcher messaging to explain compatibility fallback and custom-secret override behavior.

## 1.2

- Rebranded project naming to `Remote3Dworker` across worker CLI, desktop UI, packaging, updater user-agent, and docs.
- Updated default updater repository to `dolegadolegowski/Remote3Dworker`.
- Added desktop settings/token backward compatibility fallback for legacy app keys:
  - legacy `QSettings` org: `ConvertModelForMetaQuest`
  - legacy keyring service: `ConvertModelForMetaQuestWorkerToken`
- Updated unit tests for renamed worker archive and user-agent strings.

## 1.1

- Fixed connection-code secret loading when worker is started outside Finder launcher.
- Added shared env loader that auto-loads local `.cmq_worker.env` in both:
  - `scripts/worker_desktop_app.py`
  - `scripts/run_worker.py`
- Added unit tests for local env-file loading behavior.

## 1.0

- Released stable worker version `1.0`.
- Finalized desktop launcher flow with local secret file support (`.cmq_worker.env`) and safer packaging defaults.
- Maintained remote worker pipeline compatibility with current `Medical 3D Models` API integration.

## 0.48

- Added local launcher env-file support: `Run Worker.command` now auto-loads `.cmq_worker.env` when present.
- Added clear startup hint when connection-code secret is missing, so users can fix `Invalid code, missing connection code secret` quickly.
- Excluded `.cmq_worker.env` from repository and ZIP packaging output to reduce accidental secret leaks.
- Added `.cmq_worker.env.example` template file for safe local setup.
- Extended desktop startup checks with optional `Connection code secret` status line.
- Added launcher test coverage for `.cmq_worker.env` packaging exclusion.

## 0.47

- Switched project repository visibility to `PUBLIC` on GitHub.
- Removed hardcoded connection-code shared secret from source code.
- Connection-code crypto key is now loaded from environment (`CMQ_CONNECTION_CODE_SECRET`, legacy fallback `WORKER_CONNECTION_CODE_SHARED_SECRET`) and never stored in repository files.
- Added security scan script `scripts/security_scan.sh` that checks both worktree and full git history for high-confidence credential leak patterns.
- Added tests for connection-code secret requirement and security scan script presence/executability.

## 0.46

- Improved desktop updater UX for repositories without published GitHub Releases.
- `releases/latest` HTTP `404` is now treated as informational update status (`No published GitHub release found`) instead of hard error.
- Added updater test coverage for `404` no-release scenario.
- Desktop update status label/log now displays `status_message` from updater responses.

## 0.45

- Fixed desktop startup crash after preflight checks introduced by update controls initialization order.
- `_refresh_connect_button_state()` now safely guards update actions/buttons before tray actions are created.
- Added regression assertion in desktop UI source test for guarded update action access.

## 0.44

- Added desktop self-update subsystem integrated with GitHub releases.
- Desktop app now checks for updates on startup and can perform manual check via `Check Updates`.
- Added `Install Update` action (window + tray) with automatic restart after successful update.
- Update apply strategy:
  - git worktree: `git pull --ff-only`,
  - packaged (no `.git`): download latest release ZIP and overlay project files.
- Update flow preserves local runtime data (`.venv`, `worker_runtime`, `dist`) and keeps user settings/tokens (`QSettings` + keyring).
- Added updater module tests for:
  - version normalization/comparison,
  - release parsing from GitHub response,
  - ZIP install path with preserved local directories,
  - git-mode installation path.
- Extended desktop UI/source test to verify update controls are present.

## 0.43

- Hardened `Run Worker.command` for new macOS machines:
  - auto-detects a supported Python interpreter (`3.10+`) instead of forcing system `/usr/bin/python3`,
  - recreates `.venv` if it was created with an unsupported Python version,
  - keeps a clear guided error when no supported Python is available.
- Updated launcher package install step to use `pip install --no-compile PySide6 keyring`, avoiding known `py_compile` crashes seen on some macOS Python 3.9 setups.
- Updated launcher README section with the new interpreter-selection and compatibility behavior.
- Updated launcher unit test expectations for the new bootstrap flow.

## 0.42

- Added encrypted connection-code flow in desktop worker:
  - new `Connection Code` tab with paste box,
  - automatic code decode and validation in background,
  - `Connect` is enabled only when code is valid and required fields are present.
- Added `Manual Config` tab for classic field-by-field setup (server URL/token/worker name/poll/download/work dir).
- Connect button now toggles runtime action:
  - `Connect` before startup,
  - `Disconnect` while worker loop is running.
- Added persistent settings for both modes:
  - pasted connection code is remembered,
  - decoded/manual values are stored and reused on next start.
- Added `quest_model_optimizer/connection_code.py` with shared contract decoder and button-state helper.
- Added tests for:
  - valid/invalid code parsing,
  - connect/disconnect button state transitions,
  - presence of Connection Code + Manual Config flow in desktop worker UI source.

## 0.41

- Fixed Finder launcher shutdown flow:
  - `Run Worker.command` now schedules asynchronous Terminal close after background launch,
  - avoids keeping Terminal open waiting for manual close confirmation in normal double-click flow.
- Added ZIP packaging script for distribution to another computer:
  - new `scripts/package_worker_zip.sh`,
  - produces `dist/ConvertModelForMetaQuest-worker-v<VERSION>.zip`,
  - excludes local runtime artifacts (`.venv`, `worker_runtime`, `dist`, caches).
- Updated README with launcher behavior and packaging instructions.
- Added launcher/unit coverage for:
  - Terminal auto-close script presence,
  - package script existence + executable bit + core exclusions.

## 0.40

- Added desktop startup prerequisite checks shown in a dedicated loading window before opening the worker UI.
- New startup checks include:
  - Python runtime version (3.10+),
  - Python SSL support,
  - Blender executable availability and basic `--version` validation,
  - write access to configured worker runtime directory,
  - `keyring` package availability (optional).
- Each prerequisite now reports explicit `OK` / `NOT FOUND` status and shows English install/fix instructions when missing.
- Added startup preflight summary logging inside the main worker log panel.
- Added unit tests for preflight checks and missing-dependency instruction paths.

## 0.39

- Updated `Run Worker.command` for true double-click launch mode: starts desktop worker in background and auto-closes Terminal window when launched without CLI args.
- Kept foreground mode for explicit args (for example `--help`) so diagnostics still work from terminal.
- Removed insecure HTTP option from desktop worker UX and CLI parser (`--allow-insecure-http` no longer exposed in desktop launcher flow).
- Standardized HTTPS-only validation message in remote client.
- Updated README security section to remove insecure HTTP opt-in guidance.
- Verified with tests:
  - `PYTHONPATH=src python3 -m unittest tests.test_worker_desktop_launcher_unittest tests.test_worker_app_unittest` ✅
  - `./Run Worker.command --help` ✅

## 0.38

- Added macOS single-file launcher `Run Worker.command` (double-click friendly in Finder).
- Launcher auto-prepares `.venv`, installs `PySide6` + `keyring` when missing, and starts desktop worker UI.
- Added unit tests for launcher presence/executable bit and desktop worker call wiring.

## 0.37

- Fixed macOS Qt startup crash in desktop worker (`Could not find the Qt platform plugin "cocoa"`).
- Launcher now prepares a stable plugin path under `/tmp/cmq-qt-plugins/platforms` before creating QApplication.
- Added automatic plugin copy without metadata-preserving mode (avoids plugin load failure in iCloud-based paths).

## 0.36

- Added standalone desktop worker launcher: python3 scripts/worker_desktop_app.py (no required CLI params).
- Desktop app allows entering server URL/token in GUI, stores settings via QSettings, and stores token via keyring when available.
- Added tray workflow with Reconnect, Logs, and Quit, plus status colors (green connected, orange processing, red disconnected).
- Added package entrypoint convert-model-worker-desktop.

## 0.35

- Hardened worker reconnect strategy: transient network errors (timeouts/reset/502/DNS) no longer force immediate session reset and re-register loops.
- Added claim/heartbeat coordination so heartbeat is deferred while long-poll claim is in flight, reducing control-plane pressure during outages.
- Added throttled warning logging for repetitive transient failures to reduce log noise while preserving incident visibility.
- Added `Retry-After` support from API errors and applied it to worker retry/backoff waits.
- Upgraded HTTP client error handling with normalized transport exceptions and dynamic `User-Agent` versioning.
- Added/updated unit tests for retry-after handling, deferred heartbeat behavior, transient reconnect behavior, and API error metadata parsing.

## 0.34

- Improved auto-reconnect behavior after server restart/network loss.
- Worker now resets stale session and re-registers on transient connectivity failures (connection reset/refused/timeouts, selected HTTP 5xx).
- Added heartbeat-based session reset after repeated transient failures.
- Added unit test covering reconnect flow after transient claim disconnect.

## 0.33

- Updated decimation target behavior for over-limit models to aim for a final face window of `87.5%-100%` of face limit.
- With default `--face-limit 400000`, optimization now targets `350000-400000` faces.
- Reworked correction/emergency pass ratio logic to avoid overly aggressive reductions and improve window stability.
- Added `target_face_window` and `face_window_met` fields in Blender report output.

## 0.32

- Updated default face limit from `300000` to `400000` across CLI, worker runtime, Blender worker args, and regression runner.
- Updated documentation examples to reflect new default limit (`--face-limit 400000`).

## 0.31

- Improved Blender decimation stability for hard multi-object scenes by adding an emergency fallback pass when thresholded passes cannot reach face limit.
- Emergency fallback decimates all meshes (`min_object_faces_for_decimate=0`) only as last resort and records strategy in report.
- Verified on `27_uklad_nerwowy.obj`: `1982240 -> 253410` faces (`face_limit_met=true`).

## 0.30

- Fixed worker fail-report contract: `/api/v1/jobs/{id}/fail` now includes `lease_token` (and `claim_token` alias).
- Added strict guard for missing lease token in fail-report flow to fail fast with clear message.
- Added unit tests for fail-report payload (`lease_token`) and missing-lease validation.

## 0.29

- Added idempotent upload handling: HTTP 409 conflicts indicating server-side `DONE` state are now treated as successful upload completion.
- Reduced false-positive worker errors for transient network issues by logging loop-level network resets/timeouts as warning.
- Added unit test for upload conflict recovery (`JOB_STATUS_CONFLICT` / status `DONE`).

## 0.28

- Added transfer progress logging for remote worker download and upload operations with percentage progress bars.
- Suppressed repetitive heartbeat runtime-config INFO logs; now warning is logged only when heartbeat response misses runtime config.
- Improved retry resilience for transient network failures (timeouts, connection reset, broken pipe) by extending retries up to 4 attempts.
- Added unit tests for transfer progress callback flow and transient retry extension.

## 0.27

- Simplified worker startup so only `--server-url` and `--token` are required; worker identity is auto-generated.
- Added server-driven runtime configuration support from `register` and `heartbeat` responses (`runtime_config` + flat legacy fallback).
- Added live runtime updates for poll wait, heartbeat interval, retries, backoff, and network timeouts without restarting worker.
- Marked legacy CLI overrides as deprecated fallback options with runtime warning.
- Added unit tests for auto identity, server runtime config application, heartbeat runtime updates, and minimal CLI startup.

## 0.26

- Added separate network timeouts for API, download, and upload operations.
- Exposed CLI flags: `--http-timeout-seconds`, `--download-timeout-seconds`, `--upload-timeout-seconds`.
- Increased resilience against slow/unstable uploads without slowing claim/heartbeat timeout behavior.

## 0.25

- Added worker auto-reconnect by forcing session re-registration after repeated loop failures.
- Added API-aware reconnect triggers for HTTP session/lease errors (401/403/404/410/412 and selected 409 cases).
- Added CLI flag `--reconnect-after-failures` and regression test for reconnect-after-claim-failure scenario.

## 0.24

- Expanded worker upload checksum compatibility: metadata now includes checksum aliases in top-level, `checksums`, and `worker_metadata`.
- Added multipart checksum fallback fields (`result_checksum`/`result_sha256`/`output_sha256` and source aliases).
- Added `worker_metadata_json` field for servers expecting separate worker metadata envelope.

## 0.23

- Improved GUI connection status readability with brighter colors and bold font.
- Added worker version display in GUI header and window title (`v<version>`).
- Wired GUI version source from project `VERSION` via `read_version()`.

## 0.22

- Added result file checksum propagation to worker upload metadata (`result_checksum`/`result_sha256`/`output_sha256`).
- Worker now includes both source and result checksums in `metadata_json` for stricter backend validation.
- Added unit assertion covering result checksum presence in upload metadata.

## 0.21

- Added source checksum propagation for worker uploads (`source_checksum`/`source_sha256`/`input_sha256`) in `metadata_json`.
- Worker now computes SHA256 of downloaded input and stores it in claim metadata before upload.
- Added upload guard for missing source checksum and unit tests covering checksum metadata contract.

## 0.20

- Updated worker upload contract for Medical 3D Models API: multipart now sends `lease_token`, `metadata_json`, and `result_file`.
- Added explicit runtime check for missing lease token before upload to fail fast with clear error.
- Added unit coverage for upload contract fields and missing-lease-token guard.

## 0.19

- Improved claim endpoint compatibility by sending `worker_id` in query and JSON body.
- Added unit test validating claim request query (`wait`, `worker_id`) to prevent API mismatch regressions.

## 0.18

- Added lease-aware download support for APIs requiring `worker_id` and `lease_token` query params.
- Worker now extracts lease token from claim payload/response and appends it to same-origin download URLs.
- Added regression tests covering lease token propagation and same-origin download query parameter handling.

## 0.17

- Fixed worker download flow for relative job URLs returned by server claim endpoint.
- Added URL normalization in remote client to resolve `/api/...` paths against configured `SERVER_URL`.
- Added regression unit test for relative `download_url` handling to prevent `unknown url type` failures.

## 0.16

- Added explicit `worker_id` support in register payload for API compatibility.
- Added fallback to local `worker_id` when register response omits worker id.
- Added legacy CLI compatibility flags (`--gui`, `--claim-wait`, `--heartbeat-interval`, `--lease-timeout`).
- Verified registration flow no longer fails with HTTP 422 missing `worker_id`.

## 0.15

- Added graceful fallback to headless mode when Tkinter GUI is unavailable (`_tkinter` missing).
- Prevented startup crash on `--with-gui` in Python builds without Tk support.

## 0.14

- Added download integrity validation (optional `sha256` / `input_sha256`) before model processing.
- Added max download size guard (`--max-download-bytes`) to mitigate oversized payload abuse.
- Added security unit tests for checksum mismatch, size overflow, and validation-failure job reporting.

## 0.13

- Fixed token leakage risk by limiting `Authorization` header to same-origin download endpoints.
- External `download_url` (signed storage links) is now fetched without bearer token.
- Added security unit tests for same-origin vs cross-origin download header behavior.

## 0.12

- Added secure-by-default HTTPS enforcement for remote worker server URL.
- Added explicit `--allow-insecure-http` override for local development scenarios.
- Added tests validating HTTP rejection and opt-in override behavior.

## 0.11

- Added remote HTTPS worker client with register/heartbeat/claim/download/upload/fail flow.
- Added worker loop with exponential backoff and retry handling for active jobs.
- Added small Tkinter GUI window with connection, download, geometry summary, upload status, and timestamped logs.
- Added headless worker mode (`--no-gui`) and worker app entrypoint (`scripts/run_worker.py`).
- Added unit tests for API client, worker summary parser, worker loop success/failure behavior, and headless startup.

## 0.10

- Refactored importer selection to extension-to-operator fallback mapping.
- Added optional support mapping for `.3ds` and `.dxf` operators when available.
- Improved importer error reporting with fallback attempt details.

## 0.9

- Added strict final face-limit guard (`--fail-if-over-limit`).
- Pipeline can now fail fast when decimation cannot reach required face cap.
- Propagated strict mode through regression runner defaults.

## 0.8

- Added Blender process timeout control (`--blender-timeout-seconds`).
- Added timeout-aware error handling in runner for hung conversions.
- Threaded timeout option through integration regression runner.

## 0.7

- Added optional parallel regression execution via `--jobs`.
- Kept deterministic default behavior with `--jobs 1`.
- Validated parallel smoke test on selected attached models.

## 0.6

- Added bottleneck timing capture (import/cleanup/export) in regression runner.
- Added aggregated performance statistics across all test cases.
- Added Markdown summary output for quick review of regression + performance.

## 0.5

- Added stdlib unit test suite and dedicated runner (`scripts/run_unit_tests.py`).
- Kept regression workflow independent from `pytest` installation.
- Fixed unittest discovery path handling for local execution.

## 0.4

- Added cleanup guard to skip normal recalculation on very large meshes.
- Exposed `--cleanup-skip-normal-recalc-above-faces` in CLI and report settings.
- Improved cleanup reporting with explicit normal-recalc status.

## 0.3

- Added per-object decimation ratio planning for multi-mesh scenes.
- Added small-object protection threshold (`--min-object-faces-for-decimate`).
- Fixed over-aggressive ratio-map target math to keep reductions closer to face limit.
- Added unit coverage for per-object ratio planning.

## 0.2

- Added configurable decimation and cleanup tuning parameters in CLI.
- Added pass-limit control for correction decimation.
- Added settings snapshot to JSON execution report for reproducibility.

## 0.1

- Added initial project scaffold.
- Implemented CLI + Blender worker pipeline.
- Added face-limit logic with conditional decimation and correction passes.
- Added cleanup operations and GLB export.
- Added regression runner and reduction unit tests.
