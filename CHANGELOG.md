# Changelog

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
