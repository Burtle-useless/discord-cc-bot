"""向後相容：舊的 `[[FILE: 絕對路徑]]` 標記也要真的把檔案送出去。

**這是遷移遺留的洞。** 舊版 `discord_bot.py` 用文字標記交檔案，重寫成
`lu/` 時整組沒搬過來，新的 system prompt 也改成教 `send_file` 工具了——但**舊對話
的脈絡還在**：那些 session 裡有幾十次「打了標記、檔案就出現」的成功案例，模型照著
學，於是繼續輸出標記。而新程式不認得它，結果是**標記原樣印在頻道上**，使用者看到
一行路徑以為檔案給了，實際上什麼都沒送。

失敗方式決定了要修哪一邊：靠 prompt 糾正不可靠（模型跟著脈絡走），而漏送檔案
是靜默的——沒有人會發現。所以認回這個標記，讓它跟 `send_file` 一樣有效。
"""
from __future__ import annotations

import re

# 舊版原樣：`[[FILE: C:\path\to\file.docx]]`。路徑可能含空格與中文，非貪婪吃到 ]]
FILE_MARK = re.compile(r"\[\[FILE:\s*(.+?)\s*\]\]")


def split_file_marks(text: str) -> tuple[str, list[str]]:
    """把 `[[FILE:]]` 從文字裡拆出來。

    回 (清乾淨的文字, 路徑清單)。順序保留——模型交兩個檔時通常有主次之分。
    重複的路徑只留一次：同一則訊息裡標記兩次同一個檔，送兩次只是洗版。
    """
    if not text or "[[FILE:" not in text:
        return text, []
    seen: set[str] = set()
    paths: list[str] = []
    for m in FILE_MARK.finditer(text):
        p = m.group(1).strip().strip('"').strip("'")
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    # 標記自己佔一行時會留下空行，順手把連續空行收乾淨
    clean = FILE_MARK.sub("", text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, paths
