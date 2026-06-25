"""Discord bot — 本地跑，橋接 Claude Code CLI。"""

import asyncio
import random
import traceback
import json
import os
import re
import time
import uuid
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    AssistantMessage,
    ToolUseBlock,
    StreamEvent,
)
from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message

import wt_core  # 本地模組：git worktree 平行協作核心邏輯
import coord_core  # 本地模組：跨頻道協作（AI Lounge）核心邏輯

try:
    from croniter import croniter as _croniter
    _HAS_CRONITER = True
except ImportError:
    _HAS_CRONITER = False

# 從 .env 載入設定（沒裝 python-dotenv 時，退而仰賴系統環境變數）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
            "at most 1-2 questions at a time, tightly tied to the user's original request, no tangents; "
            "do not exceed 4-5 rounds of follow-up for the whole task; once you have enough, stop asking and act or conclude. "
            "If the user asks you to send them a file, just output a file marker in your reply, strictly in this format (keep the brackets): "
            "[[FILE: absolute path of the file]], "
            "and the bot will upload that file to Discord. You can output several markers to send multiple files. "
            "[Memory honesty] Long conversations get auto-compacted, so you may not remember changes you made earlier. "
            "When you see code in the project you don't remember, do not assert it was another session or wasn't you; "
            "verify with git log, file mtimes, or the compaction summary, and if you can't, honestly say you're unsure whether it was you — never fabricate a source."
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
            "• To resume an old chat → use `/sessions` (all) or `/search <keyword>` here; picking one restores it into a new channel"
        ),
        # 權限
        "no_permission": "❌ No permission.",
        "owner_only": "❌ Only the owner can run this command.",
        # 更新公告
        "update_header": "🚀 **Bot updated v{ver}**",
        "change_feat": "✨ Feature (minor)",
        "change_fix": "🐛 Fix (patch)",
        "change_major": "🚀 Major release",
        # 指令
        "cmd_new_desc": "Reset the conversation, start a new session",
        "new_done": "✅ Conversation reset. What would you like to do next?",
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
        "cmd_search_desc": "Search past conversation content (by keyword)",
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
        "removechannel_cant_main": "❌ Can't remove the main channel.",
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
            "`/search <keyword>` — search past conversation content\n"
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
            "每次最多問 1~2 題，問題必須緊扣使用者原始需求，不要發散或歪題；"
            "整個任務累積追問不要超過 4~5 輪，蒐集到足夠資訊就停止提問、直接動手或給結論。"
            "若使用者要你把檔案傳給他，只要在回覆中輸出檔案標記，格式嚴格如下（方括號照打）："
            "[[FILE: 檔案的絕對路徑]]，"
            "bot 會自動把該檔案上傳到 Discord。可一次輸出多個標記傳多個檔案。"
            "【記憶誠實】長對話的歷史會被自動壓縮，你可能對自己稍早做過的改動沒有印象。"
            "看到專案裡你不記得的程式碼時，不要斷言是別的 session 做的、或不是你做的；"
            "先用 git log、檔案修改時間、或壓縮摘要查證，查不到就如實說無法確定是否為你先前所做，絕不杜撰來源。"
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
        "reset_soon": "即將重置",
        "in_days": "{d} 天 {h} 小時後",
        "in_hours": "{h} 小時 {m} 分後",
        "in_mins": "{m} 分後",
        "run_schedule": "⏰ **執行排程**：{task}",
        "schedule_run_failed": "❌ 排程執行失敗：{e}",
        "new_chat": "🆕 新對話",
        "untitled_chat": "新對話",
        "new_chat_channel": "🆕-新對話",
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
            "• 想接續舊對話 → 在這裡打 `/sessions`（全部）或 `/search 關鍵字`，選一個會救回成新頻道"
        ),
        "no_permission": "❌ 無權限。",
        "owner_only": "❌ 只有主帳號能執行此指令。",
        "update_header": "🚀 **Bot 更新 v{ver}**",
        "change_feat": "✨ 新功能（次版本）",
        "change_fix": "🐛 修正（修訂）",
        "change_major": "🚀 重大改版（主版本）",
        "cmd_new_desc": "重置對話，開新 session",
        "new_done": "✅ 對話已重置，接下來要做什麼？",
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
        "cmd_search_desc": "搜尋歷史對話內容（依關鍵字）",
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
        "removechannel_cant_main": "❌ 無法移除主頻道。",
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
            "`/search <關鍵字>` — 搜尋歷史對話內容\n"
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

_NO_RESPONSE = t("no_response")   # run_claude 無輸出時的哨兵字串（多處比對用同一個值）

# SDK 的 initialize timeout 由此環境變數控制（最低 60s）。健康時 init 僅約 1.2s，
# 設 60s 讓異常卡死能較快失敗、進入重試，而非乾等 3 分鐘
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "60000")

# ── 設定（從 .env 讀取，第一次使用請複製 .env.example 為 .env 並填入）───────
def _require_env(key: str) -> str:
    """讀取必填環境變數；缺少時拋出清楚的錯誤，引導使用者去設定 .env。"""
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(t("require_env", key=key))
    return val

DISCORD_TOKEN   = _require_env("DISCORD_TOKEN")
# CLAUDE_CLI 選填，預設指向 npm 全域安裝的 claude.cmd（%APPDATA%\npm\claude.cmd）
CLAUDE_CLI      = os.environ.get("CLAUDE_CLI") or os.path.expandvars(r"%APPDATA%\npm\claude.cmd")
# DEFAULT_DIR 選填，預設為使用者家目錄
DEFAULT_DIR     = Path(os.environ.get("DEFAULT_DIR") or Path.home())
MAX_MSG        = 1900
USAGE_CACHE_SEC = 180
ALLOWED_CHANNEL = int(_require_env("ALLOWED_CHANNEL"))
ALLOWED_USER    = int(_require_env("ALLOWED_USER"))
UPDATE_CHANNEL  = int(os.environ.get("UPDATE_CHANNEL") or 0)   # 更新公告推送頻道（選填，0=停用）
# 跨頻道協作（AI Lounge）：開啟後各頻道 session 會在 prompt 收到其他頻道的近期活動，
# 並可用 [[COORD: ...]] 廣播。預設關閉 → 行為與未啟用時完全相同（零影響）。
COORD_ENABLED   = (os.environ.get("COORD_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
COORD_CHANNEL   = int(os.environ.get("COORD_CHANNEL") or 0)   # 協作廣播頻道（選填，0=只更新登錄表、不發頻道）
_coord_registry = coord_core.Registry()   # 記憶體版「頻道→近期活動」登錄表（單例）
# 側欄分類／入口頻道名稱（選填）。同一個伺服器要跑多個 bot 時，各 bot 設不同的
# SIDEBAR_CATEGORY 就不會互搶同一個分類（否則兩個 bot 會一起搶「CC 對話」而衝突）。
SIDEBAR_CATEGORY = os.environ.get("SIDEBAR_CATEGORY") or "CC 對話"   # 多 session 側欄的分類容器名
SIDEBAR_ENTRY    = os.environ.get("SIDEBAR_ENTRY") or "➕新對話"      # 側欄最上方的入口頻道名（放常駐按鈕）
FALLBACK_MODEL  = "claude-sonnet-4-6"      # 主模型過載時的備援模型
# DEFAULT_MODEL 選填：新對話的預設模型。預設用標準 200K context 的 Sonnet，
# 所有方案都能跑（不需 1M context credits）。若你的方案有 1M credits 想啟用，
# 改成對應的 1M 模型別名（例如 claude-sonnet-4-6[1m]），壓縮門檻會自動跟著放大。
DEFAULT_MODEL   = os.environ.get("DEFAULT_MODEL") or "claude-sonnet-4-6"
MAX_BUFFER_SIZE = 64 * 1024 * 1024         # stream-json 解析 buffer 上限（64MB）
RETRY_MAX_ATTEMPTS = 4                       # 529/429 退避重試次數上限
RETRY_BASE_DELAY   = 1.0                     # 退避基礎秒數
# ── context 視窗上限：依「模型 + 帳號方案」套用 Anthropic 官方規則 ───────────
# 官方規則（Claude Code: Model configuration）：
#   1) 模型別名含 [1m]（例 claude-sonnet-4-6[1m]）→ 強制 1M；帳號若無資格 CC 會自己擋。
#   2) Opus 在 Max／Team／Enterprise 方案 → 訂閱內建、自動升 1M，免額外設定。
#   3) 其餘（Sonnet 任何方案、Opus 在 Pro、不支援 1M 的舊模型）→ 標準 200K。
#      這些情況要用 1M 需另購 usage credits，做法就是加 [1m] 後綴（落回規則 1）。
# 自動壓縮門檻＝上限的 85%（1M→850k、200K→170k）；實際數值在各呼叫點用 _ctx_limit() 算。
_AUTO_1M_PLANS = frozenset({"max", "team", "enterprise"})   # Opus 在這些方案自動 1M

def _is_opus(model: str) -> bool:
    """是否為 Opus 系列（本 bot 提供的 Opus 皆為支援 1M 的 4.6+）。"""
    return "opus" in (model or "").lower()

def context_limit_for(model: str, plan: str) -> int:
    """回傳該模型在該方案下的 context 視窗上限（tokens），套用上述官方規則。"""
    m = model or DEFAULT_MODEL or ""
    if "[1m]" in m:                                  # 規則 1：明確要 1M
        return 1_000_000
    if _is_opus(m) and plan in _AUTO_1M_PLANS:       # 規則 2：Opus 在 Max/Team/Enterprise 自動 1M
        return 1_000_000
    return 200_000                                   # 規則 3：其餘標準 200K
NOTIFY_AFTER_SEC = 60                        # 任務耗時超過此秒數，完成時 @使用者推播
INACTIVITY_TIMEOUT = 600                      # CC 連續無任何輸出超過此秒數才視為卡死（不限總時長，長工作流不會被誤殺）

# ── 版本與更新內容 ──────────────────────────────────────────────────────
BOT_VERSION = "1.17.2"
CHANGE_TYPE = "fix"
CHANGELOG = """\
🔧 **/sessions and /search are now ephemeral (no leftover messages)**
• The conversation list and "restored" notices are visible only to you; the entry channel no longer keeps clutter
"""

_CHANGE_TYPE_LABEL = {
    "feat":  t("change_feat"),
    "fix":   t("change_fix"),
    "major": t("change_major"),
}

_VERSION_FILE = Path(__file__).parent / "last_version.json"

def _get_last_version() -> Optional[str]:
    try:
        return json.loads(_VERSION_FILE.read_text()).get("version")
    except Exception:
        return None

def _save_last_version(v: str) -> None:
    try:
        _VERSION_FILE.write_text(json.dumps({"version": v}))
    except Exception:
        pass

# 資料檔案放在 bot/ 同目錄下
_BOT_DIR        = Path(__file__).parent
_SESSION_FILE   = _BOT_DIR / "discord_session.json"
_USERS_FILE     = _BOT_DIR / "allowed_users.json"
_SCHEDULES_FILE = _BOT_DIR / "schedules.json"
_TITLES_FILE    = _BOT_DIR / "session_titles.json"   # session_id → 自訂/AI 生成的中文標題

# ── 允許使用者持久化 ────────────────────────────────────────────────────
def _load_allowed_users() -> set[int]:
    try:
        data = json.loads(_USERS_FILE.read_text())
        return {ALLOWED_USER} | set(data)
    except Exception:
        return {ALLOWED_USER}

def _save_allowed_users(users: set[int]) -> None:
    try:
        _USERS_FILE.write_text(json.dumps(list(users)))
    except Exception:
        pass

_allowed_users: set[int] = _load_allowed_users()

# ── 帳號方案持久化（決定 Opus 是否自動 1M context）──────────────────────────
# 方案是「整個帳號層級」設定（同一個 bot＝同一組 Claude 登入），故全域存一份、不分
# session。用 /plan 指令設定；也可用環境變數 CLAUDE_PLAN 當預設值。
_PLAN_FILE = _BOT_DIR / "account_plan.json"

def _load_plan() -> str:
    """讀帳號方案：檔案優先，其次環境變數，最後 'unknown'（行為等同舊版：只認 [1m]）。"""
    try:
        v = json.loads(_PLAN_FILE.read_text()).get("plan")
        if v:
            return v
    except Exception:
        pass
    return os.environ.get("CLAUDE_PLAN") or "unknown"

def _save_plan(plan: str) -> None:
    try:
        _PLAN_FILE.write_text(json.dumps({"plan": plan}))
    except Exception:
        pass

_account_plan: str = _load_plan()

def _ctx_limit(state: dict) -> int:
    """該 session 目前的 context 上限：依其模型（未設則用 DEFAULT_MODEL）＋帳號方案。"""
    return context_limit_for(state.get("model") or DEFAULT_MODEL, _account_plan)

# ── 開車模式持久化（語音輸入↔語音回覆的總開關）──────────────────────────────
# 開車時 /drive on 載入 Whisper+XTTS、啟用「語音進→語音出」；在家 /drive off 全部卸載、
# 釋放 VRAM，回到純文字 bot。全域設定（同一個 bot 一個開關），仿帳號方案存一份。
# 預設 False（在家、不吃效能）。
_DRIVE_FILE = _BOT_DIR / "drive_mode.json"

def _load_drive() -> bool:
    """讀開車模式狀態：檔案優先，預設 False。"""
    try:
        return bool(json.loads(_DRIVE_FILE.read_text()).get("drive"))
    except Exception:
        return False

def _save_drive(on: bool) -> None:
    try:
        _DRIVE_FILE.write_text(json.dumps({"drive": on}))
    except Exception:
        pass

_drive_mode: bool = _load_drive()

# ── 允許頻道持久化（可加開多個頻道並行跑工作）──────────────────────────────
_CHANNELS_FILE = _BOT_DIR / "allowed_channels.json"

def _load_allowed_channels() -> set[int]:
    try:
        return {ALLOWED_CHANNEL} | {int(x) for x in json.loads(_CHANNELS_FILE.read_text())}
    except Exception:
        return {ALLOWED_CHANNEL}

def _save_allowed_channels(chs: set[int]) -> None:
    try:
        _CHANNELS_FILE.write_text(json.dumps(list(chs)))
    except Exception:
        pass

_allowed_channels: set[int] = _load_allowed_channels()

# ── 排程持久化 ──────────────────────────────────────────────────────────
def _load_schedules() -> list[dict]:
    try:
        return json.loads(_SCHEDULES_FILE.read_text())
    except Exception:
        return []

def _save_schedules(schedules: list[dict]) -> None:
    try:
        _SCHEDULES_FILE.write_text(json.dumps(schedules, ensure_ascii=False, indent=2))
    except Exception:
        pass

# ── 錯誤分類 ────────────────────────────────────────────────────────────
def classify_cc_error(err: str) -> str:
    s = err.lower()
    if "exceeded maximum buffer size" in s or "failed to decode json" in s:
        return "INPUT_TOO_LARGE"
    if "prompt is too long" in s or ("400" in s and "too long" in s):
        return "CONTEXT_FULL"
    if "529" in s or "overloaded" in s:
        return "OVERLOADED"
    if "429" in s or "rate_limit" in s or "rate limit" in s:
        return "RATE_LIMIT"
    if "failed to start claude" in s or "winerror 267" in s or "目錄名稱無效" in s:
        return "STARTUP"
    if "401" in s or "credential" in s or "authentication" in s or "unauthorized" in s:
        return "AUTH"
    if "control request timeout" in s or "initialize" in s:
        return "INIT_TIMEOUT"
    return "UNKNOWN"

USER_FACING = {
    "INPUT_TOO_LARGE": t("err_input_too_large"),
    "CONTEXT_FULL":    t("err_context_full"),
    "OVERLOADED":      t("err_overloaded"),
    "RATE_LIMIT":      t("err_rate_limit"),
    "AUTH":            t("err_auth"),
    "STARTUP":         t("err_startup"),
    "TIMEOUT":         t("err_timeout"),
    "INIT_TIMEOUT":    t("err_init_timeout"),
    "UNKNOWN":         t("err_unknown"),
}

_RETRYABLE    = {"OVERLOADED", "RATE_LIMIT", "INIT_TIMEOUT"}
_RESET_SESSION = {"CONTEXT_FULL"}


class CCError(Exception):
    """已分類的 CC 錯誤，帶 kind 與給使用者看的訊息。"""
    def __init__(self, kind: str, raw: str):
        self.kind = kind
        self.raw = raw
        self.user_msg = USER_FACING.get(kind, USER_FACING["UNKNOWN"])
        super().__init__(f"{kind}: {raw[:200]}")


# ── Anthropic 平台 incident 偵測 ────────────────────────────────────────
_incident_cache: dict = {}
_INCIDENT_CACHE_SEC = 120

def is_incident_active() -> Optional[str]:
    now = time.time()
    if "ts" in _incident_cache and now - _incident_cache["ts"] < _INCIDENT_CACHE_SEC:
        return _incident_cache.get("title")
    title = None
    try:
        req = urllib.request.Request(
            "https://status.claude.com/history.atom",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode("utf-8", errors="replace")
        entries = re.findall(r"<entry>(.*?)</entry>", raw, re.DOTALL)[:5]
        for e in entries:
            m = re.search(r"<title[^>]*>(.*?)</title>", e, re.DOTALL)
            t = (m.group(1) if m else "").strip()
            body = e.lower()
            if t and "resolved" not in body and any(
                k in body for k in ("elevated", "outage", "degraded", "error")
            ):
                title = t
                break
    except Exception as ex:
        print(f"[STATUS_CHECK] failed: {ex}", flush=True)
    _incident_cache["ts"] = now
    _incident_cache["title"] = title
    return title

# ── Session 持久化（per-channel，兩個頻道各記各的，不互相覆蓋）──────────────
def _load_sessions_map() -> dict:
    try:
        d = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        # 相容舊格式 {"session_id": x} → 遷移到主頻道
        if "session_id" in d:
            return {str(ALLOWED_CHANNEL): d["session_id"]}
        return d
    except Exception:
        return {}

def _persist_session(state: dict) -> None:
    cid = state.get("_cid")
    if cid is None:
        return
    try:
        data = _load_sessions_map()
        # 整包存：session_id + model/effort/cwd，重啟後設定不會變回預設
        data[str(cid)] = {
            "session_id": state.get("session_id"),
            "model": state.get("model"),
            "effort": state.get("effort"),
            "cwd": str(state.get("cwd") or DEFAULT_DIR),
            "wt": state.get("wt"),   # worktree 模式資訊（None 表示未啟用）
        }
        _SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

_sessions: dict[int, dict] = {}

_processing: dict[int, bool] = {}
_running_tasks: dict[int, "asyncio.Task"] = {}


class _StoppedByUser(Exception):
    """使用者透過 /stop 主動中止工作。"""


_MILESTONE_RE = re.compile(r'\[\[MILESTONE:\s*(.+?)\]\]')


async def _run_tracked(
    channel_id: int,
    prompt: str,
    state: dict,
    progress_msg: Optional[discord.Message],
) -> tuple[str, Optional[str], Optional[dict]]:
    """把 run_claude 包成可取消的 task，登記到 _running_tasks 供 /stop 使用。"""
    # 協作啟用時：把其他頻道近期活動摘要注入 prompt 最前面（只在對話主流程；
    # compact／語音解析不經過本函式，因此天然不受影響）。
    if COORD_ENABLED:
        feed = _coord_registry.format_for_prompt(exclude=channel_id)
        if feed:
            prompt = t("coord_prompt_prefix", feed=feed) + prompt
    last_kind = "UNKNOWN"
    last_raw = ""
    for attempt in range(RETRY_MAX_ATTEMPTS):
        task = asyncio.create_task(run_claude(prompt, state, progress_msg))
        _running_tasks[channel_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            raise _StoppedByUser()
        except (asyncio.TimeoutError, TimeoutError):
            raise CCError("TIMEOUT", "inactivity timeout（連續無輸出）")
        except Exception as e:
            raw = str(e).strip() or (type(e).__name__ + t("no_message"))
            tb = traceback.format_exc()
            kind = classify_cc_error(raw + " " + tb)
            last_kind, last_raw = kind, raw
            print(f"[CC_FAIL] kind={kind} attempt={attempt+1}/{RETRY_MAX_ATTEMPTS} "
                  f"type={type(e).__name__} raw={raw!r}\n{tb}", flush=True)
            if kind not in _RETRYABLE:
                raise CCError(kind, raw)
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                # INIT_TIMEOUT：前一個 CC 進程可能還沒釋放資源，多等 3 秒
                extra = 10.0 if kind == "INIT_TIMEOUT" else 0.0
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5) + extra
                if progress_msg:
                    try:
                        await progress_msg.edit(
                            content=t("retry_notice", kind=kind, delay=f"{delay:.0f}",
                                      n=attempt + 1, max=RETRY_MAX_ATTEMPTS))
                    except Exception:
                        pass
                await asyncio.sleep(delay)
        finally:
            _running_tasks.pop(channel_id, None)
    raise CCError(last_kind, last_raw)

# ── 每個 channel 的 session 狀態 ──────────────────────────────────────
_usage_cache: dict = {}

def get_state(cid: int) -> dict:
    if cid not in _sessions:
        rec = _load_sessions_map().get(str(cid))
        if isinstance(rec, str):      # 舊格式：值只是 session_id 字串
            rec = {"session_id": rec}
        rec = rec or {}
        cwd = Path(rec.get("cwd")) if rec.get("cwd") else DEFAULT_DIR
        # worktree 模式：若紀錄的 worktree 目錄已被外部移除，視為未啟用並還原到啟用前的目錄
        wt_rec = rec.get("wt")
        if wt_rec and isinstance(wt_rec, dict) and not Path(wt_rec.get("path", "")).is_dir():
            prev = wt_rec.get("prev_cwd")
            if prev and Path(prev).is_dir():
                cwd = Path(prev)
            wt_rec = None
        _sessions[cid] = {
            "session_id": rec.get("session_id"),
            "cwd": cwd if cwd.is_dir() else DEFAULT_DIR,
            "model": rec.get("model") or DEFAULT_MODEL,
            "effort": rec.get("effort"),
            "wt": wt_rec if isinstance(wt_rec, dict) else None,
            "_cid": cid,
        }
    return _sessions[cid]

def _resolve_project_dir(project_path: str) -> tuple[Path, bool]:
    """回傳 (可用目錄, 是否為原路徑)。原路徑不存在時退回預設目錄。"""
    p = Path(project_path)
    if p.is_dir():
        return p, True
    return DEFAULT_DIR, False

# ── 工具圖示 ───────────────────────────────────────────────────────────
_ICONS = {
    "Read":"📖","Write":"✏️","Edit":"✏️","MultiEdit":"✏️",
    "Bash":"⚙️","PowerShell":"⚙️","Glob":"🔍","Grep":"🔍",
    "WebSearch":"🌐","WebFetch":"🌐","TodoWrite":"📝",
    "Agent":"🤖","Task":"📋","ToolSearch":"🛠️",
    "TaskCreate":"📋","TaskUpdate":"📋","TaskList":"📋",
    "AskUserQuestion":"❓",
}

def _fmt_tool(name: str, inp: dict) -> str:
    icon = _ICONS.get(name, "🔧")
    if name in ("Read","Write","Edit","Glob") and "file_path" in inp:
        detail = Path(inp["file_path"]).name
    elif name in ("Bash","PowerShell") and "command" in inp:
        detail = inp["command"][:60].replace("\n"," ")
    elif name == "Grep" and "pattern" in inp:
        detail = inp["pattern"][:50]
    elif name == "WebSearch" and "query" in inp:
        detail = inp["query"][:60]
    else:
        detail = str(list(inp.values())[0])[:60] if inp else ""
    return f"{icon} **{name}**  `{detail}`" if detail else f"{icon} **{name}**"

# ── 讀 session 中繼資料 ──────────────────────────────────────────────────
# discord bot 在每則 prompt 前加的 [名字]: 標記，用來辨識「本 bot 自己的對話」，
# 把桌面版 CC、其他專案（如 emoji bot）的 session 濾掉，不混在同一層
_BOT_PROMPT_RE = re.compile(r'^\[.+?\]:\s')

def _session_meta(jf: Path, full: bool = False) -> dict:
    """讀 session jsonl，回傳 {is_bot, title, first_prompt, cwd}。
    full=False（預設）：非 bot session 讀到第一句就提前結束以省時（給「我的對話」用）。
    full=True：讀完整檔以取得標題（給「電腦上全部」用，非 bot session 標題在後面）。"""
    title, first_prompt, cwd, is_bot = "", "", "", False
    try:
        with jf.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"aiTitle"' in line:
                    try:
                        t = json.loads(line).get("aiTitle")
                        if t:
                            title = t
                    except Exception:
                        pass
                    continue
                if '"type":"user"' in line and not first_prompt:
                    try:
                        o = json.loads(line)
                        cwd = o.get("cwd", "") or cwd
                        content = o.get("message", {}).get("content")
                        if isinstance(content, str):
                            raw = content
                        elif isinstance(content, list):
                            raw = next((b.get("text", "") for b in content
                                        if isinstance(b, dict) and b.get("type") == "text"), "")
                        else:
                            raw = ""
                        raw = raw.strip()
                        is_bot = bool(_BOT_PROMPT_RE.match(raw))
                        first_prompt = _BOT_PROMPT_RE.sub("", raw)  # 去掉 [名字]: 前綴給顯示
                    except Exception:
                        pass
                    if not is_bot and not full:
                        break  # 非 bot session 且非完整模式，不用再往下讀
    except Exception:
        pass
    return {"is_bot": is_bot, "title": title, "first_prompt": first_prompt, "cwd": cwd}

# ── 中文標題快取（讀內容生成、可手動重命名）────────────────────────────────
def _load_titles() -> dict:
    try:
        return json.loads(_TITLES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_title(session_id: str, title: str) -> None:
    try:
        data = _load_titles()
        data[session_id] = title
        _TITLES_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _read_session_text(session_id: str, max_chars: int = 3000, keep: str = "head") -> str:
    """讀出 session 的對話文字（使用者+助理）。
    keep="head"：從頭累積到 max_chars（生成標題用，行為不變）。
    keep="both"：讀完整段後取頭尾各半、中間以省略標記接起，
    讓交接稿同時保留「原始目標」與「最近進度」兩端。"""
    claude_home = Path.home() / ".claude" / "projects"
    parts: list[str] = []
    for jf in claude_home.glob(f"*/{session_id}.jsonl"):
        try:
            with jf.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"type":"user"' not in line and '"type":"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    content = o.get("message", {}).get("content")
                    if isinstance(content, str):
                        txt = content
                    elif isinstance(content, list):
                        txt = " ".join(b.get("text", "") for b in content
                                       if isinstance(b, dict) and b.get("type") == "text")
                    else:
                        txt = ""
                    txt = _BOT_PROMPT_RE.sub("", txt.strip())
                    if txt:
                        parts.append(txt)
                    # head 模式：累積到上限就停；both 模式：讀完整段再裁頭尾
                    if keep == "head" and sum(len(p) for p in parts) > max_chars:
                        break
        except Exception:
            pass
        break
    full = "\n".join(parts)
    if keep == "both" and len(full) > max_chars:
        half = max_chars // 2
        return full[:half] + "\n\n[...]\n\n" + full[-half:]
    return full[:max_chars]

async def _ask_haiku(prompt: str, model: Optional[str] = "claude-haiku-4-5-20251001") -> str:
    """一次性、不留 session 檔的輕量呼叫。
    預設用 Haiku（生成標題用）；交接稿改傳當前 session 模型以品質優先。
    model=None 時交由 CLI 用帳號預設模型。"""
    opts = ClaudeAgentOptions(
        cwd=str(DEFAULT_DIR),
        cli_path=CLAUDE_CLI,
        model=model,
        permission_mode="bypassPermissions",
        extra_args={"no-session-persistence": None},  # 不寫 session 檔，避免污染
    )
    out = ""
    async with ClaudeSDKClient(opts) as c:
        await c.query(prompt)
        async for raw in c._query.receive_messages():
            try:
                msg = parse_message(raw)
            except MessageParseError:
                continue
            if isinstance(msg, ResultMessage):
                out = msg.result or ""
                break
    return out.strip()

async def _generate_title(session_id: str) -> Optional[str]:
    """讀 session 內容，用 Haiku 生成一個貼切的繁體中文短標題。"""
    text = _read_session_text(session_id)
    if not text:
        return None
    raw = await _ask_haiku(
        "根據以下對話內容，下一個精準貼切、能代表整段對話主題的繁體中文標題。"
        "限 15 個字以內，只輸出標題本身，不要引號、不要句號、不要任何解釋：\n\n" + text)
    title = (raw or "").strip().splitlines()[0] if raw else ""
    title = title.strip('「」"\'*#＊ 　')[:25]   # 去掉引號、markdown 符號、前後空白
    return title or None

async def _generate_handoff(session_id: str, model: Optional[str]) -> Optional[str]:
    """讀目前 session 的頭尾內容，用當前 session 模型生成一份交接稿，
    讓另一台電腦的全新 CC 貼上就能接手（對方讀不到本機檔案，故細節直接寫進稿裡）。"""
    text = _read_session_text(session_id, max_chars=50000, keep="both")
    if not text:
        return None
    raw = await _ask_haiku(t("handoff_prompt") + "\n\n" + text, model=model)
    return (raw or "").strip() or None

def _list_sessions(scope: str = "mine", limit: int = 15) -> list[dict]:
    """掃所有專案列出 session，依時間新到舊。
    scope="mine"：只回本 bot 自己的對話；scope="all"：電腦上所有對話（含桌面版）。"""
    claude_home = Path.home() / ".claude" / "projects"
    if not claude_home.exists():
        return []
    full = (scope == "all")
    titles = _load_titles()
    entries: list[dict] = []
    for proj_dir in claude_home.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            meta = _session_meta(jf, full=full)
            if scope == "mine" and not meta["is_bot"]:
                continue
            entries.append({
                "session_id": jf.stem,
                "project_path": meta["cwd"] or str(DEFAULT_DIR),
                "mtime": jf.stat().st_mtime,
                # 優先用快取的中文標題（手動或讀內容生成的）
                "title": titles.get(jf.stem) or meta["title"],
                "first_prompt": meta["first_prompt"],
            })
    return sorted(entries, key=lambda e: e["mtime"], reverse=True)[:limit]

def _session_label(session_id: Optional[str]) -> str:
    """由 session_id 找出該對話的標題（優先用快取中文標題），給狀態顯示用。"""
    if not session_id:
        return t("new_chat")
    cached = _load_titles().get(session_id)
    if cached:
        return cached[:60]
    claude_home = Path.home() / ".claude" / "projects"
    for jf in claude_home.glob(f"*/{session_id}.jsonl"):
        meta = _session_meta(jf)
        label = meta["title"] or meta["first_prompt"]
        return label[:60] if label else t("chat_n", id=session_id[:8])
    return t("chat_n", id=session_id[:8])

# ── Claude 執行 ────────────────────────────────────────────────────────
async def run_claude(
    prompt: str,
    state: dict,
    progress_msg: Optional[discord.Message] = None,
) -> tuple[str, Optional[str], Optional[dict]]:
    """回傳 (content, new_session_id, ask_question_data)"""
    # cwd 防護：切換歷史 session 可能帶入不存在的目錄（WinError 267），退回預設目錄
    if not Path(state["cwd"]).is_dir():
        state["cwd"] = DEFAULT_DIR
    options = ClaudeAgentOptions(
        cwd=str(state["cwd"]),
        cli_path=CLAUDE_CLI,
        model=state.get("model"),
        effort=state.get("effort"),
        permission_mode="bypassPermissions",
        fallback_model=FALLBACK_MODEL,
        max_buffer_size=MAX_BUFFER_SIZE,
        # 注意：此 system_prompt 會透過管線傳給 CC 子進程。實測在 Windows 上，
        # 內含 LaTeX 錢字號、反引號、或無法用系統編碼表示的 Unicode 數學符號時，
        # 會讓 init 的控制訊息損毀，導致 Control request timeout: initialize 卡死 60s。
        # 因此這裡一律「用文字描述規則」，不嵌入這些危險字元本身。
        # 啟用協作時附加純文字 coord_rule、開車模式附加 drive_rule（皆不含危險字元）；
        # 兩者都未啟用則與原本完全相同。
        system_prompt=t("system_prompt")
        + (t("coord_rule") if COORD_ENABLED else "")
        + (t("drive_rule") if _drive_mode else ""),
    )
    if state.get("session_id"):
        options.resume = state["session_id"]
    # 開啟逐字串流，讓生成中的回應能即時顯示在「思考中」訊息，提供存活訊號
    options.include_partial_messages = True

    tool_log: list[str] = []
    tool_count: int = 0
    start = time.time()
    pending_question: dict = {}
    live_text: str = ""  # 生成中累積的回應文字（給動畫即時顯示）
    last_activity = [time.time()]  # CC 最後一次有輸出的時間（閒置逾時判斷用）

    async def on_stream(upd) -> None:
        nonlocal pending_question, tool_count
        if getattr(upd, "tool_calls", None):
            for tc in upd.tool_calls:
                name = tc.get("name", "?")
                inp = tc.get("input", {})
                if name == "AskUserQuestion":
                    pending_question = inp
                tool_log.append(_fmt_tool(name, inp))
                tool_count += 1
        if getattr(upd, "type","") == "assistant" and getattr(upd, "content", None):
            text = upd.content
            line = text.strip().split("\n",1)[0][:100]
            if line and not line.startswith("["):
                tool_log.append(f"💭 {line}")

    _spinner = ["🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"]

    async def _animate() -> None:
        i = 0
        while True:
            await asyncio.sleep(1.5)
            if not progress_msg:
                continue
            elapsed = int(time.time() - start)
            body = "\n".join(tool_log[-6:])
            # 生成階段：顯示回應字數 + 即時文字尾段，作為「還活著」的存活訊號
            if live_text:
                tail = live_text[-220:].replace("\n", " ")
                body += "\n\n" + t("generating", n=len(live_text)) + f"\n> {tail}"
            icon = _spinner[i % len(_spinner)]
            i += 1
            # 頂部顯示目前 session + 模型/思考程度，工作時一眼掌握在哪個對話、用什麼設定
            if state.get("_session_label"):
                _m = state.get("model")
                _ms = _m.replace("claude-", "") if _m else t("default_inline")
                _eff = state.get("effort") or t("default_inline")
                hdr = f"💬 `{state['_session_label']}`　🧠 `{_ms}·{_eff}`\n"
            else:
                hdr = ""
            try:
                await progress_msg.edit(content=f"{hdr}{icon} **{t('thinking_inline')}** `{elapsed}s`\n{body}"[:1990])
            except Exception:
                pass

    messages = []

    async def _run_client() -> None:
        nonlocal live_text
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(prompt)
                async for raw_data in client._query.receive_messages():
                    last_activity[0] = time.time()  # 有任何訊息=還活著，重置閒置計時
                    try:
                        message = parse_message(raw_data)
                    except MessageParseError:
                        continue
                    # 逐字串流：累積生成中的回應文字供動畫顯示
                    if isinstance(message, StreamEvent):
                        ev = message.event or {}
                        if ev.get("type") == "content_block_delta":
                            delta = ev.get("delta", {})
                            if delta.get("type") == "text_delta":
                                live_text += delta.get("text", "")
                        continue
                    messages.append(message)
                    if isinstance(message, ResultMessage):
                        break
                    if isinstance(message, AssistantMessage):
                        content = getattr(message, "content", [])
                        tc_list, text_parts = [], []
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, ToolUseBlock):
                                    tc_list.append({"name": block.name, "input": getattr(block,"input",{})})
                                elif hasattr(block, "text"):
                                    text_parts.append(block.text)
                        class _U:
                            def __init__(self,**kw): self.__dict__.update(kw)
                        if tc_list:    await on_stream(_U(type="tool",      tool_calls=tc_list,  content=None))
                        if text_parts: await on_stream(_U(type="assistant", content="\n".join(text_parts), tool_calls=None))
        except Exception:
            raise

    anim_task = asyncio.create_task(_animate()) if progress_msg else None
    client_task = asyncio.create_task(_run_client())
    try:
        # 閒置逾時：不限總時長，只在「連續無輸出」超過門檻才視為卡死，
        # 讓長工作流（只要持續有輸出）能像桌面版一樣一直跑下去
        while not client_task.done():
            await asyncio.sleep(5)
            if time.time() - last_activity[0] > INACTIVITY_TIMEOUT:
                raise asyncio.TimeoutError()
        await client_task  # 完成：取回結果或重新拋出 _run_client 內的例外
    finally:
        if not client_task.done():
            client_task.cancel()
            try:
                await client_task
            except BaseException:
                pass
        if anim_task:
            anim_task.cancel()

    content, new_sid = "", None
    for m in messages:
        if isinstance(m, ResultMessage):
            content = m.result or ""
            new_sid = m.session_id or new_sid
            usage = getattr(m, "usage", None) or {}
            ctx = (usage.get("input_tokens", 0)
                   + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0))
            if ctx:
                state["ctx_tokens"] = ctx
        elif isinstance(m, AssistantMessage) and not content:
            for block in m.content:
                if hasattr(block, "text"): content += block.text

    # 清除 [[MILESTONE:...]] 標記、ThinkingBlock 殘留
    content = _MILESTONE_RE.sub("", content)
    content = re.sub(r'\[ThinkingBlock\(thinking=.*?\)\]', '', content, flags=re.DOTALL).strip()
    return content or _NO_RESPONSE, new_sid, pending_question or None

# ── [[FILE:路徑]] 標記並上傳檔案 ─────────────────────────────────────────
_FILE_MARKER = re.compile(r"\[\[FILE:\s*(.+?)\s*\]\]")
_DISCORD_FILE_LIMIT = 25 * 1024 * 1024

# ── 螢幕截圖（手機遠端看電腦畫面）──────────────────────────────────────
def _capture_screenshot_sync() -> Optional[Path]:
    """截取整個虛擬螢幕（含多螢幕）成 PNG，回傳路徑；失敗回 None。"""
    import subprocess
    import tempfile
    out = Path(tempfile.gettempdir()) / f"cc_shot_{uuid.uuid4().hex[:8]}.png"
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap $vs.Width,$vs.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($vs.X,$vs.Y,0,0,$bmp.Size);"
        f"$bmp.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30)
        return out if out.exists() and out.stat().st_size > 0 else None
    except Exception:
        return None

async def _capture_screenshot() -> Optional[Path]:
    return await asyncio.to_thread(_capture_screenshot_sync)

# ── GPU 共用工具（Whisper 與 XTTS 都會用到）──────────────────────────────
def _add_cuda_dll_path() -> None:
    """把 nvidia cuda_runtime/cuBLAS/cuDNN/nvrtc 的 DLL 目錄加進 PATH，否則 GPU 推論時
    ctranslate2/torch 會找不到 cublas64_12.dll（它走 PATH，不吃 add_dll_directory）。"""
    import importlib
    dirs: list[str] = []
    for pkg in ("nvidia.cuda_runtime", "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            mod = importlib.import_module(pkg)
            bindir = Path(mod.__path__[0]) / "bin"
            if bindir.is_dir():
                dirs.append(str(bindir))
        except Exception:
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")

def _free_vram() -> None:
    """強制回收記憶體與 GPU 顯存（卸載模型後呼叫）。"""
    import gc
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass  # 純 CPU 環境沒裝 torch 就略過

# ── 語音轉文字（faster-whisper，本機 GPU，開車用）────────────────────────
_whisper_model = None  # 單例，只載入一次

def _get_whisper():
    """延遲載入 Whisper 模型（單例）；首次會下載 large-v3 並載入 GPU。GPU 起不來自動退 CPU。"""
    global _whisper_model
    if _whisper_model is None:
        _add_cuda_dll_path()
        from faster_whisper import WhisperModel
        try:
            # 8GB VRAM 跑 large-v3 float16 綽綽有餘
            _whisper_model = WhisperModel("large-v3", device="cuda", compute_type="float16")
            print("[WHISPER] large-v3 已載入 GPU", flush=True)
        except Exception as e:
            print(f"[WHISPER] GPU 失敗，改用 CPU：{e}", flush=True)
            _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    return _whisper_model

def _transcribe(path: str) -> str:
    """把音檔轉成文字（阻塞式，呼叫端要用 asyncio.to_thread 包起來）。"""
    model = _get_whisper()
    # 不鎖語言（自動偵測，相容中英混講）；initial_prompt 偏向繁體中文
    segments, _info = model.transcribe(path, beam_size=5, initial_prompt=t("stt_prompt"))
    return "".join(seg.text for seg in segments).strip()

def _unload_whisper() -> None:
    """卸載 Whisper 模型、釋放顯存（開車模式關閉時呼叫）。"""
    global _whisper_model
    if _whisper_model is not None:
        _whisper_model = None
        _free_vram()

# ── 文字轉語音（XTTS-v2，本機 GPU，開車回覆用）────────────────────────────
# 注意：XTTS-v2 模型授權為 Coqui Public Model License（CPML），禁止商用。屬選配功能，
# 預設關閉（只有開車模式會載入）；下游若要商用請改用其他引擎或自負授權責任。
_xtts_model = None  # 單例，只載入一次

def _get_xtts():
    """延遲載入 XTTS-v2（單例）；首次會下載模型約 1.8GB。GPU 起不來自動退 CPU。"""
    global _xtts_model
    if _xtts_model is None:
        _add_cuda_dll_path()
        from TTS.api import TTS
        try:
            _xtts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda")
            print("[XTTS] xtts_v2 已載入 GPU", flush=True)
        except Exception as e:
            print(f"[XTTS] GPU 失敗，改用 CPU：{e}", flush=True)
            _xtts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
    return _xtts_model

def _unload_xtts() -> None:
    """卸載 XTTS 模型、釋放顯存（開車模式關閉時呼叫）。"""
    global _xtts_model
    if _xtts_model is not None:
        _xtts_model = None
        _free_vram()

def _xtts_language() -> str:
    """朗讀語言：跟著介面語系（zh-TW -> 中文、其餘 -> 英文）。"""
    return "zh-cn" if BOT_LANG == "zh-TW" else "en"

def _synthesize(text: str, out_path: str) -> str:
    """把文字合成成語音檔（阻塞式，呼叫端用 asyncio.to_thread 包起來）。回傳檔案路徑。"""
    model = _get_xtts()
    # XTTS-v2 是聲音克隆模型，需指定內建說話者；language 跟著介面語系
    model.tts_to_file(text=text, file_path=out_path,
                      speaker="Ana Florence", language=_xtts_language())
    return out_path

# ── 簡報轉 PDF（簡報是視覺導向，轉 PDF 讓 CC 逐頁視覺讀取更準）────────────
def _pptx_to_pdf_sync(src: Path) -> Optional[Path]:
    """用 PowerPoint COM 把 .ppt/.pptx 轉成同名 .pdf；失敗回傳 None。"""
    import pythoncom
    import win32com.client
    pdf = src.with_suffix(".pdf")
    pythoncom.CoInitialize()
    pp = None
    try:
        pp = win32com.client.Dispatch("PowerPoint.Application")
        # PowerPoint 不允許 Visible=False，改用 WithWindow=False 開啟簡報不顯示視窗
        deck = pp.Presentations.Open(str(src), WithWindow=False)
        deck.SaveAs(str(pdf), 32)  # 32 = ppSaveAsPDF
        deck.Close()
        return pdf if pdf.exists() else None
    except Exception as e:
        print(f"[PPTX2PDF] 轉檔失敗 {src.name}: {e}", flush=True)
        return None
    finally:
        try:
            if pp is not None:
                pp.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()

async def _convert_pptx(src: Path) -> Optional[Path]:
    """非同步轉檔，加 60s timeout；卡住或失敗回傳 None。"""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_pptx_to_pdf_sync, src), timeout=60)
    except Exception:
        return None

async def _maybe_auto_compact(channel: discord.TextChannel, state: dict) -> None:
    """context 達到門檻時自動執行 /compact，避免 CONTEXT_FULL。"""
    if state.get("ctx_tokens", 0) < int(_ctx_limit(state) * 0.85):
        return
    msg = await channel.send(t("compacting"))
    try:
        _, compact_sid, _ = await run_claude(
            t("compact_prompt"),
            state,
        )
        if compact_sid:
            state["session_id"] = compact_sid
            _persist_session(state)
        state["ctx_tokens"] = 0
        state["ctx_warned"] = False
        await msg.edit(content=t("compacted"))
        await asyncio.sleep(1.5)
        await msg.delete()
    except Exception as e:
        await msg.edit(content=t("compact_failed", e=e))

async def _emit_coord(channel, text: str) -> str:
    """解析回覆中的 [[COORD:]] 廣播：更新登錄表、發到協作頻道，回傳去標記後的文字。"""
    items, clean = coord_core.parse_broadcasts(text)
    if not items:
        return text
    cid = getattr(channel, "id", 0)
    name = getattr(channel, "name", None) or str(cid)
    for it in items:
        _coord_registry.update(cid, name, it)
    if COORD_CHANNEL and COORD_CHANNEL != cid:
        dest = bot.get_channel(COORD_CHANNEL)
        if dest is not None:
            for it in items:
                try:
                    await dest.send(t("coord_broadcast", name=name, task=it))
                except Exception:
                    pass
    return clean

async def _send_files_and_text(channel, text: str) -> None:
    if COORD_ENABLED:
        text = await _emit_coord(channel, text)
    paths = _FILE_MARKER.findall(text)
    clean = _FILE_MARKER.sub("", text).strip()

    if clean:
        await send_long(channel, clean)

    for p in paths:
        fp = Path(p.strip().strip('"').strip("'"))
        if not fp.exists() or not fp.is_file():
            await channel.send(t("file_not_found", fp=fp))
            continue
        if fp.stat().st_size > _DISCORD_FILE_LIMIT:
            await channel.send(t("file_too_large", name=fp.name, fp=fp))
            continue
        try:
            await channel.send(file=discord.File(str(fp)))
        except Exception as e:
            await channel.send(t("file_upload_failed", name=fp.name, e=e))

# ── 開車模式語音回覆：把 CC 附的朗讀版抽出來合成語音檔 ─────────────────────
_SPEAK_MARKER = re.compile(r"<<<SPEAK>>>(.*?)<<<ENDSPEAK>>>", re.DOTALL)

async def _voice_reply(channel, reply: str, speak: bool) -> str:
    """處理 CC 回覆裡的朗讀版（<<<SPEAK>>>...<<<ENDSPEAK>>>）標記。
    speak=True（開車模式＋語音輸入）：抽出朗讀版、合成語音檔上傳，回傳「去掉標記」的
    純文字（文字版照常完整顯示）。speak=False 或無標記：只把殘留標記清乾淨後回傳。
    任何 TTS 失敗都降級為純文字，不影響文字回覆。"""
    m = _SPEAK_MARKER.search(reply)
    if not m:
        return reply
    clean = _SPEAK_MARKER.sub("", reply).strip()
    if not speak:
        return clean
    spoken = m.group(1).strip()
    if not spoken:
        return clean
    try:
        out = Path(__file__).parent / "tmp" / f"speak_{uuid.uuid4().hex[:8]}.wav"
        out.parent.mkdir(exist_ok=True)
        await asyncio.to_thread(_synthesize, spoken, str(out))
        await channel.send(file=discord.File(str(out)))
        try:
            out.unlink()
        except Exception:
            pass
    except Exception as e:
        print(f"[XTTS] 合成失敗，降級純文字：{e}", flush=True)
    return clean

# ── 分段送訊息 ─────────────────────────────────────────────────────────
async def send_long(channel, text: str):
    # 超長回覆（會被切成 4 則以上）改存成 Markdown 檔 + 簡短預覽，手機上好讀又能存檔
    if len(text) > 3 * MAX_MSG:
        import tempfile
        fp = Path(tempfile.gettempdir()) / f"cc_reply_{uuid.uuid4().hex[:8]}.md"
        try:
            fp.write_text(text, encoding="utf-8")
            preview = text[:1500].rstrip()
            await channel.send(preview + t("reply_long_preview"),
                               file=discord.File(str(fp)))
        finally:
            try:
                fp.unlink()
            except Exception:
                pass
        return
    for i in range(0, len(text), MAX_MSG):
        await channel.send(text[i:i+MAX_MSG])
        if i + MAX_MSG < len(text):
            await asyncio.sleep(0.3)

# ── AskUserQuestion → Discord 按鈕 ─────────────────────────────────────
def _parse_ask_questions(ask_data: dict) -> list[dict]:
    if "questions" in ask_data:
        return ask_data["questions"]
    return [ask_data]

async def _handle_cc_error(prog: discord.Message, err: "CCError", state: dict) -> None:
    msg = err.user_msg
    if err.kind in _RESET_SESSION:
        state["session_id"] = None
        state["ctx_tokens"] = 0
        state["ctx_warned"] = False
        _persist_session(state)
        msg += t("session_auto_cleared")
    if err.kind == "OVERLOADED":
        inc = await asyncio.to_thread(is_incident_active)
        if inc:
            msg += t("official_status", inc=inc)
    raw = (err.raw or "").strip()
    if raw:
        msg += f"\n```\n[{err.kind}] {raw[:1500]}\n```"
    try:
        await prog.edit(content=msg[:2000])
    except Exception:
        pass

async def _process_answer(channel: discord.TextChannel, chosen: str, state: dict) -> None:
    if _processing.get(channel.id):
        await channel.send(t("still_processing"), delete_after=5)
        return
    _processing[channel.id] = True
    prog = await channel.send(t("you_chose_thinking", chosen=chosen))
    try:
        reply, new_sid, next_ask = await _run_tracked(channel.id, chosen, state, prog)
        if new_sid:
            state["session_id"] = new_sid
            _persist_session(state)
        await prog.delete()
        if next_ask:
            await _send_ask_question(channel, next_ask, state)
        elif reply and reply != _NO_RESPONSE:
            await _send_files_and_text(channel, reply)
    except _StoppedByUser:
        await prog.edit(content=t("stopped"))
    except CCError as e:
        await _handle_cc_error(prog, e, state)
    except Exception as e:
        print(f"[CC_FAIL] kind=UNCLASSIFIED raw={e!r}", flush=True)
        detail = f"{type(e).__name__}: {e}"[:1500]
        await prog.edit(content=t("unexpected_error", detail=detail)[:2000])
    finally:
        _processing[channel.id] = False

async def _send_ask_question(channel: discord.TextChannel, ask_data: dict, state: dict) -> None:
    questions = _parse_ask_questions(ask_data)
    for q in questions:
        question_text = q.get("question", t("choose_prompt"))
        raw_options: list = q.get("options", [])

        def _label(o) -> str:
            if isinstance(o, dict):
                return o.get("label") or o.get("value") or str(o)
            return str(o)

        def _desc(o) -> str:
            return o.get("description", "") if isinstance(o, dict) else ""

        opt_labels = [_label(o) for o in raw_options]
        opt_descs = [_desc(o) for o in raw_options]

        if not opt_labels:
            await channel.send(t("type_to_reply", q=question_text))
            continue

        # 每個選項顯示「粗體標題＋下一行 blockquote 說明」，讓使用者看得到 description
        _lines = []
        for i, l in enumerate(opt_labels):
            d = opt_descs[i].strip()[:200]   # 過長截斷，避免超過 Discord 2000 字上限
            _lines.append(f"`{i+1}.` **{l}**" + (f"\n> {d}" if d else ""))
        numbered = "\n".join(_lines)
        body = f"❓ **{question_text}**\n{numbered}\n" + t("ask_hint")

        state["pending_options"] = opt_labels

        view = discord.ui.View(timeout=600)
        answered = {"done": False}

        async def on_to(v: discord.ui.View = view) -> None:
            for item in v.children:
                item.disabled = True
            answered["done"] = True

        view.on_timeout = on_to

        async def _finish(inter: discord.Interaction, chosen: str) -> None:
            if answered["done"]:
                try:
                    await inter.response.send_message(t("question_ended"), ephemeral=True)
                except Exception:
                    pass
                return
            answered["done"] = True
            state.pop("pending_options", None)
            for item in view.children:
                item.disabled = True
            view.stop()
            try:
                await inter.response.edit_message(content=f"{body}\n\n" + t("selected", chosen=chosen), view=view)
            except Exception:
                try:
                    await inter.response.defer()
                except Exception:
                    pass
            await _process_answer(channel, chosen, state)

        try:
            if len(opt_labels) <= 5:
                for label in opt_labels:
                    btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.primary)
                    async def cb(inter: discord.Interaction, l: str = label) -> None:
                        await _finish(inter, l)
                    btn.callback = cb
                    view.add_item(btn)
            else:
                opts = [discord.SelectOption(label=l[:100], value=str(i),
                                             description=(opt_descs[i][:100] or None))
                        for i, l in enumerate(opt_labels)]
                sel = discord.ui.Select(placeholder=t("select_placeholder"), options=opts[:25])
                async def sel_cb(inter: discord.Interaction) -> None:
                    idx = int(sel.values[0])
                    await _finish(inter, opt_labels[idx])
                sel.callback = sel_cb
                view.add_item(sel)
            await channel.send(body, view=view)
        except Exception:
            await channel.send(body)

# ── Usage API ──────────────────────────────────────────────────────────
def fetch_usage() -> Optional[dict]:
    now = time.time()
    if "data" in _usage_cache and now - _usage_cache.get("ts",0) < USAGE_CACHE_SEC:
        return _usage_cache["data"]
    try:
        creds = json.loads((Path.home()/".claude"/".credentials.json").read_text())
        token = creds["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization":f"Bearer {token}","anthropic-beta":"oauth-2025-04-20",
                     "User-Agent":"claude-code/2.1.143","Content-Type":"application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        _usage_cache["data"] = data
        _usage_cache["ts"] = now
        return data
    except Exception:
        return None

def _bar(pct: float, w: int = 14) -> str:
    f = round(pct/100*w)
    return "█"*f + "░"*(w-f)

def _reset(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone().strftime("%H:%M")
    except Exception:
        return ts

def _countdown(ts: str) -> str:
    try:
        target = datetime.fromisoformat(ts.replace("Z","+00:00"))
        delta = target - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return t("reset_soon")
        h, m = secs // 3600, (secs % 3600) // 60
        if h >= 24:
            d = h // 24
            return t("in_days", d=d, h=h % 24)
        if h:
            return t("in_hours", h=h, m=m)
        return t("in_mins", m=m)
    except Exception:
        return ts

# ── 排程背景循環 ───────────────────────────────────────────────────────
async def _schedule_loop() -> None:
    """每 30 秒檢查到期排程並執行。"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(30)
        try:
            schedules = _load_schedules()
            now = datetime.now()
            modified = False
            for s in schedules:
                try:
                    next_run = datetime.fromisoformat(s["next_run"])
                except Exception:
                    continue
                if now < next_run:
                    continue
                # 到期 → 執行
                channel = bot.get_channel(s["channel_id"])
                if channel:
                    state = get_state(s["channel_id"])
                    await channel.send(t("run_schedule", task=s['task']))
                    try:
                        reply, new_sid, _ = await run_claude(s["task"], state)
                        if new_sid:
                            state["session_id"] = new_sid
                            _persist_session(state)
                        if reply and reply != _NO_RESPONSE:
                            await send_long(channel, reply)
                    except Exception as e:
                        await channel.send(t("schedule_run_failed", e=e))
                # 計算下次執行時間
                cron_expr = s.get("cron", "").strip()
                if _HAS_CRONITER and cron_expr:
                    try:
                        s["next_run"] = _croniter(cron_expr, now).get_next(datetime).isoformat()
                        modified = True
                    except Exception:
                        s["_delete"] = True
                        modified = True
                else:
                    # 一次性任務
                    s["_delete"] = True
                    modified = True
            if modified:
                schedules = [s for s in schedules if not s.get("_delete")]
                _save_schedules(schedules)
        except Exception as e:
            print(f"[SCHEDULE_LOOP] error: {e}", flush=True)

# ── Bot 本體 ───────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot._session_pick_cache = []

async def _update_presence(cid: int, label: str) -> None:
    """把「頻道名｜session 標題」設成 bot 的 Discord 狀態。
    多頻道時 Discord 只能有一個全域狀態，加頻道名讓你看得出顯示的是哪個頻道。"""
    ch = bot.get_channel(cid)
    name = getattr(ch, "name", None) or "cc"
    try:
        await bot.change_presence(activity=discord.CustomActivity(name=f"💬 {name}｜{label}"[:128]))
    except Exception:
        pass

# ── 多 session 側欄（頻道即對話）─────────────────────────────────────────
_sidebar_category_id: Optional[int] = None   # 「CC 對話」分類 id
_sidebar_entry_id: Optional[int] = None      # 入口頻道 id

def _safe_channel_name(title: str) -> str:
    """把標題轉成可用的頻道名：去頭尾空白、換行轉空白、截斷到 90 字。"""
    name = " ".join((title or "").split())[:90].strip()
    return name or t("untitled_chat")

WT_PREFIX = "🌿"   # worktree 頻道在側欄的視覺前綴（左側列表一眼分辨平行分支）

def _strip_wt_prefix(name: str) -> str:
    """去掉頻道名開頭的 🌿 前綴（含其後空白或連字號），沒有就原樣回傳。"""
    return re.sub(rf"^{WT_PREFIX}[\s\-]*", "", name or "").strip()

def _channel_display_name(base_title: str, wt_on: bool) -> str:
    """依 worktree 狀態組頻道名：開著加 🌿 前綴，否則就是安全標題本身。"""
    safe = _safe_channel_name(_strip_wt_prefix(base_title))
    return f"{WT_PREFIX} {safe}" if wt_on else safe

async def _rename_for_wt(channel: discord.TextChannel, wt_on: bool) -> None:
    """側欄頻道：依 worktree 狀態加／去 🌿 前綴；改名失敗不影響主流程（純視覺）。"""
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        cur = getattr(channel, "name", "") or ""
        new = _channel_display_name(cur, wt_on)
        if new != cur:
            await channel.edit(name=new)
    except Exception as e:
        print(f"[WT] 側欄前綴更新失敗：{e}", flush=True)

async def _open_sidebar_channel(guild: discord.Guild, category: discord.CategoryChannel,
                                session_id: Optional[str] = None, title: Optional[str] = None,
                                cwd: Optional[str] = None) -> Optional[discord.TextChannel]:
    """在側欄分類頂端（入口下方）建一個頻道並綁 session；有 title 就直接命名、不再自動改名。
    回傳 channel；缺權限或失敗回 None。"""
    name = _safe_channel_name(title) if title else t("new_chat_channel")
    try:
        ch = await guild.create_text_channel(name, category=category, position=1)
    except (discord.Forbidden, Exception) as e:
        print(f"[SIDEBAR] 建頻道失敗：{e}", flush=True)
        return None
    st = get_state(ch.id)
    st["session_id"] = session_id
    st["cwd"] = Path(cwd) if cwd and Path(cwd).is_dir() else DEFAULT_DIR
    st["_sidebar"] = True
    st["_named"] = bool(title)   # 救回已有標題的不用再自動改名；全新對話留待第一句後改名
    if title:
        st["_session_label"] = title
        if session_id:
            _save_title(session_id, title)   # 標題寫進快取，/sessions 顯示一致
    _allowed_channels.add(ch.id)
    if session_id:
        _persist_session(st)
    # position=1 是 Discord 全域絕對序，多頻道時不保證落在入口正下方；
    # 故建完再用可靠的相對移動，把新頻道頂到入口下方第一個
    await _bump_channel_to_top(ch)
    return ch

async def _restore_session_to_channel(inter: discord.Interaction, entry: dict) -> None:
    """把選到的歷史對話救回成側欄一個新頻道（保住一頻道一 session）。
    沒有側欄分類時退回舊行為：切換目前頻道。"""
    title = (entry.get("title") or entry.get("first_prompt") or entry.get("snippet") or t("restored_default"))[:60]
    cwd, ok = _resolve_project_dir(entry["project_path"])
    category = bot.get_channel(_sidebar_category_id) if _sidebar_category_id else None
    if inter.guild and isinstance(category, discord.CategoryChannel):
        ch = await _open_sidebar_channel(inter.guild, category,
                                         session_id=entry["session_id"], title=title, cwd=str(cwd))
        if ch:
            await inter.response.edit_message(
                content=t("restored_to_channel", mention=ch.mention), view=None)
            return
    # 退回舊行為：切換目前頻道
    state = get_state(inter.channel_id)
    state["session_id"] = entry["session_id"]
    state["cwd"] = cwd
    state["_session_label"] = title
    _persist_session(state)
    await _update_presence(inter.channel_id, title)
    note = "" if ok else t("folder_missing", cwd=cwd)
    await inter.response.edit_message(
        content=t("switched_to", title=title, cwd=cwd, note=note), view=None)

class NewChatView(discord.ui.View):
    """入口頻道的常駐「開新對話」按鈕（custom_id 固定、timeout=None，重啟後仍可點）。"""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label=t("btn_new_chat"), style=discord.ButtonStyle.primary, custom_id="cc_new_chat")
    async def new_chat(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != ALLOWED_USER:
            await interaction.response.send_message(t("owner_only_new_chat"), ephemeral=True)
            return
        guild = interaction.guild
        category = interaction.channel.category if interaction.channel else None
        if guild is None or category is None:
            await interaction.response.send_message(t("no_category"), ephemeral=True)
            return
        ch = await _open_sidebar_channel(guild, category)
        if ch is None:
            await interaction.response.send_message(
                t("open_channel_failed"), ephemeral=True)
            return
        await interaction.response.send_message(
            t("new_chat_ready", mention=ch.mention), ephemeral=True)

async def _bump_channel_to_top(channel: discord.TextChannel) -> None:
    """把側欄頻道移到入口下方（最新活動置頂）；已在頂端就不動，省 rate limit。"""
    try:
        category = channel.category
        if category is None or category.id != _sidebar_category_id:
            return
        entry = bot.get_channel(_sidebar_entry_id) if _sidebar_entry_id else None
        if entry is None:
            return
        # 已經是入口正下方第一個就不搬
        convo = [c for c in category.text_channels if c.id != _sidebar_entry_id]
        if convo and convo[0].id == channel.id:
            return
        await channel.move(after=entry, category=category)
    except Exception as e:
        print(f"[SIDEBAR] 置頂失敗：{e}", flush=True)

async def _autoname_channel(channel: discord.TextChannel, state: dict) -> None:
    """側欄頻道第一句後：讀內容生成中文標題、改頻道名（受 2 次/10 分鐘改名限制，故只改一次）。"""
    try:
        title = await _generate_title(state["session_id"])
        if not title:
            return
        _save_title(state["session_id"], title)
        state["_session_label"] = title
        await channel.edit(name=_channel_display_name(title, bool(state.get("wt"))))
        await _update_presence(channel.id, title)
    except Exception as e:
        print(f"[SIDEBAR] 自動改名失敗：{e}", flush=True)

async def _ensure_sidebar(guild: discord.Guild) -> None:
    """確保「CC 對話」分類與入口頻道存在、掛上常駐按鈕，並把分類底下頻道納入可用清單。"""
    global _sidebar_category_id, _sidebar_entry_id
    try:
        category = discord.utils.get(guild.categories, name=SIDEBAR_CATEGORY)
        if category is None:
            category = await guild.create_category(SIDEBAR_CATEGORY)
        _sidebar_category_id = category.id
        # 分類底下的對話頻道全部納入可用清單（入口頻道除外）
        entry = None
        for ch in category.text_channels:
            if ch.name == SIDEBAR_ENTRY or ch.name.startswith("➕"):
                entry = ch
            else:
                _allowed_channels.add(ch.id)
                # 旗標只存在記憶體、不寫檔，bot 重啟就消失；在此替既有側欄頻道補回，
                # 讓重啟前就存在的舊頻道也能繼續自動置頂
                st = get_state(ch.id)
                st["_sidebar"] = True
                st["_named"] = True   # 既有頻道已有名字，標記為已命名以免重啟後被自動改名
        # 入口頻道不存在就建，並固定在最上面
        if entry is None:
            entry = await guild.create_text_channel(SIDEBAR_ENTRY, category=category, position=0)
        _sidebar_entry_id = entry.id
        # 入口頻道若還沒有按鈕訊息就發一則（已發過就不重複）
        has_btn = False
        async for m in entry.history(limit=20):
            if m.author == bot.user and m.components:
                has_btn = True
                break
        if not has_btn:
            await entry.send(
                t("entry_message"),
                view=NewChatView())
        print(f"[SIDEBAR] category={_sidebar_category_id} entry={_sidebar_entry_id}", flush=True)
    except discord.Forbidden:
        print("[SIDEBAR] 缺少管理頻道權限，略過側欄初始化", flush=True)
    except Exception as e:
        print(f"[SIDEBAR] 初始化失敗：{e}", flush=True)

async def check_auth(interaction: discord.Interaction, owner_only: bool = False) -> bool:
    # 入口頻道雖不處理一般訊息，但允許在那邊用指令（/sessions、/search 等）
    if interaction.channel_id not in _allowed_channels and interaction.channel_id != _sidebar_entry_id:
        await interaction.response.send_message(t("no_permission"), ephemeral=True)
        return False
    if owner_only and interaction.user.id != ALLOWED_USER:
        await interaction.response.send_message(t("owner_only"), ephemeral=True)
        return False
    if interaction.user.id not in _allowed_users:
        await interaction.response.send_message(t("no_permission"), ephemeral=True)
        return False
    return True

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(t("ready_log", user=bot.user), flush=True)
    asyncio.create_task(_schedule_loop())
    # 開車模式：上次關機時若為開啟，重啟自動恢復載入兩個模型（開車中崩潰可自癒）；
    # 在家（預設 off）則完全不載入語音模型，不吃 GPU/VRAM。
    if _drive_mode:
        asyncio.create_task(asyncio.to_thread(_get_whisper))
        async def _bg_xtts() -> None:
            try:
                await asyncio.to_thread(_get_xtts)
            except Exception as e:
                print(f"[XTTS] 預載失敗：{e}", flush=True)
        asyncio.create_task(_bg_xtts())
    # 多 session 側欄：註冊常駐按鈕 + 確保分類/入口頻道存在
    bot.add_view(NewChatView())   # 讓重啟前發出的按鈕仍可點
    main_ch = bot.get_channel(ALLOWED_CHANNEL)
    guild = main_ch.guild if main_ch else (bot.guilds[0] if bot.guilds else None)
    if guild:
        await _ensure_sidebar(guild)
    # 啟動時把 bot 狀態設成上次的 session 標題
    sid = get_state(ALLOWED_CHANNEL).get("session_id")
    await _update_presence(ALLOWED_CHANNEL, await asyncio.to_thread(_session_label, sid))
    # 版本變更 → 推送更新公告（未設定 UPDATE_CHANNEL 時跳過）
    if UPDATE_CHANNEL and _get_last_version() != BOT_VERSION:
        try:
            ch = bot.get_channel(UPDATE_CHANNEL) or await bot.fetch_channel(UPDATE_CHANNEL)
            if ch:
                tag = _CHANGE_TYPE_LABEL.get(CHANGE_TYPE, "")
                header = t("update_header", ver=BOT_VERSION)
                if tag:
                    header += f"　`{tag}`"
                await ch.send(f"{header}\n\n{CHANGELOG}")
                _save_last_version(BOT_VERSION)
        except Exception as e:
            print(f"[UPDATE_PUSH] failed: {e}", flush=True)

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    """側欄頻道被刪 → 清掉它的 session 記錄與 state（Claude JSONL 留在硬碟，日後可救回）。"""
    if channel.id == _sidebar_entry_id:
        return
    # 頻道若有開 worktree → 嘗試清掉（不加 --force，髒的會被擋下而保留，不丟工作）
    wt_rec = None
    st = _sessions.get(channel.id)
    if st:
        wt_rec = st.get("wt")
    else:
        rec = _load_sessions_map().get(str(channel.id))
        if isinstance(rec, dict):
            wt_rec = rec.get("wt")
    if isinstance(wt_rec, dict) and wt_rec.get("repo") and wt_rec.get("path"):
        try:
            await asyncio.to_thread(wt_core.remove, wt_rec["repo"], wt_rec["path"])
        except Exception:
            pass
    _allowed_channels.discard(channel.id)
    _sessions.pop(channel.id, None)
    try:
        data = _load_sessions_map()
        if str(channel.id) in data:
            del data[str(channel.id)]
            _SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    """手動在「CC 對話」分類建頻道 → 自動綁一個空 session（bot 自己建的已在 _allowed_channels，會被跳過）。"""
    if not isinstance(channel, discord.TextChannel):
        return
    if (channel.category_id != _sidebar_category_id
            or channel.id == _sidebar_entry_id
            or channel.id in _allowed_channels):
        return
    st = get_state(channel.id)
    st["_sidebar"] = True
    st["_named"] = True   # 手動建的由使用者自己命名，不自動改名
    _allowed_channels.add(channel.id)

# ── Slash 指令 ─────────────────────────────────────────────────────────

@bot.tree.command(name="new", description=t("cmd_new_desc"))
async def cmd_new(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    st = get_state(interaction.channel_id)
    st["session_id"] = None
    st["ctx_tokens"] = 0
    st["ctx_warned"] = False
    st["_session_label"] = t("new_chat")
    _persist_session(st)
    await _update_presence(interaction.channel_id, t("new_chat"))
    await interaction.response.send_message(t("new_done"))

@bot.tree.command(name="rename", description=t("cmd_rename_desc"))
async def cmd_rename(interaction: discord.Interaction, name: Optional[str] = None):
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    sid = state.get("session_id")
    if not sid:
        await interaction.response.send_message(t("rename_no_session"), ephemeral=True)
        return
    await interaction.response.defer()
    if name and name.strip():
        title = name.strip()[:30]
    else:
        title = await _generate_title(sid)
        if not title:
            await interaction.followup.send(t("rename_gen_failed"))
            return
    _save_title(sid, title)
    state["_session_label"] = title
    await _update_presence(interaction.channel_id, title)
    await interaction.followup.send(t("renamed", title=title))

@bot.tree.command(name="stop", description=t("cmd_stop_desc"))
async def cmd_stop(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    task = _running_tasks.get(interaction.channel_id)
    if task and not task.done():
        task.cancel()
        await interaction.response.send_message(t("stop_sent"))
    else:
        await interaction.response.send_message(t("stop_nothing"), ephemeral=True)

@bot.tree.command(name="continue", description=t("cmd_continue_desc"))
async def cmd_continue(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    if state.get("session_id"):
        await interaction.response.send_message(t("continue_resume", id=state['session_id'][:8]))
    else:
        await interaction.response.send_message(t("continue_none"))

@bot.tree.command(name="status", description=t("cmd_status_desc"))
async def cmd_status(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    sid = state["session_id"]
    label = await asyncio.to_thread(_session_label, sid)
    ctx = state.get("ctx_tokens", 0)
    ctx_limit = _ctx_limit(state)
    ctx_bar = _bar(ctx / ctx_limit * 100)
    lines = [
        t("status_title"),
        t("status_convo", label=label),
        t("status_dir", cwd=state['cwd']),
    ]
    if state.get("wt"):
        lines.append(t("status_worktree", branch=state["wt"]["branch"], base=state["wt"]["base"]))
    lines += [
        t("status_session", id=sid[:8]) if sid else t("status_session_none"),
        t("status_model", model=state['model'] or t("default_inline"), fb=FALLBACK_MODEL),
        t("status_effort", effort=state['effort'] or t("default_inline")),
        t("status_context", bar=ctx_bar, ctx=f"{ctx:,}", limit=f"{ctx_limit:,}"),
    ]
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="sessions", description=t("cmd_sessions_desc"))
@discord.app_commands.choices(scope=[
    discord.app_commands.Choice(name=t("scope_mine"), value="mine"),
    discord.app_commands.Choice(name=t("scope_all"), value="all"),
])
async def cmd_sessions(interaction: discord.Interaction, scope: str = "mine"):
    if not await check_auth(interaction): return
    await interaction.response.defer(ephemeral=True)   # ephemeral：只有你看得到，入口頻道零殘留
    entries = await asyncio.to_thread(_list_sessions, scope)
    if not entries:
        await interaction.followup.send(t("no_sessions"), ephemeral=True)
        return
    bot._session_pick_cache = entries

    options = []
    for i, e in enumerate(entries):
        dt = datetime.fromtimestamp(e["mtime"]).strftime("%m/%d %H:%M")
        label = (e["title"] or e["first_prompt"] or t("untitled"))[:95]
        # 描述放第一句訊息片段，讓同標題的對話也分得出來
        desc = f"{dt} · {e['first_prompt']}" if e["first_prompt"] else dt
        options.append(discord.SelectOption(label=label, description=desc[:95], value=str(i)))

    class SessionSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder=t("pick_restore"), options=options)
        async def callback(self, inter: discord.Interaction):
            entry = bot._session_pick_cache[int(self.values[0])]
            await _restore_session_to_channel(inter, entry)

    view = discord.ui.View()
    view.add_item(SessionSelect())
    header = t("sessions_header_all") if scope == "all" else t("sessions_header_mine")
    await interaction.followup.send(header, view=view, ephemeral=True)

def _search_sessions(keyword: str, limit: int = 15) -> list[dict]:
    claude_home = Path.home() / ".claude" / "projects"
    if not claude_home.exists():
        return []
    kw = keyword.lower()
    hits: list[dict] = []
    for proj_dir in claude_home.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            meta = _session_meta(jf)
            if not meta["is_bot"]:  # 只搜本 bot 自己的對話
                continue
            snippet = ""
            try:
                with jf.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if kw in line.lower():
                            try:
                                obj = json.loads(line)
                                txt = json.dumps(obj.get("message", obj), ensure_ascii=False)
                            except Exception:
                                txt = line
                            low = txt.lower()
                            pos = low.find(kw)
                            s = max(0, pos - 30)
                            snippet = txt[s:pos + 50].replace("\n", " ").strip()
                            break
            except Exception:
                continue
            if snippet:
                hits.append({
                    "session_id": jf.stem,
                    "project_path": meta["cwd"] or str(DEFAULT_DIR),
                    "mtime": jf.stat().st_mtime,
                    "title": meta["title"],
                    "snippet": snippet,
                })
    return sorted(hits, key=lambda e: e["mtime"], reverse=True)[:limit]

@bot.tree.command(name="search", description=t("cmd_search_desc"))
async def cmd_search(interaction: discord.Interaction, keyword: str):
    if not await check_auth(interaction): return
    await interaction.response.defer(ephemeral=True)   # ephemeral：零殘留
    entries = await asyncio.to_thread(_search_sessions, keyword)
    if not entries:
        await interaction.followup.send(t("search_none", kw=keyword), ephemeral=True)
        return
    bot._session_pick_cache = entries

    options = []
    for i, e in enumerate(entries):
        dt = datetime.fromtimestamp(e["mtime"]).strftime("%m/%d %H:%M")
        label = (e["title"] or e["project_path"])[:95]
        desc = f"{dt}｜…{e['snippet'][:70]}"
        options.append(discord.SelectOption(label=label, description=desc[:100], value=str(i)))

    class SearchSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder=t("pick_restore"), options=options)
        async def callback(self, inter: discord.Interaction):
            entry = bot._session_pick_cache[int(self.values[0])]
            await _restore_session_to_channel(inter, entry)

    view = discord.ui.View()
    view.add_item(SearchSelect())
    await interaction.followup.send(t("search_header", kw=keyword, n=len(entries)), view=view, ephemeral=True)

@bot.tree.command(name="model", description=t("cmd_model_desc"))
@discord.app_commands.choices(model=[
    discord.app_commands.Choice(name=t("model_sonnet46"), value="claude-sonnet-4-6"),
    discord.app_commands.Choice(name="Sonnet 4.5",          value="claude-sonnet-4-5"),
    discord.app_commands.Choice(name="Opus 4.8",            value="claude-opus-4-8"),
    discord.app_commands.Choice(name=t("model_haiku"),   value="claude-haiku-4-5-20251001"),
    discord.app_commands.Choice(name=t("choice_default"),                 value="default"),
])
async def cmd_model(interaction: discord.Interaction, model: str):
    if not await check_auth(interaction): return
    st = get_state(interaction.channel_id)
    st["model"] = None if model == "default" else model
    _persist_session(st)   # 立刻存檔，重啟後仍記得
    await interaction.response.send_message(t("model_set", model=model))

@bot.tree.command(name="effort", description=t("cmd_effort_desc"))
@discord.app_commands.choices(effort=[
    discord.app_commands.Choice(name=t("effort_low"), value="low"),
    discord.app_commands.Choice(name="medium",      value="medium"),
    discord.app_commands.Choice(name="high",        value="high"),
    discord.app_commands.Choice(name="xhigh",       value="xhigh"),
    discord.app_commands.Choice(name=t("effort_max"), value="max"),
    discord.app_commands.Choice(name=t("choice_default"),        value="default"),
])
async def cmd_effort(interaction: discord.Interaction, effort: str):
    if not await check_auth(interaction): return
    st = get_state(interaction.channel_id)
    st["effort"] = None if effort == "default" else effort
    _persist_session(st)   # 立刻存檔，重啟後仍記得
    await interaction.response.send_message(t("effort_set", effort=effort))

@bot.tree.command(name="plan", description=t("cmd_plan_desc"))
@discord.app_commands.choices(plan=[
    discord.app_commands.Choice(name="Pro (~$20/mo)",          value="pro"),
    discord.app_commands.Choice(name="Max (~$100 or $200/mo)", value="max"),
    discord.app_commands.Choice(name="Team / Enterprise",      value="enterprise"),
    discord.app_commands.Choice(name="API / pay-as-you-go",    value="api"),
    discord.app_commands.Choice(name=t("plan_unknown"),        value="unknown"),
])
async def cmd_plan(interaction: discord.Interaction, plan: str):
    # 方案是帳號全域設定（影響所有頻道），限主帳號設定
    if not await check_auth(interaction, owner_only=True): return
    global _account_plan
    _account_plan = plan
    _save_plan(plan)   # 立刻存檔，重啟後仍記得
    # 依官方規則回報：這個方案下 Opus 會不會「自動」拿到 1M
    msg = t("plan_set_auto", plan=plan) if plan in _AUTO_1M_PLANS else t("plan_set_std", plan=plan)
    await interaction.response.send_message(msg)

@bot.tree.command(name="drive", description=t("cmd_drive_desc"))
@discord.app_commands.choices(mode=[
    discord.app_commands.Choice(name="on",  value="on"),
    discord.app_commands.Choice(name="off", value="off"),
])
async def cmd_drive(interaction: discord.Interaction, mode: str):
    # 開車模式是帳號全域開關（控制兩個 GPU 模型的生命週期），限主帳號設定
    if not await check_auth(interaction, owner_only=True): return
    global _drive_mode
    if mode == "on":
        _drive_mode = True
        _save_drive(True)   # 立刻存檔，重啟後自動恢復載入
        # 載入兩個模型耗時（首次還要下載），先即時回「載入中」避免互動 3 秒逾時
        await interaction.response.send_message(t("drive_on_loading"))
        await asyncio.to_thread(_get_whisper)
        try:
            await asyncio.to_thread(_get_xtts)
        except Exception as e:
            # TTS 載入失敗：語音輸入仍可用，只是不能語音回覆（仍維持開車模式）
            await interaction.followup.send(t("drive_xtts_fail", ex=e))
            return
        await interaction.followup.send(t("drive_on_ready"))
    else:
        _drive_mode = False
        _save_drive(False)
        # 卸載兩個模型、釋放 VRAM，回到純文字 bot
        await asyncio.to_thread(_unload_whisper)
        await asyncio.to_thread(_unload_xtts)
        await interaction.response.send_message(t("drive_off"))

@bot.tree.command(name="cd", description=t("cmd_cd_desc"))
async def cmd_cd(interaction: discord.Interaction, path: str):
    if not await check_auth(interaction): return
    p = Path(path)
    if not p.exists() or not p.is_dir():
        await interaction.response.send_message(t("cd_not_found", path=path))
        return
    st = get_state(interaction.channel_id)
    st["cwd"] = p
    _persist_session(st)   # 立刻存檔，重啟後仍記得
    await interaction.response.send_message(t("cd_done", p=p))

@bot.tree.command(name="pwd", description=t("cmd_pwd_desc"))
async def cmd_pwd(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    cwd = state["cwd"]
    if state.get("wt"):
        await interaction.response.send_message(
            t("pwd_with_wt", cwd=cwd, branch=state["wt"]["branch"]))
    else:
        await interaction.response.send_message(f"📂 `{cwd}`")

def _wt_error_text(error: str) -> str:
    """把 wt_core 回傳的錯誤代碼轉成給使用者的中文/英文訊息。"""
    mapping = {
        "not_a_repo": t("wt_err_not_repo"),
        "no_base_branch": t("wt_err_no_base"),
        "path_exists": t("wt_err_path_exists"),
    }
    return mapping.get(error, t("wt_err_git", err=error[:300]))

@bot.tree.command(name="worktree", description=t("cmd_worktree_desc"))
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="on", value="on"),
    discord.app_commands.Choice(name="merge", value="merge"),
    discord.app_commands.Choice(name="off", value="off"),
    discord.app_commands.Choice(name="list", value="list"),
])
async def cmd_worktree(interaction: discord.Interaction, action: str,
                       name: Optional[str] = None):
    """平行協作：on 開、merge 合回主分支並清理、off 移除（皆乾淨才動）、list 列出。"""
    if not await check_auth(interaction): return
    await interaction.response.defer()
    state = get_state(interaction.channel_id)
    cwd = state["cwd"]

    if action == "list":
        items = await asyncio.to_thread(wt_core.list_worktrees, cwd)
        if not items:
            await interaction.followup.send(t("wt_list_none"))
            return
        lines = [t("wt_list_title")]
        for it in items:
            br = it.get("branch") or (it.get("head", "")[:8]) or "?"
            lines.append(t("wt_list_item", branch=br, path=it.get("path", "")))
        await interaction.followup.send("\n".join(lines))
        return

    if action == "on":
        if state.get("wt"):
            w = state["wt"]
            await interaction.followup.send(
                t("wt_already_on", branch=w["branch"], path=w["path"]))
            return
        seg = name or getattr(interaction.channel, "name", None) or "session"
        res = await asyncio.to_thread(wt_core.create, cwd, seg)
        if not res.ok:
            await interaction.followup.send(_wt_error_text(res.error))
            return
        state["wt"] = {
            "path": str(res.path),
            "branch": res.branch,
            "base": res.base,
            "repo": str(res.repo),
            "prev_cwd": str(cwd),   # off 時還原到啟用前的目錄
        }
        state["cwd"] = res.path
        _persist_session(state)
        await interaction.followup.send(
            t("wt_on_done", branch=res.branch, base=res.base, path=res.path))
        if state.get("_sidebar"):
            await _rename_for_wt(interaction.channel, True)
        return

    if action == "merge":
        w = state.get("wt")
        if not w:
            await interaction.followup.send(t("wt_not_on"))
            return
        res = await asyncio.to_thread(
            wt_core.merge, w["repo"], w["path"], w["branch"], w["base"])
        if not res.ok:
            # 所有失敗情況都保持原狀、不刪任何東西（Q3-A 硬安全閘）
            if res.error == "worktree_dirty":
                await interaction.followup.send(t("wt_merge_wt_dirty"))
            elif res.error == "repo_dirty":
                await interaction.followup.send(
                    t("wt_merge_repo_dirty", base=w["base"]))
            elif res.error.startswith("repo_not_on_base"):
                cur = res.error.split(":", 1)[1] if ":" in res.error else "?"
                await interaction.followup.send(
                    t("wt_merge_not_on_base", base=w["base"], cur=cur))
            elif res.error == "merge_conflict":
                await interaction.followup.send(t("wt_merge_conflict",
                    branch=w["branch"], base=w["base"],
                    files=(res.detail or "?")[:500]))
            else:
                await interaction.followup.send(_wt_error_text(res.error))
            return
        # 合併成功 → worktree 與分支已清理，還原 cwd、清掉 wt、持久化
        prev = Path(w.get("prev_cwd") or w["repo"])
        state["cwd"] = prev if prev.is_dir() else DEFAULT_DIR
        state.pop("wt", None)
        _persist_session(state)
        await interaction.followup.send(
            t("wt_merge_done", branch=w["branch"], base=w["base"],
              cwd=state["cwd"]))
        if state.get("_sidebar"):
            await _rename_for_wt(interaction.channel, False)
        return

    # action == "off"
    w = state.get("wt")
    if not w:
        await interaction.followup.send(t("wt_not_on"))
        return
    res = await asyncio.to_thread(wt_core.remove, w["repo"], w["path"])
    if not res.ok:
        # 多半是有未提交變更 → 安全閘擋下，工作保留
        await interaction.followup.send(t("wt_off_dirty", err=res.error[:300]))
        return
    prev = Path(w.get("prev_cwd") or w["repo"])
    state["cwd"] = prev if prev.is_dir() else DEFAULT_DIR
    state.pop("wt", None)
    _persist_session(state)
    await interaction.followup.send(
        t("wt_off_done", branch=w["branch"], cwd=state["cwd"]))
    if state.get("_sidebar"):
        await _rename_for_wt(interaction.channel, False)

@bot.tree.command(name="screenshot", description=t("cmd_screenshot_desc"))
async def cmd_screenshot(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    await interaction.response.defer()
    shot = await _capture_screenshot()
    if not shot:
        await interaction.followup.send(t("screenshot_failed"))
        return
    try:
        if shot.stat().st_size > _DISCORD_FILE_LIMIT:
            await interaction.followup.send(t("screenshot_too_large"))
        else:
            await interaction.followup.send(t("screenshot_caption"), file=discord.File(str(shot)))
    finally:
        try:
            shot.unlink()
        except Exception:
            pass

@bot.tree.command(name="usage", description=t("cmd_usage_desc"))
async def cmd_usage(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    await interaction.response.defer()
    data = await asyncio.get_event_loop().run_in_executor(None, fetch_usage)
    if not data:
        await interaction.followup.send(t("usage_unavailable"))
        return
    lines = [t("usage_title")]
    shown = False
    for key, label in [("five_hour", t("usage_5h")), ("seven_day", t("usage_7d")),
                        ("seven_day_sonnet", t("usage_7d_sonnet")), ("seven_day_opus", t("usage_7d_opus"))]:
        obj = data.get(key)
        if not obj: continue
        pct = obj.get("utilization")
        if pct is None:
            pct = obj.get("percent_used", 0)
        resets = obj.get("resets_at", "")
        lines.append(t("usage_line_reset", label=label, reset=_reset(resets), countdown=_countdown(resets)))
        lines.append(f"`[{_bar(pct)}]` {pct:.0f}%")
        shown = True
    if not shown:
        await interaction.followup.send(t("usage_empty"))
        return
    lines.append(t("usage_cache_note"))
    await interaction.followup.send("\n".join(lines))

@bot.tree.command(name="handoff", description=t("cmd_handoff_desc"))
async def cmd_handoff(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    sid = state.get("session_id")
    if not sid:
        await interaction.response.send_message(t("handoff_empty"), ephemeral=True)
        return
    await interaction.response.defer()
    await interaction.followup.send(t("handoff_generating"))
    doc = await _generate_handoff(sid, state.get("model"))
    if not doc:
        await interaction.followup.send(t("handoff_empty"))
        return
    # send_long：短的直接貼成訊息（可複製）、長的存成 .md 上傳
    await send_long(interaction.channel, t("handoff_caption") + "\n\n" + doc)

@bot.tree.command(name="schedule", description=t("cmd_schedule_desc"))
async def cmd_schedule(interaction: discord.Interaction, task: str):
    if not await check_auth(interaction): return
    await interaction.response.defer()
    parse_prompt = t("schedule_parse_prompt", task=task)
    tmp_state: dict = {"session_id": None, "cwd": DEFAULT_DIR, "model": "claude-haiku-4-5-20251001", "effort": None}
    try:
        result, _, _ = await run_claude(parse_prompt, tmp_state)
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if not json_match:
            await interaction.followup.send(t("schedule_parse_failed", result=result[:300]))
            return
        parsed = json.loads(json_match.group())
        schedule: dict = {
            "id": str(uuid.uuid4())[:8],
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
            "task": parsed.get("task", task),
            "cron": parsed.get("cron", ""),
            "next_run": parsed.get("next_run", ""),
            "created_at": datetime.now().isoformat(),
        }
        schedules = _load_schedules()
        schedules.append(schedule)
        _save_schedules(schedules)
        embed = discord.Embed(title=t("schedule_created_title"), color=discord.Color.blue())
        embed.add_field(name=t("field_task"), value=schedule["task"], inline=False)
        cron_display = f"`{schedule['cron']}`" if schedule["cron"] else t("once")
        embed.add_field(name=t("field_cron"), value=cron_display, inline=True)
        embed.add_field(name=t("field_next_run"), value=schedule["next_run"][:16] if schedule["next_run"] else t("unknown"), inline=True)
        embed.set_footer(text=f"ID: {schedule['id']}")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(t("schedule_create_failed", e=e))

@bot.tree.command(name="schedules", description=t("cmd_schedules_desc"))
async def cmd_schedules(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    schedules = _load_schedules()
    if not schedules:
        await interaction.response.send_message(t("schedules_none"), ephemeral=True)
        return
    lines = [t("schedules_title")]
    for s in schedules:
        cron_disp = s.get("cron") or t("once")
        next_disp = s.get("next_run","")[:16] or t("unknown")
        lines.append(t("schedule_line", id=s['id'], task=s['task'], next=next_disp, cron=cron_disp))

    view = discord.ui.View()
    for s in schedules[:5]:
        btn = discord.ui.Button(label=t("btn_delete", id=s['id']), style=discord.ButtonStyle.danger)
        async def del_cb(inter: discord.Interaction, sid: str = s["id"]) -> None:
            sched_list = _load_schedules()
            sched_list = [x for x in sched_list if x["id"] != sid]
            _save_schedules(sched_list)
            await inter.response.send_message(t("schedule_deleted", id=sid), ephemeral=True)
        btn.callback = del_cb
        view.add_item(btn)

    await interaction.response.send_message("\n".join(lines), view=view)

@bot.tree.command(name="adduser", description=t("cmd_adduser_desc"))
async def cmd_adduser(interaction: discord.Interaction, user: discord.Member):
    if not await check_auth(interaction, owner_only=True): return
    if user.id == ALLOWED_USER:
        await interaction.response.send_message(t("adduser_already_owner"), ephemeral=True)
        return
    _allowed_users.add(user.id)
    _save_allowed_users(_allowed_users)
    await interaction.response.send_message(t("adduser_done", mention=user.mention, id=user.id))

@bot.tree.command(name="removeuser", description=t("cmd_removeuser_desc"))
async def cmd_removeuser(interaction: discord.Interaction, user: discord.Member):
    if not await check_auth(interaction, owner_only=True): return
    if user.id == ALLOWED_USER:
        await interaction.response.send_message(t("removeuser_cant_owner"), ephemeral=True)
        return
    _allowed_users.discard(user.id)
    _save_allowed_users(_allowed_users)
    await interaction.response.send_message(t("removeuser_done", mention=user.mention, id=user.id))

@bot.tree.command(name="listusers", description=t("cmd_listusers_desc"))
async def cmd_listusers(interaction: discord.Interaction):
    if not await check_auth(interaction, owner_only=True): return
    lines = [f"• `{uid}`{t('owner_tag') if uid == ALLOWED_USER else ''}" for uid in _allowed_users]
    await interaction.response.send_message(t("listusers_header") + "\n".join(lines), ephemeral=True)

@bot.tree.command(name="addchannel", description=t("cmd_addchannel_desc"))
async def cmd_addchannel(interaction: discord.Interaction):
    # 此指令本身不能用 check_auth（新頻道還沒在清單裡），改直接驗證主帳號
    if interaction.user.id != ALLOWED_USER:
        await interaction.response.send_message(t("owner_only"), ephemeral=True)
        return
    cid = interaction.channel_id
    if cid in _allowed_channels:
        await interaction.response.send_message(t("addchannel_already"), ephemeral=True)
        return
    _allowed_channels.add(cid)
    _save_allowed_channels(_allowed_channels)
    await interaction.response.send_message(
        t("addchannel_done"))

@bot.tree.command(name="removechannel", description=t("cmd_removechannel_desc"))
async def cmd_removechannel(interaction: discord.Interaction):
    if not await check_auth(interaction, owner_only=True): return
    cid = interaction.channel_id
    if cid == ALLOWED_CHANNEL:
        await interaction.response.send_message(t("removechannel_cant_main"), ephemeral=True)
        return
    _allowed_channels.discard(cid)
    _save_allowed_channels(_allowed_channels)
    await interaction.response.send_message(t("removechannel_done"))

@bot.tree.command(name="help", description=t("cmd_help_desc"))
async def cmd_help(interaction: discord.Interaction):
    if not await check_auth(interaction): return
    await interaction.response.send_message(t("help_text"))

# ── 一般訊息 → 送給 Claude ──────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id not in _allowed_channels or message.author.id not in _allowed_users:
        return
    if message.content.startswith("/"):
        await bot.process_commands(message)
        return

    if _processing.get(message.channel.id):
        await message.channel.send(t("busy_prev"), delete_after=5)
        return

    state = get_state(message.channel.id)
    text = re.sub(r"<@!?\d+>", "", message.content).strip()

    # 若有待答選項，使用者打數字 → 對應到選項文字
    pending = state.get("pending_options")
    if pending and text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(pending):
            chosen = pending[idx]
            state.pop("pending_options", None)
            await _process_answer(message.channel, chosen, state)
            return

    _processing[message.channel.id] = True
    is_voice = False  # 這則是否來自語音輸入（決定要不要語音回覆）

    # 處理附件
    if message.attachments:
        tmp_dir = Path(__file__).parent / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        saved: list[str] = []
        failed: list[str] = []
        voice_texts: list[str] = []
        voice_blocked = False  # 開車模式關閉時收到語音 → 標記，迴圈後提示
        for att in message.attachments:
            dest = tmp_dir / att.filename
            # 語音訊息 → 本機 STT 轉文字（CC 讀不了音訊），不當檔案路徑給 CC
            if (att.content_type or "").startswith("audio/"):
                if not _drive_mode:
                    voice_blocked = True  # 在家關閉中，不載模型、不轉錄
                    continue
                try:
                    req = urllib.request.Request(att.url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
                        f.write(r.read())
                    transcript = await asyncio.to_thread(_transcribe, str(dest))
                    if transcript:
                        voice_texts.append(transcript)
                except Exception as ex:
                    failed.append(t("voice_fail", filename=att.filename, ex=ex))
                continue
            try:
                req = urllib.request.Request(att.url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
                    f.write(r.read())
                # 簡報轉 PDF 後給 CC 視覺讀取；轉檔失敗則回退原檔
                if dest.suffix.lower() in (".ppt", ".pptx"):
                    pdf = await _convert_pptx(dest)
                    saved.append(str(pdf) if pdf else str(dest))
                else:
                    saved.append(str(dest))
            except Exception as ex:
                failed.append(t("attach_fail_item", filename=att.filename, ex=ex))
        # 語音辨識結果當成使用者打的字
        if voice_texts:
            is_voice = True
            heard = " ".join(voice_texts)
            await message.channel.send(t("heard", heard=heard))
            # 顯示給使用者的是乾淨原文；送給 CC 的另外包一層提示，
            # 標明這是語音辨識結果、可能有怪字，請 CC 依上下文推斷原意
            text = (text + " " if text else "") + t("voice_hint", heard=heard)
        # 開車模式關閉時收到語音 → 提示要先開（文字附件仍照常處理）
        if voice_blocked and not voice_texts:
            await message.channel.send(t("drive_off_voice"))
        if failed and not saved and not text:
            await message.channel.send(t("attach_failed", failed=', '.join(failed)))
            _processing[message.channel.id] = False
            return
        if saved:
            paths = "\n".join(saved)
            text = (text + "\n\n" if text else "") + t("uploaded_files", paths=paths)

    if not text:
        _processing[message.channel.id] = False
        return

    state.pop("pending_options", None)

    # Speaker ID：告知 CC 是誰在說話
    speaker = message.author.display_name
    full_prompt = f"[{speaker}]: {text}"

    # Auto-compact：context 達門檻先壓縮
    await _maybe_auto_compact(message.channel, state)

    # 目前 session 標題，顯示在思考中訊息頂部，工作時一眼知道在哪個對話
    state["_session_label"] = await asyncio.to_thread(_session_label, state.get("session_id"))

    progress_msg = await message.channel.send(t("thinking"))
    t0 = time.time()
    try:
        reply, new_sid, ask_data = await _run_tracked(
            message.channel.id, full_prompt, state, progress_msg
        )
        if new_sid:
            state["session_id"] = new_sid
            _persist_session(state)
            # 更新標題與 bot 狀態（新對話此時才有 session 檔可讀標題）
            state["_session_label"] = await asyncio.to_thread(_session_label, new_sid)
            await _update_presence(message.channel.id, state["_session_label"])
        await progress_msg.delete()
        if ask_data:
            await _send_ask_question(message.channel, ask_data, state)
        elif reply and reply != _NO_RESPONSE:
            # 開車模式＋這則是語音輸入 → 解析朗讀版、合成語音檔；回傳去掉標記的文字版
            reply = await _voice_reply(message.channel, reply, speak=(is_voice and _drive_mode))
            await _send_files_and_text(message.channel, reply)
        # 長任務完成 → @使用者推播（手機會震，可離開後再回來）
        elapsed = time.time() - t0
        if elapsed >= NOTIFY_AFTER_SEC:
            tip = t("notify_need_answer") if ask_data else t("notify_done")
            await message.channel.send(f"{message.author.mention} ✅ {tip} · {elapsed:.0f}s")
        # 側欄頻道：第一句處理完 → 讀內容生成中文標題、改頻道名（只改一次）
        if state.get("_sidebar") and not state.get("_named") and state.get("session_id"):
            state["_named"] = True
            asyncio.create_task(_autoname_channel(message.channel, state))
        # 側欄頻道：最新有活動 → 移到入口下方置頂（已在頂端不動）
        elif state.get("_sidebar"):
            asyncio.create_task(_bump_channel_to_top(message.channel))
    except _StoppedByUser:
        await progress_msg.edit(content=t("stopped"))
    except CCError as e:
        await _handle_cc_error(progress_msg, e, state)
        if time.time() - t0 >= NOTIFY_AFTER_SEC:
            await message.channel.send(t("notify_error", mention=message.author.mention))
    except Exception as e:
        print(f"[CC_FAIL] kind=UNCLASSIFIED raw={e!r}", flush=True)
        detail = f"{type(e).__name__}: {e}"[:1500]
        await progress_msg.edit(content=t("unexpected_error", detail=detail)[:2000])
    finally:
        _processing[message.channel.id] = False

    await bot.process_commands(message)

def _acquire_single_instance_lock() -> Optional["socket.socket"]:
    """單一實例防呆：綁定固定本機 port，綁不上代表已有 bot 在跑，回傳 None。
    用 socket 而非鎖檔，進程結束 port 自動釋放，不會有殘留鎖問題。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 47361))  # 此 port 專供本 bot 當鎖用
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None

if __name__ == "__main__":
    _lock = _acquire_single_instance_lock()
    if _lock is None:
        print(t("instance_running"), flush=True)
        raise SystemExit(0)
    bot.run(DISCORD_TOKEN)
