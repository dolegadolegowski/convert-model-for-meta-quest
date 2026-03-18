# Changelog

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
