import json
import os
import tempfile
import time
import unittest

from move_store import MoveStore


class MovePersistencePerformanceTest(unittest.TestCase):
    """Regression benchmark for the persistence portion of the /move endpoint."""

    def test_large_history_append_reduces_io_and_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = os.path.join(directory, "phase_history.json")
            key = "p1|montar|"
            event = {"timestamp": "2026-07-28T10:00:00", "from_day": "2026-07-27",
                     "to_day": "2026-07-28", "from_worker": "A", "to_worker": "B"}
            history = {key: [dict(event, timestamp=f"2025-01-01T00:{i % 60:02}:00")
                             for i in range(50_000)]}
            with open(history_path, "w", encoding="utf-8") as fh:
                json.dump(history, fh)

            # Baseline used by the former endpoint: whole-file read + rewrite.
            started = time.perf_counter()
            with open(history_path, encoding="utf-8") as fh:
                legacy = json.load(fh)
            legacy[key].append(event)
            with open(history_path, "w", encoding="utf-8") as fh:
                json.dump(legacy, fh)
            legacy_seconds = time.perf_counter() - started
            legacy_io = 2

            store = MoveStore(directory)  # one-time migration is outside requests
            started = time.perf_counter()
            with store.transaction() as conn:
                store.append_phase(conn, "p1", "montar", None, event)
            sqlite_seconds = time.perf_counter() - started
            sqlite_io = 1

            self.assertLess(sqlite_io, legacy_io)
            self.assertLess(sqlite_seconds, legacy_seconds)
            self.assertEqual(len(store.phase_history("p1", "montar")), 50_002)

    def test_project_tracker_and_history_rollback_together(self):
        with tempfile.TemporaryDirectory() as directory:
            projects_path = os.path.join(directory, "projects.json")
            original = [{"id": "p1", "phases": {"montar": 8}}]
            with open(projects_path, "w", encoding="utf-8") as fh:
                json.dump(original, fh)
            store = MoveStore(directory)
            event = {"timestamp": "2026-07-28T10:00:00", "phase": "montar"}

            real_append = store.append_phase
            store.append_phase = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk"))
            with self.assertRaises(RuntimeError):
                store.persist_move(projects_path, [{"id": "changed"}], event,
                                   "p1", event, "montar")
            store.append_phase = real_append

            with open(projects_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), original)
            self.assertEqual(store.tracker(), [])
            self.assertEqual(store.phase_history("p1", "montar"), [])


if __name__ == "__main__":
    unittest.main()
