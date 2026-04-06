# t9-ipc

[한국어 README](README.ko.md)

**Two or more Claude Code sessions. Talking to each other. In 5 minutes.**

Running multiple Claude Code sessions at once? They can't see each other by default. This gives them a tiny shared mailbox so they can.

```
Session A                    Session B
  "hey B, I'm done"  ────►  "A is done, my turn"
```

## What it does

- **Discovery** — who else is running right now?
- **Direct messages** — one session to another
- **Broadcast** — shout to everyone
- **Real-time delivery** — messages pop into the other session's conversation automatically (via Claude's MCP Channels)

That's it. No accounts, no servers, no npm install. Python standard library only.

## 5-minute setup

### 1. Clone

```bash
git clone https://github.com/HanbeenMoon/t9-ipc.git
cd t9-ipc
```

### 2. Point Claude Code at it

Open (or create) `~/.claude.json` and add:

```json
{
  "mcpServers": {
    "t9-ipc": {
      "command": "python3",
      "args": ["mcp/server.py"],
      "cwd": "/absolute/path/to/t9-ipc"
    }
  }
}
```

Replace `/absolute/path/to/t9-ipc` with wherever you cloned it.

### 3. Restart Claude Code

You're done. Open two sessions and ask one of them:

> who else is online?

It will call `t9_ipc_who` and list the other session. Then:

> send "hello" to \<the other session id\>

Messages from other sessions show up automatically in your conversation — tagged with `<channel source="t9-ipc">`.

## The tools Claude gets

| Tool | What it does |
|------|--------------|
| `t9_ipc_who` | List currently active sessions |
| `t9_ipc_send` | Send a message to a specific session |
| `t9_ipc_broadcast` | Send to everyone |
| `t9_ipc_unread` | List unread messages (fallback, for clients without Channels) |
| `t9_ipc_set_name` | Give this session a readable name |

You never call these yourself — Claude does, when you ask things like "tell the other session to wait" or "who else is running?".

## Is this for me?

- You run 2+ Claude Code sessions at once and want them to coordinate
- You want something tiny you can read in 20 minutes and trust
- You don't want to install a runtime, a package manager, or a server

Deliberately small and sharp. Four files, one MCP server, no configuration required.

## How it works (if you're curious)

Three moving parts, that's it:

1. **`data/ipc/heartbeats.json`** — every live session pings this file every tool call. Dead sessions get cleaned automatically.
2. **`data/ipc/inbox/*.md`** — each message is a markdown file with a little YAML header. The files *are* the mailbox. No database required for the protocol.
3. **`mcp/server.py`** — a tiny MCP server that watches the inbox and pushes new files to Claude as they arrive.

Messages look like this:

```markdown
---
from: session_alpha
to: session_beta
subject: status update
created: 2026-04-06 15:30:00
---

done with the parser, pushing to main
```

You can read them with your eyes. You can `grep` them. If the server crashes, your messages are still there.

## Configuration (optional)

All optional. Defaults work out of the box.

| Environment variable | What it does |
|----------------------|--------------|
| `IPC_SESSION_ID` | Override the auto-generated session name |
| `IPC_DB_PATH` | Move the SQLite cache somewhere else |
| `IPC_TG_TOKEN` + `IPC_TG_CHAT` | Forward `escalation`-type messages to a Telegram bot |

## Platform support

Linux, macOS, and WSL. Not native Windows — the locking uses `fcntl`, which is Unix-only.

## Credits

Respect to [@dv-hua](https://github.com/dv-hua) and [TAP](https://github.com/HUA-Labs/tap).

## License

MIT
