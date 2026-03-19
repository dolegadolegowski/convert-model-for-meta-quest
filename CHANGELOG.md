# Changelog

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
