# Convert Model For Meta Quest

Python project that runs Blender in background mode to import 3D models, check face count, reduce geometry when needed, run mesh cleanup operations, and export `.glb` output.

## Goal

Automate conversion/optimization so that imported models are capped below a configurable face limit (default `400000`) and exported as GLB with reproducible logging and JSON reporting.

## Why output goes to `output/`

By default, output files are stored in project-local `output/` instead of next to source scripts. This keeps generated artifacts separate from code, makes test runs reproducible, and prevents accidental overwrite of source assets.

## Features

- CLI usage from terminal.
- New empty Blender scene before each import (`read_factory_settings(use_empty=True)`).
- Wide importer coverage via Blender operators (OBJ, FBX, GLTF/GLB, STL, PLY, USD*, DAE, X3D/WRL, Alembic, `.3ds`, `.dxf` when add-on available, and `.blend` open).
- Mesh detection across all scene objects.
- Face counting on all mesh objects.
- Conditional decimation:
  - skip if `faces <= limit`;
  - apply decimate only when needed;
  - for over-limit models target a final window of `87.5%-100%` of limit (default: `350000-400000`);
  - compute ratio from actual face count;
  - run correction pass(es) if first pass is not enough.
  - if thresholded passes still cannot reach limit, run last-resort emergency pass on all meshes.
- Cleanup pipeline (safe subset of Mesh > Clean Up equivalents):
  - merge-by-distance (`remove_doubles`),
  - dissolve degenerate geometry,
  - remove loose edges/vertices,
  - recalculate face normals,
  - mesh validation/repair.
- GLB export to deterministic output filename `{input_stem}_optimized.glb`.
- JSON report with key metrics and timings.

## Multi-object strategy

This project uses **per-object decimation with shared ratio and correction passes**.

Rationale:

- preserves object boundaries, names, and transforms,
- avoids side effects of force-joining separate meshes,
- keeps behavior predictable for complex scenes,
- allows global target control by checking total faces after each pass.
- protects small objects by default using `--min-object-faces-for-decimate`.

## Requirements

- macOS/Linux/Windows with Blender installed.
- Python `>=3.10`.
- Blender executable available either:
  - in `PATH` as `blender`, or
  - explicitly via `--blender-exec`, or
  - env `BLENDER_EXECUTABLE`.

For macOS this repo auto-detects `/Applications/Blender.app/Contents/MacOS/Blender`.

## Usage

```bash
python3 scripts/optimize_model.py \
  --input /absolute/path/model.obj \
  --output-dir output \
  --report-dir reports \
  --face-limit 400000 \
  --max-decimate-passes 4 \
  --initial-target-safety 0.995 \
  --correction-target-safety 0.99 \
  --min-object-faces-for-decimate 1500 \
  --cleanup-merge-distance 1e-6 \
  --cleanup-degenerate-distance 1e-8 \
  --cleanup-skip-normal-recalc-above-faces 500000 \
  --blender-timeout-seconds 1800 \
  --fail-if-over-limit 1 \
  --blender-exec /Applications/Blender.app/Contents/MacOS/Blender
```

Optional explicit output path:

```bash
python3 scripts/optimize_model.py \
  --input /absolute/path/model.obj \
  --output /absolute/path/output/model_optimized.glb
```

## Remote worker mode (HTTPS)

The project can also run as a remote worker connected to a server queue (for example `Medical 3D Models`).

Expected server endpoints:

- `POST /api/v1/workers/register`
- `POST /api/v1/workers/heartbeat`
- `POST /api/v1/jobs/claim?wait=<seconds>`
- `GET /api/v1/jobs/{job_id}/download` (or `download_url` from claim payload)
- `POST /api/v1/jobs/{job_id}/result`
- `POST /api/v1/jobs/{job_id}/fail`

Worker configuration via environment variables:

- `SERVER_URL`
- `WORKER_TOKEN`
- `WORKER_NAME` (optional legacy override)
- `WORKER_ID` (optional legacy override)

Security defaults:

- Worker enforces `https://` server URL by default.
- Bearer token is attached only for same-origin download endpoints; external signed URLs are fetched without auth header.
- Downloaded file is validated against optional job checksum (`sha256` / `input_sha256`) and size limit (`--max-download-bytes`).
- Worker auto-generates identity (`worker_name` from hostname and `worker_id` as `worker-<hostname>-<short_uuid>`).
- Worker keeps current session during transient outages (timeouts/reset/502/DNS) and re-registers only on explicit session-invalid responses (for example worker not found/expired lease/auth errors).
- Worker honors HTTP `Retry-After` when provided by server (for overload/backpressure responses).

Minimal worker start (recommended):

```bash
python3 scripts/run_worker.py \
  --server-url https://your-server.example \
  --token YOUR_TOKEN
```

Run worker with GUI:

```bash
python3 scripts/run_worker.py \
  --server-url https://your-server.example \
  --token YOUR_TOKEN \
  --with-gui
```

Run worker headless:

```bash
python3 scripts/run_worker.py \
  --server-url https://your-server.example \
  --token YOUR_TOKEN \
  --no-gui
```

Dry-run for local validation:

```bash
python3 scripts/run_worker.py --server-url https://your-server.example --token YOUR_TOKEN --no-gui --dry-run
```

Server-driven runtime config:

After register (and optionally heartbeat), server may return `runtime_config` with:

| key | meaning |
| --- | --- |
| `poll_wait_seconds` | long-poll wait for claim |
| `heartbeat_interval` | heartbeat cadence |
| `reconnect_after_failures` | failures before forced re-register |
| `max_backoff_seconds` | retry backoff ceiling |
| `http_timeout_seconds` | API call timeout (register/claim/heartbeat) |
| `download_timeout_seconds` | model download timeout |
| `upload_timeout_seconds` | result upload timeout |
| `download_retries` | download retry attempts |
| `upload_retries` | upload retry attempts |

The worker accepts both:
- `runtime_config` object (preferred),
- flat top-level legacy keys in register/heartbeat response.

Example runtime-config log line:

```text
2026-03-19 12:10:20,512 | INFO | Applied server runtime config from register: download_retries=4, poll_wait_seconds=20, upload_timeout_seconds=900
```

Note: runtime-config `INFO` is emitted only when at least one server value changed.

Transfer progress log lines:

```text
2026-03-19 16:10:20,111 | INFO | Download 18_heart.obj [########............]  40% (12.4MB / 31.0MB)
2026-03-19 16:10:55,222 | INFO | Upload 18_heart_optimized.glb [##############......]  70% (22.1MB / 31.6MB)
```

Upload idempotency note:

- If network drops right after upload send, worker may retry.
- If server responds with conflict meaning job is already `DONE`, worker treats that as successful completion to avoid duplicate-failure false alarms.

Legacy CLI compatibility (deprecated fallbacks):

- `--gui` is accepted as alias of `--with-gui`.
- `--claim-wait` is accepted as alias of `--poll-wait`.
- `--worker-id`, `--worker-name`, `--poll-wait`, `--heartbeat-interval`, `--lease-timeout`,
  `--http-timeout-seconds`, `--download-timeout-seconds`, `--upload-timeout-seconds`,
  `--reconnect-after-failures` are still accepted as fallback overrides with warning.

GUI window displays:

- connection status (`CONNECTED` / `DISCONNECTED`),
- last fully downloaded file with timestamp,
- geometry summary, for example `HOL.obj: 559697 -> 298151 (decimate)`,
- last upload status (`SUCCESS` / `FAILED`) with timestamp,
- scrolling timestamped logs (`INFO/WARN/ERROR`).

## Regression tests on attached files

```bash
python3 scripts/run_regression.py \
  --jobs 1 \
  --blender-timeout-seconds 1800 \
  --fail-if-over-limit 1 \
  --blender-exec /Applications/Blender.app/Contents/MacOS/Blender
```

Regression run writes:

- `reports/regression_summary.json`
- `reports/regression_summary.md` (includes bottleneck stage and aggregated timings)

Parallel mode is available with `--jobs > 1` for independent files, but may increase memory pressure due multiple Blender processes.

Lightweight unit tests without external dependencies:

```bash
python3 scripts/run_unit_tests.py
```

Default test cases:

- `/Users/damiandd/Desktop/kosz.obj`
- `/Users/damiandd/Desktop/robot.obj`
- `/Users/damiandd/Desktop/HOL.obj`
- `/Users/damiandd/Desktop/puzle kosci/All.obj`
- `/Users/damiandd/Desktop/Modele studenci/szkielet/Group4.obj`

## Known limitations

- Import capability still depends on enabled Blender importers in local build.
- Decimate can alter topology and UV quality; pipeline aims for minimum required reduction, not perfect visual preservation.
- Some aggressive cleanup actions available in UI are intentionally not auto-run to avoid destructive behavior.
- Remote worker mode expects server API compatible with listed `/api/v1/...` endpoints.
- GUI mode requires Tkinter and an available desktop display session.
- If Tkinter is not available, worker automatically falls back to headless mode.

## Changelog and versioning

- Current version is stored in `VERSION`.
- Changes are recorded in `CHANGELOG.md`.

## Double-click Single-file Launcher (macOS)
If you want one-file launch from Finder, use:

```bash
./Run Worker.command
```

You can also double-click `Run Worker.command` in Finder.
On first run it auto-detects Python `3.10+`, recreates `.venv` if it was built with an older interpreter, installs `PySide6` + `keyring` (with `--no-compile` for macOS compatibility), and starts the worker UI.
In normal double-click mode it starts the app in background and closes the Terminal window automatically.
If `.cmq_worker.env` exists in project root, launcher auto-loads it before app start (recommended place for local `CMQ_CONNECTION_CODE_SECRET`).

## Desktop Worker Launcher (no required args)
Use the single-file desktop launcher:

```bash
python3 scripts/worker_desktop_app.py
```

After launch, use the `Connection Code` tab and paste the encrypted code generated in server admin panel.
When code is valid, `Connect` becomes active; after connection the same button switches to `Disconnect`.
Connection-code decryption requires secret from environment:
`CMQ_CONNECTION_CODE_SECRET` (legacy fallback: `WORKER_CONNECTION_CODE_SHARED_SECRET`).
Recommended for Finder launch:

```bash
cp .cmq_worker.env.example .cmq_worker.env
# edit .cmq_worker.env and set real shared secret value from server
```

Manual entry is still available in the `Manual Config` tab (server URL, token, worker name, poll wait, download limit, work dir).
Both code-based and manual settings are persisted with `QSettings` and reused on next launch.
Tray menu exposes `Reconnect`, `Logs`, and `Quit`.

### Desktop auto-update (GitHub)

- App checks GitHub for updates shortly after startup and shows status next to current version.
- Use `Check Updates` (window button or tray menu) to force manual check.
- If newer version is available, `Install Update` becomes active.
- If repo has no published GitHub Release yet, app shows informational status instead of an error.
- Update source:
  - Git checkout: app runs `git pull --ff-only`.
  - Non-git package: app downloads latest GitHub release ZIP and overlays project files.
- During ZIP update, local runtime data is preserved (`.venv`, `worker_runtime`, `dist` are not overwritten).
- Saved settings and tokens are preserved automatically because they are stored in `QSettings` + keyring outside project files.
- After successful update app restarts automatically.

### Security scan before each release/iteration

Run:

```bash
./scripts/security_scan.sh
```

This scan checks tracked files and full git history for common leaked credentials (GitHub/OpenAI/AWS token patterns, private-key headers, bearer secrets).

## Packaging ZIP for another computer

To build a portable worker source package (without local `.venv` and runtime folders):

```bash
./scripts/package_worker_zip.sh
```

The archive is created in `dist/` as:

`ConvertModelForMetaQuest-worker-v<VERSION>.zip`

### Startup prerequisite checks (desktop app)

At startup the desktop worker now opens a loading dialog that verifies required prerequisites:

- Python runtime version (3.10+)
- Python SSL support
- Blender executable availability
- Writable worker runtime directory
- `keyring` package (optional, for secure token storage)
- Connection-code secret (optional, required only for `Connection Code` tab)

Each check is displayed with `OK` or `NOT FOUND`.  
If a prerequisite is missing, the dialog shows an English installation/fix instruction (for example Blender install commands).
