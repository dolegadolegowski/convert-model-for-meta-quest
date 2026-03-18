"""Blender-side worker script executed with --python in background mode."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import bmesh
import bpy

# Ensure local package is importable when script runs inside Blender.
WORKER_FILE = Path(__file__).resolve()
PROJECT_ROOT = WORKER_FILE.parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quest_model_optimizer.reduction import (  # noqa: E402
    compute_correction_ratio,
    compute_object_ratio_map,
    should_decimate,
)


def _set_log_level(level: str) -> None:
    os.environ["CMQ_LOG_LEVEL"] = level.upper()


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Blender model optimization worker")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--face-limit", type=int, default=300000)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--max-decimate-passes", type=int, default=4)
    parser.add_argument("--initial-target-safety", type=float, default=0.995)
    parser.add_argument("--correction-target-safety", type=float, default=0.99)
    parser.add_argument("--cleanup-merge-distance", type=float, default=1e-6)
    parser.add_argument("--cleanup-degenerate-distance", type=float, default=1e-8)
    parser.add_argument("--min-object-faces-for-decimate", type=int, default=1500)
    parser.add_argument("--cleanup-skip-normal-recalc-above-faces", type=int, default=500000)
    return parser.parse_args(argv)


def op_available(path: str) -> bool:
    current = bpy.ops
    for part in path.split("."):
        if not hasattr(current, part):
            return False
        current = getattr(current, part)
    return True


def import_model(input_path: Path) -> dict:
    ext = input_path.suffix.lower()

    # Start from a clean scene every run.
    bpy.ops.wm.read_factory_settings(use_empty=True)

    before_count = len(bpy.context.scene.objects)
    details = {
        "extension": ext,
        "scene_object_count_before_import": before_count,
        "import_operator": None,
        "success": False,
    }

    if ext == ".obj":
        if op_available("wm.obj_import"):
            details["import_operator"] = "wm.obj_import"
            bpy.ops.wm.obj_import(filepath=str(input_path))
        elif op_available("import_scene.obj"):
            details["import_operator"] = "import_scene.obj"
            bpy.ops.import_scene.obj(filepath=str(input_path))
        else:
            raise RuntimeError("OBJ importer not available in this Blender build")
    elif ext == ".fbx":
        details["import_operator"] = "import_scene.fbx"
        bpy.ops.import_scene.fbx(filepath=str(input_path))
    elif ext in {".glb", ".gltf"}:
        details["import_operator"] = "import_scene.gltf"
        bpy.ops.import_scene.gltf(filepath=str(input_path))
    elif ext == ".stl":
        if op_available("wm.stl_import"):
            details["import_operator"] = "wm.stl_import"
            bpy.ops.wm.stl_import(filepath=str(input_path))
        else:
            details["import_operator"] = "import_mesh.stl"
            bpy.ops.import_mesh.stl(filepath=str(input_path))
    elif ext == ".ply":
        if op_available("wm.ply_import"):
            details["import_operator"] = "wm.ply_import"
            bpy.ops.wm.ply_import(filepath=str(input_path))
        else:
            details["import_operator"] = "import_mesh.ply"
            bpy.ops.import_mesh.ply(filepath=str(input_path))
    elif ext in {".abc"}:
        details["import_operator"] = "wm.alembic_import"
        bpy.ops.wm.alembic_import(filepath=str(input_path))
    elif ext in {".usd", ".usda", ".usdc", ".usdz"}:
        details["import_operator"] = "wm.usd_import"
        bpy.ops.wm.usd_import(filepath=str(input_path))
    elif ext == ".dae":
        details["import_operator"] = "wm.collada_import"
        bpy.ops.wm.collada_import(filepath=str(input_path))
    elif ext in {".x3d", ".wrl"}:
        details["import_operator"] = "import_scene.x3d"
        bpy.ops.import_scene.x3d(filepath=str(input_path))
    elif ext == ".blend":
        details["import_operator"] = "wm.open_mainfile"
        bpy.ops.wm.open_mainfile(filepath=str(input_path))
    else:
        raise RuntimeError(f"Unsupported extension: {ext}")

    details["success"] = True
    details["scene_object_count_after_import"] = len(bpy.context.scene.objects)
    return details


def mesh_objects() -> list:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.data is not None]


def count_faces(objs: list) -> int:
    return int(sum(len(obj.data.polygons) for obj in objs))


def apply_decimate_pass(objs: list, ratio_map: dict[str, float], pass_index: int) -> dict:
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")

    affected = 0
    for obj in objs:
        if len(obj.data.polygons) <= 8:
            continue
        ratio = ratio_map.get(obj.name, 1.0)
        if ratio >= 0.999:
            continue
        modifier = obj.modifiers.new(name=f"AutoDecimate_{pass_index}", type="DECIMATE")
        modifier.ratio = ratio
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        obj.select_set(False)
        affected += 1

    return {
        "pass_index": pass_index,
        "ratio_map": ratio_map,
        "affected_objects": affected,
    }


def cleanup_mesh_object(
    obj,
    merge_dist: float,
    degenerate_dist: float,
    skip_normal_recalc_above_faces: int,
) -> dict:
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    stats = {
        "object": obj.name,
        "verts_before": len(bm.verts),
        "edges_before": len(bm.edges),
        "faces_before": len(bm.faces),
        "removed_loose_edges": 0,
        "removed_loose_verts": 0,
    }

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_dist)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=degenerate_dist)

    loose_edges = [edge for edge in bm.edges if not edge.link_faces]
    if loose_edges:
        stats["removed_loose_edges"] = len(loose_edges)
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")

    loose_verts = [vert for vert in bm.verts if not vert.link_edges]
    if loose_verts:
        stats["removed_loose_verts"] = len(loose_verts)
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")

    face_count_before_normals = len(bm.faces)
    if face_count_before_normals == 0:
        stats["normal_recalc"] = "skipped_no_faces"
    elif face_count_before_normals <= skip_normal_recalc_above_faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        stats["normal_recalc"] = "performed"
    else:
        stats["normal_recalc"] = "skipped_large_mesh"

    bm.to_mesh(mesh)
    bm.free()

    invalid_found = mesh.validate(clean_customdata=True)
    mesh.update()

    stats["invalid_geometry_fixed"] = bool(invalid_found)
    stats["verts_after"] = len(mesh.vertices)
    stats["edges_after"] = len(mesh.edges)
    stats["faces_after"] = len(mesh.polygons)
    return stats


def cleanup_scene_meshes(
    objs: list,
    merge_dist: float,
    degenerate_dist: float,
    skip_normal_recalc_above_faces: int,
) -> dict:
    cleanup_stats = []
    errors = []
    for obj in objs:
        try:
            cleanup_stats.append(
                cleanup_mesh_object(
                    obj,
                    merge_dist=merge_dist,
                    degenerate_dist=degenerate_dist,
                    skip_normal_recalc_above_faces=skip_normal_recalc_above_faces,
                )
            )
        except Exception as exc:  # pragma: no cover - blender runtime
            errors.append({"object": obj.name, "error": str(exc)})

    return {
        "objects": cleanup_stats,
        "errors": errors,
        "error_count": len(errors),
    }


def export_glb(output_path: Path) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        use_selection=False,
        export_apply=True,
    )
    return {
        "operator_result": list(result),
        "output_exists": output_path.exists(),
    }


def optimize(
    input_path: Path,
    output_path: Path,
    face_limit: int,
    max_decimate_passes: int,
    initial_target_safety: float,
    correction_target_safety: float,
    cleanup_merge_distance: float,
    cleanup_degenerate_distance: float,
    min_object_faces_for_decimate: int,
    cleanup_skip_normal_recalc_above_faces: int,
) -> dict:
    start = time.time()
    report = {
        "status": "error",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "face_limit": face_limit,
        "timings": {},
    }

    t0 = time.time()
    report["import"] = import_model(input_path)
    report["timings"]["import_seconds"] = round(time.time() - t0, 4)

    objs = mesh_objects()
    if not objs:
        raise RuntimeError("No mesh objects found after import")

    report["mesh_count"] = len(objs)
    report["mesh_names"] = [obj.name for obj in objs]

    faces_before = count_faces(objs)
    report["faces_before"] = faces_before
    face_map_before = {obj.name: len(obj.data.polygons) for obj in objs}

    decimate = {"applied": False, "passes": []}

    if should_decimate(faces_before, face_limit):
        decimate["applied"] = True
        ratio_map = compute_object_ratio_map(
            face_counts=face_map_before,
            target_total_faces=int(face_limit * initial_target_safety),
            min_object_faces_for_decimate=min_object_faces_for_decimate,
            safety=1.0,
        )
        pass_info = apply_decimate_pass(objs, ratio_map=ratio_map, pass_index=1)
        faces_after = count_faces(objs)
        pass_info["faces_after_pass"] = faces_after
        decimate["passes"].append(pass_info)

        max_corrections = max(0, max_decimate_passes - 1)
        pass_idx = 2
        while faces_after > face_limit and pass_idx <= max_corrections + 1:
            correction_ratio = compute_correction_ratio(
                faces_after,
                face_limit,
                safety=correction_target_safety,
            )
            correction_face_map = {obj.name: len(obj.data.polygons) for obj in objs}
            correction_ratio_map = compute_object_ratio_map(
                face_counts=correction_face_map,
                target_total_faces=int(face_limit * correction_target_safety),
                min_object_faces_for_decimate=min_object_faces_for_decimate,
                safety=correction_ratio,
            )
            correction_info = apply_decimate_pass(
                objs,
                ratio_map=correction_ratio_map,
                pass_index=pass_idx,
            )
            faces_after = count_faces(objs)
            correction_info["faces_after_pass"] = faces_after
            decimate["passes"].append(correction_info)
            pass_idx += 1

    report["decimate"] = decimate
    report["faces_after_decimate"] = count_faces(objs)

    t1 = time.time()
    report["cleanup"] = cleanup_scene_meshes(
        objs,
        merge_dist=cleanup_merge_distance,
        degenerate_dist=cleanup_degenerate_distance,
        skip_normal_recalc_above_faces=cleanup_skip_normal_recalc_above_faces,
    )
    report["timings"]["cleanup_seconds"] = round(time.time() - t1, 4)

    report["faces_after_cleanup"] = count_faces(objs)

    t2 = time.time()
    report["export"] = export_glb(output_path)
    report["timings"]["export_seconds"] = round(time.time() - t2, 4)

    report["faces_final"] = report["faces_after_cleanup"]
    report["settings"] = {
        "max_decimate_passes": max_decimate_passes,
        "initial_target_safety": initial_target_safety,
        "correction_target_safety": correction_target_safety,
        "cleanup_merge_distance": cleanup_merge_distance,
        "cleanup_degenerate_distance": cleanup_degenerate_distance,
        "min_object_faces_for_decimate": min_object_faces_for_decimate,
        "cleanup_skip_normal_recalc_above_faces": cleanup_skip_normal_recalc_above_faces,
    }
    report["status"] = "success"
    report["timings"]["total_seconds"] = round(time.time() - start, 4)
    return report


def write_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> int:
    argv = sys.argv
    if "--" not in argv:
        print("[ERROR] Missing '--' separator for blender worker args")
        return 2

    args = parse_args(argv[argv.index("--") + 1 :])
    _set_log_level(args.log_level)

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()

    log("INFO", f"Input: {input_path}")
    log("INFO", f"Output: {output_path}")
    log("INFO", f"Report: {report_path}")

    if not input_path.exists():
        payload = {
            "status": "error",
            "error": f"Input path does not exist: {input_path}",
            "input_path": str(input_path),
            "output_path": str(output_path),
        }
        write_report(report_path, payload)
        log("ERROR", payload["error"])
        return 2

    try:
        payload = optimize(
            input_path=input_path,
            output_path=output_path,
            face_limit=args.face_limit,
            max_decimate_passes=args.max_decimate_passes,
            initial_target_safety=args.initial_target_safety,
            correction_target_safety=args.correction_target_safety,
            cleanup_merge_distance=args.cleanup_merge_distance,
            cleanup_degenerate_distance=args.cleanup_degenerate_distance,
            min_object_faces_for_decimate=args.min_object_faces_for_decimate,
            cleanup_skip_normal_recalc_above_faces=args.cleanup_skip_normal_recalc_above_faces,
        )
        write_report(report_path, payload)
        log(
            "SUMMARY",
            (
                f"status={payload['status']} faces_before={payload.get('faces_before')} "
                f"faces_final={payload.get('faces_final')} "
                f"decimate_applied={payload.get('decimate', {}).get('applied')}"
            ),
        )
        return 0
    except Exception as exc:  # pragma: no cover - blender runtime
        payload = {
            "status": "error",
            "error": str(exc),
            "input_path": str(input_path),
            "output_path": str(output_path),
        }
        write_report(report_path, payload)
        log("ERROR", f"Pipeline failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
