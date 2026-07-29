"""SQLite persistence for project moves.

The old JSON tracker and phase-history files were read and rewritten for every
move.  This store migrates them once and thereafter appends movement records.
"""
import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager


class MoveStore:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "moves.sqlite3")
        os.makedirs(data_dir, exist_ok=True)
        self._initialize()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize(self):
        with closing(self.connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tracker_events (
                    id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                    project_id TEXT, project TEXT, client TEXT, phase TEXT,
                    reason TEXT, affected_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tracker_project_phase
                    ON tracker_events(project_id, phase, id DESC);
                CREATE TABLE IF NOT EXISTS phase_events (
                    id INTEGER PRIMARY KEY, project_id TEXT NOT NULL,
                    phase TEXT NOT NULL, part TEXT NOT NULL DEFAULT '',
                    timestamp TEXT NOT NULL, from_day TEXT, to_day TEXT,
                    from_worker TEXT, to_worker TEXT
                );
                CREATE INDEX IF NOT EXISTS phase_project_phase
                    ON phase_events(project_id, phase, part, id DESC);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    projects_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            """)
            done = conn.execute("SELECT 1 FROM metadata WHERE key='json_migrated'").fetchone()
            if not done:
                self._migrate_json(conn)
                conn.execute("INSERT INTO metadata VALUES ('json_migrated', '1')")

    def _read_json(self, name, default):
        try:
            with open(os.path.join(self.data_dir, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _migrate_json(self, conn):
        for item in self._read_json("tracker.json", []):
            if isinstance(item, dict):
                self.append_tracker(conn, item, item.get("pid"))
        for key, entries in self._read_json("phase_history.json", {}).items():
            try:
                pid, phase, part = key.split("|", 2)
            except ValueError:
                continue
            for item in entries if isinstance(entries, list) else []:
                if isinstance(item, dict):
                    self.append_phase(conn, pid, phase, part, item)

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def append_tracker(self, conn, event, pid=None):
        conn.execute("""INSERT INTO tracker_events
            (timestamp, project_id, project, client, phase, reason, affected_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (
            event.get("timestamp", ""), None if pid is None else str(pid),
            event.get("project", ""), event.get("client", ""),
            event.get("phase", ""), event.get("reason", ""),
            json.dumps(event.get("affected", []), ensure_ascii=False)))

    def append_phase(self, conn, pid, phase, part, event):
        conn.execute("""INSERT INTO phase_events
            (project_id, phase, part, timestamp, from_day, to_day, from_worker, to_worker)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
            str(pid), phase, "" if part in (None, "", "None") else str(part),
            event.get("timestamp", ""), event.get("from_day"), event.get("to_day"),
            event.get("from_worker"), event.get("to_worker")))

    def persist_move(self, projects_file, projects, tracker, pid, phase_event=None,
                     phase=None, part=None):
        """Commit the project document and both events as one SQLite transaction.

        The JSON project file remains the compatibility representation.  It is
        replaced atomically before committing SQLite; failures remove the temp
        file and roll back the event inserts.
        """
        tmp = None
        with self.transaction() as conn:
            self.append_tracker(conn, tracker, pid)
            if phase_event is not None:
                self.append_phase(conn, pid, phase, part, phase_event)
            conn.execute("""INSERT INTO project_state VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                projects_json=excluded.projects_json, updated_at=excluded.updated_at""",
                (json.dumps(projects, ensure_ascii=False), tracker.get("timestamp", "")))
            directory = os.path.dirname(projects_file) or "."
            fd, tmp = tempfile.mkstemp(prefix=".projects-", dir=directory, text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    # Keep the compatibility JSON ASCII-only.  ``schedule.py``
                    # has historically been deployed on Windows and older
                    # versions opened this file with the active ANSI codepage;
                    # literal UTF-8 names (for example "Oficina técnica") were
                    # consequently decoded as "Oficina tÃ©cnica".
                    json.dump(projects, fh, ensure_ascii=True)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, projects_file)
                tmp = None
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)

    def tracker(self):
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT * FROM tracker_events ORDER BY id").fetchall()
        return [{"timestamp": r["timestamp"], "project": r["project"],
                 "client": r["client"], "phase": r["phase"], "reason": r["reason"],
                 "affected": json.loads(r["affected_json"])} for r in rows]

    def phase_history(self, pid, phase, part=None):
        part = "" if part in (None, "", "None") else str(part)
        with closing(self.connect()) as conn:
            rows = conn.execute("""SELECT timestamp, from_day, to_day, from_worker, to_worker
                FROM phase_events WHERE project_id=? AND phase=? AND part=? ORDER BY id DESC""",
                (str(pid), phase, part)).fetchall()
        return [dict(row) for row in rows]
