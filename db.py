import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path("bot.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                media_type TEXT NOT NULL,
                url TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        # На случай если downloads уже существовала до появления колонки url
        # (база создана более старой версией бота).
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(downloads)")
        }

        if "url" not in columns:
            conn.execute("ALTER TABLE downloads ADD COLUMN url TEXT")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def touch_user(user_id: int, username: Optional[str], first_name: Optional[str]):
    now = _now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
            """,
            (user_id, username, first_name, now, now),
        )


def log_download(user_id: int, platform: str, media_type: str, url: str):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO downloads (user_id, platform, media_type, url, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, platform, media_type, url, _now()),
        )


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def get_stats() -> dict:
    with _connect() as conn:
        total_users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        new_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (_since(1),)
        ).fetchone()[0]

        new_week = conn.execute(
            "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (_since(7),)
        ).fetchone()[0]

        active_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (_since(1),)
        ).fetchone()[0]

        active_week = conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (_since(7),)
        ).fetchone()[0]

        total_downloads = conn.execute(
            "SELECT COUNT(*) FROM downloads"
        ).fetchone()[0]

        downloads_today = conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (_since(1),)
        ).fetchone()[0]

        by_platform = conn.execute(
            "SELECT platform, COUNT(*) AS cnt FROM downloads GROUP BY platform"
        ).fetchall()

        by_type = conn.execute(
            "SELECT media_type, COUNT(*) AS cnt FROM downloads GROUP BY media_type"
        ).fetchall()

    return {
        "total_users": total_users,
        "new_today": new_today,
        "new_week": new_week,
        "active_today": active_today,
        "active_week": active_week,
        "total_downloads": total_downloads,
        "downloads_today": downloads_today,
        "by_platform": {row["platform"]: row["cnt"] for row in by_platform},
        "by_type": {row["media_type"]: row["cnt"] for row in by_type},
    }


def get_recent_users(limit: int = 20) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """
            SELECT user_id, username, first_name, first_seen, last_seen
            FROM users
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_user_downloads(user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """
            SELECT platform, media_type, url, created_at
            FROM downloads
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()


def get_top_users(limit: int = 10) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """
            SELECT u.user_id, u.username, u.first_name, COUNT(d.id) AS downloads
            FROM users u
            JOIN downloads d ON d.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY downloads DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
