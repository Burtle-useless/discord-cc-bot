"""介面語言（i18n）：BOT_LANG 與所有使用者可見字串。

主程式透過 `from i18n import BOT_LANG, t` 取用；缺鍵時自動退回英文。
新增字串時 en 與 zh-TW 兩份都要加。
"""
import os

# ── 介面語言（i18n）─────────────────────────────────────────────────────
# BOT_LANG 選填，預設 en；可設 zh-TW。決定 bot 介面文字與「要求 Claude 回覆」的語言。
BOT_LANG = (os.environ.get("BOT_LANG") or "en").strip()

# 所有使用者可見字串集中於此，依語言取用；缺鍵時退回英文。註解仍維持繁體中文。
_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "require_env": "Missing required environment variable {key}; copy .env.example to .env and fill it in (see README).",
        # 錯誤訊息
        "err_input_too_large": "📥 Input too large to process. Please shorten it or send plain text.",
        "err_context_full": "📦 This conversation is too long (context is full). Use /new to start a fresh one.",
        "err_overloaded": "⚠️ Anthropic is temporarily overloaded. Auto-retry / fallback model didn't help — please try again shortly.",
        "err_rate_limit": "⏳ Rate limited. Please wait a moment and try again.",
        "err_auth": "🔑 Authentication problem — needs a manual check.",
        "err_startup": "📁 Claude Code failed to start (usually an invalid working dir). Reverted to the default dir — retry or use /cd.",
        "err_timeout": "⏱️ Claude Code produced no output for 10 minutes and was treated as stuck (length itself is unlimited). Retry or split the task.",
        "err_init_timeout": "⏱️ Claude Code init timed out, please try again.",
        "err_unknown": "❌ Unexpected error occurred. It has been logged.",
        "retry_notice": "⚠️ **{kind}**, auto-retrying in {delay}s ({n}/{max})...",
        "no_response": "(no response)",
        "empty_retry_nudge": "(Your previous turn ended with internal thinking only and produced no visible text. Write out your full reply as plain text now.)",
        "continue_nudge": "(If the whole task is already complete, output [[DONE]] and nothing else. If there are still steps you announced but have not carried out, continue and finish them now.)",
        "max_tokens_hint": "⚠️ The model spent its entire output-token budget on internal thinking and left no visible reply. Lower the thinking effort with /effort, or split the request into smaller steps, then try again.",
        "no_message": " (no message)",
        "stt_prompt": "",
        "cmd_drive_desc": "Drive mode: load voice models and reply with audio (on/off)",
        "drive_on_loading": "Drive mode ON -- loading voice models (first run downloads ~1.8GB)...",
        "drive_on_ready": "Drive mode ready. Voice in -> voice out.",
        "drive_xtts_fail": "Drive mode ON, but the TTS engine failed to load, so replies stay text-only (voice input still works): {ex}",
        "drive_off": "Drive mode OFF -- voice models unloaded, VRAM freed. Back to text-only.",
        "drive_off_voice": "Drive mode is off, so voice messages aren't transcribed. Turn it on first with /drive on.",
        "drive_unavailable": "Drive mode isn't installed (drive_core.py was removed). Voice features are off; text still works.",
        "drive_rule": (
            " [Drive mode] When the user's message is marked as transcribed from voice input, "
            "they are driving and listening, not reading. After your normal full text reply, "
            "append a spoken version wrapped EXACTLY as <<<SPEAK>>> your spoken summary <<<ENDSPEAK>>>. "
            "The spoken version is for the ear: plain natural sentences, no code, no symbols, "
            "no bullet points, no markdown, no URLs; just say the key points concisely. "
            "If the user's message is NOT from voice input, do not add the SPEAK block."
        ),
        "system_prompt": (
            "You are Claude Code, invoked through a Discord bot. "
            "The user controls the files and programs on this Windows PC through Discord. "
            "Reply in English, and do your internal thinking (reasoning) in English too. "
            "[Environment] This is Windows. Prefer the PowerShell tool for file operations and running commands; "
            "if you use the Bash tool, write paths with forward slashes or wrap them in quotes, "
            "because Windows backslash paths get eaten as escapes in bash and fail. "
            "[Multiple speakers] Messages may start with a bracketed user name, meaning different people are talking. "
            "Use it to tell who is asking what and stay consistent about who you're addressing. "
            "[Formatting] Discord does not support LaTeX, so do not output LaTeX math syntax "
            "(no dollar signs wrapping formulas). Write math in plain text with common symbols, "
            "e.g. divergence as div, spell out Greek letters (sigma, tau, theta), "
            "powers as ^2, fractions as a/b, vectors in bold or with an arrow word, so Discord renders fine. "
            "Wrap code in Markdown triple-backtick code blocks, keep prose concise, no HTML tags. "
            "The AskUserQuestion tool is integrated with Discord: after you call it, "
            "the bot turns the options into Discord buttons; the user's click comes back as the next message. "
            "Important: after calling AskUserQuestion, output no extra text and do not assume it failed — just wait for the user's reply. "
            "Questioning discipline (important): asking questions on Discord is costly, so stay focused — "
            "ask only 1 question at a time (Discord is one-question-one-answer, so multiple questions get eaten), tightly tied to the user's original request, no tangents; "
            "do not exceed 4-5 rounds of follow-up for the whole task; once you have enough, stop asking and act or conclude. "
            "If the user asks you to send them a file, just output a file marker in your reply, strictly in this format (keep the brackets): "
            "[[FILE: absolute path of the file]], "
            "and the bot will upload that file to Discord. You can output several markers to send multiple files. "
            "[Memory honesty] Long conversations get auto-compacted, so you may not remember changes you made earlier. "
            "When you see code in the project you don't remember, do not assert it was another session or wasn't you; "
            "verify with git log, file mtimes, or the compaction summary, and if you can't, honestly say you're unsure whether it was you — never fabricate a source. "
            "[Transparent execution] This is the user's top-priority rule: the user must be able to follow what you are doing at every step and why. "
            "Hard requirement — before every single tool call (reading/writing files, running commands, git, web search, etc.), "
            "you must first say in one short sentence, in the interface language, what this step does and why you are doing it, then act. "
            "Never fire off several tool calls in a row without narrating them; even for a chain of small steps, each step must have its own sentence of explanation beforehand. "
            "Especially for actions that modify files or run system commands, state each intent clearly before executing — never do it all silently and only report afterward. "
            "[Keep going until done] Carry a task the user gives you through to actual completion before ending the turn. "
            "Do not merely announce a plan (e.g. 'let me first do X', 'next I'll handle Y') or stop after one or two steps and hand control back — "
            "the bot shows the ended turn as 'done', so the user thinks it is complete when it is not and has to keep typing 'continue'. "
            "Unless you need AskUserQuestion to proceed, after stating each step's intent keep acting until the whole task is finished. "
            "Transparent execution asks you to narrate before acting, but narrating keeps the user informed; it is not a reason to stop. "
            "[Completion marker] When the whole task is genuinely finished, output [[DONE]] on its own line at the very end of your reply; the bot strips it before the user sees it. "
            "Especially after you have used tools to carry out a task and it is fully complete, always end with [[DONE]] — otherwise the bot assumes there is more to do and nudges you to continue."
        ),
        # 跨頻道協作（AI Lounge）。coord_rule 會在啟用時附加到 system_prompt 後面，
        # 因此一律用文字描述、不嵌入反引號/錢字號等會破壞 Windows init 的危險字元。
        "coord_rule": (
            " [Coordination] You share this machine with other Discord channels, each its own session. "
            "Before starting work that touches shared files or running destructive commands, "
            "first read the recent activity from other channels shown at the top of the user message. "
            "When you begin a notable task, announce it once with a marker (keep the brackets): "
            "[[COORD: short one-line summary]] — the bot strips it from your reply and broadcasts it to other channels. "
            "Keep summaries short and free of backticks or dollar signs."
        ),
        "coord_prompt_prefix": "[Recent activity in other channels]\n{feed}\n[End recent activity]\n\n",
        "coord_broadcast": "🛰️ **#{name}**: {task}",
        # 自動壓縮
        "compact_prompt": (
            "/compact When compacting, fully preserve: the changes actually made in this conversation "
            "(file paths, names of added/modified functions, commands, and fields), version bumps, and unfinished todos. "
            "What was DONE matters more than what was discussed — do not omit it."
        ),
        "compacting": "🗜️ **Context near the limit, auto-compacting...**",
        "compacted": "🗜️ Context auto-compacted, continuing...",
        "compact_failed": "⚠️ Auto-compact failed ({e}), continuing with the original reply...",
        # 檔案
        "file_not_found": "⚠️ File not found: `{fp}`",
        "file_too_large": "⚠️ File too large to upload (>25MB): `{name}`\nPath: `{fp}`",
        "file_upload_failed": "⚠️ Upload failed `{name}`: {e}",
        "reply_long_preview": "\n\n…(long content — full version attached 📄)",
        # 錯誤處理
        "session_auto_cleared": "\n(session auto-cleared; your next message starts a fresh conversation)",
        "official_status": "\n(official status: {inc})",
        # 回答處理
        "still_processing": "⏳ Still working, please try again shortly.",
        "cmd_recall_desc": "Fact-check: compare the real chat log (your messages + channel history) against what I remember",
        "recall_count_desc": "How many recent messages to pull (default 20)",
        "recall_gathering": "🔍 Pulling the last {count} messages (ledger + channel history) for a reality check...",
        "recall_checking": "🔍 **Comparing the records against my memory...**",
        "recall_prompt": (
            "[Reality check] The user wants you to check yourself for memory drift or hallucination. "
            "Below are two first-hand, uncompressed records.\n\n"
            "1. User message ledger (auto-saved by the bot, only the user's own messages):\n{ledger}\n\n"
            "2. Discord channel history (includes your replies, oldest to newest):\n{history}\n\n"
            "Go through what you currently believe the user said or asked for, and point out explicitly: "
            "(1) where you misremembered or misunderstood; (2) things you thought the user said but are NOT "
            "in the records (hallucination); (3) important instructions you missed. Where your understanding "
            "matches, note it briefly; if there is no significant discrepancy overall, say so plainly. "
            "Don't be sycophantic, don't re-run any task, just report the comparison."
        ),
        "you_chose_thinking": "✅ You chose: **{chosen}**\n⏳ **Thinking...**",
        "stopped": "🛑 Stopped",
        "unexpected_error": "❌ Unexpected error.\n```\n{detail}\n```",
        # AskUserQuestion
        "choose_prompt": "Please choose:",
        "type_to_reply": "❓ **{q}**\n(just type your answer)",
        "ask_hint": "_Tap a button, or reply with the number / text_",
        "question_ended": "ℹ️ This question has ended.",
        "selected": "✅ Selected: **{chosen}**",
        "select_placeholder": "Pick an option...",
        # 危險動作確認（第二階段）
        "confirm_prompt": "⚠️ **Confirm dangerous action** — Claude wants to run:\n{detail}\nThis looks destructive. Tap **Execute** to allow, **Cancel** to skip. Auto-cancels in {mins} min with no response.",
        "confirm_exec": "✅ Execute",
        "confirm_cancel": "❌ Cancel",
        "confirm_done_exec": "✅ **Executed** (you approved)",
        "confirm_done_cancel": "❌ **Cancelled** (you declined)",
        "confirm_deny_cancel": "The user tapped Cancel, so this dangerous action was NOT run. Stop and ask the user how to proceed.",
        "confirm_deny_timeout": "The user did not confirm within {mins} minutes, so this dangerous action was skipped for safety. Do not retry it automatically; ask the user.",
        "confirm_no_channel": "Could not reach the Discord channel to confirm, so this dangerous action was skipped for safety.",
        "ask_delegated_reason": "This is NOT an error. Your question has already been presented to the user as Discord buttons. Your turn ends here: stop emitting any text now, do not retry, do not re-ask in plain text, and do not assume an answer or decide on the user's behalf. When the user taps a button, their choice arrives as the next message for you to continue from.",
        # 危險確認開關（/confirm）
        "cmd_confirm_desc": "Toggle the dangerous-action confirmation prompt on/off",
        "confirm_switch_on": "on (ask before destructive commands)",
        "confirm_switch_off": "off (run without asking)",
        "confirm_toggle_on": "🔒 Dangerous-action confirmation is now **ON** — destructive commands will ask first.",
        "confirm_toggle_off": "🔓 Dangerous-action confirmation is now **OFF** — commands run without asking.",
        # 倒數
        "reset_soon": "resetting soon",
        "in_days": "in {d}d {h}h",
        "in_hours": "in {h}h {m}m",
        "in_mins": "in {m}m",
        # 排程迴圈
        "run_schedule": "⏰ **Running scheduled task**: {task}",
        "schedule_run_failed": "❌ Scheduled task failed: {e}",
        # 側欄
        "new_chat": "🆕 New chat",
        "untitled_chat": "new-chat",
        "new_chat_channel": "🆕-new-chat",
        # 側欄分類／入口頻道的預設名稱（可被環境變數 SIDEBAR_CATEGORY / SIDEBAR_ENTRY 覆蓋）
        "sidebar_category_default": "CC Chats",
        "sidebar_entry_default": "➕new-chat",
        "chat_n": "Chat {id}",
        "restored_default": "Restored chat",
        "restored_to_channel": "✅ Restored the conversation into a new channel → {mention} (click to continue)",
        "already_open": "↪️ This conversation is already open at {mention} — jump over there instead of opening a duplicate.",
        "folder_missing": "\n⚠️ Original folder is gone, using `{cwd}` instead",
        "switched_to": "✅ Switched: **{title}**\n📂 `{cwd}`{note}\n\nSend a message to continue.",
        "btn_new_chat": "➕ New chat",
        "owner_only_new_chat": "❌ Only the owner can start a new chat.",
        "no_category": "❌ No category found, can't create a channel.",
        "open_channel_failed": "❌ Failed to create channel (maybe missing the \"Manage Channels\" permission).",
        "new_chat_ready": "✅ New chat ready → {mention} (go there and type your first message)",
        "entry_message": (
            "**🗂️ CC Conversations**\n"
            "• Just type anything here → instantly start a new chat (this channel becomes that conversation, and a fresh entry channel is added on top)\n"
            "• To resume an old chat → use `/sessions` (all) or `/search <query>` (by meaning); picking one restores it into a new channel"
        ),
        # 權限
        "no_permission": "❌ No permission.",
        "owner_only": "❌ Only the owner can run this command.",
        # 指令
        "cmd_rename_desc": "Rename the current conversation (blank = auto-generate a title from content)",
        "rename_no_session": "⚠️ No active conversation to name.",
        "rename_gen_failed": "❌ Failed to generate a title from content. Try `/rename <custom name>`.",
        # 自動標題生成 prompt（語言跟著介面走：en 生英文標題、zh-TW 生中文標題）
        "title_prompt": (
            "Based on the conversation below, write one short, precise title that captures "
            "the topic. At most 5 words. Output only the title itself - no quotes, "
            "no period, no explanation:\n\n"
        ),
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
        "status_worktree": "🌿 Worktree: `{branch}` (base `{base}`)",
        "status_session": "🔗 Session: `{id}...`",
        "status_session_none": "🔗 Session: none",
        "status_model": "🤖 Model: `{model}` (fallback: `{fb}`)",
        "status_effort": "🧠 Effort: `{effort}`",
        "status_context": "📈 Context: `[{bar}]` `{ctx}` / {limit} tokens",
        "default_inline": "default",
        "choice_default": "Default",
        "cmd_sessions_desc": "List past conversations and switch",
        "scope_mine": "My conversations",
        "scope_all": "All on this PC (incl. desktop app)",
        "no_sessions": "📭 No past conversations.",
        "pick_restore": "Pick a conversation to restore...",
        "untitled": "(untitled)",
        "sessions_header_all": "🖥️ **All conversations on this PC** (incl. desktop app)",
        "sessions_header_mine": "📋 **Past conversations** (only this bot's)",
        "cmd_search_desc": "Search past conversations by meaning (semantic search)",
        "search_none": "🔍 No conversation mentions “{kw}”.",
        "search_header": "🔍 **Results for “{kw}”** ({n})",
        "cmd_model_desc": "Set the default Claude model (account-wide)",
        "model_sonnet46": "Sonnet 4.6 (recommended)",
        "model_haiku": "Haiku 4.5 (fast)",
        "model_set": "✅ Default model: `{model}`  (use /model_session to override one chat)",
        "cmd_model_session_desc": "Override the model for THIS chat only",
        "model_session_set": "✅ This chat's model: `{model}`",
        "cmd_effort_desc": "Set the default thinking effort (account-wide)",
        "effort_low": "low (fast)",
        "effort_max": "max (strongest)",
        "effort_set": "✅ Default effort: `{effort}`  (use /effort_session to override one chat)",
        "cmd_effort_session_desc": "Override the thinking effort for THIS chat only",
        "effort_session_set": "✅ This chat's effort: `{effort}`",
        "choice_follow_default": "Follow account default",
        "cmd_plan_desc": "Set your Claude subscription plan (controls the 1M context window)",
        "plan_unknown": "Not sure / clear setting",
        "plan_set_auto": "✅ Plan set to `{plan}`. Opus now auto-uses the 1M context window; other models stay at 200K (add the `[1m]` alias when you need 1M).",
        "plan_set_std": "✅ Plan set to `{plan}`. Context stays at the standard 200K. To use 1M, add the `[1m]` alias to the model — on this plan that needs 1M usage credits.",
        "cmd_cd_desc": "Change the working directory",
        "cd_not_found": "❌ Directory doesn't exist: `{path}`",
        "cd_done": "📂 Switched to `{p}`",
        "cmd_pwd_desc": "Show the current working directory",
        "pwd_with_wt": "📂 `{cwd}`\n🌿 branch `{branch}`",
        # worktree 平行協作
        "cmd_worktree_desc": "Parallel work: give this channel its own git worktree (branch)",
        "wt_on_done": "🌿 Worktree on. Branch `{branch}` (from `{base}`)\n📂 `{path}`\nEdits here stay isolated until /worktree merge or /worktree off.",
        "wt_already_on": "🌿 Already on a worktree: branch `{branch}`\n📂 `{path}`",
        "wt_not_on": "This channel isn't on a worktree.",
        "wt_off_done": "✅ Worktree removed. Branch `{branch}` kept (work preserved).\n📂 Back to `{cwd}`",
        "wt_off_dirty": "⚠️ Can't remove — there are uncommitted changes. Commit or discard them first.\n```\n{err}\n```",
        "wt_merge_done": "✅ Merged `{branch}` into `{base}`, removed the worktree and deleted the branch.\n📂 Back to `{cwd}`",
        "wt_merge_conflict": "⚠️ Merge conflict (`{branch}` → `{base}`). Aborted — nothing changed.\nConflicting files:\n```\n{files}\n```\nFix it in the worktree (merge `{base}` in, resolve, commit), then /worktree merge again.",
        "wt_merge_wt_dirty": "⚠️ The worktree has uncommitted changes. Commit them first, then /worktree merge.",
        "wt_merge_repo_dirty": "⚠️ The main repo (`{base}`) has uncommitted changes. Commit or stash them there first.",
        "wt_merge_not_on_base": "⚠️ The main repo isn't on `{base}` (it's on `{cur}`). Switch it back to `{base}` first.",
        "wt_list_none": "No worktrees (this dir isn't a git repo, or none created yet).",
        "wt_list_title": "**🌿 Worktrees**",
        "wt_list_item": "• `{branch}` → `{path}`",
        "wt_err_not_repo": "❌ Current dir isn't a git repo. Use /cd into a repo first.",
        "wt_err_no_base": "❌ Can't read the current branch (detached HEAD?). Checkout a branch first.",
        "wt_err_path_exists": "❌ A worktree folder for this name already exists. Use /worktree off first, or pick another name.",
        "wt_err_git": "❌ git worktree failed:\n```\n{err}\n```",
        "cmd_screenshot_desc": "Capture the PC's current screen",
        "screenshot_failed": "❌ Screenshot failed.",
        "screenshot_too_large": "❌ Screenshot file too large to upload.",
        "screenshot_caption": "🖥️ Current screen:",
        "cmd_usage_desc": "Show plan usage (5h / 7d)",
        "usage_unavailable": "❌ Couldn't fetch usage (maybe rate-limited; try again in 3 min)",
        "usage_title": "**📊 Plan usage**\n",
        "usage_5h": "5-hour limit",
        "profile_usage_title": "📊 Plan usage",
        "profile_usage_line": "{label}: {pct}% (resets {countdown})",
        "profile_usage_updated": "Updated {hhmm}",
        "usage_7d": "7-day limit (all models)",
        "usage_7d_sonnet": "7-day Sonnet",
        "usage_7d_opus": "7-day Opus",
        "usage_line_reset": "**{label}**　resets: {reset} ({countdown})",
        "usage_empty": "⚠️ Got usage but it was empty; the API format may have changed.",
        "usage_cache_note": "\n_data cached for 3 min_",
        "cmd_schedule_desc": "Create a scheduled task (natural language)",
        "schedule_parse_prompt": (
            "You are a schedule parser. The current time is {now} (host local time). "
            "Compute the next run time relative to this.\n"
            "Parse the following scheduling request into pure JSON "
            "(no explanation, no markdown code block):\n"
            "{{\"task\": \"task description\", \"cron\": \"cron expression\", \"next_run\": \"ISO 8601 time\"}}\n"
            "cron format is 'min hour day month weekday'; next_run is ISO format in the host's local time and must be later than the current time.\n"
            "If it is a one-time task (e.g. \"8pm tonight\", \"3pm tomorrow\"), leave cron empty and set next_run to that moment.\n"
            "Scheduling request: {task}"
        ),
        "schedule_parse_failed": "❌ Claude Code couldn't parse the schedule, please try again.\n```\n{result}\n```",
        "schedule_created_title": "⏰ Schedule created",
        "field_task": "Task",
        "field_cron": "Cron",
        "once": "one-time",
        "field_next_run": "Next run",
        "unknown": "unknown",
        "schedule_create_failed": "❌ Failed to create schedule: {e}",
        "cmd_schedules_desc": "List and manage schedules",
        "schedules_none": "📭 No schedules yet.",
        "schedules_title": "**⏰ Schedules**\n",
        "schedule_line": "`{id}` — {task}\n　　next: `{next}` | Cron: `{cron}`",
        "btn_delete": "Delete {id}",
        "schedule_deleted": "✅ Deleted schedule `{id}`",
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
        "cmd_handoff_desc": "Generate a handoff brief of this conversation to continue on another machine",
        "handoff_generating": "📝 Generating a handoff brief (using your current session model)...",
        "handoff_empty": "Nothing to hand off yet — this channel has no active conversation.",
        "handoff_caption": "📋 **Handoff brief** — paste this into the Claude Code box on the other machine to continue:",
        "handoff_prompt": (
            "You are about to hand this conversation off to a fresh Claude Code instance on another computer. "
            "Write a complete, self-contained handoff brief in English, addressed to that other Claude Code in the second person. "
            "It cannot read any of our files, so embed every necessary detail (key code, names, paths, settings) directly in the brief — "
            "never tell it to 'go look at some file'. "
            "Use these sections: Background and goal; Current progress and conclusions; Key decisions and why; "
            "To-do and next steps; Notes and constraints (including the user's preferences). "
            "Output only the brief itself. Here is the conversation (head and tail, middle elided as [...]):"
        ),
        "cmd_help_desc": "Show all commands",
        "help_text": (
            "**🤖 Claude Code Bot**\n\n"
            "Just send a message → it goes to Claude (no @ needed)\n\n"
            "`/guide [topic]` — the user manual (start here if unsure what something does)\n"
            "`/new` — reset the conversation\n"
            "`/rename [name]` — rename the conversation (blank = auto-title from content)\n"
            "`/stop` — stop the current task immediately\n"
            "`/continue` — resume the previous session\n"
            "`/sessions` — switch to a past conversation\n"
            "`/search <query>` — find past conversations by meaning\n"
            "`/handoff` — generate a handoff brief to continue on another machine\n"
            "`/status` — current status\n"
            "`/model` · `/effort` — set the account-wide default model / effort\n"
            "`/model_session` · `/effort_session` — override just this chat\n"
            "`/plan` — set your subscription plan (controls the 1M context window)\n"
            "`/drive on|off` — drive mode: voice in, voice out (loads/unloads local models)\n"
            "`/cd <path>` — change the working directory\n"
            "`/pwd` — current directory\n"
            "`/screenshot` — capture the PC screen\n"
            "`/usage` — plan usage\n"
            "`/schedule <natural language>` — create a scheduled task\n"
            "`/schedules` — list and delete schedules\n"
            "`/addchannel` — add this channel (run multiple channels in parallel)\n"
            "`/removechannel` — remove this channel\n"
            "`/adduser @user` — grant access (owner only)\n"
            "`/removeuser @user` — revoke access (owner only)\n"
            "`/listusers` — list users with access"
        ),
        # 一般訊息
        # /guide 內建使用說明書（總覽 + 各主題頁；ephemeral 不洗版）
        "cmd_guide_desc": (
            "The built-in user manual - pick a topic for a plain-language walkthrough"
        ),
        "guide_topic_basics": (
            "Basics"
        ),
        "guide_topic_sessions": (
            "Conversations"
        ),
        "guide_topic_model": (
            "Models & usage"
        ),
        "guide_topic_voice": (
            "Voice (drive)"
        ),
        "guide_topic_files": (
            "Files"
        ),
        "guide_topic_schedule": (
            "Scheduling"
        ),
        "guide_topic_worktree": (
            "Parallel work"
        ),
        "guide_topic_safety": (
            "Safety"
        ),
        "guide_overview": (
            "📖 **ClaudeCC User Guide**\n"
            "\n"
            "This bot turns Discord into a remote control: you type, Claude Code acts on your PC.\n"
            "Use `/guide` with a topic for details:\n"
            "\n"
            "• **Basics** — starting out, reading the progress message\n"
            "• **Conversations** — finding old chats, renaming, handoff\n"
            "• **Models & usage** — switching models, effort, quota\n"
            "• **Voice (drive)** — voice in, voice out, fully local\n"
            "• **Files** — sending/receiving files, screenshots\n"
            "• **Scheduling** — natural-language scheduled tasks\n"
            "• **Parallel work** — one branch per channel\n"
            "• **Safety** — access control and confirmations"
        ),
        "guide_basics": (
            "📖 **Basics**\n"
            "\n"
            "**Talking**: just type in a conversation channel — no @ needed. Every message becomes a Claude Code turn on your PC: it can read/write files, run commands, and browse the web.\n"
            "\n"
            "**New conversation**: press the button in the ➕ entry channel; a new channel appears above — one channel = one independent conversation. After your first message the channel auto-renames to a fitting title.\n"
            "\n"
            "**Reading the progress message**:\n"
            "• 📥 the top line echoes the command being processed — verify it's really yours\n"
            "• 💬 the second line shows the conversation title and model in use\n"
            "• ⚙️📖✏️ are tools being used; a leading ⚠️ means it modifies files or runs system commands — worth a glance\n"
            "• 💭 is a thought snippet; ✍️ means the reply is being generated\n"
            "\n"
            "**Stop mid-task**: `/stop` halts the current job immediately.\n"
            "**Resume**: `/continue` picks up the previous session; see `/guide Conversations` for more.\n"
            "**Long replies** are attached as a .md file automatically."
        ),
        "guide_sessions": (
            "📖 **Conversations**\n"
            "\n"
            "**Finding old chats**:\n"
            "• `/sessions` — list past conversations (default: this bot's own; pick \"All on this PC\" to include the desktop app's)\n"
            "• `/search roughly what it was about` — semantic search; describe the content, no exact keywords needed\n"
            "Picking one restores it into a fresh channel; the original stays untouched.\n"
            "\n"
            "**Renaming**: `/rename new name` — or leave blank to auto-generate a title from content. The channel name and bot presence update together.\n"
            "\n"
            "**Handoff to another machine**: `/handoff` condenses the conversation into a brief you can paste into Claude Code elsewhere and continue seamlessly (necessary details are embedded — the other machine can't read this one's files).\n"
            "\n"
            "**Closing a conversation**: just delete the Discord channel. The transcript stays on disk and can be restored later via /sessions or /search."
        ),
        "guide_model": (
            "📖 **Models & usage**\n"
            "\n"
            "**Model**: `/model` sets the account-wide default — Sonnet (daily driver), Opus (strongest, heavier on quota), Haiku (fast). Use `/model_session` to override just the current chat (kept across restarts until you change it back).\n"
            "**Effort**: `/effort` sets the default thinking effort (low to max; higher thinks deeper but slower); `/effort_session` overrides the current chat.\n"
            "**Plan**: `/plan` tells the bot your subscription so it applies the official 1M-context rule (automatic for Opus on Max/Team/Enterprise).\n"
            "**Quota**: `/usage` shows 5-hour and 7-day usage bars with reset countdowns.\n"
            "**Status**: `/status` shows conversation, directory, model, and a context usage bar at a glance.\n"
            "\n"
            "**When context fills up**: nothing to do — it auto-compacts near the limit and keeps going; if it truly overflows, the session resets automatically and your next message starts fresh."
        ),
        "guide_voice": (
            "📖 **Voice (drive mode)**\n"
            "\n"
            "**What for**: work with Claude hands-free while driving or doing chores — voice in, voice out, fully local (Whisper listens, F5-TTS speaks), nothing goes to cloud STT/TTS.\n"
            "\n"
            "**On**: `/drive on` (loads models onto the GPU; first run downloads them)\n"
            "**Use**: hold Discord's mic button and send a voice message → the bot shows \"🎤 Heard: …\" → you get the normal text reply plus a spoken summary as an audio file\n"
            "**Off**: `/drive off` (unloads models, frees VRAM)\n"
            "\n"
            "**Notes**:\n"
            "• The switch is global and survives restarts\n"
            "• Speech recognition can mishear; Claude is told to infer intent from context\n"
            "• Typed messages never trigger voice replies — only voice input does"
        ),
        "guide_files": (
            "📖 **Files**\n"
            "\n"
            "**Sending**: drag files into the channel and Claude can read them. Images are understood visually; PowerPoint files are auto-converted to PDF for page-by-page reading.\n"
            "**Receiving**: ask it to \"send me the X file\" and it uploads straight to the channel (25MB limit).\n"
            "**Screen**: `/screenshot` captures the PC's current screen — handy for checking progress while away.\n"
            "**Directories**: `/cd path` changes the working directory, `/pwd` shows it. Per-channel, remembered across restarts."
        ),
        "guide_schedule": (
            "📖 **Scheduling**\n"
            "\n"
            "**Create**: `/schedule tidy my desktop every morning at 8` — plain language works; the bot parses it into a one-time or recurring schedule.\n"
            "**Manage**: `/schedules` lists everything with delete buttons.\n"
            "**Execution**: when due, the task runs in its original channel and reports back. If the channel happens to be busy, it waits for the next free slot instead of colliding."
        ),
        "guide_worktree": (
            "📖 **Parallel work (git worktree)**\n"
            "\n"
            "**Scenario**: two conversations editing the same repo at once, without stepping on each other's files.\n"
            "\n"
            "**Usage**:\n"
            "• `/worktree on` — gives this channel its own branch + separate folder; edits here are fully isolated (the channel gets a 🌿 prefix)\n"
            "• `/worktree merge` — done: merge back into the base branch and clean up automatically\n"
            "• `/worktree off` — stop without merging (the branch is kept; no work is lost)\n"
            "• `/worktree list` — see what exists\n"
            "\n"
            "**Safety gates**: with uncommitted changes, merge/off are refused — your work is never silently dropped; merge conflicts abort cleanly and list the conflicting files."
        ),
        "guide_safety": (
            "📖 **Safety**\n"
            "\n"
            "**Understand the nature first**: messages here = actions on your PC. Access control is two allowlists:\n"
            "• `/adduser` / `/removeuser` / `/listusers` — who may use the bot (granting access = letting them operate your PC; be deliberate)\n"
            "• `/addchannel` / `/removechannel` — which channels work (multiple channels can run tasks in parallel)\n"
            "\n"
            "**Dangerous-action confirmation**: with `/confirm on`, destructive commands (delete, format, git push, shutdown…) pop a button for your approval first, auto-cancelling on timeout. Off by default. It guards against slips — it is not a sandbox.\n"
            "\n"
            "**Transparency works for you**: the 📥 command echo guards against \"it's running something else\"; ⚠️ makes file edits and system commands visible at a glance; plans are stated before acting. Anything looks off — `/stop` any time."
        ),
        "busy_prev": "⏳ Still handling the previous message, please try again shortly.",
        "heard": "🎤 Heard: {heard}",
        "voice_hint": "(The following was transcribed from voice input and may contain homophones or misrecognized words; please infer my intended meaning from context before responding): {heard}",
        "attach_failed": "❌ Attachment download failed: {failed}",
        "uploaded_files": "The user uploaded the following files (paths):\n{paths}",
        "thinking": "⏳ **Thinking...**",
        "thinking_inline": "Thinking",
        "generating": "✍️ generating `{n} chars`",
        "notify_need_answer": "needs your answer",
        "notify_done": "done",
        "notify_error": "{mention} ⚠️ Ended (an error occurred)",
        "voice_fail": "{filename} (voice transcription failed: {ex})",
        "attach_fail_item": "{filename} ({ex})",
        "instance_running": "Another bot instance is already running; aborting this start.",
        "ready_log": "Discord bot online: {user}",
    },
    "zh-TW": {
        "require_env": "缺少必要環境變數 {key}；請複製 .env.example 為 .env 並填入（詳見 README）",
        "err_input_too_large": "📥 輸入太大超過處理上限，請拆小內容或改用純文字。",
        "err_context_full": "📦 對話太長了（context 已滿），請用 /new 開新對話。",
        "err_overloaded": "⚠️ Anthropic 服務暫時過載，已自動重試／切換備援模型仍未成功，請稍候再試。",
        "err_rate_limit": "⏳ 觸發速率限制，稍等一下再試。",
        "err_auth": "🔑 認證出問題了，需要人工檢查。",
        "err_startup": "📁 CC 啟動失敗（多為工作目錄無效），已退回預設目錄，請重試或用 /cd 切換。",
        "err_timeout": "⏱️ CC 連續 10 分鐘沒有任何輸出，已視為卡住中止（長度本身不再受限）。可直接重試或拆小任務。",
        "err_init_timeout": "⏱️ CC 初始化超時，請再試一次。",
        "err_unknown": "❌ 發生未預期錯誤，已記錄 log。",
        "retry_notice": "⚠️ **{kind}**，{delay}s 後自動重試（{n}/{max}）...",
        "no_response": "（無回應）",
        "empty_retry_nudge": "（你上一回合只有內部思考、沒有輸出任何文字就結束了。請現在把要回覆的內容完整用文字寫出來。）",
        "continue_nudge": "（若整個任務已經完成，請只輸出 [[DONE]]、不要多做。若還有你宣告過卻尚未執行的步驟，請現在繼續把它們做完。）",
        "max_tokens_hint": "⚠️ 模型把整個輸出 token 額度都花在內部思考上，沒有留下可見的回覆。請用 /effort 調低思考程度，或把問題拆成小一點的步驟，再送一次。",
        "no_message": "（無訊息）",
        "stt_prompt": "以下是繁體中文的語音。",
        "cmd_drive_desc": "開車模式：載入語音模型並用語音回覆（on/off）",
        "drive_on_loading": "開車模式開啟 — 載入語音模型中（首次會下載約 1.8GB）...",
        "drive_on_ready": "開車模式就緒。語音進、語音出。",
        "drive_xtts_fail": "開車模式已開，但 TTS 引擎載入失敗，回覆暫時只有文字（語音輸入仍可用）：{ex}",
        "drive_off": "開車模式關閉 — 語音模型已卸載、VRAM 已釋放，回到純文字。",
        "drive_off_voice": "開車模式關閉中，語音訊息不會被辨識。要用請先 /drive on。",
        "drive_unavailable": "開車模組未安裝（drive_core.py 已移除）。語音功能停用，文字照常運作。",
        "drive_rule": (
            "【開車模式】當使用者這則訊息標明是語音輸入辨識而來，"
            "代表他正在開車、用聽的而非用讀的。請在正常的完整文字回覆之後，"
            "另外附一段朗讀版，嚴格用 <<<SPEAK>>> 你的口語摘要 <<<ENDSPEAK>>> 包住。"
            "朗讀版是給耳朵聽的：自然口語句子，不要程式碼、不要符號、不要條列、"
            "不要 markdown、不要網址，簡潔講重點即可。"
            "若使用者這則不是語音輸入，就不要附 SPEAK 區塊。"
        ),
        "system_prompt": (
            "你是透過 Discord bot 被呼叫的 Claude Code。"
            "使用者透過 Discord 控制這台 Windows 電腦上的檔案和程式。"
            "所有回覆使用繁體中文；內部思考（thinking）也一律使用繁體中文。"
            "【環境】這是 Windows。檔案操作與執行指令優先使用 PowerShell 工具；"
            "若要用 Bash 工具，路徑一律改用正斜線或用引號包住，"
            "因為 Windows 的反斜線路徑在 bash 會被當跳脫字元吃掉而失敗。"
            "【多人對話】訊息可能以方括號使用者名稱開頭，代表不同的人在說話。"
            "請據此識別誰在問什麼，保持對話對象的一致性。"
            "【格式規範】Discord 不支援 LaTeX，請勿輸出 LaTeX 數學語法"
            "（不要用錢字號 dollar 包住算式）。數學式請用純文字與常見符號書寫，"
            "例如散度寫成 div、希臘字母直接拼出（sigma、tau、theta）、"
            "平方寫成 ^2、分數寫成 a/b、向量用粗體或加箭頭文字，讓 Discord 能正常顯示。"
            "程式碼請用 Markdown 的三個反引號程式碼區塊包住，一般文字保持簡潔，不用 HTML 標籤。"
            "AskUserQuestion 工具已與 Discord 整合：你呼叫它之後，"
            "bot 會自動把選項轉成 Discord 按鈕顯示給使用者，使用者點選後答案會作為下一則訊息傳回給你。"
            "重要：呼叫 AskUserQuestion 後，不要輸出任何額外文字，也不要假設工具失敗，直接等待使用者的回應即可。"
            "提問紀律（重要）：在 Discord 上問問題成本高，務必聚焦——"
            "每次只問 1 題（Discord 一問一答，一次只能收一題答案，問多題會被吞掉），問題必須緊扣使用者原始需求，不要發散或歪題；"
            "整個任務累積追問不要超過 4~5 輪，蒐集到足夠資訊就停止提問、直接動手或給結論。"
            "若使用者要你把檔案傳給他，只要在回覆中輸出檔案標記，格式嚴格如下（方括號照打）："
            "[[FILE: 檔案的絕對路徑]]，"
            "bot 會自動把該檔案上傳到 Discord。可一次輸出多個標記傳多個檔案。"
            "【記憶誠實】長對話的歷史會被自動壓縮，你可能對自己稍早做過的改動沒有印象。"
            "看到專案裡你不記得的程式碼時，不要斷言是別的 session 做的、或不是你做的；"
            "先用 git log、檔案修改時間、或壓縮摘要查證，查不到就如實說無法確定是否為你先前所做，絕不杜撰來源。"
            "【透明執行】這是使用者最在意、優先級最高的規則：使用者要能隨時看懂你每一步在做什麼、為什麼。"
            "硬性要求——每一次工具呼叫（讀寫檔案、執行指令、git、搜尋網路等）之前，"
            "都必須先用一句簡短中文說明這一步要做什麼、為什麼這樣做，再動手。"
            "不可以連續呼叫多個工具卻中間都不講話；即使是一連串小步驟，每一步之前也都要有它自己的中文說明。"
            "尤其改動檔案或執行系統指令，務必逐一講清楚意圖再執行，絕不悶著頭一次做完才說。"
            "【持續執行到完成】使用者交給你的任務要一路做到真正完成，再結束這一回合。"
            "不要只宣告計畫（例如「我先來做X」「接下來處理Y」）、或做一兩步就停下來把控制權交回——"
            "bot 會把結束的回合顯示成『完成』，使用者會以為做完了、其實沒有，只能一直打「繼續」。"
            "除非你需要用 AskUserQuestion 問使用者才能繼續，否則講完每一步意圖後就接著動手，直到整體任務完成為止。"
            "上面的透明執行要你動手前先講一句，但「講」是為了讓使用者看懂、不是講完就停。"
            "【完成標記】整個任務真正完成時，在回覆的最後單獨輸出 [[DONE]]，bot 會在使用者看到前把它清掉。"
            "尤其當你動用工具執行任務、且已全部完成時，務必以 [[DONE]] 結尾——否則 bot 會以為還沒做完而請你繼續。"
        ),
        # 跨頻道協作（AI Lounge）。coord_rule 啟用時會附加到 system_prompt 後面，
        # 因此一律用文字描述、不嵌入反引號/錢字號等會破壞 Windows init 的危險字元。
        "coord_rule": (
            "【協作】你和其他 Discord 頻道共用這台機器，每個頻道是各自獨立的 session。"
            "在開始會動到共享檔案的工作、或執行破壞性指令前，"
            "請先讀使用者訊息最上方列出的其他頻道近期活動。"
            "當你要開始一項重要工作時，用標記宣告一次（方括號照打）："
            "[[COORD: 一行簡短說明]]——bot 會把它從你的回覆移除並廣播給其他頻道。"
            "說明請簡短，不要含反引號或錢字號。"
        ),
        "coord_prompt_prefix": "【其他頻道近期活動】\n{feed}\n【近期活動結束】\n\n",
        "coord_broadcast": "🛰️ **#{name}**：{task}",
        "compact_prompt": (
            "/compact 壓縮時務必完整保留：本次對話實際完成的改動"
            "（檔案路徑、新增或修改的函式與指令與欄位名稱）、版本號變動、尚未完成的待辦。"
            "做了什麼比討論了什麼更重要，不可省略。"
        ),
        "compacting": "🗜️ **上下文接近上限，正在自動壓縮...**",
        "compacted": "🗜️ 上下文已自動壓縮，繼續處理中...",
        "compact_failed": "⚠️ 自動壓縮失敗（{e}），繼續原始回覆...",
        "file_not_found": "⚠️ 找不到檔案：`{fp}`",
        "file_too_large": "⚠️ 檔案太大無法上傳（>25MB）：`{name}`\n路徑：`{fp}`",
        "file_upload_failed": "⚠️ 上傳失敗 `{name}`：{e}",
        "reply_long_preview": "\n\n…（內容較長，完整版見附件 📄）",
        "session_auto_cleared": "\n（已自動清除 session，下一則訊息會開新對話）",
        "official_status": "\n（官方狀態：{inc}）",
        "still_processing": "⏳ 還在處理中，請稍後再試。",
        "cmd_recall_desc": "核對：拿真實對話紀錄（你的原話＋頻道歷史）比對我的記憶，抓幻覺",
        "recall_count_desc": "要撈最近幾則訊息（預設 20）",
        "recall_gathering": "🔍 正在撈取最近 {count} 則（原話帳本＋頻道歷史）來核對...",
        "recall_checking": "🔍 **正在拿紀錄跟我的記憶比對...**",
        "recall_prompt": (
            "【真實紀錄核對】使用者要你檢查自己有沒有記憶偏差或幻覺。以下是兩份未經壓縮的第一手紀錄。\n\n"
            "一、使用者原話帳本（bot 自動存檔，只含使用者本人的發言）：\n{ledger}\n\n"
            "二、Discord 頻道歷史（含你的回覆，由舊到新）：\n{history}\n\n"
            "請逐條對照你目前 context／記憶中「使用者說過什麼、要求過什麼」，明確指出："
            "(1) 你記錯或誤解的地方；(2) 你以為使用者說過、但紀錄裡其實沒有的內容（幻覺）；"
            "(3) 你遺漏的重要指示。若某處理解與紀錄相符，簡短帶過；若整體無明顯落差，直說「無明顯落差」。"
            "不要客套、不要重新執行任務，只做核對回報。"
        ),
        "you_chose_thinking": "✅ 你選了：**{chosen}**\n⏳ **思考中...**",
        "stopped": "🛑 已停止",
        "unexpected_error": "❌ 發生未預期錯誤。\n```\n{detail}\n```",
        "choose_prompt": "請選擇：",
        "type_to_reply": "❓ **{q}**\n（請直接打字回覆）",
        "ask_hint": "_點按鈕，或直接打數字／文字回答_",
        "question_ended": "ℹ️ 此問題已結束。",
        "selected": "✅ 已選擇：**{chosen}**",
        "select_placeholder": "選擇一個選項...",
        # 危險動作確認（第二階段）
        "confirm_prompt": "⚠️ **危險動作確認** — Claude 想執行：\n{detail}\n這看起來具破壞性。按 **執行** 放行、**取消** 略過。{mins} 分鐘無回應自動取消。",
        "confirm_exec": "✅ 執行",
        "confirm_cancel": "❌ 取消",
        "confirm_done_exec": "✅ **已執行**（你已核准）",
        "confirm_done_cancel": "❌ **已取消**（你選擇不執行）",
        "confirm_deny_cancel": "使用者按了取消，因此這個危險動作未執行。請停下來詢問使用者要怎麼處理。",
        "confirm_deny_timeout": "使用者在 {mins} 分鐘內未確認，為安全起見略過這個危險動作。不要自動重試，請詢問使用者。",
        "confirm_no_channel": "無法連到 Discord 頻道進行確認，為安全起見略過這個危險動作。",
        "ask_delegated_reason": "這不是錯誤。你的問題已經以 Discord 按鈕的形式呈現給使用者了。本回合到此為止：請立刻停止輸出任何文字，不要重試、不要改用純文字再問一次、不要自行假設答案或代替使用者做決定。使用者點選按鈕後，他的選擇會作為下一則新訊息傳給你，你屆時再繼續。",
        # 危險確認開關（/confirm）
        "cmd_confirm_desc": "開啟／關閉危險動作確認提示",
        "confirm_switch_on": "開（破壞性指令先問過）",
        "confirm_switch_off": "關（不問直接執行）",
        "confirm_toggle_on": "🔒 危險動作確認已**開啟** — 破壞性指令會先問你。",
        "confirm_toggle_off": "🔓 危險動作確認已**關閉** — 指令不再詢問、直接執行。",
        "reset_soon": "即將重置",
        "in_days": "{d} 天 {h} 小時後",
        "in_hours": "{h} 小時 {m} 分後",
        "in_mins": "{m} 分後",
        "run_schedule": "⏰ **執行排程**：{task}",
        "schedule_run_failed": "❌ 排程執行失敗：{e}",
        "new_chat": "🆕 新對話",
        "untitled_chat": "新對話",
        "new_chat_channel": "🆕-新對話",
        # 側欄分類／入口頻道的預設名稱（可被環境變數 SIDEBAR_CATEGORY / SIDEBAR_ENTRY 覆蓋）
        "sidebar_category_default": "CC 對話",
        "sidebar_entry_default": "➕新對話",
        "chat_n": "對話 {id}",
        "restored_default": "救回的對話",
        "restored_to_channel": "✅ 已把對話救回成新頻道 → {mention}（點我過去繼續）",
        "already_open": "↪️ 這段對話已經開在 {mention} 了，直接點過去即可，不用再開一個重複的。",
        "folder_missing": "\n⚠️ 原資料夾不存在，已改用 `{cwd}`",
        "switched_to": "✅ 已切換：**{title}**\n📂 `{cwd}`{note}\n\n傳訊息繼續對話。",
        "btn_new_chat": "➕ 開新對話",
        "owner_only_new_chat": "❌ 只有主帳號能開新對話。",
        "no_category": "❌ 找不到分類，無法開頻道。",
        "open_channel_failed": "❌ 開頻道失敗（可能缺少「管理頻道」權限）。",
        "new_chat_ready": "✅ 已開好新對話 → {mention}（點我過去，在裡面打第一句話）",
        "entry_message": (
            "**🗂️ CC 對話**\n"
            "• 直接在這裡輸入任何內容 → 立刻開始一段新對話（這個頻道會就地變成該對話，並自動補一個新入口到最上面）\n"
            "• 想接續舊對話 → 打 `/sessions`（全部）或 `/search 查詢`（用大概意思找），選一個會救回成新頻道"
        ),
        "no_permission": "❌ 無權限。",
        "owner_only": "❌ 只有主帳號能執行此指令。",
        "cmd_rename_desc": "重新命名目前對話（留空＝讀內容自動生成中文標題）",
        "rename_no_session": "⚠️ 目前沒有進行中的對話可命名。",
        "rename_gen_failed": "❌ 讀內容生成標題失敗，可改用 `/rename 自訂名稱`。",
        # 自動標題生成 prompt（語言跟著介面走：en 生英文標題、zh-TW 生中文標題）
        "title_prompt": (
            "根據以下對話內容，下一個精準貼切、能代表整段對話主題的繁體中文標題。"
            "限 15 個字以內，只輸出標題本身，不要引號、不要句號、不要任何解釋：\n\n"
        ),
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
        "status_worktree": "🌿 Worktree：`{branch}`（base `{base}`）",
        "status_session": "🔗 Session：`{id}...`",
        "status_session_none": "🔗 Session：無",
        "status_model": "🤖 模型：`{model}`（備援：`{fb}`）",
        "status_effort": "🧠 思考：`{effort}`",
        "status_context": "📈 Context：`[{bar}]` `{ctx}` / {limit} tokens",
        "default_inline": "預設",
        "choice_default": "預設",
        "cmd_sessions_desc": "列出歷史對話並切換",
        "scope_mine": "我的對話",
        "scope_all": "電腦上全部（桌面版）",
        "no_sessions": "📭 沒有任何歷史對話。",
        "pick_restore": "選擇要救回的對話...",
        "untitled": "（無標題）",
        "sessions_header_all": "🖥️ **電腦上所有對話**（含桌面版）",
        "sessions_header_mine": "📋 **歷史對話**（只列本 bot 的對話）",
        "cmd_search_desc": "依語意搜尋歷史對話（用大概意思找，免精準關鍵字）",
        "search_none": "🔍 找不到提到「{kw}」的對話。",
        "search_header": "🔍 **搜尋「{kw}」的結果**（{n} 筆）",
        "cmd_model_desc": "設定帳號預設 Claude 模型（所有對話通用）",
        "model_sonnet46": "Sonnet 4.6（推薦）",
        "model_haiku": "Haiku 4.5（快速）",
        "model_set": "✅ 帳號預設模型：`{model}`　（用 /model_session 可單獨覆寫某個對話）",
        "cmd_model_session_desc": "只覆寫「這個對話」的模型",
        "model_session_set": "✅ 這個對話的模型：`{model}`",
        "cmd_effort_desc": "設定帳號預設思考程度（所有對話通用）",
        "effort_low": "low（快速）",
        "effort_max": "max（最強）",
        "effort_set": "✅ 帳號預設思考程度：`{effort}`　（用 /effort_session 可單獨覆寫某個對話）",
        "cmd_effort_session_desc": "只覆寫「這個對話」的思考程度",
        "effort_session_set": "✅ 這個對話的思考程度：`{effort}`",
        "choice_follow_default": "跟隨帳號預設",
        "cmd_plan_desc": "設定你的 Claude 訂閱方案（決定 1M context 視窗）",
        "plan_unknown": "不確定／清除設定",
        "plan_set_auto": "✅ 方案設為 `{plan}`。Opus 現在會自動使用 1M context；其他模型維持 200K（需要時在模型加 `[1m]` 後綴）。",
        "plan_set_std": "✅ 方案設為 `{plan}`。Context 維持標準 200K。要用 1M 請在模型加 `[1m]` 後綴——此方案需要 1M usage credits。",
        "cmd_cd_desc": "切換工作目錄",
        "cd_not_found": "❌ 目錄不存在：`{path}`",
        "cd_done": "📂 切換到 `{p}`",
        "cmd_pwd_desc": "顯示目前工作目錄",
        "pwd_with_wt": "📂 `{cwd}`\n🌿 分支 `{branch}`",
        # worktree 平行協作
        "cmd_worktree_desc": "平行協作：給這個頻道專屬的 git worktree（獨立分支）",
        "wt_on_done": "🌿 已開啟 worktree。分支 `{branch}`（源自 `{base}`）\n📂 `{path}`\n在這裡的改動會獨立隔離，直到你 /worktree merge 或 /worktree off。",
        "wt_already_on": "🌿 已經在 worktree 上了：分支 `{branch}`\n📂 `{path}`",
        "wt_not_on": "這個頻道目前沒有開 worktree。",
        "wt_off_done": "✅ 已移除 worktree。分支 `{branch}` 保留（工作不會遺失）。\n📂 回到 `{cwd}`",
        "wt_off_dirty": "⚠️ 無法移除——有未提交的變更，請先提交或捨棄。\n```\n{err}\n```",
        "wt_merge_done": "✅ 已把 `{branch}` 合併進 `{base}`，並移除 worktree、刪除分支。\n📂 回到 `{cwd}`",
        "wt_merge_conflict": "⚠️ 合併衝突（`{branch}` → `{base}`），已中止，什麼都沒改動。\n衝突檔案：\n```\n{files}\n```\n請在 worktree 裡解決（先把 `{base}` 併進來、修正、提交）後再 /worktree merge。",
        "wt_merge_wt_dirty": "⚠️ worktree 還有未提交的變更，請先提交再 /worktree merge。",
        "wt_merge_repo_dirty": "⚠️ 主 repo（`{base}`）有未提交的變更，請先在那邊提交或暫存。",
        "wt_merge_not_on_base": "⚠️ 主 repo 目前不在 `{base}`（在 `{cur}`）。請先切回 `{base}` 再合併。",
        "wt_list_none": "沒有 worktree（此目錄不是 git repo，或尚未建立）。",
        "wt_list_title": "**🌿 Worktree 清單**",
        "wt_list_item": "• `{branch}` → `{path}`",
        "wt_err_not_repo": "❌ 目前目錄不是 git repo。請先用 /cd 切到 repo。",
        "wt_err_no_base": "❌ 讀不到目前分支（detached HEAD？）。請先 checkout 一個分支。",
        "wt_err_path_exists": "❌ 這個名字的 worktree 資料夾已存在。請先 /worktree off，或換個名字。",
        "wt_err_git": "❌ git worktree 失敗：\n```\n{err}\n```",
        "cmd_screenshot_desc": "截取電腦目前畫面",
        "screenshot_failed": "❌ 截圖失敗。",
        "screenshot_too_large": "❌ 截圖檔案過大，無法上傳。",
        "screenshot_caption": "🖥️ 目前畫面：",
        "cmd_usage_desc": "查看 Plan 用量（5h / 7d）",
        "usage_unavailable": "❌ 無法取得用量（可能被 rate limit，3 分鐘後再試）",
        "usage_title": "**📊 Plan 用量**\n",
        "usage_5h": "5 小時限制",
        "profile_usage_title": "📊 方案用量",
        "profile_usage_line": "{label}：{pct}%（{countdown}重置）",
        "profile_usage_updated": "{hhmm} 更新",
        "usage_7d": "7 天限制（全模型）",
        "usage_7d_sonnet": "7 天 Sonnet",
        "usage_7d_opus": "7 天 Opus",
        "usage_line_reset": "**{label}**　重置：{reset}（{countdown}）",
        "usage_empty": "⚠️ 取得用量但內容為空，API 格式可能有變。",
        "usage_cache_note": "\n_資料快取 3 分鐘_",
        "cmd_schedule_desc": "建立排程任務（自然語言）",
        "schedule_parse_prompt": (
            "你是排程解析器。現在時間是 {now}（台灣時間 UTC+8）。\n"
            "請以此為基準計算使用者要求的下一次執行時刻，將排程需求解析成純 JSON（不加說明、不加 markdown code block）：\n"
            "{{\"task\": \"任務描述\", \"cron\": \"cron 表達式\", \"next_run\": \"ISO 8601 時間\"}}\n"
            "cron 格式為 '分 時 日 月 週'；next_run 為台灣時間（UTC+8）的 ISO 格式，且必須晚於現在時間。\n"
            "若為一次性任務（例如「今晚八點」「明天下午三點」），cron 留空字串、next_run 給該次時刻。\n"
            "排程需求：{task}"
        ),
        "schedule_parse_failed": "❌ CC 無法解析排程，請再試一次。\n```\n{result}\n```",
        "schedule_created_title": "⏰ 排程已建立",
        "field_task": "任務",
        "field_cron": "Cron",
        "once": "一次性",
        "field_next_run": "下次執行",
        "unknown": "未知",
        "schedule_create_failed": "❌ 建立排程失敗：{e}",
        "cmd_schedules_desc": "列出並管理排程",
        "schedules_none": "📭 目前沒有排程。",
        "schedules_title": "**⏰ 排程列表**\n",
        "schedule_line": "`{id}` — {task}\n　　下次：`{next}` | Cron：`{cron}`",
        "btn_delete": "刪除 {id}",
        "schedule_deleted": "✅ 已刪除排程 `{id}`",
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
        "cmd_handoff_desc": "把目前對話生成交接稿，換另一台電腦接手",
        "handoff_generating": "📝 正在生成交接稿（用你目前 session 的模型）...",
        "handoff_empty": "目前沒有可交接的內容 — 這個頻道還沒有進行中的對話。",
        "handoff_caption": "📋 **交接稿** — 把下面貼到另一台電腦的 Claude Code 輸入框就能接手：",
        "handoff_prompt": (
            "你要把這段對話交接給另一台電腦上、全新的 Claude Code 接手。"
            "請用繁體中文寫一份完整、可獨立閱讀的交接稿，以第二人稱對那台 Claude Code 說話。"
            "對方讀不到我們這邊的任何檔案，所以必要細節（關鍵程式碼、命名、路徑、設定）要直接寫進交接稿裡，"
            "不要叫對方「去看某個檔案」。"
            "請用這幾個段落組織：背景與目標；目前進度與結論；已做的關鍵決策與理由；待辦與下一步；"
            "注意事項與限制（含使用者的偏好）。"
            "只輸出交接稿本身。以下是對話內容（取頭尾，中間以 [...] 省略）："
        ),
        "cmd_help_desc": "顯示所有指令",
        "help_text": (
            "**🤖 Claude Code Bot**\n\n"
            "直接傳訊息 → 送給 Claude（不需要 @）\n\n"
            "`/guide [主題]` — 使用說明書（不知道某功能怎麼用就看這）\n"
            "`/new` — 重置對話\n"
            "`/rename [名稱]` — 重新命名對話（留空＝讀內容自動生成中文標題）\n"
            "`/stop` — 立即停止目前工作\n"
            "`/continue` — 繼續上次 session\n"
            "`/sessions` — 切換歷史對話\n"
            "`/search <查詢>` — 依語意搜尋歷史對話\n"
            "`/handoff` — 生成交接稿，換另一台電腦接手\n"
            "`/status` — 目前狀態\n"
            "`/model` · `/effort` — 設定帳號預設模型／思考程度\n"
            "`/model_session` · `/effort_session` — 只覆寫目前這個對話\n"
            "`/plan` — 設定訂閱方案（決定 1M context 視窗）\n"
            "`/drive on|off` — 開車模式：語音進語音出（載入/卸載本機模型）\n"
            "`/cd <路徑>` — 切換工作目錄\n"
            "`/pwd` — 目前目錄\n"
            "`/screenshot` — 截取電腦目前畫面\n"
            "`/usage` — Plan 用量\n"
            "`/schedule <自然語言>` — 建立排程\n"
            "`/schedules` — 列出並刪除排程\n"
            "`/addchannel` — 把目前頻道加入（可開多頻道並行跑工作）\n"
            "`/removechannel` — 把目前頻道移出\n"
            "`/adduser @user` — 新增使用權限（主帳號限定）\n"
            "`/removeuser @user` — 移除使用權限（主帳號限定）\n"
            "`/listusers` — 列出有權限的使用者"
        ),
        # /guide 內建使用說明書（總覽 + 各主題頁；ephemeral 不洗版）
        "cmd_guide_desc": (
            "內建使用說明書——挑個主題看白話解說"
        ),
        "guide_topic_basics": (
            "入門"
        ),
        "guide_topic_sessions": (
            "對話管理"
        ),
        "guide_topic_model": (
            "模型與用量"
        ),
        "guide_topic_voice": (
            "語音（開車）"
        ),
        "guide_topic_files": (
            "檔案"
        ),
        "guide_topic_schedule": (
            "排程"
        ),
        "guide_topic_worktree": (
            "平行作業"
        ),
        "guide_topic_safety": (
            "安全"
        ),
        "guide_overview": (
            "📖 **ClaudeCC 使用說明書**\n"
            "\n"
            "這隻 bot 把 Discord 變成遙控器：你打字，電腦上的 Claude Code 動手。\n"
            "用 `/guide` 加主題看詳細說明：\n"
            "\n"
            "• **入門** — 怎麼開始對話、看懂進度訊息\n"
            "• **對話管理** — 找回舊對話、命名、換電腦接手\n"
            "• **模型與用量** — 換模型、思考程度、額度查詢\n"
            "• **語音（開車）** — 語音進、語音出，全程本機\n"
            "• **檔案** — 傳檔給 Claude、拿檔案回來、截圖\n"
            "• **排程** — 用自然語言排定時任務\n"
            "• **平行作業** — 一個頻道一條分支，互不踩腳\n"
            "• **安全** — 權限控管與危險動作確認"
        ),
        "guide_basics": (
            "📖 **入門**\n"
            "\n"
            "**開始對話**：直接在對話頻道打字就行，不用 @。每一句話都會交給電腦上的 Claude Code 處理，它能讀寫檔案、跑指令、上網查資料。\n"
            "\n"
            "**開新對話**：到「➕新對話」入口頻道按按鈕，上面會多一個新頻道——一個頻道就是一段獨立對話，互不干擾。第一句話之後頻道會自動改成貼切的標題。\n"
            "\n"
            "**看懂「思考中」訊息**：\n"
            "• 📥 頂部那行是「正在處理的指令原文」，可核對它跑的是不是你說的話\n"
            "• 💬 第二行是目前對話標題與使用的模型\n"
            "• ⚙️📖✏️ 是它正在用的工具；⚠️ 開頭代表會改檔案或跑系統指令，多看一眼\n"
            "• 💭 是它的想法片段；✍️ 是回覆生成中\n"
            "\n"
            "**中途想停**：`/stop` 立即中止目前工作。\n"
            "**接續舊話**：`/continue` 恢復上一段 session；更多見 `/guide 對話管理`。\n"
            "**訊息太長**：超長回覆會自動存成 .md 檔附上，手機也好讀。"
        ),
        "guide_sessions": (
            "📖 **對話管理**\n"
            "\n"
            "**找回舊對話**：\n"
            "• `/sessions` — 列出過往對話（預設只列 bot 自己的；選「電腦上全部」連桌面版的也列）\n"
            "• `/search 大概意思` — 語意搜尋，不用記精準關鍵字，描述內容就找得到\n"
            "選中後會「救回成一個新頻道」，原頻道不受影響。\n"
            "\n"
            "**命名**：`/rename 新名字` 改標題；留空會讀內容自動生成。頻道名稱、bot 狀態列會一起更新。\n"
            "\n"
            "**換台電腦接手**：`/handoff` 會把這段對話濃縮成一份交接稿，貼到另一台電腦的 Claude Code 就能無縫接續（對方讀不到這台的檔案，必要細節已寫進交接稿）。\n"
            "\n"
            "**關掉對話**：直接刪 Discord 頻道即可。對話紀錄還在硬碟上，之後仍能用 /sessions 或 /search 救回。"
        ),
        "guide_model": (
            "📖 **模型與用量**\n"
            "\n"
            "**換模型**：`/model` 設帳號預設 — Sonnet（日常推薦）、Opus（最強、較吃額度）、Haiku（快）。用 `/model_session` 只覆寫目前這個對話（重啟仍保留，直到你改回）。\n"
            "**思考程度**：`/effort` 設帳號預設（low 到 max，越高想得越深、也越慢）；`/effort_session` 只覆寫目前這個對話。\n"
            "**訂閱方案**：`/plan` 告訴 bot 你的方案，它會按官方規則決定 Opus 有沒有 1M context（Max／Team／Enterprise 自動有）。\n"
            "**額度**：`/usage` 看 5 小時與 7 天用量條、重置倒數。\n"
            "**目前狀態**：`/status` 一次看對話、目錄、模型、context 用量條。\n"
            "\n"
            "**context 滿了怎辦**：不用管——接近上限會自動壓縮再繼續；真的爆了會自動清 session，下一句話開新對話。"
        ),
        "guide_voice": (
            "📖 **語音（開車模式）**\n"
            "\n"
            "**用途**：開車或做家事時，用嘴巴跟電腦上的 Claude 工作——語音進、語音出，全程本機處理（Whisper 聽、F5-TTS 說），不經雲端。\n"
            "\n"
            "**開**：`/drive on`（載入模型吃 GPU／VRAM，首次會下載）\n"
            "**用**：按住 Discord 的麥克風傳語音訊息 → bot 顯示「🎤 聽到：…」→ 文字回覆照常，另附一段口語摘要的語音檔\n"
            "**關**：`/drive off`（卸載模型、釋放 VRAM）\n"
            "\n"
            "**提醒**：\n"
            "• 開關是全域的，重啟會記得上次狀態\n"
            "• 語音辨識偶有同音字，已提示 Claude 依上下文推斷原意\n"
            "• 打字訊息不會觸發語音回覆，只有語音輸入會"
        ),
        "guide_files": (
            "📖 **檔案**\n"
            "\n"
            "**給檔案**：直接把檔案拖進頻道，Claude 就能讀。圖片看得懂內容；PowerPoint 會自動轉成 PDF 讓它逐頁讀。\n"
            "**拿檔案**：跟它說「把 XX 檔傳給我」，它會直接上傳到頻道（上限 25MB）。\n"
            "**看螢幕**：`/screenshot` 截取電腦目前畫面傳上來，出門在外看進度很方便。\n"
            "**切目錄**：`/cd 路徑` 換工作目錄、`/pwd` 看現在在哪。每個頻道記各自的目錄。"
        ),
        "guide_schedule": (
            "📖 **排程**\n"
            "\n"
            "**建立**：`/schedule 每天早上八點幫我整理桌面` — 用自然語言描述就行，bot 會解析成排程（一次性或週期性都可以）。\n"
            "**管理**：`/schedules` 列出全部排程、按按鈕刪除。\n"
            "**執行**：時間到了會在原頻道跑，結果直接回報。若當時頻道正忙，會自動順延到空檔再跑，不會打架。"
        ),
        "guide_worktree": (
            "📖 **平行作業（git worktree）**\n"
            "\n"
            "**情境**：想同時開兩個對話改同一個 repo，又怕互相踩到檔案。\n"
            "\n"
            "**用法**：\n"
            "• `/worktree on` — 給這個頻道一條專屬分支＋獨立資料夾，這裡的改動完全隔離（頻道名會多 🌿）\n"
            "• `/worktree merge` — 做完了：合併回主分支並自動清理\n"
            "• `/worktree off` — 不合併、先收工（分支保留，工作不會不見）\n"
            "• `/worktree list` — 看目前有哪些\n"
            "\n"
            "**安全閘**：有未提交的變更時 merge／off 都會被擋下，絕不默默丟掉你的工作；合併衝突會中止並列出衝突檔案。"
        ),
        "guide_safety": (
            "📖 **安全**\n"
            "\n"
            "**先懂本質**：這裡的訊息＝在你電腦上執行動作。權限控管是兩張白名單：\n"
            "• `/adduser`／`/removeuser`／`/listusers` — 誰可以用（給別人＝給他操作你電腦的能力，慎重）\n"
            "• `/addchannel`／`/removechannel` — 哪些頻道可以用（多頻道可並行跑工作）\n"
            "\n"
            "**危險動作確認**：`/confirm on` 開啟後，偵測到破壞性指令（刪除、格式化、git push、關機…）會先跳按鈕請你放行，逾時自動取消。預設是關的。它防的是手滑，不是沙箱。\n"
            "\n"
            "**透明化在幫你**：📥 指令核對防「它在跑別的東西」；⚠️ 讓改檔和系統指令一眼可見；動手前先講計畫。看到不對勁隨時 `/stop`。"
        ),
        "busy_prev": "⏳ 還在處理上一則訊息，請稍後再試。",
        "heard": "🎤 聽到：{heard}",
        "voice_hint": "（以下內容由語音輸入辨識而來，可能含同音字或辨識錯誤的怪字，請依上下文推斷我的原意再回應）：{heard}",
        "attach_failed": "❌ 附件下載失敗：{failed}",
        "uploaded_files": "使用者上傳了以下檔案，路徑如下：\n{paths}",
        "thinking": "⏳ **思考中...**",
        "thinking_inline": "思考中",
        "generating": "✍️ 生成中 `{n} 字`",
        "notify_need_answer": "需要你回答",
        "notify_done": "完成",
        "notify_error": "{mention} ⚠️ 已結束（發生錯誤）",
        "voice_fail": "{filename}（語音轉文字失敗：{ex}）",
        "attach_fail_item": "{filename}（{ex}）",
        "instance_running": "已有另一個 bot 實例在執行，本次啟動中止。",
        "ready_log": "Discord bot 已上線：{user}",
    },
}

def t(key: str, **kw) -> str:
    """取目前語言的字串；缺鍵退回英文，有具名參數則套用 .format。"""
    s = _STRINGS.get(BOT_LANG, _STRINGS["en"]).get(key)
    if s is None:
        s = _STRINGS["en"].get(key, key)
    return s.format(**kw) if kw else s

