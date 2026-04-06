"""t9-ipc configuration — paths and optional integrations.

All paths are derived from the project root. No hardcoded absolute paths.
API keys are loaded from environment variables only.
"""
import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────
# PROJECT_ROOT is the parent of this lib/ directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Database: project-local SQLite. Override with IPC_DB_PATH env var.
DB_PATH = Path(os.environ.get("IPC_DB_PATH", str(PROJECT_ROOT / ".t9_ipc.db")))

# IPC directories
IPC_DIR = PROJECT_ROOT / "data" / "ipc"
IPC_INBOX = IPC_DIR / "inbox"
IPC_ARCHIVE = IPC_DIR / "archive"
HEARTBEATS_FILE = IPC_DIR / "heartbeats.json"

# Session file: tracks current session ID
SESSION_FILE = Path(os.environ.get(
    "IPC_SESSION_FILE",
    str(Path.home() / ".t9_ipc_session")
))

# ─── Optional: Telegram notification ─────────────────────────
TG_TOKEN = os.environ.get("IPC_TG_TOKEN", "")
TG_CHAT = os.environ.get("IPC_TG_CHAT", "")
