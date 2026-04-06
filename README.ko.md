# t9-ipc

**Claude Code 세션 2개 이상. 서로 대화. 5분 안에.**

Claude Code 세션을 동시에 여러 개 띄우면 서로 존재를 모른다. 이걸 작은 공용 메일박스로 연결해준다.

```
세션 A                       세션 B
  "B야, 나 끝냈어"  ────►  "A 끝났네, 내 차례"
```

## 뭘 하는 물건인가

- **탐색** — 지금 다른 세션 누가 돌아가고 있나?
- **쪽지** — 한 세션이 다른 세션한테
- **공지** — 전원에게 한방에
- **실시간 전달** — 다른 세션의 메시지가 내 대화창에 자동으로 뜸 (Claude MCP Channels)

그게 전부. 계정 없음. 서버 없음. `npm install` 없음. Python 표준 라이브러리만.

## 5분 설치

### 1. Clone

```bash
git clone https://github.com/HanbeenMoon/t9-ipc.git
cd t9-ipc
```

### 2. Claude Code에 연결

`~/.claude.json` 열고 (없으면 새로 만들고) 아래 추가:

```json
{
  "mcpServers": {
    "t9-ipc": {
      "command": "python3",
      "args": ["mcp/server.py"],
      "cwd": "/절대경로/t9-ipc"
    }
  }
}
```

`/절대경로/t9-ipc`는 방금 clone한 경로로.

### 3. Claude Code 재시작

끝. 세션 두 개 켜놓고 한쪽에서:

> 지금 다른 세션 누구 있어?

Claude가 `t9_ipc_who` 호출하고 다른 세션을 보여준다. 그 다음:

> 저 세션한테 "안녕" 보내줘

다른 세션의 메시지는 내 대화창에 자동으로 뜬다 — `<channel source="t9-ipc">` 태그로.

## Claude가 쓸 수 있는 도구

| 도구 | 역할 |
|------|------|
| `t9_ipc_who` | 지금 활성인 세션 목록 |
| `t9_ipc_send` | 특정 세션에 메시지 |
| `t9_ipc_broadcast` | 전원에게 |
| `t9_ipc_unread` | 안 읽은 메시지 목록 (Channels 미지원 클라이언트 대비) |
| `t9_ipc_set_name` | 이 세션 이름 지정 |

직접 부를 일은 없다. "다른 세션한테 기다리라고 해"처럼 말하면 Claude가 알아서 호출.

## 나한테 맞는 물건인가

- Claude Code 세션 2개 이상 굴려본 적 있고, 서로 이야기 시켰으면 좋겠다
- 20분 안에 코드 전부 읽고 신뢰할 수 있는 작은 도구를 원한다
- 런타임/패키지 매니저/서버 같은 거 설치하고 싶지 않다

일부러 작고 뾰족하게. 파일 몇 개, MCP 서버 하나, 설정 없음.

## 작동 방식 (궁금하면)

움직이는 부품은 세 개:

1. **`data/ipc/heartbeats.json`** — 살아있는 세션이 도구 호출할 때마다 여기 체크인. 죽은 세션은 자동 정리.
2. **`data/ipc/inbox/*.md`** — 메시지 하나당 YAML 헤더 붙은 마크다운 파일 하나. 이 파일들 자체가 메일박스. 프로토콜에는 DB 필요 없음.
3. **`mcp/server.py`** — inbox 감시하다가 새 파일 뜨면 Claude한테 push하는 작은 MCP 서버.

메시지는 이렇게 생김:

```markdown
---
from: session_alpha
to: session_beta
subject: 상황 공유
created: 2026-04-06 15:30:00
---

파서 끝. main에 푸시함
```

눈으로 읽을 수 있음. `grep` 가능. 서버 죽어도 메시지 그대로 남음.

## 설정 (선택)

전부 선택. 기본값으로 바로 돌아감.

| 환경변수 | 역할 |
|----------|------|
| `IPC_SESSION_ID` | 자동 생성된 세션 이름 덮어쓰기 |
| `IPC_DB_PATH` | SQLite 캐시 경로 변경 |
| `IPC_TG_TOKEN` + `IPC_TG_CHAT` | `escalation` 타입 메시지를 텔레그램 봇으로 전달 |

## 플랫폼 지원

Linux, macOS, WSL. 네이티브 Windows는 안 됨 — 파일 잠금에 `fcntl` 써서.

## Credits

[@dv-hua](https://github.com/dv-hua) 와 [TAP](https://github.com/HUA-Labs/tap) 에 respect.

## 라이선스

MIT
