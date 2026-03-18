# Convert Model For Meta Quest

Python project that runs Blender in background mode to import 3D models, check face count, reduce geometry when needed, run mesh cleanup operations, and export `.glb` output.

## Goal

Automate conversion/optimization so that imported models are capped below a configurable face limit (default `300000`) and exported as GLB with reproducible logging and JSON reporting.

## Why output goes to `output/`

By default, output files are stored in project-local `output/` instead of next to source scripts. This keeps generated artifacts separate from code, makes test runs reproducible, and prevents accidental overwrite of source assets.

## Features

- CLI usage from terminal.
- New empty Blender scene before each import (`read_factory_settings(use_empty=True)`).
- Wide importer coverage via Blender operators (OBJ, FBX, GLTF/GLB, STL, PLY, USD*, DAE, X3D/WRL, Alembic, and `.blend` open).
- Mesh detection across all scene objects.
- Face counting on all mesh objects.
- Conditional decimation:
  - skip if `faces <= limit`;
  - apply decimate only when needed;
  - compute ratio from actual face count;
  - run correction pass(es) if first pass is not enough.
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
  --blender-exec /Applications/Blender.app/Contents/MacOS/Blender
```

Optional explicit output path:

```bash
python3 scripts/optimize_model.py \
  --input /absolute/path/model.obj \
  --output /absolute/path/output/model_optimized.glb
```

## Regression tests on attached files

```bash
python3 scripts/run_regression.py \
  --blender-exec /Applications/Blender.app/Contents/MacOS/Blender
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

## Changelog and versioning

- Current version is stored in `VERSION`.
- Changes are recorded in `CHANGELOG.md`.
