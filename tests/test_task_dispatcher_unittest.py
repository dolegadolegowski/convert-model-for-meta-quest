from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quest_model_optimizer.task_dispatcher import (
    dispatch_claim_processing,
    extract_job_task,
    resolve_reduce_size_face_limit,
)
from quest_model_optimizer.worker_models import JobClaim, ProcessingOutcome


class RecordingProcessor:
    def __init__(self, face_limit: int = 400000, fail_if_over_limit: bool = False) -> None:
        self.options = type("Options", (), {"face_limit": face_limit, "fail_if_over_limit": fail_if_over_limit})()
        self.process_calls: list[tuple[Path, Path, Path]] = []
        self.override_calls: list[dict[str, int | bool]] = []

    def with_option_overrides(self, **overrides):
        child = RecordingProcessor(
            face_limit=int(overrides.get("face_limit", self.options.face_limit)),
            fail_if_over_limit=bool(overrides.get("fail_if_over_limit", self.options.fail_if_over_limit)),
        )
        child.override_calls = self.override_calls
        child.process_calls = self.process_calls
        self.override_calls.append(dict(overrides))
        return child

    def process(self, input_path: Path, output_path: Path, report_path: Path) -> ProcessingOutcome:
        self.process_calls.append((input_path, output_path, report_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"glb")
        report_path.write_text("{}", encoding="utf-8")
        return ProcessingOutcome(
            success=True,
            output_path=output_path,
            report_path=report_path,
            report={"faces_final": self.options.face_limit - 1},
            returncode=0,
        )


class TaskDispatcherTests(unittest.TestCase):
    def test_extract_job_task_defaults_to_convert(self) -> None:
        task = extract_job_task({})
        self.assertEqual(task.task_type, "convert")
        self.assertEqual(task.params, {})

    def test_extract_job_task_reads_aliases_and_nested_params(self) -> None:
        task = extract_job_task({
            "job_type": "reduce",
            "task": {"params": {"target_triangles": 250000}},
        })
        self.assertEqual(task.task_type, "reduce_size")
        self.assertEqual(task.params.get("target_triangles"), 250000)

    def test_reduce_size_face_limit_defaults_and_clamps(self) -> None:
        self.assertEqual(resolve_reduce_size_face_limit({}, default_face_limit=400000), 400000)
        self.assertEqual(
            resolve_reduce_size_face_limit({"target_triangles": 900000}, default_face_limit=400000),
            400000,
        )
        self.assertEqual(
            resolve_reduce_size_face_limit({"target_triangles": 275000}, default_face_limit=400000),
            275000,
        )

    def test_dispatch_convert_uses_base_processor(self) -> None:
        processor = RecordingProcessor()
        claim = JobClaim(job_id="job-1", input_filename="mesh.obj", download_url=None, payload={"task_type": "convert"})
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outcome = dispatch_claim_processing(processor, claim, root / "in.obj", root / "out.glb", root / "report.json")
        self.assertTrue(outcome.success)
        self.assertEqual(len(processor.override_calls), 0)
        self.assertEqual(len(processor.process_calls), 1)

    def test_dispatch_reduce_size_uses_face_limit_override(self) -> None:
        processor = RecordingProcessor(face_limit=400000)
        claim = JobClaim(
            job_id="job-2",
            input_filename="mesh.obj",
            download_url=None,
            payload={"task_type": "reduce_size", "task_params": {"target_triangles": 255000}},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outcome = dispatch_claim_processing(processor, claim, root / "in.obj", root / "out.glb", root / "report.json")
        self.assertTrue(outcome.success)
        self.assertEqual(len(processor.override_calls), 1)
        self.assertEqual(processor.override_calls[0]["face_limit"], 255000)
        self.assertTrue(processor.override_calls[0]["fail_if_over_limit"])

    def test_dispatch_rejects_unsupported_task(self) -> None:
        processor = RecordingProcessor()
        claim = JobClaim(job_id="job-3", input_filename="mesh.obj", download_url=None, payload={"task_type": "voxelize"})
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "unsupported task_type"):
                dispatch_claim_processing(processor, claim, root / "in.obj", root / "out.glb", root / "report.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
