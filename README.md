# Cursor Telegram bot (local agent)

Long-running Telegram bot on WSL host. Messages → Cursor SDK **local** agent against HugSalesSolo.

Standalone repo (not part of HugSalesSolo). Points at the shop via `REPO_CWD` in `.env`.

## Setup

```bash
cd /home/starl/cursor-tg-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, CURSOR_API_KEY
# for topics: FORUM_MODE=1, FORUM_CHAT_ID=-100…, ALLOWED_CHAT_IDS=-100…
```

Bot must be **admin** in the forum group with permission to **manage topics**.

## Run

```bash
./run.sh
# or: source .venv/bin/activate && python -m bot
```

Long polling (no public webhook). Keep Docker up if the agent runs PHPUnit/PHPStan.

## Forum mode (topics)

| Action | Behaviour |
|--------|-----------|
| `/task …` | Creates a **new** forum topic (name = start of prompt), runs agent there |
| Text or `/ask …` **inside** a topic | Follow-up on that topic’s agent session |
| Text in General | Ignored (use `/task`) |

Session key = `(chat_id, thread_id)` → separate Cursor agent per topic.

## Commands

| Command | Action |
|---------|--------|
| `/task <text>` | New task (new topic if `FORUM_MODE=1`) |
| `/ask <text>` | Follow-up in current topic/chat |
| `/info` | Chat/group id, forum flags, thread id |
| `/status` | Idle / running / queue |
| `/cancel` | Cancel current run |
| `/diff` | `git status` + `git diff --stat` |
| `/new` | Drop agent session for this topic/chat |
| `/phpunit [args]` | `bash ./scripts/phpunit.sh …` |
| `/phpstan [args]` | `bash ./scripts/phpstan.sh …` |

One active agent **run** per repo (global queue). Commits/pushes only if you ask in the prompt.

## Security

Only `ALLOWED_USER_IDS` can drive the agent. Local SDK runs tools without IDE approvals — treat the bot like shell access to the repo.
