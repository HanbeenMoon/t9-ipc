# t9-ipc

**Inter-session communication for Claude Code.**

When you run multiple Claude Code sessions simultaneously, they can't talk to each other. t9-ipc fixes that — sessions discover each other via heartbeats, exchange messages through files, and get real-time notifications via Claude's MCP Channels protocol.

## How It Works

```
Session A                    Session B
    │                            │
    ├─ heartbeat_update() ──────►│◄── heartbeat_update()
    │                            │
    ├─ msg_send("hello") ──►  inbox/*.md  ◄── fs.watch (2s poll)
    │                            │
    │                     Channel notification
    │                            │
    │                   "Session A says hello"
```

**Core principle: File = truth, DB = cache.**

Messages are stored as markdown files in `data/ipc/inbox/`. The SQLite database is a queryable cache. If the DB disappears, the files remain. The MCP server watches the inbox directory and pushes real-time notifications to Claude via the Channels protocol.

## Quick Start

### 1. Clone

```bash
git clone https://github.com/HanbeenMoon/t9-ipc.git
```

### 2. Add to your `.mcp.json`

```json
{
  "mcpServers": {
    "t9-ipc": {
      "command": "python3",
      "args": ["mcp/server.py"],
      "cwd": "/path/to/t9-ipc"
    }
  }
}
```

### 3. Use in Claude Code

Once configured, your Claude sessions can:

- **Discover peers**: `t9_ipc_who` — lists active sessions via heartbeat
- **Send messages**: `t9_ipc_send` — direct message or broadcast
- **Broadcast**: `t9_ipc_broadcast` — notify all sessions
- **Check inbox**: `t9_ipc_unread` — fallback for non-Channels clients

Messages from other sessions appear automatically as `<channel source="t9-ipc">` tags in your conversation.

## Architecture

```
t9-ipc/
├── lib/
│   ├── config.py       # Paths and optional integrations
│   ├── ipc.py          # Core: sessions, messages, locks, heartbeats
│   └── notify.py       # Optional: Telegram notifications
├── mcp/
│   └── server.py       # MCP server with Channels support
├── data/
│   └── ipc/
│       ├── inbox/      # Message files (truth)
│       └── archive/    # Processed messages
└── examples/
    └── mcp.json        # Example MCP configuration
```

### Session Discovery

Sessions announce themselves by writing to `heartbeats.json`. Each entry includes PID, timestamp, and current work. Stale sessions (PID dead or 24h timeout) are automatically cleaned.

### File Locking

Prevents concurrent edits to the same file across sessions:

```python
from lib.ipc import lock_acquire, lock_release

if lock_acquire(session_id, "path/to/file.py"):
    # safe to edit
    lock_release(session_id, "path/to/file.py")
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `IPC_SESSION_ID` | No | Override auto-detected session ID |
| `IPC_TG_TOKEN` | No | Telegram Bot token (for escalation alerts) |
| `IPC_TG_CHAT` | No | Telegram chat ID (for escalation alerts) |
| `IPC_DB_PATH` | No | Custom SQLite database path |
| `IPC_SESSION_FILE` | No | Custom session file path (default: `~/.t9_ipc_session`) |
### Database

SQLite database is created at `t9-ipc/.t9_ipc.db` by default. Override with `IPC_DB_PATH`. WAL mode is enabled for concurrent access.

## Dependencies

**None.** Pure Python 3.10+ stdlib. No pip install required.

## Platform support

Linux, macOS, and WSL. Native Windows is not supported — the
heartbeat file locking uses `fcntl.flock`, which is Unix-only.

## Credits

Inspired by [TAP](https://github.com/HUA-Labs/tap) by @dv-hua, which
pioneered the file-as-truth approach to inter-agent communication.

## License

MIT
