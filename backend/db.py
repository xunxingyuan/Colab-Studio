"""SQLite (WAL) persistence layer for accounts, settings, runs, receipts, and harvested media."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from backend.models import (
    AccountInfo,
    AppSettings,
    BatchJob,
    DeploymentRun,
    HarvestArtifact,
    UnassignReceipt,
)


class StudioDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    job_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    # ---------- Settings ----------

    def get_settings(self) -> AppSettings:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = 'settings'").fetchone()
            if row:
                return AppSettings.model_validate_json(row["value"])
            return AppSettings()

    def save_settings(self, settings: AppSettings) -> AppSettings:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES ('settings', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (settings.model_dump_json(),),
            )
        return settings

    # ---------- Accounts ----------

    def list_accounts(self) -> list[AccountInfo]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY added_at ASC").fetchall()
            return [
                AccountInfo(
                    id=r["id"],
                    email=r["email"],
                    display_name=r["display_name"],
                    added_at=r["added_at"],
                    is_active=bool(r["is_active"]),
                )
                for r in rows
            ]

    def upsert_account(self, account: AccountInfo) -> None:
        with self._lock, self._connect() as conn:
            if account.is_active:
                conn.execute("UPDATE accounts SET is_active = 0")
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, added_at, is_active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    email = excluded.email,
                    display_name = excluded.display_name,
                    is_active = excluded.is_active
                """,
                (account.id, account.email, account.display_name, account.added_at, int(account.is_active)),
            )

    def set_active_account(self, account_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE accounts SET is_active = 0")
            conn.execute("UPDATE accounts SET is_active = 1 WHERE id = ?", (account_id,))

    def delete_account(self, account_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    # ---------- Runs ----------

    def save_run(self, run: DeploymentRun) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload",
                (run.run_id, run.started_at, run.model_dump_json()),
            )

    def list_runs(self, limit: int = 30) -> list[DeploymentRun]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [DeploymentRun.model_validate_json(r["payload"]) for r in rows]

    def get_run(self, run_id: str) -> Optional[DeploymentRun]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return DeploymentRun.model_validate_json(row["payload"]) if row else None

    # ---------- Receipts ----------

    def save_receipt(self, receipt: UnassignReceipt) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO receipts (receipt_id, timestamp, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(receipt_id) DO UPDATE SET payload = excluded.payload",
                (receipt.receipt_id, receipt.timestamp, receipt.model_dump_json()),
            )

    def list_receipts(self, limit: int = 50) -> list[UnassignReceipt]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM receipts ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            return [UnassignReceipt.model_validate_json(r["payload"]) for r in rows]

    # ---------- Harvested Artifacts ----------

    def save_artifact(self, artifact: HarvestArtifact) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, created_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (artifact.id, artifact.created_at, artifact.model_dump_json()),
            )

    def list_artifacts(self, limit: int = 100) -> list[HarvestArtifact]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM artifacts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [HarvestArtifact.model_validate_json(r["payload"]) for r in rows]

    def get_artifact(self, artifact_id: str) -> Optional[HarvestArtifact]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            return HarvestArtifact.model_validate_json(row["payload"]) if row else None

    def get_secret_names(self) -> list[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = 'secret_names'").fetchone()
            return json.loads(row["value"]) if row else []

    def set_secret_names(self, names: list[str]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES ('secret_names', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(sorted(set(names))),),
            )

    # ---------- Batch Jobs ----------

    def save_batch_job(self, job: BatchJob) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO batch_jobs (job_id, started_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET payload = excluded.payload",
                (job.job_id, job.started_at, job.model_dump_json()),
            )

    def list_batch_jobs(self, limit: int = 30) -> list[BatchJob]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM batch_jobs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [BatchJob.model_validate_json(r["payload"]) for r in rows]

    def get_batch_job(self, job_id: str) -> Optional[BatchJob]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return BatchJob.model_validate_json(row["payload"]) if row else None

