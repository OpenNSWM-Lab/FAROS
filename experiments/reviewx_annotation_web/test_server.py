from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from experiments.reviewx_annotation_web.server import (
    activate_batch,
    database_status,
    export_csv,
    import_tasks,
    initialize_db,
    list_batches,
    save_annotation,
    task_rows,
)


class TaskReplacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = self.root / "annotations.db"
        initialize_db(self.db)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_tasks(self, name: str, ids: list[str]) -> Path:
        path = self.root / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["annotationId", "claimText"])
            writer.writeheader()
            for task_id in ids:
                writer.writerow({"annotationId": task_id, "claimText": task_id})
        return path

    def test_replace_removes_old_unanswered_tasks(self) -> None:
        import_tasks(self.db, self.write_tasks("old.csv", ["old_1", "old_2"]))
        import_tasks(self.db, self.write_tasks("new.csv", ["new_1"]), replace=True)
        status = database_status(self.db)
        self.assertEqual(status["taskCount"], 1)
        self.assertEqual(status["annotationCount"], 0)
        self.assertTrue(status["taskSetSha256"])

    def test_replace_refuses_when_annotation_exists(self) -> None:
        import_tasks(self.db, self.write_tasks("old.csv", ["old_1"]))
        save_annotation(self.db, "reviewer", {"annotationId": "old_1"})
        with self.assertRaisesRegex(ValueError, "refusing to replace"):
            import_tasks(self.db, self.write_tasks("new.csv", ["new_1"]), replace=True)
        self.assertEqual(database_status(self.db)["taskCount"], 1)

    def test_empty_task_set_cannot_replace_existing_tasks(self) -> None:
        import_tasks(self.db, self.write_tasks("old.csv", ["old_1"]))
        with self.assertRaisesRegex(ValueError, "contains no tasks"):
            import_tasks(self.db, self.write_tasks("empty.csv", []), replace=True)
        self.assertEqual(database_status(self.db)["taskCount"], 1)

    def test_coverage_label_is_required_for_completion(self) -> None:
        import_tasks(self.db, self.write_tasks("tasks.csv", ["task_1"]))
        payload = {"annotationId": "task_1", **{
            field: 4 for field in (
                "humanCorrectness", "humanActionability", "humanSpecificity",
                "humanGrounding", "humanSeverityAgreement",
            )
        }}
        self.assertEqual(save_annotation(self.db, "reviewer", payload)["status"], "draft")
        payload["humanCoverageLabel"] = "partial"
        result = save_annotation(self.db, "reviewer", payload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["humanCoverageLabel"], "partial")

    def test_invalid_coverage_label_is_rejected(self) -> None:
        import_tasks(self.db, self.write_tasks("tasks.csv", ["task_1"]))
        with self.assertRaisesRegex(ValueError, "humanCoverageLabel is invalid"):
            save_annotation(self.db, "reviewer", {
                "annotationId": "task_1", "humanCoverageLabel": "maybe",
            })

    def test_batches_isolate_same_task_ids_annotations_and_exports(self) -> None:
        first = self.write_tasks("first.csv", ["shared_1"])
        second = self.write_tasks("second.csv", ["shared_1"])
        import_tasks(self.db, first, batch_id="batch_a", batch_name="Batch A", activate=True)
        save_annotation(self.db, "reviewer", {
            "annotationId": "shared_1", "batchId": "batch_a", "humanNotes": "first batch",
        })
        import_tasks(self.db, second, batch_id="batch_b", batch_name="Batch B")
        self.assertEqual(database_status(self.db)["activeBatchId"], "batch_a")
        self.assertIn("first batch", export_csv(self.db))
        activate_batch(self.db, "batch_b")
        self.assertEqual(task_rows(self.db, "reviewer")[0]["annotation"]["status"], "unrated")
        save_annotation(self.db, "reviewer", {
            "annotationId": "shared_1", "batchId": "batch_b", "humanNotes": "second batch",
        })
        exported = export_csv(self.db)
        self.assertIn("second batch", exported)
        self.assertNotIn("first batch", exported)
        batches = {row["id"]: row for row in list_batches(self.db)}
        self.assertEqual(batches["batch_a"]["annotationCount"], 1)
        self.assertEqual(batches["batch_b"]["annotationCount"], 1)

    def test_stale_page_cannot_write_after_batch_switch(self) -> None:
        tasks = self.write_tasks("tasks.csv", ["shared_1"])
        import_tasks(self.db, tasks, batch_id="batch_a", activate=True)
        import_tasks(self.db, tasks, batch_id="batch_b")
        activate_batch(self.db, "batch_b")
        with self.assertRaisesRegex(ValueError, "active batch changed"):
            save_annotation(self.db, "reviewer", {
                "annotationId": "shared_1", "batchId": "batch_a",
            })

    def test_initialize_migrates_legacy_tasks_and_annotations(self) -> None:
        legacy_db = self.root / "legacy.db"
        connection = sqlite3.connect(legacy_db)
        connection.executescript("""
            CREATE TABLE tasks(id TEXT PRIMARY KEY, position INTEGER, payload_json TEXT, imported_at TEXT);
            CREATE TABLE annotations(
                task_id TEXT, annotator_id TEXT, human_correctness INTEGER,
                human_actionability INTEGER, human_specificity INTEGER,
                human_grounding INTEGER, human_severity_agreement INTEGER,
                human_notes TEXT, status TEXT, updated_at TEXT,
                PRIMARY KEY(task_id, annotator_id)
            );
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO tasks VALUES ('legacy_1', 1, '{"annotationId":"legacy_1"}', 'now');
            INSERT INTO annotations VALUES ('legacy_1', 'old_reviewer', 5, 5, 5, 5, 5, 'kept', 'completed', 'now');
            INSERT INTO metadata VALUES ('csv_fields', '["annotationId"]');
            INSERT INTO metadata VALUES ('task_set_sha256', 'legacy_hash');
        """)
        connection.commit()
        connection.close()
        initialize_db(legacy_db)
        status = database_status(legacy_db)
        self.assertEqual(status["activeBatchId"], "legacy")
        self.assertEqual(status["taskCount"], 1)
        self.assertEqual(status["annotationCount"], 1)
        self.assertIn("kept", export_csv(legacy_db))


if __name__ == "__main__":
    unittest.main()
