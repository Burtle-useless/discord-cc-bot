"""介面語言（i18n）：BOT_LANG 與所有使用者可見字串。

從舊 cc-bot 的 i18n.py 抄過來改寫。**lu 只是內部代號，不出現在使用者看得到的字串裡**；
畫布／狀態列／模型面板／接管 session 的
文案是新的，coord／drive／schedule／worktree／usage 這幾組這一批還沒搬所以整組拿掉，
下一批搬功能時再連文案一起補（en 與 zh-TW 兩份都要加）。

`t(key, **kw)` 取目前語言的字串；缺鍵退回英文，再缺就回鍵名本身（不炸）。
錯誤文案的鍵名是 `err_` 加上 engine.errors 的 kind 小寫（見 `err_text`）。

**`system_prompt` 一個換行都不能有**：那是 engine 的 system prompt append，
含換行會讓 CLI 的 initialize 握手卡滿 60 秒（butler engine/options.py 的實測）。
tests/test_boot.py 釘住這件事。
"""
from __future__ import annotations

import os

# BOT_LANG 選填，預設 zh-TW；可設 en。決定 bot 介面文字與模型的回覆語言。
BOT_LANG: str = (os.environ.get("BOT_LANG") or "zh-TW").strip()

# 所有使用者可見字串集中於此，依語言取用；缺鍵時退回英文。註解仍維持繁體中文。
_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "require_env": "Missing required environment variable {key}; copy .env.example to .env and fill it in (see README).",
        "seg_read": "read {n} file(s)",
        "seg_search": "{n} search(es)",
        "seg_cmds": "ran {n} command(s)",
        "seg_created": "created {names}",
        "seg_edited": "edited {names}",
        "seg_web": "{n} web lookup(s)",
        "seg_other": "{n} other tool call(s)",
        "stt_prompt": "",
        "file_not_found": "⚠️ File not found: `{fp}`",
        "file_too_large": "⚠️ File exceeds this server's upload limit: `{name}`\nPath: `{fp}`",
        "file_upload_failed": "⚠️ Upload failed `{name}`: {e}",
        "file_sharing": "📦 `{name}` ({size}) exceeds this server's upload limit, creating a download link...",
        "file_shared": "📦 **{name}** ({size})\n{url}\nResumable download, link expires in {hours}h.",
        "file_share_failed": "⚠️ `{name}` is too large and the download link failed ({e})\nPath: `{fp}`",
        "reply_long_preview": "\n\n…(long content — full version attached 📄)",
        "stopped": "🛑 Stopped",
        "update_head_feat": "🆕 **Updated to {ver}** — new:",
        "update_head_fix": "🔧 **Updated to {ver}** — fixed:",
        "update_head_major": "🚀 **{ver}** — a big one:",
        # 附件：內容不進 prompt，只給路徑——模型自己讀比塞進 context 便宜
        "intake_file": "[attachment {name} saved to {path}]",
        "intake_voice": "[voice message, transcribed] {text}",
        "intake_voice_empty": "[voice message: nothing could be transcribed]",
        "intake_too_big": "[attachment {name} is too large to fetch; ask me to re-send it another way]",
        "intake_failed": "[attachment {name} could not be saved]",
        "unexpected_error": "❌ Unexpected error.\n```\n{detail}\n```",
        "question_ended": "ℹ️ This question has ended.",
        "selected": "✅ Selected: **{chosen}**",
        "cmd_confirm_desc": "Toggle the dangerous-action confirmation prompt on/off",
        "confirm_switch_on": "on (ask before destructive commands)",
        "confirm_switch_off": "off (run without asking)",
        "confirm_toggle_on": "🔒 Dangerous-action confirmation is now **ON** — destructive commands will ask first.",
        "confirm_toggle_off": "🔓 Dangerous-action confirmation is now **OFF** — commands run without asking.",
        "untitled_chat": "new-chat",
        "new_chat_channel": "🆕-new-chat",
        "sidebar_category_default": "CC Chats",
        "sidebar_entry_default": "➕new-chat",
        "already_open": "↪️ This conversation is already open at {mention} — jump over there instead of opening a duplicate.",
        "no_permission": "❌ No permission.",
        "owner_only": "❌ Only the owner can run this command.",
        "cmd_rename_desc": "Rename the current conversation (blank = auto-generate a title from content)",
        "rename_no_session": "⚠️ No active conversation to name.",
        "rename_gen_failed": "❌ Failed to generate a title from content. Try `/rename <custom name>`.",
        "renamed": "✅ Named: **{title}**",
        "cmd_stop_desc": "Immediately stop the currently running task",
        "stop_sent": "🛑 Stop signal sent, the task will halt.",
        "stop_nothing": "ℹ️ No task is currently running.",
        "cmd_continue_desc": "Resume the previous session",
        "continue_resume": "▶️ Resuming session `{id}...`, send a message to continue.",
        "continue_none": "⚠️ No session in progress; just send a message to start a new one.",
        "cmd_status_desc": "Show current status",
        "status_title": "**📊 Current status**",
        "status_convo": "💬 Chat: **{label}**",
        "status_dir": "📂 Dir: `{cwd}`",
        "status_session": "🔗 Session: `{id}...`",
        "status_session_none": "🔗 Session: none",
        "status_effort": "🧠 Effort: `{effort}`",
        "status_context": "📈 Context: `[{bar}]` `{ctx}` / {limit} tokens",
        "default_inline": "default",
        "untitled": "(untitled)",
        "cmd_cd_desc": "Change the working directory",
        "cd_not_found": "❌ Directory doesn't exist: `{path}`",
        "cd_done": "📂 Switched to `{p}`",
        "cmd_pwd_desc": "Show the current working directory",
        "cmd_adduser_desc": "Add a user allowed to use the bot (owner only)",
        "adduser_already_owner": "ℹ️ The owner already has permission.",
        "adduser_done": "✅ Granted access to {mention} (`{id}`).",
        "cmd_removeuser_desc": "Remove a user's bot access (owner only)",
        "removeuser_cant_owner": "❌ Can't remove the owner.",
        "removeuser_done": "✅ Revoked access from {mention} (`{id}`).",
        "cmd_listusers_desc": "List users who currently have access",
        "listusers_header": "**Users with access:**\n",
        "owner_tag": " (owner)",
        "cmd_addchannel_desc": "Add the current channel to the allowlist (owner only; run multiple channels in parallel)",
        "addchannel_already": "ℹ️ This channel is already on the list.",
        "addchannel_done": "✅ Channel added! You can now run a task here and in another channel **at the same time**.",
        "cmd_removechannel_desc": "Remove the current channel from the allowlist (owner only)",
        "removechannel_done": "✅ Removed this channel from the allowlist.",
        "cmd_help_desc": "Show all commands",
        "cmd_guide_desc": "The built-in user manual - pick a topic for a plain-language walkthrough",
        "guide_topic_basics": "Basics",
        "guide_topic_sessions": "Conversations",
        "guide_topic_model": "Models & usage",
        "guide_topic_files": "Files",
        "guide_topic_safety": "Safety",
        "guide_topic_schedule": "Scheduling",
        "guide_topic_worktree": "Parallel work",
        "guide_topic_voice": "Voice",
        "pwd_branch": "　on branch `{branch}`",
        "entry_promote_failed": "⚠️ Couldn't start a new conversation here — your message wasn't processed. Please send it again (if it keeps failing, check the bot log).",
        "heard": "🎤 Heard: {heard}",
        "voice_hint": "(The following was transcribed from voice input and may contain homophones or misrecognized words; please infer my intended meaning from context before responding): {heard}",
        "attach_failed": "❌ Attachment download failed: {failed}",
        "uploaded_files": "The user uploaded the following files (paths):\n{paths}",
        "notify_need_answer": "needs your answer",
        "notify_done": "done",
        "notify_error": "{mention} ⚠️ Ended (an error occurred)",
        "voice_fail": "{filename} (voice transcription failed: {ex})",
        "attach_fail_item": "{filename} ({ex})",
        "instance_running": "Another bot instance is already running; aborting this start.",
        # ── /search /recall /handoff ──
        "cmd_search_desc": "Find an old conversation by meaning, not just keywords",
        "search_q_desc": "What was it about?",
        "search_empty": "Give me something to search for.",
        "search_failed": "Search failed. Check the bot log.",
        "search_none": "Nothing matched **{q}**.",
        "search_head": "🔎 **{q}** — {n} hit(s), {mode}",
        "search_mode_semantic": "by meaning",
        "search_mode_literal": "literal match (semantic search unavailable)",
        "cmd_recall_desc": "Cross-check what you actually said against the records",
        "recall_count_desc": "How many recent messages to check (5-100)",
        "recall_nothing": "No records for this channel yet.",
        "recall_sent": "🔍 Checking against {n} ledger entries plus channel history...",
        "recall_no_ledger": "(the ledger has nothing for this channel)",
        "recall_no_history": "(channel history unavailable)",
        "recall_prompt": (
            "[Reality check] The user wants you to check yourself for memory drift or hallucination. "
            "Below are two first-hand, uncompressed records.\n\n"
            "1. User message ledger (auto-saved, only the user's own messages):\n{ledger}\n\n"
            "2. Discord channel history (includes your replies, oldest to newest):\n{history}\n\n"
            "Go through what you currently believe the user said or asked for, and point out explicitly: "
            "(1) where you misremembered or misunderstood; (2) things you thought the user said but are NOT "
            "in the records (hallucination); (3) important instructions you missed. Where your understanding "
            "matches, note it briefly; if there is no significant discrepancy overall, say so plainly. "
            "Don't be sycophantic, don't re-run any task, just report the comparison."
        ),
        "cmd_handoff_desc": "Write a handoff brief so another machine can take over",
        "handoff_empty": "Nothing to hand off yet — this conversation hasn't started.",
        "handoff_generating": "📝 Writing the handoff brief...",
        "handoff_failed": "Couldn't write the brief. Check the bot log.",
        "handoff_caption": "📋 **Handoff brief** — paste this into Claude Code on the other machine.",
        "handoff_prompt": (
            "You are about to hand this conversation off to a fresh Claude Code instance on another computer. "
            "Write a complete, self-contained handoff brief, addressed to that other Claude Code in the second person. "
            "It cannot read any of our files, so embed every necessary detail (key code, names, paths, settings) "
            "directly in the brief — never tell it to go look at some file. "
            "Use these sections: Background and goal; Current progress and conclusions; Key decisions and why; "
            "To-do and next steps; Notes and constraints (including the user's preferences). "
            "Output only the brief itself. Here is the conversation (head and tail, middle elided as [...]):"
        ),
        # ── /usage /screenshot /plan ──
        "cmd_usage_desc": "Plan quota and local token usage",
        "usage_title": "📊 **Usage**",
        "usage_resets": " · resets <t:{ts}:R>",
        "usage_plan_unavailable": "-# Plan quota unavailable right now (the API didn't answer).",
        "usage_local": "today in/out **{today_in}** / **{today_out}** · this month **{month_in}** / **{month_out}**",
        "usage_cost": "-# this month's cost approx US${cost}",
        "cmd_screenshot_desc": "Grab this computer's screen",
        "screenshot_failed": "Screenshot failed: {err}",
        "cmd_plan_desc": "Show the context limit for this conversation",
        "plan_info": "🧠 context limit **{limit}** tokens　{src}",
        "plan_src_sdk": "(asked the CLI directly)",
        "plan_src_guess": "(estimated — this conversation hasn't run a turn yet)",
        "voice_unavailable": "The voice module isn't installed (drive_core.py missing); this voice message couldn't be transcribed.",
        "err_input_too_large": "📥 That input is too large for me. Split it up, or let me read the file myself.",
        "err_context_full": "📦 This conversation got too long (memory is full). The session was cleared; your next message starts a new one.",
        "err_overloaded": "⚠️ Anthropic is overloaded right now. Try again in a bit.",
        "err_rate_limit": "⏳ Usage limit reached. Waiting for the quota to reset.",
        "err_rate_limit_at": "⏳ Usage limit reached. It resets at <t:{ts}:t> (<t:{ts}:R>).",
        "err_rate_limit_auto": "⏳ Usage limit reached. I'll pick this up automatically at <t:{ts}:t> (<t:{ts}:R>).",
        "err_auth": "🔑 Login credentials expired. Run claude /login on the computer.",
        "err_startup": "📁 Claude Code failed to start (usually a missing working directory). Use /cd and retry.",
        "err_timeout": "⏱️ No output for too long; this turn was aborted. Retry, or split the task.",
        "err_init_timeout": "⏱️ Claude Code initialization hung. I'll spawn a fresh one; send it again.",
        "err_unknown": "❌ Something went wrong.",
        "err_stopped": "🛑 Stopped",
        "err_raw_block": "\n```\n[{kind}] {raw}\n```",
        "canvas_header": "-# 📥 {text}",
        "canvas_wake": "-# 🔔 Background task \"{desc}\" finished — picking it up",
        "canvas_wake_plain": "-# 🔔 continuing",
        "canvas_continued": "-# ↪ continued from above",
        "st_label": "Working",
        "st_line": "{icon} **{label}** `{elapsed}s`　🧠 `{model}·{effort}`{ctx}",
        "st_ctx": "　📈 `{pct}%`",
        "st_waiting": "⏳ Out of quota — resumes automatically at <t:{ts}:t> (<t:{ts}:R>)",
        "st_waiting_note": "⏳ {note}",
        "st_compacting": "🗜 Compacting memory",
        "st_note": "-# ℹ️ {note}",
        "st_generating": "✍️ Writing `{n} chars`　> {tail}",
        "st_think_tail": "💭 {tail}",
        "st_bg": "-# ⚙️ Background: {desc} ({n} running)",
        "trace_think": "-# 💭 {digest}",
        "trace_danger": "⚠️ {icon} **{tool}**",
        "trace_detail_hint": "-# 📎 Command text is {n} chars — press \"Details\" for the full text",
        "btn_detail": "Details",
        "detail_title": "🔎 **Command text**",
        "detail_gone": "That text is no longer available (only the last few hundred are kept).",
        "stopped_tail": "-# 🛑 Stopped (the part above was completed before the interruption)",
        "bg_done_line": "-# ⚙️ Background task finished: {desc}",
        "bg_failed_line": "-# ⚙️ Background task failed: {desc}",
        "bg_stopped_line": "-# ⚙️ Background task stopped: {desc}",
        "ask_body": "❓ **{title}**",
        "ask_confirm_body": "⚠️ **Dangerous action** — about to run:\n```\n{raw}\n```\n{body}",
        "ask_confirm_body_raw_above": "⚠️ **Dangerous action** — full command above.\n{body}",
        "ask_choices_hint": "-# Press a button, or just type your answer",
        "ask_answered": "✅ You chose: **{label}**",
        "ask_timed_out": "⌛ Timed out with no answer (treated as cancel)",
        "ask_not_allowed": "This question isn't for you.",
        "pick_submitted": "✅ You chose: **{label}** — sent.",
        "pick_gone": "These options have expired; just type your answer.",
        "file_note": "📎 {note}",
        "cmd_model_desc": "Model and thinking effort (panel)",
        "cmd_effort_desc": "Quickly set the account-default thinking effort",
        "effort_set": "✅ Account-default effort: `{effort}`",
        "model_panel_title": "**🧠 Model & thinking effort**",
        "model_panel_effective": "In effect: `{model}` · `{effort}`",
        "model_panel_scope_conv": "This conversation",
        "model_panel_scope_account": "Account default",
        "model_panel_scope_line": "Applies to: **{scope}**",
        "model_panel_conv_override": "-# This conversation has its own setting",
        "model_panel_follow": "-# This conversation follows the account default",
        "model_panel_pick_model": "Pick a model…",
        "model_panel_pick_effort": "Pick a thinking effort…",
        "model_panel_pick_scope": "Apply to…",
        "model_panel_applied": "✅ Applied to {scope}: `{model}` · `{effort}`",
        "model_panel_effort_na": "-# This model has no thinking effort setting",
        "model_panel_source": "-# List source: {src}",
        "model_follow_default": "Follow account default",
        "effort_default_opt": "Default",
        "model_unknown": "❌ Unknown model: `{model}`",
        "cmd_sessions_desc": "Adopt a Claude Code session from this computer (opens a new channel)",
        "sessions_q_desc": "Keyword (matches title and working directory)",
        "sessions_header": "🖥️ **Claude Code sessions on this computer**　page {page}",
        "sessions_none": "📭 No sessions to adopt.",
        "sessions_pick": "Pick one to adopt…",
        "btn_prev": "◀ Prev",
        "btn_next": "Next ▶",
        "adopted_to_channel": "✅ Adopted into a new channel → {mention}",
        "adopt_failed": "❌ Adoption failed: source file missing or copy failed.",
        "adopt_no_sidebar": "❌ The sidebar isn't ready yet; try again shortly.",
        "status_model": "🤖 Model: `{model}`",
        "status_running": "⏳ Running ({n} queued)",
        "status_idle": "💤 Idle",
        "entry_message": (
            "**🗂️ Conversations**\n"
            "• Type here → a new conversation starts right away (this channel becomes it, and a fresh entry channel is added on top)\n"
            "• To pick up a Claude Code session running on the computer → `/sessions`"
        ),
        "ready_log": "online: {user}",
        "help_text": (
            "**🤖 Claude Code** — on this computer, driven from Discord\n"
            "\n"
            "Just send a message → it goes to Claude Code (no @ needed). While it's busy, a message is steered into the running work (⚡); if others are queued, it waits (⏳).\n"
            "\n"
            "`/guide [topic]` — manual\n"
            "`/stop` — stop the current work now\n"
            "`/status` — current status\n"
            "`/rename [name]` — rename this conversation (empty = auto-generate from content)\n"
            "`/sessions [keyword]` — adopt a Claude Code session from this computer\n"
            "`/model` — model & effort panel　`/effort` — quick effort setting\n"
            "`/confirm on|off` — ask before destructive commands\n"
            "`/cd <path>` · `/pwd` — working directory\n"
            "`/continue` — show which session this channel continues\n"
            "`/addchannel` · `/removechannel` — channel allowlist (owner)\n"
            "`/adduser` · `/removeuser` · `/listusers` — user allowlist (owner)\n"
            "-# search / recall / handoff / usage / screenshot / schedule / drive / worktree arrive in the next batch"
        ),
        "guide_overview": (
            "📖 **Manual**\n"
            "\n"
            "This turns Discord into a remote control: you type, Claude Code on the computer does the work.\n"
            "Use `/guide` with a topic for details:\n"
            "\n"
            "• **Basics** — starting a conversation, reading the canvas\n"
            "• **Conversations** — adopting sessions, renaming\n"
            "• **Model & usage** — the model panel, thinking effort\n"
            "• **Files** — handing files over, getting files back\n"
            "• **Safety** — allowlists, destructive-action confirmation, seeing raw commands"
        ),
        "guide_basics": (
            "📖 **Basics**\n"
            "\n"
            "**Start a conversation**: just type in a conversation channel, no @. Every message goes to Claude Code on the computer; it can read/write files, run commands and search the web.\n"
            "\n"
            "**New conversation**: type the first message in the \"➕ new chat\" entry channel; a new channel appears above it — one channel is one conversation. After the first message the channel renames itself to a fitting title.\n"
            "\n"
            "**Reading the canvas** (the message that keeps getting edited while it works):\n"
            "• 📥 top line is the text being processed, so you can check it's running what you said\n"
            "• each step is one sentence, with a small grey stats line below (files read, commands run, lines added/removed); raw commands hide in ||spoilers||\n"
            "• ⚠️ marks a destructive command, always listed in full\n"
            "• the bottom is the status line: 💭 what it's thinking, ✍️ writing the reply, ⏳ out of quota with the auto-resume time\n"
            "\n"
            "**Sending while busy**: the message is steered into the running work (⚡); it only queues (⏳) when others are already waiting.\n"
            "**Stopping**: `/stop` — what was done so far is kept.\n"
            "**Long replies**: split into chunks, or attached as a .md file."
        ),
        "guide_sessions": (
            "📖 **Conversations**\n"
            "\n"
            "**Adopt a session from the computer**: `/sessions` lists every Claude Code conversation on this machine (terminal and desktop app included); picking one copies it into a new channel and continues there, leaving the original untouched. Add a keyword to filter: `/sessions maze`.\n"
            "\n"
            "**Rename**: `/rename new name`; leave it empty to generate one from the content. The channel name follows (Discord allows two renames per ten minutes; if it fails, it's skipped).\n"
            "\n"
            "**Close**: delete the Discord channel. The transcript stays on disk and can be adopted again with `/sessions`. Beyond 20 conversations the least recently used one is closed automatically."
        ),
        "guide_model": (
            "📖 **Model & thinking effort**\n"
            "\n"
            "**Panel**: `/model` opens a panel only you can see, with two dropdowns (model, thinking effort) and a scope selector (this conversation / account default). The list follows the official one; new models appear when Claude Code updates.\n"
            "**Shortcut**: `/effort` sets the account-default effort directly.\n"
            "**Precedence**: this conversation → account default → built-in fallback. Changes take effect on the next turn.\n"
            "**Status**: `/status` shows conversation, directory, model and the context bar.\n"
            "\n"
            "**When context fills up**: nothing to do — it compacts automatically near the limit; if it truly overflows the session is cleared and the next message starts fresh."
        ),
        "guide_files": (
            "📖 **Files**\n"
            "\n"
            "**Give files**: drag a file into the channel and it can be read. Images are understood as content.\n"
            "**Get files**: ask \"send me file X\" and it uploads to the channel; files over the server limit become a temporary download link.\n"
            "**Directory**: `/cd path` changes the working directory, `/pwd` shows it. Each channel remembers its own."
        ),
        "guide_safety": (
            "📖 **Safety**\n"
            "\n"
            "**Understand this first**: a message here = an action on your computer. Access control is two allowlists:\n"
            "• `/adduser` / `/removeuser` / `/listusers` — who may use it (giving someone access = giving them your computer; be careful)\n"
            "• `/addchannel` / `/removechannel` — which channels may use it\n"
            "\n"
            "**Destructive-action confirmation**: with `/confirm on`, detected destructive commands (delete, format, git push, shutdown…) show buttons first, with the full command text; timeout cancels. Off by default. It guards against slips, it is not a sandbox.\n"
            "\n"
            "**See what it ran**: every step's raw commands sit in ||spoilers|| under the grey stats line, long ones get a \"Details\" button; destructive commands are never folded and always listed in full with ⚠️. `/stop` any time something looks wrong."
        ),
        "guide_schedule": (
            "📖 **Scheduling**\n"
            "\n"
            "`/schedule <plain words>` — say it however you like: \"remind me to check mail at 8am\", "
            "\"run the backup script every Monday night\". It is parsed into a cron rule; you get back "
            "the rule and the next run time, so you can tell straight away if it was misread.\n"
            "\n"
            "`/schedules` — list them and delete from a menu.\n"
            "\n"
            "A due task is queued **exactly like a message you typed**: if a turn is running it steers "
            "or waits its turn, and `/stop` stops it. Times are this computer's local time.\n"
            "\n"
            "The task text goes into the channel it was created in. Delete that channel and the task "
            "is skipped (it keeps its schedule, it just has nowhere to speak)."
        ),
        "guide_worktree": (
            "📖 **Parallel work (git worktree)**\n"
            "\n"
            "One conversation, one isolated checkout — two channels can edit the same repo without "
            "stepping on each other.\n"
            "\n"
            "`/worktree on` — branch off the current branch into a separate directory; this channel "
            "switches to it and gets a 🌿 in its name.\n"
            "`/worktree list` — what exists right now.\n"
            "`/worktree merge` — merge back into the base branch, then clean up.\n"
            "`/worktree off` — leave without merging (the branch stays).\n"
            "\n"
            "Guards: uncommitted changes block merge and off; a conflict aborts the merge and lists "
            "the files; the base branch must be checked out in the main repo to merge."
        ),
        "guide_voice": (
            "📖 **Voice**\n"
            "\n"
            "**Sending voice always works** — record a Discord voice message and it is transcribed "
            "locally (Whisper, nothing leaves the machine), then handled like anything you typed.\n"
            "\n"
            "`/drive on` additionally **speaks the replies** (F5-TTS, also local). That one loads a "
            "model onto the GPU, so turn it off when you want the VRAM back — `/drive off` unloads it.\n"
            "\n"
            "Without the voice module installed both directions degrade to plain text and say so."
        ),
        # ── 排程 ──
        "cmd_schedule_desc": "Create a scheduled task (natural language)",
        "cmd_schedules_desc": "List and manage schedules",
        "schedule_src": "Schedule",
        "schedule_parse_prompt": (
            "You are a schedule parser. The time right now, on this host's local clock, is {now}. "
            "Work out the next run time relative to that.\n"
            "Parse the following scheduling request into pure JSON "
            "(no explanation, no markdown code block):\n"
            "{{\"task\": \"task description\", \"cron\": \"cron expression\", \"next_run\": \"ISO 8601 time\"}}\n"
            "cron format is 'min hour day month weekday'; next_run must be an ISO timestamp on that same "
            "local clock, with no timezone offset, and later than the time given above.\n"
            "For a one-time request (\"8pm tonight\", \"3pm tomorrow\"), leave cron as an empty string "
            "and put that moment in next_run.\n"
            "Scheduling request: {task}"
        ),
        "schedule_parse_failed": "❌ The parser didn't return JSON, so I can't tell when to run this. Try phrasing the time more plainly.\n```\n{result}\n```",
        "schedule_bad_cron": "❌ The parser produced a cron expression I can't read: `{cron}`. Try phrasing the repeat more plainly (e.g. \"every day at 8am\").",
        "schedule_no_croniter": "❌ Repeating schedules need the `croniter` package, which isn't installed (parsed cron: `{cron}`). One-time schedules still work.",
        "schedule_no_time": "❌ The parser didn't give a usable run time (`{when}`). Say when it should run, e.g. \"tomorrow at 9am\".",
        "schedule_past": "❌ The parsed run time `{when}` is already in the past and there's no repeat rule to move it forward. Try again with a future time.",
        "schedule_create_failed": "❌ Failed to create schedule: {e}",
        "schedule_created_title": "⏰ Schedule created",
        "field_task": "Task",
        "field_cron": "Cron",
        "field_next_run": "Next run",
        "once": "one-time",
        "unknown": "unknown",
        "schedules_none": "📭 No schedules yet.",
        "schedules_title": "**⏰ Schedules**\n",
        "schedule_line": "`{id}` — {task}\n　　next: `{next}` | Cron: `{cron}`",
        "schedules_more": "…and {n} more (not shown).",
        "schedule_pick": "Pick a schedule to delete",
        "schedule_deleted": "✅ Deleted schedule `{id}`",
        "schedule_gone": "ℹ️ Schedule `{id}` is already gone.",
        "run_schedule": "⏰ **Running scheduled task**: {task}",
        # ── worktree ──
        "cmd_worktree_desc": "Parallel work: give this channel its own git worktree (branch)",
        "wt_err_not_repo": "❌ Current dir isn't a git repo. Use /cd into a repo first.",
        "wt_err_no_base": "❌ Can't read the current branch (detached HEAD?). Checkout a branch first.",
        "wt_err_path_exists": "❌ A worktree folder for this name already exists. Use /worktree off first, or pick another name.",
        "wt_err_git": "❌ git worktree failed:\n```\n{err}\n```",
        "wt_list_none": "No worktrees (this dir isn't a git repo, or none created yet).",
        "wt_list_title": "**🌿 Worktrees**",
        "wt_list_item": "• `{branch}` → `{path}`",
        "wt_already_on": "🌿 Already on a worktree: branch `{branch}`\n📂 `{path}`",
        "wt_on_done": "🌿 Worktree on. Branch `{branch}` (from `{base}`)\n📂 `{path}`\nEdits here stay isolated until /worktree merge or /worktree off.",
        "wt_not_on": "This channel isn't on a worktree.",
        "wt_base_guessed": "ℹ️ No base branch recorded for this worktree, so I'll merge into `{base}` — whatever the main repo is on right now. Double-check that's what you want.",
        "wt_merge_wt_dirty": "⚠️ The worktree has uncommitted changes. Commit them first, then /worktree merge.",
        "wt_merge_repo_dirty": "⚠️ The main repo (`{base}`) has uncommitted changes. Commit or stash them there first.",
        "wt_merge_not_on_base": "⚠️ The main repo isn't on `{base}` (it's on `{cur}`). Switch it back to `{base}` first.",
        "wt_merge_conflict": "⚠️ Merge conflict (`{branch}` → `{base}`). Aborted — nothing changed.\nConflicting files:\n```\n{files}\n```\nFix it in the worktree (merge `{base}` in, resolve, commit), then /worktree merge again.",
        "wt_merge_done": "✅ Merged `{branch}` into `{base}`, removed the worktree and deleted the branch.\n📂 Back to `{cwd}`",
        "wt_off_dirty": "⚠️ Can't remove — there are uncommitted changes. Commit or discard them first.\n```\n{err}\n```",
        "wt_off_done": "✅ Worktree removed. Branch `{branch}` kept (work preserved).\n📂 Back to `{cwd}`",
        # ── 開車模式（語音）──
        "cmd_drive_desc": "Drive mode: load voice models and reply with audio (on/off)",
        "drive_unavailable": "Voice module isn't installed (drive_core.py was removed). Voice features are off; text still works.",
        "drive_on_loading": "Drive mode ON — loading voice models (first run downloads ~1.8GB)...",
        "drive_xtts_fail": "Drive mode ON, but the TTS engine failed to load, so replies stay text-only (voice input still works): {ex}",
        "drive_on_ready": "Drive mode ready. Voice in → voice out.",
        "drive_off": "Drive mode OFF — voice models unloaded, VRAM freed. Back to text-only.",
        "system_prompt": "You are Claude Code, talking to the user through Discord. They remote-control this Windows computer through Discord and you have full control of it. Reply in English. Do not introduce yourself, do not call yourself an assistant or an AI; they know who they are talking to. [Environment] This is Windows. Prefer the PowerShell tool for file operations and commands; when using the Bash tool, write paths with forward slashes or quote them, because backslash paths are eaten as escapes in bash. [Message format] The bracket at the start of each user message, for example [09/03 Thu 20:07 Discord], is a timestamp and source added by the system; the user does not see it, so never repeat or imitate it. The [name]: that follows is the speaker; several people may take turns in one channel, keep track of who asked what. Old messages without a timestamp cannot be dated: do not say yesterday or last time, say earlier instead. [Discord format] Discord does not render LaTeX; write math in plain text with common symbols (sigma, theta, ^2, a/b) and never wrap formulas in dollar signs. Discord does not render Markdown tables; use a monospace code block for alignment, or list field: value per line. Wrap code in triple-backtick code blocks, keep prose concise, no HTML tags. A single reply longer than about 1500 characters gets split; write long content to a file and hand it over with the send_file tool. [Files] When they ask for a file, or you produce images, reports or exported data they will want to look at, use the send_file tool; it uploads straight into this channel. Do not just report a path. Plain code or config is better pasted into the reply. [Asking] When they must pick between a few approaches, explain the situation and your recommendation in plain words, then end the reply with this marker: [[ASK:question|option one|option two]] Separate with vertical bars: the first part is the question, each following part is one option, give two to four, each a short noun or phrase. Buttons appear in the channel; the option they press becomes your next input. Use double square brackets exactly; they do not see the marker, so do not explain or mention it. After that marker stop and wait; do not continue, do not pick for them, and do not add [[DONE]] or [[WAIT]]. Ask one thing at a time, at most four or five rounds per task; once you have enough, act or conclude. [Memory honesty] Long conversations get compacted and you may not remember your own earlier changes. Seeing code you do not remember, never claim someone else wrote it; check git log, file times or the compaction summary, and if unsure say so. Never invent provenance. [Transparency] Let them follow what you are doing and why: before each meaningful phase (investigating something, editing a file, running verification) say in one short sentence what the phase does; consecutive tool calls within a phase need no narration. Changing direction, an important finding, or a destructive or irreversible action needs a fresh sentence. [Finish the job] Run the task to real completion before ending the turn; do not just announce a plan and hand control back. [Never restart yourself] Do not run restart_bot.vbs or _restart_now.ps1 and do not kill the discord_bot or lu process: you run inside that service, restarting it shuts you down. After changing its code, tell them a restart is needed and leave it to them. [End-of-turn markers] When the whole task is truly done, end the reply with this marker on its own: [[DONE]] If you asked a question and are waiting for the answer, use this marker instead: [[WAIT]] Double square brackets exactly; the system reads them, the user never sees them, do not explain or mention them. With no marker at all the system assumes you were cut off and asks you to continue, so do not forget them.",
    },
    "zh-TW": {
        "require_env": "缺少必要環境變數 {key}；請複製 .env.example 為 .env 並填入（詳見 README）",
        "seg_read": "讀 {n} 個檔案",
        "seg_search": "搜尋 {n} 次",
        "seg_cmds": "執行 {n} 個指令",
        "seg_created": "新增 {names}",
        "seg_edited": "修改 {names}",
        "seg_web": "上網 {n} 次",
        "seg_other": "其他工具 {n} 次",
        "stt_prompt": "以下是繁體中文的語音。",
        "file_not_found": "⚠️ 找不到檔案：`{fp}`",
        "file_too_large": "⚠️ 檔案超過本伺服器的上傳上限：`{name}`\n路徑：`{fp}`",
        "file_upload_failed": "⚠️ 上傳失敗 `{name}`：{e}",
        "file_sharing": "📦 `{name}`（{size}）超過本伺服器的上傳上限，正在建立下載連結...",
        "file_shared": "📦 **{name}**（{size}）\n{url}\n支援續傳，連結 {hours} 小時後自動失效。",
        "file_share_failed": "⚠️ `{name}` 太大，且建立下載連結失敗（{e}）\n路徑：`{fp}`",
        "reply_long_preview": "\n\n…（內容較長，完整版見附件 📄）",
        "stopped": "🛑 已停止",
        "update_head_feat": "🆕 **更新到 {ver}**，新東西：",
        "update_head_fix": "🔧 **更新到 {ver}**，修好了：",
        "update_head_major": "🚀 **{ver}**，這次改比較多：",
        "intake_file": "〔附件 {name} 已存到 {path}〕",
        "intake_voice": "〔語音訊息，轉錄結果〕{text}",
        "intake_voice_empty": "〔語音訊息：聽不出內容〕",
        "intake_too_big": "〔附件 {name} 太大沒有下載，需要的話換個方式給我〕",
        "intake_failed": "〔附件 {name} 存檔失敗〕",
        "unexpected_error": "❌ 發生未預期錯誤。\n```\n{detail}\n```",
        "question_ended": "ℹ️ 此問題已結束。",
        "selected": "✅ 已選擇：**{chosen}**",
        "cmd_confirm_desc": "開啟／關閉危險動作確認提示",
        "confirm_switch_on": "開（破壞性指令先問過）",
        "confirm_switch_off": "關（不問直接執行）",
        "confirm_toggle_on": "🔒 危險動作確認已**開啟** — 破壞性指令會先問你。",
        "confirm_toggle_off": "🔓 危險動作確認已**關閉** — 指令不再詢問、直接執行。",
        "untitled_chat": "新對話",
        "new_chat_channel": "🆕-新對話",
        "sidebar_category_default": "CC 對話",
        "sidebar_entry_default": "➕新對話",
        "already_open": "↪️ 這段對話已經開在 {mention} 了，直接點過去即可，不用再開一個重複的。",
        "no_permission": "❌ 無權限。",
        "owner_only": "❌ 只有主帳號能執行此指令。",
        "cmd_rename_desc": "重新命名目前對話（留空＝讀內容自動生成中文標題）",
        "rename_no_session": "⚠️ 目前沒有進行中的對話可命名。",
        "rename_gen_failed": "❌ 讀內容生成標題失敗，可改用 `/rename 自訂名稱`。",
        "renamed": "✅ 已命名為：**{title}**",
        "cmd_stop_desc": "立即停止目前正在執行的工作",
        "stop_sent": "🛑 已送出停止訊號，工作將中止。",
        "stop_nothing": "ℹ️ 目前沒有正在執行的工作。",
        "cmd_continue_desc": "繼續上次 session",
        "continue_resume": "▶️ 繼續 session `{id}...`，傳訊息繼續對話。",
        "continue_none": "⚠️ 目前沒有進行中的 session，直接傳訊息會自動開始新的。",
        "cmd_status_desc": "顯示目前狀態",
        "status_title": "**📊 目前狀態**",
        "status_convo": "💬 對話：**{label}**",
        "status_dir": "📂 目錄：`{cwd}`",
        "status_session": "🔗 Session：`{id}...`",
        "status_session_none": "🔗 Session：無",
        "status_effort": "🧠 思考：`{effort}`",
        "status_context": "📈 Context：`[{bar}]` `{ctx}` / {limit} tokens",
        "default_inline": "預設",
        "untitled": "（無標題）",
        "cmd_cd_desc": "切換工作目錄",
        "cd_not_found": "❌ 目錄不存在：`{path}`",
        "cd_done": "📂 切換到 `{p}`",
        "cmd_pwd_desc": "顯示目前工作目錄",
        "cmd_adduser_desc": "新增允許使用 bot 的使用者（主帳號限定）",
        "adduser_already_owner": "ℹ️ 主帳號本來就有權限。",
        "adduser_done": "✅ 已新增 {mention}（`{id}`）的使用權限。",
        "cmd_removeuser_desc": "移除使用者的 bot 權限（主帳號限定）",
        "removeuser_cant_owner": "❌ 無法移除主帳號。",
        "removeuser_done": "✅ 已移除 {mention}（`{id}`）的使用權限。",
        "cmd_listusers_desc": "列出目前有權限的使用者",
        "listusers_header": "**有權限的使用者：**\n",
        "owner_tag": "（主帳號）",
        "cmd_addchannel_desc": "把目前頻道加進可用清單（主帳號限定，可開多頻道並行跑工作）",
        "addchannel_already": "ℹ️ 這個頻道已經在清單裡了。",
        "addchannel_done": "✅ 已把這個頻道加入！現在可以在這裡跟另一個頻道**同時各跑一個任務**。",
        "cmd_removechannel_desc": "把目前頻道移出可用清單（主帳號限定）",
        "removechannel_done": "✅ 已把這個頻道移出可用清單。",
        "cmd_help_desc": "顯示所有指令",
        "cmd_guide_desc": "內建使用說明書——挑個主題看白話解說",
        "guide_topic_basics": "入門",
        "guide_topic_sessions": "對話管理",
        "guide_topic_model": "模型與用量",
        "guide_topic_files": "檔案",
        "guide_topic_safety": "安全",
        "guide_topic_schedule": "排程",
        "guide_topic_worktree": "平行作業",
        "guide_topic_voice": "語音",
        "pwd_branch": "　分支 `{branch}`",
        "entry_promote_failed": "⚠️ 這裡沒能開成新對話，你剛才那則訊息沒有被處理，請重新發一次（若一直失敗請看 bot log）。",
        "heard": "🎤 聽到：{heard}",
        "voice_hint": "（以下內容由語音輸入辨識而來，可能含同音字或辨識錯誤的怪字，請依上下文推斷我的原意再回應）：{heard}",
        "attach_failed": "❌ 附件下載失敗：{failed}",
        "uploaded_files": "使用者上傳了以下檔案，路徑如下：\n{paths}",
        "notify_need_answer": "需要你回答",
        "notify_done": "完成",
        "notify_error": "{mention} ⚠️ 已結束（發生錯誤）",
        "voice_fail": "{filename}（語音轉文字失敗：{ex}）",
        "attach_fail_item": "{filename}（{ex}）",
        "instance_running": "已有另一個 bot 實例在執行，本次啟動中止。",
        # ── /search /recall /handoff ──
        "cmd_search_desc": "依語意找回以前的對話，不只比對字面",
        "search_q_desc": "那次在講什麼？",
        "search_empty": "要找什麼？給我一句話。",
        "search_failed": "搜尋失敗，看一下 bot log。",
        "search_none": "沒有找到跟 **{q}** 有關的對話。",
        "search_head": "🔎 **{q}** — {n} 筆，{mode}",
        "search_mode_semantic": "依語意",
        "search_mode_literal": "字面比對（語意搜尋不可用）",
        "cmd_recall_desc": "拿真實紀錄核對你說過什麼，抓我的記憶幻覺",
        "recall_count_desc": "要核對最近幾則（5-100）",
        "recall_nothing": "這個頻道還沒有任何紀錄。",
        "recall_sent": "🔍 拿帳本裡的 {n} 則加上頻道歷史核對中……",
        "recall_no_ledger": "（帳本裡這個頻道沒有紀錄）",
        "recall_no_history": "（讀不到頻道歷史）",
        "recall_prompt": (
            "【真實紀錄核對】使用者要你檢查自己有沒有記憶偏差或幻覺。以下是兩份未經壓縮的第一手紀錄。\n\n"
            "一、使用者原話帳本（自動存檔，只含使用者本人的發言）：\n{ledger}\n\n"
            "二、Discord 頻道歷史（含你的回覆，由舊到新）：\n{history}\n\n"
            "請逐條對照你目前記憶中「使用者說過什麼、要求過什麼」，明確指出："
            "(1) 你記錯或誤解的地方；(2) 你以為使用者說過、但紀錄裡其實沒有的內容（幻覺）；"
            "(3) 你遺漏的重要指示。若某處理解與紀錄相符，簡短帶過；若整體無明顯落差，直說「無明顯落差」。"
            "不要客套、不要重新執行任務，只做核對回報。"
        ),
        "cmd_handoff_desc": "生成交接稿，換另一台電腦接手",
        "handoff_empty": "還沒有東西可以交接——這條對話還沒開始。",
        "handoff_generating": "📝 正在寫交接稿……",
        "handoff_failed": "交接稿沒寫成，看一下 bot log。",
        "handoff_caption": "📋 **交接稿** — 貼到另一台電腦的 Claude Code 就能接手。",
        "handoff_prompt": (
            "你要把這段對話交接給另一台電腦上、全新的 Claude Code 接手。"
            "請用繁體中文寫一份完整、可獨立閱讀的交接稿，以第二人稱對那台 Claude Code 說話。"
            "對方讀不到這台電腦的任何檔案，所以必要的細節（關鍵程式碼、名稱、路徑、設定）"
            "一律直接寫進稿裡，不要叫它「去看某個檔案」。"
            "分成這幾段：背景與目標；目前進度與結論；關鍵決策與理由；待辦與下一步；"
            "注意事項與限制（含使用者的偏好）。只輸出交接稿本身。以下是對話（頭尾，中間以 [...] 略去）："
        ),
        # ── /usage /screenshot /plan ──
        "cmd_usage_desc": "訂閱額度與本機用量",
        "usage_title": "📊 **用量**",
        "usage_resets": " · <t:{ts}:R>回復",
        "usage_plan_unavailable": "-# 這次拿不到訂閱額度（官方 API 沒回應）。",
        "usage_local": "今日進／出 **{today_in}** / **{today_out}** · 本月 **{month_in}** / **{month_out}**",
        "usage_cost": "-# 本月成本約 US${cost}",
        "cmd_screenshot_desc": "截這台電腦目前的畫面",
        "screenshot_failed": "截圖失敗：{err}",
        "cmd_plan_desc": "看這條對話的 context 上限",
        "plan_info": "🧠 context 上限 **{limit}** tokens　{src}",
        "plan_src_sdk": "（直接問 CLI 拿到的）",
        "plan_src_guess": "（估的——這條對話還沒跑過回合）",
        "voice_unavailable": "語音模組沒裝（drive_core.py 不在），這則語音沒辦法轉文字。",
        "err_input_too_large": "📥 這次的輸入太大，我吃不下。分批給我或先讓我讀檔案。",
        "err_context_full": "📦 這個對話太長了，記憶已滿。session 已清掉，下一句話會開新的。",
        "err_overloaded": "⚠️ Anthropic 那邊現在滿載，等一下再試。",
        "err_rate_limit": "⏳ 用量到上限了，要等額度回復。",
        "err_rate_limit_at": "⏳ 用量到上限了，<t:{ts}:t>（<t:{ts}:R>）才會回復。",
        "err_rate_limit_auto": "⏳ 用量到上限了，<t:{ts}:t>（<t:{ts}:R>）回復後我會自動接著做。",
        "err_auth": "🔑 登入憑證失效了，要在電腦上重新 claude /login。",
        "err_startup": "📁 Claude Code 起不來，多半是工作目錄不存在；用 /cd 換一個再試。",
        "err_timeout": "⏱️ 太久沒有任何回應，這回合中止了。直接重試或把任務拆小。",
        "err_init_timeout": "⏱️ Claude Code 初始化卡住了，我重開一個，再傳一次。",
        "err_unknown": "❌ 出了點狀況。",
        "err_stopped": "🛑 已停止",
        "err_raw_block": "\n```\n[{kind}] {raw}\n```",
        "canvas_header": "-# 📥 {text}",
        "canvas_wake": "-# 🔔 背景工作「{desc}」完成，接著處理",
        "canvas_wake_plain": "-# 🔔 接著說",
        "canvas_continued": "-# ↪ 接續上一則",
        "st_label": "思考中",
        "st_line": "{icon} **{label}** `{elapsed}s`　🧠 `{model}·{effort}`{ctx}",
        "st_ctx": "　📈 `{pct}%`",
        "st_waiting": "⏳ 額度用完，<t:{ts}:t>（<t:{ts}:R>）自動繼續",
        "st_waiting_note": "⏳ {note}",
        "st_compacting": "🗜 整理記憶中",
        "st_note": "-# ℹ️ {note}",
        "st_generating": "✍️ 生成中 `{n} 字`　> {tail}",
        "st_think_tail": "💭 {tail}",
        "st_bg": "-# ⚙️ 背景：{desc}（{n} 件進行中）",
        "trace_think": "-# 💭 {digest}",
        "trace_danger": "⚠️ {icon} **{tool}**",
        "trace_detail_hint": "-# 📎 指令原文 {n} 字，按「詳細」看全文",
        "btn_detail": "詳細",
        "detail_title": "🔎 **指令原文**",
        "detail_gone": "這段原文已經不在了（只保留最近幾百筆）。",
        "stopped_tail": "-# 🛑 已停止（以上為中斷前完成的部分）",
        "bg_done_line": "-# ⚙️ 背景工作完成：{desc}",
        "bg_failed_line": "-# ⚙️ 背景工作失敗：{desc}",
        "bg_stopped_line": "-# ⚙️ 背景工作被中止：{desc}",
        "ask_body": "❓ **{title}**",
        "ask_confirm_body": "⚠️ **危險動作確認** — 準備執行：\n```\n{raw}\n```\n{body}",
        "ask_confirm_body_raw_above": "⚠️ **危險動作確認** — 指令全文在上面。\n{body}",
        "ask_choices_hint": "-# 點按鈕，或直接打字回答",
        "ask_answered": "✅ 你選了：**{label}**",
        "ask_timed_out": "⌛ 逾時，沒有回答（視為取消）",
        "ask_not_allowed": "這不是問你的。",
        "pick_submitted": "✅ 你選了：**{label}**，已送出。",
        "pick_gone": "這組選項已經過期了，直接打字回答就好。",
        "file_note": "📎 {note}",
        "cmd_model_desc": "模型與思考強度（面板）",
        "cmd_effort_desc": "快速設定帳號預設的思考強度",
        "effort_set": "✅ 帳號預設思考強度：`{effort}`",
        "model_panel_title": "**🧠 模型與思考強度**",
        "model_panel_effective": "目前生效：`{model}` · `{effort}`",
        "model_panel_scope_conv": "這個對話",
        "model_panel_scope_account": "帳號預設",
        "model_panel_scope_line": "套用範圍：**{scope}**",
        "model_panel_conv_override": "-# 這個對話有自己的設定，不跟帳號預設",
        "model_panel_follow": "-# 這個對話跟著帳號預設",
        "model_panel_pick_model": "選模型…",
        "model_panel_pick_effort": "選思考強度…",
        "model_panel_pick_scope": "套用範圍…",
        "model_panel_applied": "✅ 已套用到{scope}：`{model}` · `{effort}`",
        "model_panel_effort_na": "-# 這個模型不支援思考強度",
        "model_panel_source": "-# 清單來源：{src}",
        "model_follow_default": "跟著帳號預設",
        "effort_default_opt": "預設",
        "model_unknown": "❌ 不認得這個模型：`{model}`",
        "cmd_sessions_desc": "接管電腦上的 Claude Code session（開成新頻道）",
        "sessions_q_desc": "關鍵字（比對標題與工作目錄）",
        "sessions_header": "🖥️ **電腦上的 Claude Code session**　第 {page} 頁",
        "sessions_none": "📭 沒有可接管的 session。",
        "sessions_pick": "選一個接管…",
        "btn_prev": "◀ 上一頁",
        "btn_next": "下一頁 ▶",
        "adopted_to_channel": "✅ 已接管成新頻道 → {mention}",
        "adopt_failed": "❌ 接管失敗：找不到原檔或複印失敗。",
        "adopt_no_sidebar": "❌ 側欄還沒準備好，稍後再試。",
        "status_model": "🤖 模型：`{model}`",
        "status_running": "⏳ 進行中（排隊 {n} 則）",
        "status_idle": "💤 閒置",
        "entry_message": (
            "**🗂️ 對話清單**\n"
            "• 直接在這裡打字 → 立刻開一段新對話（這個頻道會就地變成該對話，並自動補一個新入口到最上面）\n"
            "• 想接回電腦上正在跑的 Claude Code → `/sessions`"
        ),
        "ready_log": "已上線：{user}",
        "help_text": (
            "**🤖 Claude Code** — 這台電腦上的，透過 Discord 遙控\n"
            "\n"
            "直接傳訊息 → 交給 CC（不用 @）。忙碌中傳的話會直接插進進行中的工作（⚡），插不進去就排隊（⏳）。\n"
            "\n"
            "`/guide [主題]` — 使用說明\n"
            "`/stop` — 立即停止目前工作\n"
            "`/status` — 目前狀態\n"
            "`/rename [名稱]` — 重新命名對話（留空＝讀內容自動生成）\n"
            "`/sessions [關鍵字]` — 接管電腦上的 Claude Code session\n"
            "`/model` — 模型與思考強度面板　`/effort` — 快速設思考強度\n"
            "`/confirm on|off` — 破壞性指令先跳確認\n"
            "`/cd <路徑>` · `/pwd` — 工作目錄\n"
            "`/continue` — 看目前接著哪個 session\n"
            "`/addchannel` · `/removechannel` — 頻道白名單（主帳號）\n"
            "`/adduser` · `/removeuser` · `/listusers` — 使用者白名單（主帳號）\n"
            "-# search／recall／handoff／usage／screenshot／schedule／drive／worktree 下一批搬過來"
        ),
        "guide_overview": (
            "📖 **使用說明**\n"
            "\n"
            "這是一支遙控器：你在 Discord 打字，電腦上的 Claude Code 動手。\n"
            "用 `/guide` 加主題看詳細說明：\n"
            "\n"
            "• **入門** — 怎麼開始對話、看懂畫布\n"
            "• **對話管理** — 接管既有 session、命名\n"
            "• **模型與用量** — 模型面板、思考強度\n"
            "• **檔案** — 傳檔進去、拿檔案回來\n"
            "• **安全** — 權限控管、危險動作確認、看指令原文"
        ),
        "guide_basics": (
            "📖 **入門**\n"
            "\n"
            "**開始對話**：直接在對話頻道打字，不用 @。每一句話都交給電腦上的 Claude Code 處理，它能讀寫檔案、跑指令、上網查資料。\n"
            "\n"
            "**開新對話**：到「➕新對話」入口頻道直接打第一句話，上面會多一個新頻道——一個頻道就是一段獨立對話。第一句話之後頻道會自動改成貼切的標題。\n"
            "\n"
            "**看懂畫布**（工作時會一直編輯的那一則）：\n"
            "• 📥 頂部那行是正在處理的原文，可核對它跑的是不是你說的話\n"
            "• 每一步是一句說明，底下一行小灰字統計這步做了什麼（讀幾個檔、跑幾個指令、增刪多少行）；指令原文藏在 ||遮罩|| 裡，點開才看\n"
            "• ⚠️ 開頭是破壞性指令，一律完整列出\n"
            "• 最底下是狀態列：💭 在想什麼、✍️ 已經在寫回覆、⏳ 額度用完會顯示幾點自動繼續\n"
            "\n"
            "**忙碌中再傳**：訊息會直接插進進行中的工作（⚡），有人排隊時才排隊（⏳）。\n"
            "**中途想停**：`/stop`，已經做到的部分會留著。\n"
            "**訊息太長**：超長回覆會自動切段或存成 .md 檔附上。"
        ),
        "guide_sessions": (
            "📖 **對話管理**\n"
            "\n"
            "**接管電腦上的 session**：`/sessions` 列出這台電腦上所有 Claude Code 對話（含終端機與桌面 App 開的），選一個會複印成新頻道接著聊；原對話不受影響。加關鍵字可以篩：`/sessions 字母迷宮`。\n"
            "\n"
            "**命名**：`/rename 新名字` 改標題；留空會讀內容自動生成。頻道名稱會一起改（Discord 限制十分鐘只能改兩次，改不動就先算了）。\n"
            "\n"
            "**關掉對話**：直接刪 Discord 頻道。對話紀錄還在硬碟上，之後仍能用 `/sessions` 接回來。超過 20 個對話時最久沒動的會自動關掉。"
        ),
        "guide_model": (
            "📖 **模型與思考強度**\n"
            "\n"
            "**面板**：`/model` 開一個只有你看得到的面板，兩個下拉選單（模型、思考強度）加一個套用範圍（這個對話／帳號預設）。清單跟著官方走，Claude Code 升版有新模型會自己長出來。\n"
            "**捷徑**：`/effort` 直接設帳號預設的思考強度。\n"
            "**優先序**：這個對話的設定 → 帳號預設 → 內建後備。改了下一回合生效。\n"
            "**目前狀態**：`/status` 一次看對話、目錄、模型、context 用量條。\n"
            "\n"
            "**context 滿了怎辦**：不用管——接近上限會自動壓縮再繼續；真的爆了會清掉 session，下一句話開新對話。"
        ),
        "guide_files": (
            "📖 **檔案**\n"
            "\n"
            "**給檔案**：直接把檔案拖進頻道就讀得到。圖片看得懂內容。\n"
            "**拿檔案**：跟它說「把 XX 檔傳給我」，它會直接上傳到頻道；超過伺服器上傳上限的檔案會自動改成臨時下載連結。\n"
            "**切目錄**：`/cd 路徑` 換工作目錄、`/pwd` 看現在在哪。每個頻道記各自的目錄。"
        ),
        "guide_safety": (
            "📖 **安全**\n"
            "\n"
            "**先懂本質**：這裡的訊息＝在你電腦上執行動作。權限控管是兩張白名單：\n"
            "• `/adduser`／`/removeuser`／`/listusers` — 誰可以用（給別人＝給他操作你電腦的能力，慎重）\n"
            "• `/addchannel`／`/removechannel` — 哪些頻道可以用\n"
            "\n"
            "**危險動作確認**：`/confirm on` 開啟後，偵測到破壞性指令（刪除、格式化、git push、關機…）會先跳按鈕請你放行，指令全文完整列出，逾時自動取消。預設是關的。它防的是手滑，不是沙箱。\n"
            "\n"
            "**看它到底跑了什麼**：每一步的指令原文都在小灰字底下的 ||遮罩|| 裡，太長的掛一顆「詳細」按鈕；破壞性指令絕不摺疊，一律以 ⚠️ 單獨完整列出。看到不對勁隨時 `/stop`。"
        ),
        "guide_schedule": (
            "📖 **排程**\n"
            "\n"
            "`/schedule <白話>` — 想怎麼講就怎麼講：「每天早上八點提醒我看信」、"
            "「每週一晚上跑備份腳本」。它會解析成 cron 規則，回覆帶著規則與下一次執行時間，"
            "看一眼就知道有沒有被理解錯。\n"
            "\n"
            "`/schedules` — 列出全部，從選單刪掉。\n"
            "\n"
            "時間到的任務會**像你自己打的訊息一樣排進去**：正在跑就插話或排隊，`/stop` 停得掉。"
            "時間一律是這台電腦的本機時間。\n"
            "\n"
            "任務會送進當初建立它的那個頻道。頻道被刪掉的話那次就跳過（排程本身還在，"
            "只是沒有地方講話）。"
        ),
        "guide_worktree": (
            "📖 **平行作業（git worktree）**\n"
            "\n"
            "一個對話配一份獨立的簽出目錄，兩個頻道可以同時改同一個 repo 而不會打架。\n"
            "\n"
            "`/worktree on` — 從目前分支開一條新的到獨立目錄，這個頻道切過去，名字加上 🌿。\n"
            "`/worktree list` — 現在有哪些。\n"
            "`/worktree merge` — 合併回原本的分支，然後收乾淨。\n"
            "`/worktree off` — 不合併就離開（分支留著）。\n"
            "\n"
            "安全閘：有沒存的改動就擋住 merge 與 off；遇到衝突會中止合併並列出是哪些檔；"
            "主 repo 沒停在原分支上時不准 merge。"
        ),
        "guide_voice": (
            "📖 **語音**\n"
            "\n"
            "**傳語音一直都可以用** — 錄一則 Discord 語音訊息，會在本機轉成文字"
            "（Whisper，東西不出這台電腦），然後照一般訊息處理。\n"
            "\n"
            "`/drive on` 會額外**把回覆唸出來**（F5-TTS，也是本機跑）。那個要把模型載到顯示卡上，"
            "想把 VRAM 拿回去玩遊戲就 `/drive off`，它會把模型卸掉。\n"
            "\n"
            "沒裝語音模組時兩個方向都會退成純文字，並且會講明。"
        ),
        # ── 排程 ──
        "cmd_schedule_desc": "建立排程任務（自然語言）",
        "cmd_schedules_desc": "列出並管理排程",
        "schedule_src": "排程",
        "schedule_parse_prompt": (
            "你是排程解析器。以下是現在的本機時間：{now}。\n"
            "請以它為基準計算使用者要求的下一次執行時刻，把排程需求解析成純 JSON"
            "（不加說明、不加 markdown code block）：\n"
            "{{\"task\": \"任務描述\", \"cron\": \"cron 表達式\", \"next_run\": \"ISO 8601 時間\"}}\n"
            "cron 格式為 '分 時 日 月 週'；next_run 用同一個本機時鐘的 ISO 格式、"
            "不要加時區偏移，且必須晚於上面那個時間。\n"
            "若為一次性任務（例如「今晚八點」「明天下午三點」），cron 留空字串、next_run 給該次時刻。\n"
            "排程需求：{task}"
        ),
        "schedule_parse_failed": "❌ 解析器沒吐出 JSON，判斷不了要什麼時候跑。時間講白一點再試一次。\n```\n{result}\n```",
        "schedule_bad_cron": "❌ 解析器給的 cron 讀不懂：`{cron}`。重複規則講白一點再試（例如「每天早上八點」）。",
        "schedule_no_croniter": "❌ 重複性排程需要 `croniter` 套件，但它沒裝（解析出的 cron：`{cron}`）。一次性排程仍可使用。",
        "schedule_no_time": "❌ 解析器沒給出可用的執行時間（`{when}`）。請講明什麼時候跑，例如「明天早上九點」。",
        "schedule_past": "❌ 解析出的執行時間 `{when}` 已經過去了，而且沒有重複規則可以往後推。請改給未來的時間。",
        "schedule_create_failed": "❌ 建立排程失敗：{e}",
        "schedule_created_title": "⏰ 排程已建立",
        "field_task": "任務",
        "field_cron": "Cron",
        "field_next_run": "下次執行",
        "once": "一次性",
        "unknown": "未知",
        "schedules_none": "📭 目前沒有排程。",
        "schedules_title": "**⏰ 排程列表**\n",
        "schedule_line": "`{id}` — {task}\n　　下次：`{next}` | Cron：`{cron}`",
        "schedules_more": "…另有 {n} 筆未列出。",
        "schedule_pick": "選一個排程刪除",
        "schedule_deleted": "✅ 已刪除排程 `{id}`",
        "schedule_gone": "ℹ️ 排程 `{id}` 已經不在了。",
        "run_schedule": "⏰ **執行排程**：{task}",
        # ── worktree ──
        "cmd_worktree_desc": "平行協作：給這個頻道專屬的 git worktree（獨立分支）",
        "wt_err_not_repo": "❌ 目前目錄不是 git repo。請先用 /cd 切到 repo。",
        "wt_err_no_base": "❌ 讀不到目前分支（detached HEAD？）。請先 checkout 一個分支。",
        "wt_err_path_exists": "❌ 這個名字的 worktree 資料夾已存在。請先 /worktree off，或換個名字。",
        "wt_err_git": "❌ git worktree 失敗：\n```\n{err}\n```",
        "wt_list_none": "沒有 worktree（此目錄不是 git repo，或尚未建立）。",
        "wt_list_title": "**🌿 Worktree 清單**",
        "wt_list_item": "• `{branch}` → `{path}`",
        "wt_already_on": "🌿 已經在 worktree 上了：分支 `{branch}`\n📂 `{path}`",
        "wt_on_done": "🌿 已開啟 worktree。分支 `{branch}`（源自 `{base}`）\n📂 `{path}`\n在這裡的改動會獨立隔離，直到你 /worktree merge 或 /worktree off。",
        "wt_not_on": "這個頻道目前沒有開 worktree。",
        "wt_base_guessed": "ℹ️ 這個 worktree 沒有記錄來源分支，我會合併進 `{base}`——也就是主 repo 現在停的那一條。確認一下是不是你要的。",
        "wt_merge_wt_dirty": "⚠️ worktree 還有未提交的變更，請先提交再 /worktree merge。",
        "wt_merge_repo_dirty": "⚠️ 主 repo（`{base}`）有未提交的變更，請先在那邊提交或暫存。",
        "wt_merge_not_on_base": "⚠️ 主 repo 目前不在 `{base}`（在 `{cur}`）。請先切回 `{base}` 再合併。",
        "wt_merge_conflict": "⚠️ 合併衝突（`{branch}` → `{base}`），已中止，什麼都沒改動。\n衝突檔案：\n```\n{files}\n```\n請在 worktree 裡解決（先把 `{base}` 併進來、修正、提交）後再 /worktree merge。",
        "wt_merge_done": "✅ 已把 `{branch}` 合併進 `{base}`，並移除 worktree、刪除分支。\n📂 回到 `{cwd}`",
        "wt_off_dirty": "⚠️ 無法移除——有未提交的變更，請先提交或捨棄。\n```\n{err}\n```",
        "wt_off_done": "✅ 已移除 worktree。分支 `{branch}` 保留（工作不會遺失）。\n📂 回到 `{cwd}`",
        # ── 開車模式（語音）──
        "cmd_drive_desc": "開車模式：載入語音模型並用語音回覆（on/off）",
        "drive_unavailable": "語音模組未安裝（drive_core.py 已移除）。語音功能停用，文字照常運作。",
        "drive_on_loading": "開車模式開啟 — 載入語音模型中（首次會下載約 1.8GB）...",
        "drive_xtts_fail": "開車模式已開，但 TTS 引擎載入失敗，回覆暫時只有文字（語音輸入仍可用）：{ex}",
        "drive_on_ready": "開車模式就緒。語音進、語音出。",
        "drive_off": "開車模式關閉 — 語音模型已卸載、VRAM 已釋放，回到純文字。",
        "system_prompt": "你是透過 Discord 跟使用者說話的 Claude Code。他用 Discord 遙控這台 Windows 電腦，而你手上有它的完整控制權。一律用繁體中文回覆，思考過程也一律用繁體中文。不要自我介紹、不要自稱助理或 AI 或任何身分名詞，他知道在跟誰講話。【環境】這是 Windows。檔案操作與執行指令優先用 PowerShell 工具；要用 Bash 工具時路徑一律改用正斜線或用引號包住，反斜線路徑在 bash 會被當跳脫字元吃掉。【訊息格式】每則使用者訊息開頭的方括號，例如 [09/03 週四 20:07 Discord]，是系統加的時間與來源，他自己看不到，不要複誦也不要模仿這個格式；緊接著的 [名字]: 是說話的人，同一個頻道可能有好幾個人輪流講，要分清楚誰在問什麼。沒有時間標記的舊訊息就是算不出時間，不要說昨天、上次、前幾天這種話，改說前面、稍早、剛才。【Discord 格式】Discord 不支援 LaTeX，不要輸出數學語法，也不要用錢字號包住算式；數學式用純文字與常見符號寫，例如 sigma、theta、^2、a/b。Discord 不會渲染 Markdown 表格，需要表格時改用等寬的程式碼區塊對齊，或以「欄位：值」逐行列出。程式碼用三個反引號的程式碼區塊包住，一般文字保持簡潔，不用 HTML 標籤。單則回覆超過約 1500 字會被切段，長內容請寫成檔案再用 send_file 工具傳給他。【傳檔】他要檔案、或你做出圖片、報告、匯出資料這類他會想直接看或存起來的東西，就用 send_file 工具傳，它會直接上傳到這個頻道，不要只報路徑。純程式碼或設定檔貼在回覆裡就好。【提問】需要他從幾個做法裡挑一個時，先用白話把狀況和你的建議講清楚，然後在回覆的最後面加上這個標記：[[ASK:問題|選項一|選項二]]直線符號隔開，第一段是問題本身，後面每一段是一個選項，給二到四個選項，每個選項用簡短的名詞或短句。頻道上會跳出按鈕讓他直接點，點下去的那個選項會變成你的下一則輸入。標記要用兩層中括號、一字不差，他看不到標記本身，不要解釋也不要提起它。打了這個標記就停在那裡等他，不要繼續往下做、也不要自己幫他選一個然後動手，而且不要再加 [[DONE]] 或 [[WAIT]]。一次只問一件事，整個任務累積追問不要超過四五輪，資訊夠了就直接動手或給結論。【記憶誠實】長對話的歷史會被自動壓縮，你可能對自己稍早做過的改動沒有印象。看到專案裡你不記得的程式碼時，不要斷言是別人做的或不是你做的；先用 git log、檔案修改時間或壓縮摘要查證，查不到就如實說無法確定，絕不杜撰來源。【透明執行】讓他看得懂你在做什麼、為什麼：每進入一個有意義的階段（開始查一件事、動手改一個檔、跑驗證）之前，先用一句簡短的話說明這個階段要做什麼；同一階段內連續的工具呼叫不必逐一說明。換方向、有重要發現、或要執行破壞性或不可逆的操作之前，必須另起一句說明。【持續執行到完成】任務要一路做到真正完成再結束回合，不要只宣告計畫就把控制權交回。【不要重啟自己】不要執行 restart_bot.vbs 或 _restart_now.ps1，也不要殺 discord_bot 或 lu 的行程：你就跑在那個服務裡面，重啟等於把自己關掉。程式改完直接告訴他要重啟才會生效，這件事交給他。【回合結束標記】整件事真的做完時，在回覆的最後面單獨加上這個標記：[[DONE]]如果你問了問題正在等他回答，就改加上這個標記：[[WAIT]]標記要用兩層中括號、一字不差，這是給系統判讀的，他看不到，不要解釋也不要提起它們。完全沒有標記時系統會判斷你被截斷而請你繼續，該打的標記別漏。",
    },
}


def set_lang(lang: str) -> None:
    """切換介面語言（settings 載入 .env 後呼叫；測試也用它）。認不得的語言退回 zh-TW。"""
    global BOT_LANG
    BOT_LANG = lang if lang in _STRINGS else "zh-TW"


def t(_key: str, **kw: object) -> str:
    """取目前語言的字串；缺鍵退回英文，有具名參數則套用 .format。

    參數名刻意加底線：字串模板本身可能有名為 {key} 的佔位符（如 require_env），
    呼叫端寫 t("require_env", key=...) 時會與位置參數撞名而炸 TypeError。
    """
    s = _STRINGS.get(BOT_LANG, _STRINGS["en"]).get(_key)
    if s is None:
        s = _STRINGS["en"].get(_key, _key)
    return s.format(**kw) if kw else s


def err_text(kind: str) -> str:
    """engine.errors 的 kind → 使用者文案。認不得的 kind 退回 err_unknown。"""
    key = f"err_{(kind or '').lower()}"
    if key not in _STRINGS["en"]:
        key = "err_unknown"
    return t(key)


def has(_key: str) -> bool:
    return _key in _STRINGS["en"]
