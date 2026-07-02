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
            "Reply in English. "
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
            "[Transparent execution] The user must be able to tell what you're doing at any moment. "
            "Before you start any real operation (reading/writing files, running commands, git, web search), "
            "first say in one short sentence what you're about to do and why, then act; "
            "especially for actions that modify files or run system commands, state your intent clearly before executing — do not do it all silently and only report afterward."
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
        "folder_missing": "\n⚠️ Original folder is gone, using `{cwd}` instead",
        "switched_to": "✅ Switched: **{title}**\n📂 `{cwd}`{note}\n\nSend a message to continue.",
        "btn_new_chat": "➕ New chat",
        "owner_only_new_chat": "❌ Only the owner can start a new chat.",
        "no_category": "❌ No category found, can't create a channel.",
        "open_channel_failed": "❌ Failed to create channel (maybe missing the \"Manage Channels\" permission).",
        "new_chat_ready": "✅ New chat ready → {mention} (go there and type your first message)",
        "entry_message": (
            "**🗂️ CC Conversations**\n"
            "• Tap the button below → start a new chat (creates a channel above)\n"
            "• To resume an old chat → use `/sessions` (all) or `/search <query>` (by meaning) here; picking one restores it into a new channel"
        ),
        # 權限
        "no_permission": "❌ No permission.",
        "owner_only": "❌ Only the owner can run this command.",
        # 指令
        "cmd_new_desc": "Reset the conversation, start a new session",
        "new_done": "✅ Conversation reset. What would you like to do next?",
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
        "cmd_model_desc": "Pick the Claude model",
        "model_sonnet46": "Sonnet 4.6 (recommended)",
        "model_haiku": "Haiku 4.5 (fast)",
        "model_set": "✅ Model set to `{model}`",
        "cmd_effort_desc": "Pick the thinking effort level",
        "effort_low": "low (fast)",
        "effort_max": "max (strongest)",
        "effort_set": "✅ Effort set to `{effort}`",
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
        "usage_7d": "7-day limit (all models)",
        "usage_7d_sonnet": "7-day Sonnet",
        "usage_7d_opus": "7-day Opus",
        "usage_line_reset": "**{label}**　resets: {reset} ({countdown})",
        "usage_empty": "⚠️ Got usage but it was empty; the API format may have changed.",
        "usage_cache_note": "\n_data cached for 3 min_",
        "cmd_schedule_desc": "Create a scheduled task (natural language)",
        "schedule_parse_prompt": (
            "You are a schedule parser. Parse the following scheduling request into pure JSON "
            "(no explanation, no markdown code block):\n"
            "{{\"task\": \"task description\", \"cron\": \"cron expression\", \"next_run\": \"ISO 8601 time\"}}\n"
            "cron format is 'min hour day month weekday'; next_run is ISO format in the host's local time.\n"
            "If it is a one-time task, leave cron as an empty string.\n"
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
            "`/new` — reset the conversation\n"
            "`/rename [name]` — rename the conversation (blank = auto-title from content)\n"
            "`/stop` — stop the current task immediately\n"
            "`/continue` — resume the previous session\n"
            "`/sessions` — switch to a past conversation\n"
            "`/search <query>` — find past conversations by meaning\n"
            "`/handoff` — generate a handoff brief to continue on another machine\n"
            "`/status` — current status\n"
            "`/model` — pick the model\n"
            "`/effort` — pick the thinking effort\n"
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
            "所有回覆使用繁體中文。"
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
            "【透明執行】使用者要能隨時知道你在做什麼。每當你要開始一段實際操作"
            "（讀寫檔案、執行指令、git、搜尋網路）之前，先用一句簡短中文說明你接下來要做什麼、為什麼，再動手；"
            "尤其會改動檔案或執行系統指令這類動作，務必先講清楚意圖再執行，不要悶著頭一次做完才說。"
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
        "folder_missing": "\n⚠️ 原資料夾不存在，已改用 `{cwd}`",
        "switched_to": "✅ 已切換：**{title}**\n📂 `{cwd}`{note}\n\n傳訊息繼續對話。",
        "btn_new_chat": "➕ 開新對話",
        "owner_only_new_chat": "❌ 只有主帳號能開新對話。",
        "no_category": "❌ 找不到分類，無法開頻道。",
        "open_channel_failed": "❌ 開頻道失敗（可能缺少「管理頻道」權限）。",
        "new_chat_ready": "✅ 已開好新對話 → {mention}（點我過去，在裡面打第一句話）",
        "entry_message": (
            "**🗂️ CC 對話**\n"
            "• 點下面按鈕 → 開一個新對話（在上方建新頻道）\n"
            "• 想接續舊對話 → 在這裡打 `/sessions`（全部）或 `/search 查詢`（用大概意思找），選一個會救回成新頻道"
        ),
        "no_permission": "❌ 無權限。",
        "owner_only": "❌ 只有主帳號能執行此指令。",
        "cmd_new_desc": "重置對話，開新 session",
        "new_done": "✅ 對話已重置，接下來要做什麼？",
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
        "cmd_model_desc": "選擇 Claude 模型",
        "model_sonnet46": "Sonnet 4.6（推薦）",
        "model_haiku": "Haiku 4.5（快速）",
        "model_set": "✅ 模型設為 `{model}`",
        "cmd_effort_desc": "選擇思考程度",
        "effort_low": "low（快速）",
        "effort_max": "max（最強）",
        "effort_set": "✅ 思考程度設為 `{effort}`",
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
        "usage_7d": "7 天限制（全模型）",
        "usage_7d_sonnet": "7 天 Sonnet",
        "usage_7d_opus": "7 天 Opus",
        "usage_line_reset": "**{label}**　重置：{reset}（{countdown}）",
        "usage_empty": "⚠️ 取得用量但內容為空，API 格式可能有變。",
        "usage_cache_note": "\n_資料快取 3 分鐘_",
        "cmd_schedule_desc": "建立排程任務（自然語言）",
        "schedule_parse_prompt": (
            "你是排程解析器。將以下排程需求解析成純 JSON（不加說明、不加 markdown code block）：\n"
            "{{\"task\": \"任務描述\", \"cron\": \"cron 表達式\", \"next_run\": \"ISO 8601 時間\"}}\n"
            "cron 格式為 '分 時 日 月 週'，next_run 為台灣時間（UTC+8）的 ISO 格式。\n"
            "若為一次性任務，cron 留空字串。\n"
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
            "`/new` — 重置對話\n"
            "`/rename [名稱]` — 重新命名對話（留空＝讀內容自動生成中文標題）\n"
            "`/stop` — 立即停止目前工作\n"
            "`/continue` — 繼續上次 session\n"
            "`/sessions` — 切換歷史對話\n"
            "`/search <查詢>` — 依語意搜尋歷史對話\n"
            "`/handoff` — 生成交接稿，換另一台電腦接手\n"
            "`/status` — 目前狀態\n"
            "`/model` — 選擇模型\n"
            "`/effort` — 選擇思考程度\n"
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

