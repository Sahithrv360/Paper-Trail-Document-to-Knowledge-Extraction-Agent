"""Small SQLite registry for document identity, versions, and queued work."""

import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class DocumentRegistry:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    last_error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    content_hash TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'process',
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(document_id, content_hash, action)
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "claimed_at" not in columns:  # additive, repeatable migration
                connection.execute("ALTER TABLE jobs ADD COLUMN claimed_at TEXT")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        # WAL lets the watcher read while a job or review writes; writers still serialize.
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            connection.execute("PRAGMA journal_mode=WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def observe(self, path: Path, content_hash: str) -> str:
        """Register a stable file version; unchanged content creates no new job."""
        canonical_path = str(Path(path).resolve())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE path = ?", (canonical_path,)
            ).fetchone()
            if row and row["content_hash"] == content_hash and row["status"] != "deleted":
                return row["document_id"]
            reactivated = bool(row and row["status"] == "deleted")

            if row:
                document_id = row["document_id"]
                version = row["version"] + 1
                connection.execute(
                    "UPDATE documents SET content_hash=?, version=?, status='queued', "
                    "last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
                    (content_hash, version, document_id),
                )
            else:
                # Reuse identity when a uniquely deleted document is moved and unchanged.
                matches = connection.execute(
                    "SELECT document_id FROM documents WHERE content_hash=? AND status='deleted'",
                    (content_hash,),
                ).fetchall()
                document_id = matches[0]["document_id"] if len(matches) == 1 else str(uuid.uuid4())
                if len(matches) == 1:
                    reactivated = True
                    connection.execute(
                        "UPDATE documents SET path=?, status='queued', last_error=NULL, "
                        "updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
                        (canonical_path, document_id),
                    )
                    # A rename is detected as a delete followed by a stable new path.
                    # Cancel its not-yet-started tombstone when the file reappears.
                    connection.execute(
                        "UPDATE jobs SET status='cancelled' WHERE document_id=? "
                        "AND action='delete' AND status='queued'",
                        (document_id,),
                    )
                else:
                    connection.execute(
                        "INSERT INTO documents(document_id,path,content_hash,version,status) "
                        "VALUES(?,?,?,1,'queued')",
                        (document_id, canonical_path, content_hash),
                    )
            connection.execute(
                "INSERT OR IGNORE INTO jobs(document_id,content_hash,action) VALUES(?,?,'process')",
                (document_id, content_hash),
            )
            if reactivated:
                # The (document, hash, action) uniqueness would otherwise swallow the job
                # for a file that was deleted and then restored unchanged.
                connection.execute(
                    "UPDATE jobs SET status='queued', attempts=0, last_error=NULL "
                    "WHERE document_id=? AND content_hash=? AND action='process' "
                    "AND status IN ('complete','failed','cancelled')",
                    (document_id, content_hash),
                )
            return document_id

    def mark_missing(self, live_paths: set[str]) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id,path FROM documents WHERE status != 'deleted'"
            ).fetchall()
            for row in rows:
                if row["path"] not in live_paths:
                    connection.execute(
                        "UPDATE documents SET status='deleted', updated_at=CURRENT_TIMESTAMP "
                        "WHERE document_id=?",
                        (row["document_id"],),
                    )
                    delete_hash = self._hash_for(connection, row["document_id"])
                    connection.execute(
                        "INSERT OR IGNORE INTO jobs(document_id,content_hash,action,status) "
                        "VALUES(?,?,'delete','queued')",
                        (row["document_id"], delete_hash),
                    )
                    # A second deletion of the same content must not be swallowed.
                    connection.execute(
                        "UPDATE jobs SET status='queued', attempts=0, last_error=NULL "
                        "WHERE document_id=? AND content_hash=? AND action='delete' "
                        "AND status IN ('complete','failed','cancelled')",
                        (row["document_id"], delete_hash),
                    )

    @staticmethod
    def _hash_for(connection, document_id):
        row = connection.execute(
            "SELECT content_hash FROM documents WHERE document_id=?", (document_id,)
        ).fetchone()
        return row["content_hash"]

    def get_document(self, document_id: str):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id=?", (document_id,)
            ).fetchone()
            return dict(row) if row else None

    def documents(self):
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM documents ORDER BY path")]

    def jobs(self):
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY job_id")]

    def find_document_by_path(self, path: Path):
        canonical_path = str(Path(path).resolve())
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM documents WHERE path=?", (canonical_path,)).fetchone()
            return dict(row) if row else None

    def enqueue_reprocess(self, document_id: str) -> int:
        """Queue an explicit same-version rerun without fabricating a new document version."""
        with self._connect() as connection:
            document = connection.execute(
                "SELECT * FROM documents WHERE document_id=?", (document_id,)
            ).fetchone()
            if document is None:
                raise KeyError(f"Unknown document: {document_id}")
            if document["status"] == "deleted":
                raise ValueError("Cannot reprocess a deleted document")
            connection.execute(
                "INSERT OR IGNORE INTO jobs(document_id,content_hash,action) VALUES(?,?,'reprocess')",
                (document_id, document["content_hash"]),
            )
            job = connection.execute(
                "SELECT * FROM jobs WHERE document_id=? AND content_hash=? AND action='reprocess'",
                (document_id, document["content_hash"]),
            ).fetchone()
            if job["status"] in {"complete", "failed", "cancelled"}:
                connection.execute(
                    "UPDATE jobs SET status='queued', attempts=0, last_error=NULL WHERE job_id=?",
                    (job["job_id"],),
                )
            connection.execute(
                "UPDATE documents SET status='queued', last_error=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE document_id=? AND status!='deleted'", (document_id,)
            )
            return job["job_id"]

    def claim_next_job(self):
        """Atomically claim the oldest queued job and persist its processing state."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY job_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET status='processing', attempts=attempts+1, "
                "claimed_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (row["job_id"],),
            )
            if row["action"] in {"process", "reprocess"}:
                connection.execute(
                    "UPDATE documents SET status='processing', updated_at=CURRENT_TIMESTAMP "
                    "WHERE document_id=? AND content_hash=?",
                    (row["document_id"], row["content_hash"]),
                )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)
            ).fetchone()
            return dict(claimed)

    def complete_job(self, job_id: int) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown job: {job_id}")
            connection.execute(
                "UPDATE jobs SET status='complete', last_error=NULL WHERE job_id=?",
                (job_id,),
            )
            if row["action"] in {"process", "reprocess"}:
                connection.execute(
                    "UPDATE documents SET status='complete', updated_at=CURRENT_TIMESTAMP "
                    "WHERE document_id=? AND content_hash=? AND status!='deleted'",
                    (row["document_id"], row["content_hash"]),
                )

    def recover_interrupted_jobs(self, max_attempts: int = 3,
                                 older_than_seconds: float = None) -> int:
        """Return jobs left in ``processing`` by a crash to the queue (or fail them).

        With a single worker, every ``processing`` job at start-up was interrupted. With
        ``older_than_seconds`` only claims older than that are recovered.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM jobs WHERE status='processing'"
            args = ()
            if older_than_seconds is not None:
                query += (" AND (claimed_at IS NULL OR "
                          "claimed_at <= datetime('now', ?))")
                args = (f"-{int(older_than_seconds)} seconds",)
            rows = connection.execute(query, args).fetchall()
            for row in rows:
                next_status = "queued" if row["attempts"] < max_attempts else "failed"
                error = "interrupted while processing (recovered at start-up)"
                connection.execute("UPDATE jobs SET status=?, last_error=? WHERE job_id=?",
                                   (next_status, error, row["job_id"]))
                if row["action"] in {"process", "reprocess"}:
                    connection.execute(
                        "UPDATE documents SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE document_id=? AND content_hash=? AND status='processing'",
                        (next_status, error, row["document_id"], row["content_hash"]))
            return len(rows)

    def fail_job(self, job_id: int, error: str, max_attempts: int = 3) -> str:
        """Persist a failure and return it to the queue until attempts are exhausted."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown job: {job_id}")
            next_status = "queued" if row["attempts"] < max_attempts else "failed"
            connection.execute(
                "UPDATE jobs SET status=?, last_error=? WHERE job_id=?",
                (next_status, error, job_id),
            )
            if row["action"] in {"process", "reprocess"}:
                connection.execute(
                    "UPDATE documents SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE document_id=? AND content_hash=? AND status!='deleted'",
                    (next_status, error, row["document_id"], row["content_hash"]),
                )
            return next_status
