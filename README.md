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
  them into a fresh channel; `/search` finds conversations by meaning, not just keywords.
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
> (`claude-sonnet-4-6`), which works on every plan with no usage credits. Tell
> the bot your plan with `/plan` and it applies Anthropic's official rule: on
> **Max / Team / Enterprise**, Opus automatically gets the 1M context window with
> no extra setup. For every other case (Sonnet on any plan, or Opus on Pro), opt
> into 1M by adding the `[1m]` alias to the model (e.g. `claude-sonnet-4-6[1m]`),
> which requires 1M usage credits. If you ever see *"Usage credits required for
> 1M context"*, a 1M model was selected without the entitlement.

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
| `/search` | Search past conversations by meaning (semantic; needs `fastembed`, else keyword) |
| `/stop` | Stop the currently running task |
| `/status` | Show current status |
| `/model` | Pick the Claude model |
| `/effort` | Pick the thinking effort level |
| `/plan` | Set your subscription plan (applies the official 1M-context rule) |
| `/drive` | Drive mode: voice in, voice out (on/off) |
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
  use). Runs on **CPU** out of the box. For **NVIDIA GPU** acceleration, also run
  `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` **inside the same venv** — a
  common pitfall is installing them into the system Python instead, which leaves
  the bot unable to load `cublas64_12.dll` and voice transcription fails.
- **`/schedule`** → `croniter`.
- **PowerPoint → PDF** → `pywin32` + Microsoft PowerPoint installed.

If you don't want a feature, you can skip its dependency.

### Drive mode (voice reply, XTTS-v2)

> ⚠️ The XTTS-v2 model is under the **Coqui Public Model License (CPML)** —
> **non-commercial use only**. `/drive` is off by default; skip this whole
> section unless you want voice *replies* (voice *input* needs only
> `faster-whisper`, above).

A plain `pip install coqui-tts` is **not enough** on its own — these are the
exact steps we tested. Run them **inside your venv**:

1. Python packages (note the `transformers` pin — coqui-tts breaks on
   `transformers` 5.x, which removed a symbol it imports):
   ```
   pip install "coqui-tts>=0.24.1" "transformers>=4.57,<5" torchcodec pypinyin
   ```
   - `torchcodec` — PyTorch ≥ 2.9 uses it for audio I/O.
   - `pypinyin` — required for Chinese (`zh`) synthesis.

2. **FFmpeg "shared" libraries (Windows).** torchcodec loads
   `avcodec/avformat/...` DLLs at runtime. Download an FFmpeg **shared** build
   (e.g. BtbN `ffmpeg-n7.1-…-win64-gpl-shared`), then either add its `bin\` to
   your `PATH`, **or** copy the `av*.dll / sw*.dll / postproc*.dll` files next to
   torchcodec's own DLLs (`…\site-packages\torchcodec\`). Without this you get
   `RuntimeError: Could not load libtorchcodec`.

3. **GPU (recommended).** A plain install pulls the **CPU** build of PyTorch, so
   XTTS runs on CPU (tens of seconds per reply). For an NVIDIA GPU, install a
   **CUDA 12.x** build of torch, e.g.:
   ```
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
   ```
   ⚠️ Use a **cu12x** build (cu126 / cu128) — **not** cu13x, even if your driver
   supports CUDA 13. Voice *input* (faster-whisper / ctranslate2) loads CUDA-12
   cuDNN, and a cu13 torch build clashes with it in the same process
   (`CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`), which makes /drive voice replies
   silently fall back to text. On GPU, synthesis is ~1–3 s per reply.

4. **Accept the model license.** Set `COQUI_TOS_AGREED=1` in your `.env`.
   Otherwise the first `/drive on` hangs forever waiting for an interactive
   license prompt — the background launcher has no console to answer it.

The first `/drive on` downloads the ~1.8 GB XTTS-v2 model.

---

## Security notes

- Your `.env` holds a bot token and is **gitignored** — never commit it.
- The bot runs Claude Code with `bypassPermissions`, i.e. it can edit files and
  run commands on your machine without prompting. Only allow trusted users
  (`ALLOWED_USER`) and keep the bot in a private server.

---

## License

[MIT](LICENSE)
