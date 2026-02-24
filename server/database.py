"""SQLite 데이터베이스 — aiosqlite 기반 비동기 저장소."""
import os
import time
import uuid
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "claude-web.db"
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
MAX_BLOB_SIZE = 10 * 1024 * 1024  # 10MB

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(str(DB_PATH))
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db():
    """테이블 생성 + FTS 설정."""
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user TEXT NOT NULL,
            title TEXT DEFAULT '새 대화',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            elapsed REAL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY,
            message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            mime_type TEXT,
            size INTEGER,
            data BLOB,
            file_path TEXT,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_id);
        CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user);
        CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);

        CREATE TABLE IF NOT EXISTS conv_sessions (
            conversation_id TEXT PRIMARY KEY,
            claude_session_id TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
    """)

    # FTS table
    await db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
        USING fts5(content, conversation_id UNINDEXED, content='messages', content_rowid='id')
    """)

    # FTS triggers
    for trigger_sql in [
        """CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content, conversation_id)
            VALUES (new.id, new.content, new.conversation_id);
        END""",
        """CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, conversation_id)
            VALUES ('delete', old.id, old.content, old.conversation_id);
        END""",
        """CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, conversation_id)
            VALUES ('delete', old.id, old.content, old.conversation_id);
            INSERT INTO messages_fts(rowid, content, conversation_id)
            VALUES (new.id, new.content, new.conversation_id);
        END""",
    ]:
        await db.execute(trigger_sql)

    await db.commit()
    logger.info("SQLite DB 초기화 완료: %s", DB_PATH)


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


# ── Session Mapping (conversation_id → Claude CLI session_id) ──


async def save_session_mapping(conversation_id: str, claude_session_id: str):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO conv_sessions (conversation_id, claude_session_id, updated_at) VALUES (?, ?, ?)",
        (conversation_id, claude_session_id, time.time()),
    )
    await db.commit()


async def get_session_mapping(conversation_id: str) -> str | None:
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT claude_session_id FROM conv_sessions WHERE conversation_id = ?",
        (conversation_id,),
    )
    return row[0][0] if row else None


# ── Conversations ──────────────────────────────────


async def save_conversation(conv_id: str, user: str, title: str = "새 대화") -> str:
    db = await get_db()
    now = time.time()
    await db.execute(
        "INSERT OR REPLACE INTO conversations (id, user, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, user, title, now, now),
    )
    await db.commit()
    return conv_id


async def update_conversation_title(conv_id: str, title: str):
    db = await get_db()
    await db.execute(
        "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
        (title, time.time(), conv_id),
    )
    await db.commit()


async def get_conversations(user: str) -> list[dict]:
    try:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id, user, title, created_at, updated_at FROM conversations WHERE user=? ORDER BY updated_at DESC LIMIT 100",
            (user,),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("대화 목록 조회 실패: %s", e)
        return []


async def delete_conversation(conv_id: str, user: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM conversations WHERE id=? AND user=?", (conv_id, user)
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_all_conversations(user: str):
    db = await get_db()
    await db.execute("DELETE FROM conversations WHERE user=?", (user,))
    await db.commit()


# ── Messages ───────────────────────────────────────


async def save_message(conversation_id: str, role: str, content: str, elapsed: float | None = None) -> int:
    db = await get_db()
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO messages (conversation_id, role, content, elapsed, created_at) VALUES (?, ?, ?, ?, ?)",
        (conversation_id, role, content, elapsed, now),
    )
    await db.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id)
    )
    await db.commit()
    return cursor.lastrowid


async def get_messages(conversation_id: str) -> list[dict]:
    try:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT id, conversation_id, role, content, elapsed, created_at FROM messages WHERE conversation_id=? ORDER BY id LIMIT 1000",
            (conversation_id,),
        )
        result = []
        for r in rows:
            msg = dict(r)
            # Load attachments
            try:
                att_rows = await db.execute_fetchall(
                    "SELECT id, filename, original_name, mime_type, size FROM attachments WHERE message_id=?",
                    (r["id"],),
                )
                msg["files"] = [dict(a) for a in att_rows] if att_rows else []
            except Exception as e:
                logger.warning("첨부파일 로드 실패 (message_id=%s): %s", r["id"], e)
                msg["files"] = []
            result.append(msg)
        return result
    except Exception as e:
        logger.error("메시지 조회 실패 (conv_id=%s): %s", conversation_id, e)
        return []


# ── Attachments ────────────────────────────────────


async def save_attachment(
    message_id: int, filename: str, original_name: str,
    mime_type: str | None, size: int, data: bytes | None = None, file_path: str | None = None,
) -> str:
    db = await get_db()
    att_id = uuid.uuid4().hex[:12]
    await db.execute(
        "INSERT INTO attachments (id, message_id, filename, original_name, mime_type, size, data, file_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (att_id, message_id, filename, original_name, mime_type, size, data, file_path, time.time()),
    )
    await db.commit()
    return att_id


async def get_attachment(att_id: str) -> dict | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM attachments WHERE id=?", (att_id,)
    )
    return dict(rows[0]) if rows else None


# ── Search ─────────────────────────────────────────


async def search_conversations(user: str, query: str) -> list[dict]:
    """FTS로 메시지 내용 검색, 대화 단위로 그룹핑."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """
        SELECT DISTINCT c.id, c.title, c.created_at, c.updated_at,
               snippet(messages_fts, 0, '<mark>', '</mark>', '...', 40) as snippet
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user = ? AND messages_fts MATCH ?
        ORDER BY c.updated_at DESC
        LIMIT 50
        """,
        (user, query),
    )
    return [dict(r) for r in rows]
