# Cursor Telegram Bot

![Cursor Telegram Bot preview](docs/preview.jpg)

Long-running Telegram bot that forwards messages to a **Cursor SDK local agent**. Each allowed user can drive coding tasks against a target git repository on the same machine.

No public webhook — the bot uses Telegram long polling.

## Requirements

- Python 3.11+
- A [Cursor](https://cursor.com) account with an API key
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Installation

```bash
git clone https://github.com/starline/CursorTgBot.git
cd your-project          # repo the agent should edit
/path/to/CursorTgBot/run.sh
```

On first start the script creates a venv, copies `.env`, and asks only for:

1. **Telegram bot token**
2. **Cursor API key**
3. **Repo path** (default: the directory you launched from)

Everything else has defaults (`CURSOR_MODEL=auto`, `BOT_DATA_DIR=./data`, forum mode off).

Then open a DM with the bot and send any message — **the first user is allowlisted automatically** (id written to `.env`). Restart is not required.

Keep the process running (`tmux`, systemd, …). The agent needs network access to Cursor and write access to the target repo.

### Optional `.env` tweaks

| Variable | Default | When to set |
|----------|---------|-------------|
| `REPO_CWD` | launch directory | Bot lives elsewhere / you always start from the bot folder |
| `ALLOWED_USER_IDS` | first DM auto-claims | Lock to specific users up front |
| `ALLOWED_CHAT_IDS` | any chat | Restrict to a group |
| `FORUM_MODE` / `FORUM_CHAT_ID` | off | One Telegram topic per `/task` |
| `CURSOR_MODEL` | `auto` | Pin a model id |
| `BOT_DATA_DIR` | `./data` | Custom session DB path |

In a group, send `/info` to get the chat id for `ALLOWED_CHAT_IDS` / `FORUM_CHAT_ID`.

## Desktop app

A settings window and tray icon around the same bot. Windows is the packaged target. Linux and macOS use the same Python shell; their installer scripts are ready to run on those systems.

### Windows

From a checkout (Python 3.11+):

```bat
CursorTgBot.bat
```

The script creates `.venv`, installs `requirements-desktop.txt`, and opens the window. Closing the window hides it to the tray. **Выход** stops the bot.

To build an `.exe` on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Output: `dist\CursorTgBot\CursorTgBot.exe` and `dist\CursorTgBot-windows.zip`. The frozen app stores `.env` in `%APPDATA%\CursorTgBot`.

### Linux and macOS

Same window, from the repo:

```bash
python3 launch_desktop.py
```

Folder builds (run on that OS):

```bash
./packaging/linux/build.sh
./packaging/macos/build.sh
```

Login autostart is implemented for all three systems (Windows Run key, XDG autostart, LaunchAgent).

## Usage

### Private chat

1. Start a chat with your bot.
2. Send `/task <what you want done>` to start an agent run.
3. Use `/ask <follow-up>` (or plain text) for follow-ups in the same session.

### Forum mode (topics)

Enable with `FORUM_MODE=1` and set `FORUM_CHAT_ID` (or a single `ALLOWED_CHAT_IDS` entry). The bot must be a **group admin** with permission to **manage topics**.

| Action | Behaviour |
|--------|-----------|
| `/task …` | Creates a **new** forum topic (name = start of the prompt) and runs the agent there |
| Text or `/ask …` **inside** a topic | Follow-up on that topic’s agent session |
| Text in General | Ignored (use `/task`) |

Session key = `(chat_id, thread_id)` → one Cursor agent session per topic.

### Commands

| Command | Action |
|---------|--------|
| `/task <text>` | New task (new topic if forum mode is on) |
| `/ask <text>` | Follow-up in the current topic/chat |
| `/backlog` | Active backlog tasks (`proposed` / `ready` / `doing`) from `REPO_CWD` |
| `/backlog deferred` | Deferred backlog tasks |
| `/info` | Chat/group id, forum flags, thread id |
| `/status` | Idle / running / queue |
| `/cancel` | Cancel the current run |
| `/diff` | `git status` + `git diff --stat` in `REPO_CWD` |
| `/new` | Drop the agent session for this topic/chat |
| `/phpunit [args]` | Runs `bash ./scripts/phpunit.sh …` in `REPO_CWD` (if present) |
| `/phpstan [args]` | Runs `bash ./scripts/phpstan.sh …` in `REPO_CWD` (if present) |

Only one agent **run** is active at a time (global queue). The agent will not commit or push unless you ask for that in the prompt.

### Reply formatting (Telegram)

Agent replies are wrapped with Telegram-specific formatting in `bot/prompt.py` (no markdown tables; backlog lists as plain-text blocks). Short «дай список задач» / `/backlog` use HTML from `bot/backlog_view.py`. Target-repo Cursor rules (e.g. HugSalesSolo) keep the IDE table format and should not duplicate Telegram styles.

## Security

- Only allowlisted users can use the bot (first DM auto-claims if the list was empty).
- Optionally restrict chats with `ALLOWED_CHAT_IDS`.
- The local Cursor SDK runs tools **without IDE approval prompts** — treat this bot like shell access to `REPO_CWD`.
- Never commit `.env`. Keep the repo private if your deployment details are sensitive, or rotate keys if they leak.

## Project layout

```
CursorTgBot/
├── bot/                 # Telegram handlers + Cursor agent runner
├── desktop/             # Settings window and tray (Windows, Linux, macOS)
├── packaging/           # PyInstaller spec and per-OS build scripts
├── data/                # Local session store (gitignored)
├── .env.example
├── requirements.txt
├── requirements-desktop.txt
├── launch_desktop.py    # Desktop entry
├── CursorTgBot.bat      # Windows launcher
└── run.sh               # Terminal launcher
```
