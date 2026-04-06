#!/usr/bin/env python3
"""t9-ipc MCP Server — Claude Channels for inter-session communication.

File = truth, DB = cache. Heartbeat-based session discovery.
Pushes real-time notifications via Claude Channels protocol.

Usage (.mcp.json):
    {
      "mcpServers": {
        "t9-ipc": {
          "command": "python3",
          "args": ["mcp/server.py"],
          "cwd": "/path/to/t9-ipc"
        }
      }
    }
"""

import json
import sys
import os
import threading
import time
from pathlib import Path

# Project path setup
MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.ipc import (
    msg_send, heartbeat_update, heartbeat_who, inbox_unread,
    _VALID_SESSION_ID,
)
from lib.config import IPC_INBOX, SESSION_FILE

# Agent name — auto-detect from session file or environment.
# Must match lib/ipc._VALID_SESSION_ID or heartbeat storage drops it.
import datetime as _dt


def _autogen_name() -> str:
    """Generate a valid default session ID."""
    ts = _dt.datetime.now().strftime("%Y%m%d")
    return f"{ts}_{os.getpid()}_{os.urandom(2).hex()}"


def _validate_name(name: str) -> str | None:
    """Return name if valid, else None."""
    if name and _VALID_SESSION_ID.match(name):
        return name
    return None


_candidate = None
if SESSION_FILE.exists():
    _candidate = _validate_name(SESSION_FILE.read_text(encoding="utf-8").strip())
if _candidate is None and os.environ.get("IPC_SESSION_ID"):
    _candidate = _validate_name(os.environ["IPC_SESSION_ID"])
AGENT_NAME = _candidate or _autogen_name()

# Input limits
MAX_SUBJECT = 200
MAX_BODY = 10240
MAX_NAME = 64


# ─── MCP Tool Definitions ────────────────────────────────────

TOOLS = {
    "t9_ipc_send": {
        "description": "Send a message to a specific session. Use to='all' for broadcast.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Target session ID (exact match, use t9_ipc_who to list) or 'all'"},
                "subject": {"type": "string", "description": "Message subject"},
                "body": {"type": "string", "description": "Message body", "default": ""},
                "priority": {"type": "string", "enum": ["normal", "high", "critical"], "default": "normal"},
            },
            "required": ["to", "subject"]
        },
    },
    "t9_ipc_broadcast": {
        "description": "Broadcast a message to all active sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Message subject"},
                "body": {"type": "string", "description": "Message body", "default": ""},
            },
            "required": ["subject"]
        },
    },
    "t9_ipc_who": {
        "description": "List currently active sessions (heartbeat-based).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "t9_ipc_unread": {
        "description": "List unread messages (file-based). Fallback for clients without Channels support.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID (exact match)"},
            },
            "required": ["session_id"]
        },
    },
    "t9_ipc_set_name": {
        "description": "Set this agent's name. Call at session start.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name (session ID)"},
            },
            "required": ["name"]
        },
    },
}


# ─── Tool Handlers ────────────────────────────────────────────

def handle_tool(name: str, arguments: dict) -> str:
    global AGENT_NAME

    # Auto-heartbeat on every tool call
    heartbeat_update(AGENT_NAME)

    if name == "t9_ipc_set_name":
        requested = arguments["name"][:MAX_NAME]
        if not _VALID_SESSION_ID.match(requested):
            return (
                f"Invalid name '{requested}'. Must match "
                f"[A-Za-z0-9_-]{{3,64}}. Current name: {AGENT_NAME}"
            )
        AGENT_NAME = requested
        heartbeat_update(AGENT_NAME)
        return f"Agent name set: {AGENT_NAME}"

    elif name == "t9_ipc_send":
        to = arguments["to"][:MAX_NAME]
        subject = arguments["subject"][:MAX_SUBJECT]
        body = arguments.get("body", "")[:MAX_BODY]
        priority = arguments.get("priority", "normal")
        if priority not in ("normal", "high", "critical"):
            priority = "normal"
        from_sid = AGENT_NAME if AGENT_NAME != "unknown" else "system"
        msg_send(from_sid, to, "ipc", subject, body, priority)
        return f"Message sent -> {to}: {subject}"

    elif name == "t9_ipc_broadcast":
        subject = arguments["subject"][:MAX_SUBJECT]
        body = arguments.get("body", "")[:MAX_BODY]
        from_sid = AGENT_NAME if AGENT_NAME != "unknown" else "system"
        msg_send(from_sid, "all", "broadcast", subject, body)
        return f"Broadcast: {subject}"

    elif name == "t9_ipc_who":
        alive = heartbeat_who()
        if not alive:
            return "No active sessions (heartbeat-based)"
        lines = [f"Active sessions: {len(alive)}"]
        for a in alive:
            lines.append(
                f"  [{a['session_id'][:16]}] "
                f"PID={a.get('pid', '?')} last={a.get('timestamp', '?')}"
            )
        return "\n".join(lines)

    elif name == "t9_ipc_unread":
        sid = arguments["session_id"][:MAX_NAME]
        msgs = inbox_unread(sid)
        if not msgs:
            return f"No unread messages (session {sid})"
        lines = [f"Unread messages: {len(msgs)}"]
        for m in msgs:
            lines.append(f"  {m['file']} — {m['subject']}")
        return "\n".join(lines)

    return f"Unknown tool: {name}"


# ─── fs.watch → Channel Notification ─────────────────────────

def send_channel_notification(content: str, meta: dict = None):
    """Push a Channel notification to the Claude Code session."""
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/claude/channel",
        "params": {"content": content, "meta": meta or {}},
    }
    sys.stdout.write(json.dumps(notification) + "\n")
    sys.stdout.flush()


def _watch_inbox():
    """Watch inbox directory for new files and push Channel notifications."""
    known = set(f.name for f in IPC_INBOX.glob("*.md"))

    while True:
        time.sleep(2)
        try:
            current = set(f.name for f in IPC_INBOX.glob("*.md"))
            new_files = current - known

            for fname in sorted(new_files):
                filepath = IPC_INBOX / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except OSError:
                    continue

                # Parse frontmatter
                from_sid, to_sid, subject = "", "", ""
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith("from:"):
                        from_sid = line.split(":", 1)[1].strip()
                    elif line.startswith("to:"):
                        to_sid = line.split(":", 1)[1].strip()
                    elif line.startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
                    elif line == "---" and from_sid:
                        break

                if not from_sid or not to_sid:
                    continue

                # Skip own messages (exact match)
                if from_sid == AGENT_NAME:
                    continue

                # Only notify if addressed to us or broadcast
                if to_sid in ("all", "broadcast"):
                    pass
                elif to_sid != AGENT_NAME:
                    continue

                send_channel_notification(
                    content=content,
                    meta={
                        "from": from_sid,
                        "to": to_sid,
                        "subject": subject,
                        "filename": fname,
                        "source": "t9-ipc",
                    }
                )

            known = current
        except Exception as e:
            sys.stderr.write(f"[t9-ipc] watch error: {e}\n")


# ─── JSON-RPC Handler ────────────────────────────────────────

def handle_jsonrpc(request: dict) -> dict | None:
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "experimental": {"claude/channel": {}},
                },
                "serverInfo": {"name": "t9-ipc", "version": "0.3.1"},
                "instructions": (
                    "t9-ipc: Inter-session communication for Claude Code. "
                    "Messages from other sessions appear as "
                    '<channel source="t9-ipc"> tags. '
                    "Call t9_ipc_set_name at session start to register."
                ),
            }
        }

    elif method == "notifications/initialized":
        watcher = threading.Thread(target=_watch_inbox, daemon=True)
        watcher.start()
        return None

    elif method == "tools/list":
        tools_list = [
            {"name": n, "description": t["description"],
             "inputSchema": t["inputSchema"]}
            for n, t in TOOLS.items()
        ]
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": tools_list}
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        if tool_name in TOOLS:
            result = handle_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
        }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return None


# ─── Main Loop ────────────────────────────────────────────────

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_jsonrpc(request)
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass
        except Exception as e:
            sys.stderr.write(f"[t9-ipc] error: {e}\n")
            err = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32603, "message": "Internal error"}
            }
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
