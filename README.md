# Convert Model For Meta Quest

Python project that runs Blender in background mode to import 3D models, check face count, reduce geometry when needed, run mesh cleanup operations, and export `.glb` output.

## Goal

Automate conversion/optimization so that imported models are capped below a configurable face limit (default `300000`) and exported as GLB with reproducible logging and JSON reporting.

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
  --face-limit 300000 \
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
- For local development only, `http://` can be enabled using `--allow-insecure-http`.
- Bearer token is attached only for same-origin download endpoints; external signed URLs are fetched without auth header.
- Downloaded file is validated against optional job checksum (`sha256` / `input_sha256`) and size limit (`--max-download-bytes`).
- Worker auto-generates identity (`worker_name` from hostname and `worker_id` as `worker-<hostname>-<short_uuid>`).
- Worker auto-reconnects by re-registering session after repeated failures.

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
