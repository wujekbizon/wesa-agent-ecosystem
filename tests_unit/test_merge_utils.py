import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import UUID, uuid4


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from merge_utils import (  # noqa: E402
    CategoryMergeError,
    MergePaths,
    _category_lock,
    rebuild_category,
    release_is_current,
)


class CategoryMergeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.paths = MergePaths(
            data_dir=root / "data",
            outputs_dir=root / "outputs",
            done_dir=root / "done",
            registry_db=root / "data" / "wolfmed.db",
            legacy_registry_json=root / "data" / "missing_registry.json",
        )
        self.paths.data_dir.mkdir(parents=True)
        self.paths.outputs_dir.mkdir(parents=True)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def question(text, question_id=None, course="wrong", category="wrong"):
        return {
            "id": question_id,
            "meta": {"course": course, "category": category},
            "data": {
                "question": text,
                "answers": [
                    {"text": "A", "isCorrect": True},
                    {"text": "B", "isCorrect": False},
                    {"text": "C", "isCorrect": False},
                    {"text": "D", "isCorrect": False},
                ],
            },
        }

    def add_manifest(self, name, category, chunks):
        manifest = {
            "book": name,
            "category": category,
            "validated_count": sum(status == "validated" for _, status, _ in chunks),
            "merge_status": "pending",
            "chunks": [],
        }
        for chunk_id, status, questions in chunks:
            manifest["chunks"].append({"id": chunk_id, "status": status})
            if questions is not None:
                (self.paths.outputs_dir / f"{chunk_id}_validated.json").write_text(
                    json.dumps(questions), encoding="utf-8"
                )
        manifest_path = self.paths.data_dir / f"{name}_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_rebuilds_all_complete_manifests_and_normalizes_metadata(self):
        kept_uuid = str(uuid4())
        self.add_manifest(
            "book-1",
            "opiekun-medyczny",
            [("book-1_page_1-10", "validated", [self.question("Question one?", kept_uuid)])],
        )
        self.add_manifest(
            "book-2",
            "opiekun-medyczny",
            [
                (
                    "book-2_page_1-10",
                    "validated",
                    [self.question("Question two?", "bad-id"), self.question("QUESTION ONE?")],
                )
            ],
        )
        self.add_manifest(
            "book-3",
            "opiekun-medyczny",
            [("book-3_page_1-10", "validating", None)],
        )

        result = rebuild_category("opiekun-medyczny", self.paths)
        entries = json.loads(result.output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.question_count, 2)
        self.assertEqual(result.skipped_manifests, ("book-3_manifest.json",))
        self.assertEqual(entries[0]["id"], kept_uuid)
        self.assertEqual(UUID(entries[1]["id"]).version, 4)
        self.assertEqual({entry["meta"]["course"] for entry in entries}, {"opiekun-medyczny"})
        self.assertEqual({entry["meta"]["category"] for entry in entries}, {"opiekun-medyczny"})

    def test_deleted_output_recovers_identically_and_rerun_is_idempotent(self):
        self.add_manifest(
            "book-1",
            "opiekun-medyczny",
            [("book-1_chunk", "validated", [self.question("Stable question?", "bad-id")])],
        )
        first = rebuild_category("opiekun-medyczny", self.paths)
        first_bytes = first.output_path.read_bytes()
        first_hash = hashlib.sha256(first_bytes).hexdigest()

        first.output_path.unlink()
        recovered = rebuild_category("opiekun-medyczny", self.paths)
        rerun = rebuild_category("opiekun-medyczny", self.paths)

        self.assertEqual(hashlib.sha256(recovered.output_path.read_bytes()).hexdigest(), first_hash)
        self.assertEqual(recovered.output_sha256, first.output_sha256)
        self.assertFalse(rerun.changed)
        self.assertTrue(release_is_current("opiekun-medyczny", self.paths))

    def test_corrupt_completed_artifact_preserves_last_good_output(self):
        self.add_manifest(
            "book-1",
            "opiekun-medyczny",
            [("book-1_chunk", "validated", [self.question("Valid question?", str(uuid4()))])],
        )
        first = rebuild_category("opiekun-medyczny", self.paths)
        last_good = first.output_path.read_bytes()
        (self.paths.outputs_dir / "book-1_chunk_validated.json").write_text(
            "{broken", encoding="utf-8"
        )

        with self.assertRaises(CategoryMergeError):
            rebuild_category("opiekun-medyczny", self.paths)

        self.assertEqual(first.output_path.read_bytes(), last_good)

    def test_changed_release_snapshots_previous_output(self):
        self.add_manifest(
            "book-1",
            "pielegniarstwo-test",
            [("book-1_chunk", "validated", [self.question("First question?", str(uuid4()))])],
        )
        first = rebuild_category("pielegniarstwo-test", self.paths)
        first_bytes = first.output_path.read_bytes()
        self.add_manifest(
            "book-2",
            "pielegniarstwo-test",
            [("book-2_chunk", "validated", [self.question("Second question?", str(uuid4()))])],
        )

        second = rebuild_category("pielegniarstwo-test", self.paths)
        history = list(
            (self.paths.done_dir / "history" / "pielegniarstwo-test_all_merged").glob("*.json")
        )

        self.assertTrue(second.changed)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), first_bytes)

    def test_output_tampering_invalidates_release(self):
        self.add_manifest(
            "book-1",
            "opiekun-medyczny",
            [("book-1_chunk", "validated", [self.question("Valid question?", str(uuid4()))])],
        )
        result = rebuild_category("opiekun-medyczny", self.paths)
        result.output_path.write_text("[]", encoding="utf-8")
        self.assertFalse(release_is_current("opiekun-medyczny", self.paths))

    def test_sanitizes_missing_visual_references_and_drops_unresolved_questions(self):
        self.add_manifest(
            "book-1",
            "opiekun-medyczny",
            [
                (
                    "book-1_chunk",
                    "validated",
                    [
                        self.question(
                            "Na podstawie poniższej tabeli, jakie działanie należy wykonać?"
                        ),
                        self.question("Który wynik pokazano na rycinie diagnostycznej?"),
                        self.question("Jak odczytać wynik z tablicy Snellena?"),
                    ],
                )
            ],
        )

        result = rebuild_category("opiekun-medyczny", self.paths)
        entries = json.loads(result.output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.question_count, 2)
        self.assertEqual(
            entries[0]["data"]["question"], "Jakie działanie należy wykonać?"
        )
        self.assertEqual(
            entries[1]["data"]["question"], "Jak odczytać wynik z tablicy Snellena?"
        )

    def test_category_lock_serializes_writers(self):
        events = []

        def first_writer():
            with _category_lock("opiekun-medyczny", self.paths.done_dir):
                events.append("first-start")
                time.sleep(0.2)
                events.append("first-end")

        def second_writer():
            time.sleep(0.02)
            with _category_lock("opiekun-medyczny", self.paths.done_dir):
                events.append("second-start")
                events.append("second-end")

        first = threading.Thread(target=first_writer)
        second = threading.Thread(target=second_writer)
        first.start()
        second.start()
        first.join()
        second.join()

        self.assertEqual(
            events, ["first-start", "first-end", "second-start", "second-end"]
        )


if __name__ == "__main__":
    unittest.main()
