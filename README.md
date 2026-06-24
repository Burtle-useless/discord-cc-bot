# Discord CC Bot

Control [Claude Code](https://docs.claude.com/en/docs/claude-code) on your
Windows PC from Discord. Send a message in a Discord channel and the bot runs
it through the Claude Code CLI on your machine — editing files, running
commands, searching the web, and more — then streams the result back to you.

> **Language:** the bot's interface and Claude's replies default to **English**.
> Set `BOT_LANG=zh-TW` in your `.env` to switch the whole UI (and Claude's
> replies) to Traditional Chinese. (Source-code comments are in Chinese.)

---

## Features

- **Chat-driven Claude Code** — every message in the bound channel becomes a
  Claude Code turn on your PC, with live "thinking" progress updates.
- **Multi-conversation sidebar** — each Discord channel under a category maps to
  its own Claude session, like tabs. Create, rename, and delete conversations.
- **Session history** — `/sessions` lists past conversations and restores any of
  them into a fresh channel; `/search` finds conversations by keyword.
- **Voice input** — send a voice message and it's transcribed locally with
  Whisper (no cloud STT), then handled as a normal prompt.
- **File transfer** — the bot can upload files back to Discord; you can also
  drop files into the chat for Claude to read.
- **Interactive questions** — Claude's `AskUserQuestion` is rendered as Discord
  buttons; your click is fed back as the answer.
- **Scheduling** — `/schedule` sets up recurring tasks (cron under the hood).
- **Model & effort control** — switch model (Sonnet / Opus / Haiku) and thinking
  effort on the fly; automatic fallback model when the primary is overloaded.
- **Resilience** — retries on rate limits (429/529), context auto-compaction,
  and per-message error isolation so one bad turn won't kill the session.
- **Screenshots, usage stats, PowerPoint→PDF** and more.

---

## Prerequisites

1. **Windows** (this build is Windows-only).
2. **Python 3.10+**.
3. **Node.js** and the **Claude Code CLI**, installed and signed in:
   ```
   npm install -g @anthropic-ai/claude-code
   claude            # run once and complete login
   ```
   You need an account/plan that can use Claude Code.
4. A **Discord bot application** (see Setup below).

---

## Setup

### 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   and create a **New Application**.
2. Open the **Bot** tab → **Reset Token** → copy the token (this is your
   `DISCORD_TOKEN`).
3. Still on the **Bot** tab, enable **Message Content Intent** (under
   "Privileged Gateway Intents"). The bot needs this to read your messages.
4. Open **OAuth2 → URL Generator**, tick `bot` and `applications.commands`,
   give it permissions (Send Messages, Manage Channels, Read Message History,
   Attach Files), then open the generated URL to invite the bot to your server.

### 2. Get the IDs you need

Enable **Developer Mode** in Discord (Settings → Advanced → Developer Mode),
then right-click to copy:

- **`ALLOWED_CHANNEL`** — right-click the channel the bot should listen in →
  *Copy Channel ID*.
- **`ALLOWED_USER`** — right-click your own name → *Copy User ID*.

### 3. Install and configure

```
git clone https://github.com/Burtle-useless/discord-cc-bot.git
cd discord-cc-bot

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
```

Then open `.env` and fill in `DISCORD_TOKEN`, `ALLOWED_CHANNEL`, and
`ALLOWED_USER`. The rest are optional (see comments in the file).

> **Model & context:** by default the bot uses standard 200K context
> (`claude-sonnet-4-6`), which works on every plan with no usage credits. If you
> ever see *"Usage credits required for 1M context"*, it means a 1M-context model
> was selected without credits — the default avoids that. To opt into the 1M
> window (only if your plan has the credits), set `DEFAULT_MODEL` to a 1M alias
> such as `claude-sonnet-4-6[1m]` in `.env`.

---

## Running

With the virtualenv active:

```
python discord_bot.py
```

To run it **silently in the background** (no console window), double-click
`launch_bot.vbs`. It uses the `.venv` Python if present and logs to
`discord_bot.log`.

**Control panel (optional):** double-click `control-panel.vbs` for a tiny GUI
to **Start / Stop / Restart** the bot and **View Log** — no command line needed.
It shows a live running/stopped status and works from the project folder.

> **Won't start, and the log is empty?** The launcher needs a working Python with
> the dependencies. It looks for a virtualenv named `.venv` or `venv` in the
> project folder, and otherwise falls back to the `py` launcher. Make sure you
> created the venv and ran `pip install -r requirements.txt` **inside it**. (A
> bare `python` on Windows can be the Microsoft Store stub, which silently does
> nothing — that's the usual cause of an empty log.)

Now type a message in your bound channel — the bot should respond.

---

## Commands

| Command | What it does |
| --- | --- |
| `/new` | Reset the conversation, start a fresh session |
| `/continue` | Resume the previous session |
| `/rename` | Rename the current conversation (blank = auto-title) |
| `/sessions` | List past conversations and restore one |
| `/search` | Search past conversations by keyword |
| `/stop` | Stop the currently running task |
| `/status` | Show current status |
| `/model` | Pick the Claude model |
| `/effort` | Pick the thinking effort level |
| `/cd`, `/pwd` | Change / show the working directory |
| `/screenshot` | Capture the PC screen |
| `/usage` | Show plan usage (5h / 7d) |
| `/schedule`, `/schedules` | Create / manage scheduled tasks |
| `/adduser`, `/removeuser`, `/listusers` | Manage who may use the bot |
| `/addchannel`, `/removechannel` | Run the bot in additional channels |
| `/help` | Show all commands |

---

## Optional features

Some features need extra packages (already listed in `requirements.txt`):

- **Voice transcription** → `faster-whisper` (downloads a Whisper model on first
  use; a CUDA GPU is used automatically if available, otherwise CPU).
- **`/schedule`** → `croniter`.
- **PowerPoint → PDF** → `pywin32` + Microsoft PowerPoint installed.

If you don't want a feature, you can skip its dependency.

---

## Security notes

- Your `.env` holds a bot token and is **gitignored** — never commit it.
- The bot runs Claude Code with `bypassPermissions`, i.e. it can edit files and
  run commands on your machine without prompting. Only allow trusted users
  (`ALLOWED_USER`) and keep the bot in a private server.

---

## License

[MIT](LICENSE)
