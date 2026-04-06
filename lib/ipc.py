"""t9-ipc: Inter-Session Communication for Claude Code

File = truth, DB = cache. Messages are stored as markdown files.
Heartbeat-based session discovery with PID liveness checks.

Designed for Claude Code multi-session orchestration via MCP.
"""
import sqlite3
import os
import json
import re
import uuid
import fcntl
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from lib.config import (
    DB_PATH, IPC_DIR, IPC_INBOX, IPC_ARCHIVE,
    HEARTBEATS_FILE, SESSION_FILE, PROJECT_ROOT,
)

# Ensure directories exist
IPC_INBOX.mkdir(parents=True, exist_ok=True)
IPC_ARCHIVE.mkdir(parents=True, exist_ok=True)

# ─── Database ─────────────────────────────────────────────────

_tables_initialized = False
_DB_BOOTSTRAP_LOCK = DB_PATH.parent / ".t9_ipc.db.bootlock"


def _bootstrap_db() -> None:
    """First-open DB bootstrap: WAL mode + tables, serialized across
    concurrent processes via a filesystem lock. Concurrent cold starts
    were racing on PRAGMA journal_mode=WAL and raising
    sqlite3.OperationalError: database is locked.
    """
    global _tables_initialized
    if _tables_initialized:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DB_BOOTSTRAP_LOCK.touch(exist_ok=True)
    with open(_DB_BOOTSTRAP_LOCK, "r+") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=30)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                _ensure_tables(conn)
                conn.commit()
            finally:
                conn.close()
            _tables_initialized = True
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


@contextmanager
def _db():
    """SQLite connection with automatic cleanup."""
    _bootstrap_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def _ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        status TEXT DEFAULT 'active',
        working_on TEXT DEFAULT '',
        pid INTEGER,
        claimed_project TEXT DEFAULT '',
        capacity TEXT DEFAULT 'idle'
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_session TEXT NOT NULL,
        to_session TEXT,
        type TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'normal',
        created_at TEXT NOT NULL,
        read_at TEXT,
        expires_at TEXT
    );
    CREATE TABLE IF NOT EXISTS file_locks (
        filepath TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        locked_at TEXT NOT NULL,
        operation TEXT DEFAULT 'edit',
        owner_pid INTEGER
    );
    """)
    # Migrate old schema (add owner_pid column if missing)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(file_locks)").fetchall()]
    if "owner_pid" not in cols:
        conn.execute("ALTER TABLE file_locks ADD COLUMN owner_pid INTEGER")
    conn.commit()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sanitize(text: str, max_len: int = 200) -> str:
    """Remove newlines and truncate for safe use in frontmatter/filenames."""
    return text.replace('\n', ' ').replace('\r', '')[:max_len]


# ─── File-based Messages + Heartbeat ─────────────────────────

def _save_msg_file(from_sid: str, to_sid: str, msg_type: str, subject: str,
                   body: str = "", priority: str = "normal") -> Path:
    """Save message as a file (truth). Returns the file path.

    Filename includes microsecond timestamp + uuid4 suffix to prevent
    collision when the same (from, to, subject) triple is sent multiple
    times in the same day.
    """
    now = datetime.now()
    ts = now.strftime("%Y%m%dT%H%M%S%f")
    uid = uuid.uuid4().hex[:8]
    from_short = re.sub(r'[^\w\-]', '_', (from_sid or "system")[:16])
    to_short = re.sub(
        r'[^\w\-]', '_',
        "all" if not to_sid or to_sid == "all" else to_sid[:16]
    )
    safe_subject = re.sub(r'[^\w\-]', '_', subject)[:40]
    filename = f"{ts}-{uid}-{from_short}-{to_short}-{safe_subject}.md"
    filepath = IPC_INBOX / filename

    safe_subj = _sanitize(subject)
    content = f"""---
from: {_sanitize(from_sid, 64)}
to: {_sanitize(to_sid or 'broadcast', 64)}
type: {msg_type}
subject: {safe_subj}
priority: {priority}
created: {_now()}
---

{body[:10240]}
"""
    # Atomic write via temp + rename (unique temp name)
    tmp = filepath.with_suffix(f".tmp.{uid}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, filepath)
    return filepath


_HEARTBEAT_LOCK = HEARTBEATS_FILE.with_suffix(".lock")

# Session ID validation — any reasonable alphanumeric identifier
_VALID_SESSION_ID = re.compile(r"^[\w\-]{3,64}$")


def heartbeat_update(session_id: str, status: str = "active",
                     pid: int | None = None,
                     working_on: str | None = None) -> None:
    """Update heartbeat file. Called automatically on every tool invocation.

    Concurrent-safe via fcntl.flock on a sidecar lock file. Writers
    serialize on the lock, read-modify-write the JSON, then atomically
    replace the file using a unique temp path.
    """
    if pid is None:
        pid = os.getpid()

    # Ensure lock file exists
    _HEARTBEAT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    _HEARTBEAT_LOCK.touch(exist_ok=True)

    with open(_HEARTBEAT_LOCK, "r+") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            data: dict = {}
            if HEARTBEATS_FILE.exists():
                try:
                    data = json.loads(HEARTBEATS_FILE.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = {}

            existing = data.get(session_id, {})
            data[session_id] = {
                "timestamp": _now(),
                "status": status,
                "pid": pid,
                "working_on": working_on or existing.get("working_on", ""),
            }

            # Clean stale sessions
            stale_cutoff = (datetime.now() - timedelta(hours=24)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            cleaned = {}
            for k, v in data.items():
                if not _VALID_SESSION_ID.match(k):
                    continue
                if v.get("status") == "signing-off":
                    continue
                ts = v.get("timestamp", "")
                if ts and ts < stale_cutoff:
                    continue
                if k != session_id:
                    entry_pid = v.get("pid")
                    if entry_pid and not _pid_alive(entry_pid):
                        continue
                cleaned[k] = v

            # Atomic write via unique temp + os.replace
            tmp = HEARTBEATS_FILE.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
            tmp.write_text(
                json.dumps(cleaned, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            os.replace(tmp, HEARTBEATS_FILE)
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


def heartbeat_who() -> list[dict]:
    """List alive sessions based on heartbeat file."""
    if not HEARTBEATS_FILE.exists():
        return []
    try:
        data = json.loads(HEARTBEATS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    alive = []
    for sid, info in data.items():
        if info.get("status") == "signing-off":
            continue
        pid = info.get("pid")
        if pid and not _pid_alive(pid):
            continue
        alive.append({"session_id": sid, **info})
    return alive


def inbox_unread(session_id: str) -> list[dict]:
    """Scan inbox for messages addressed to this session or broadcast.

    Routing is determined from the frontmatter `to:` field and requires
    an exact session_id match (or 'all' / 'broadcast'). Prefix matching
    was removed because it leaked messages across sessions that shared
    the same leading characters.
    """
    if not IPC_INBOX.exists():
        return []
    unread = []
    for f in sorted(IPC_INBOX.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        to_field = ""
        subject = ""
        in_front = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "---":
                if in_front:
                    break
                in_front = True
                continue
            if not in_front:
                continue
            if stripped.startswith("to:"):
                to_field = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("subject:"):
                subject = stripped.split(":", 1)[1].strip()
        if not to_field:
            continue
        if to_field in ("all", "broadcast", session_id):
            unread.append({
                "file": f.name, "path": str(f),
                "to": to_field, "subject": subject
            })
    return unread


def _pid_alive(pid) -> bool:
    """Check if a PID is alive (Linux/WSL/macOS)."""
    if pid is None:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ─── Session Management ──────────────────────────────────────

def session_register(session_id: str, pid: int) -> None:
    """Register a new session."""
    with _db() as conn:
        _cleanup_stale(conn)
        conn.execute(
            "INSERT OR REPLACE INTO sessions (id, started_at, status, pid) "
            "VALUES (?,?,?,?)",
            (session_id, _now(), "active", pid)
        )
        conn.commit()
    SESSION_FILE.write_text(session_id)


def session_end(session_id: str) -> int:
    """End a session and release all its locks. Returns released lock count."""
    with _db() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at=?, status='ended' WHERE id=?",
            (_now(), session_id)
        )
        released = conn.execute(
            "DELETE FROM file_locks WHERE session_id=?", (session_id,)
        ).rowcount
        conn.commit()
    return released


def session_list() -> dict:
    """List active sessions and locks."""
    with _db() as conn:
        _cleanup_stale(conn)
        rows = conn.execute(
            "SELECT id, started_at, status, working_on, pid "
            "FROM sessions WHERE status='active' ORDER BY started_at"
        ).fetchall()
        locks = conn.execute(
            "SELECT filepath, session_id FROM file_locks"
        ).fetchall()
    return {
        "sessions": [
            {"id": r[0], "started_at": r[1], "working_on": r[3], "pid": r[4]}
            for r in rows
        ],
        "locks": [{"filepath": fp, "session_id": sid} for fp, sid in locks],
    }


def _cleanup_stale(conn):
    """Clean crashed sessions and locks whose owner process is dead.

    Locks are reclaimed by owner_pid liveness, not by session-table
    membership. Unregistered lock holders (which are allowed — see
    lock_acquire) are never erased as long as their PID is alive.
    """
    # Mark crashed sessions
    active = conn.execute(
        "SELECT id, pid FROM sessions WHERE status='active'"
    ).fetchall()
    for sid, pid in active:
        if pid and not _pid_alive(pid):
            conn.execute(
                "UPDATE sessions SET status='crashed', ended_at=? WHERE id=?",
                (_now(), sid)
            )

    # Reclaim locks whose owner PID is dead. Legacy rows with NULL
    # owner_pid (pre-migration) fall back to the session table.
    rows = conn.execute(
        "SELECT filepath, session_id, owner_pid FROM file_locks"
    ).fetchall()
    for fp, sid, owner_pid in rows:
        if owner_pid is not None:
            if not _pid_alive(owner_pid):
                conn.execute("DELETE FROM file_locks WHERE filepath=?", (fp,))
        else:
            sess = conn.execute(
                "SELECT status, pid FROM sessions WHERE id=?", (sid,)
            ).fetchone()
            if sess is None:
                continue  # unregistered legacy lock — leave alone
            status, pid = sess
            if status in ("ended", "crashed"):
                conn.execute("DELETE FROM file_locks WHERE filepath=?", (fp,))
            elif pid and not _pid_alive(pid):
                conn.execute("DELETE FROM file_locks WHERE filepath=?", (fp,))
    conn.commit()


# ─── Messages ────────────────────────────────────────────────

def msg_send(from_sid: str, to_sid: str, msg_type: str, subject: str,
             body: str = "", priority: str = "normal") -> None:
    """Send a message. File = truth, DB = cache.

    The file is written first. Only if the write succeeds do we update
    the DB cache. This preserves the "file = truth" contract: any row
    in the DB is backed by an actual inbox file.
    """
    # File first — this is the canonical truth
    _save_msg_file(from_sid, to_sid, msg_type, subject, body, priority)

    # Then DB cache
    expires = None
    if msg_type in ("lock", "unlock", "alert"):
        expires = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    elif msg_type == "work_progress":
        expires = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        conn.execute(
            "INSERT INTO messages (from_session, to_session, type, subject, "
            "body, priority, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (from_sid, to_sid if to_sid != "all" else None, msg_type,
             subject, body, priority, _now(), expires)
        )
        conn.commit()

    # Optional escalation notification
    try:
        from lib.notify import notify_escalation
        if msg_type == "escalation":
            notify_escalation(subject, body)
    except ImportError:
        pass


def msg_check(session_id: str) -> list[dict]:
    """Check pending messages for a session."""
    with _db() as conn:
        conn.execute(
            "UPDATE messages SET status='expired' "
            "WHERE expires_at IS NOT NULL AND expires_at < ? "
            "AND status='pending'",
            (_now(),)
        )
        conn.commit()
        rows = conn.execute("""
            SELECT id, from_session, type, subject, body, priority, created_at
            FROM messages
            WHERE status='pending'
              AND (to_session=? OR to_session IS NULL)
            ORDER BY CASE priority
                WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2
            END, created_at
        """, (session_id,)).fetchall()
    return [
        {"id": r[0], "from": r[1], "type": r[2], "subject": r[3],
         "body": r[4], "priority": r[5], "created_at": r[6]}
        for r in rows
    ]


def msg_read(msg_id: int, session_id: str) -> None:
    """Mark a message as read (only if addressed to this session)."""
    with _db() as conn:
        conn.execute(
            "UPDATE messages SET status='read', read_at=? "
            "WHERE id=? AND (to_session=? OR to_session IS NULL)",
            (_now(), msg_id, session_id)
        )
        conn.commit()


def msg_act(msg_id: int, session_id: str) -> None:
    """Mark a message as acted upon (only if addressed to this session)."""
    with _db() as conn:
        conn.execute(
            "UPDATE messages SET status='acted', read_at=? "
            "WHERE id=? AND (to_session=? OR to_session IS NULL)",
            (_now(), msg_id, session_id)
        )
        conn.commit()


# ─── File Locks ──────────────────────────────────────────────

def _canonical_path(filepath: str) -> str | None:
    """Resolve to canonical path and reject anything outside PROJECT_ROOT.

    Returns the canonical string, or None if the path is invalid or
    escapes the project root (path traversal prevention).
    """
    try:
        resolved = Path(filepath).resolve()
    except (ValueError, OSError):
        return None
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return None
    return str(resolved)


def lock_acquire(session_id: str, filepath: str,
                 operation: str = "edit") -> bool:
    """Acquire a file lock. Returns True if successful.

    Uses BEGIN IMMEDIATE + INSERT OR IGNORE for atomic acquisition.
    Concurrent callers cannot both win the same lock.

    The owner PID is stored directly on file_locks, so stale locks
    from crashed processes can be reclaimed without requiring a row
    in the sessions table. session_register() is not required.

    filepath is canonicalized and must be inside PROJECT_ROOT.
    """
    canonical = _canonical_path(filepath)
    if canonical is None:
        return False

    my_pid = os.getpid()

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT session_id, owner_pid FROM file_locks WHERE filepath=?",
                (canonical,)
            ).fetchone()
            if existing:
                if existing[0] == session_id:
                    conn.execute("COMMIT")
                    return True
                # Stale if owner PID is dead
                owner_pid = existing[1]
                if owner_pid and not _pid_alive(owner_pid):
                    conn.execute(
                        "DELETE FROM file_locks WHERE filepath=?", (canonical,)
                    )
                else:
                    conn.execute("COMMIT")
                    return False
            cur = conn.execute(
                "INSERT OR IGNORE INTO file_locks "
                "(filepath, session_id, locked_at, operation, owner_pid) "
                "VALUES (?,?,?,?,?)",
                (canonical, session_id, _now(), operation, my_pid)
            )
            won = cur.rowcount == 1
            conn.execute("COMMIT")
            return won
        except Exception:
            conn.execute("ROLLBACK")
            raise


def lock_release(session_id: str, filepath: str) -> None:
    """Release a file lock."""
    canonical = _canonical_path(filepath)
    if canonical is None:
        return
    with _db() as conn:
        conn.execute(
            "DELETE FROM file_locks WHERE filepath=? AND session_id=?",
            (canonical, session_id)
        )
        conn.commit()


def lock_check(filepath: str) -> dict | None:
    """Check if a file is locked. Returns lock info or None."""
    canonical = _canonical_path(filepath)
    if canonical is None:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT session_id, locked_at, operation "
            "FROM file_locks WHERE filepath=?",
            (canonical,)
        ).fetchone()
    if row:
        return {"session_id": row[0], "locked_at": row[1], "operation": row[2]}
    return None
