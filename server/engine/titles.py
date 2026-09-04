"""對話標題：自動命名與接管後改名。

原本長在 transport/app.py 裡，但它用到的全是 engine 的東西（`load_history`、
`generate_title`、`state.set_title`、`conv_renamed` 事件），跟 HTTP 無關；
cc-bot 那邊也要同一套。這裡拿的是一個 `protocol.Frontend`，不知道 SSE 的存在——
要用哪個 frontend 由呼叫端（transport）決定並帶進來。
"""
from __future__ import annotations

from protocol import Frontend, make_event

from . import state as state_mod
from .history import load_history
from .meta import generate_title


def title_source(conv_id: str, first_message: str) -> str:
    """挑一段拿去生標題的文字：優先用整輪對話，讀不到才退回第一則訊息。

    只讀第一則訊息生不出好標題——開場常常是「幫我看一下」「這個怎麼壞了」，
    本身沒有資訊量，助理的回答才有。cc-bot 一直是讀 session 內容的，這裡對齊它。

    走 load_history 而不是自己開 jsonl：它已經濾掉 compact 摘要與各種維運注入
    （續跑提示、<task-notification>…），那些混進來會直接變成標題。
    退路是必要的：session 檔由 CLI 寫出，回合剛結束時不保證讀得到。
    """
    # 標題要的是開頭：對話尾端是當下在忙什麼，開頭才是這條對話在講什麼。
    # 用 head 而不是「撈全部再切前 20」——後者會把整份逐字稿（動輒上 MB）
    # 解析完才丟掉九成，head 湊滿 20 則就停止讀檔。
    try:
        msgs = load_history(conv_id, head=20)
    except Exception:
        msgs = []
    # system 那幾則（「背景工作完成，助理接手」）是畫面用的說明，不是對話內容
    text = "\n".join(m["text"] for m in msgs if m.get("role") != "system").strip()
    return text or first_message


async def apply_title(conv_id: str, source_text: str, frontend: Frontend) -> bool:
    """生標題並套用，成功回 True。

    抽出來共用是因為有兩條路徑要命名：自己開的新對話（autoname），以及
    接管電腦上既有 session（rename_adopted）。兩者只差「拿什麼文字去生」，
    後半的存檔與通知一模一樣。

    改名一定要發 conv_renamed 事件：標題存在伺服器，App 的側欄是自己一份
    複本，不通知的話要等下一次重拉清單才會變。
    """
    title = await generate_title(source_text)
    if not title:
        return False
    state_mod.set_title(conv_id, title)
    await frontend.emit(make_event(
        conv_id, "-", "status", note=f"conv_renamed:{title}",
    ))
    return True


async def rename_adopted(conv_id: str, frontend: Frontend) -> None:
    """接管既有 session 之後，把暫用標題換成 Haiku 生的短標題。

    接管當下只能拿開場白前 60 字頂著——那句話常常是「幫我看一下」，或是一整段
    貼上來的錯誤訊息，在側欄裡看不出這條對話在講什麼。既有 session 的內容本來
    就是滿的，這裡不必等使用者先說話，接完就能直接生。

    做成背景任務而不是在端點裡等：Haiku 要幾秒，卡在那邊會讓「接手中…」轉半天，
    而使用者要的只是趕快進到對話裡。標題晚幾秒自己變好即可。
    """
    try:
        text = title_source(conv_id, "")
        if text:
            await apply_title(conv_id, text, frontend)
    except Exception as e:
        print(f"[AUTONAME] 接管後改名失敗 conv={conv_id}: {e!r}", flush=True)


async def autoname(conv_id: str, first_message: str, frontend: Frontend) -> None:
    """背景生標題：先用訊息前綴立即可見，Haiku 成功再升級。失敗就保持前綴。

    整段包 try/except 的理由：這是 create_task 起的背景任務，丟出的例外不會
    冒到請求端，asyncio 只在 log 裡留一行「Task exception was never retrieved」。
    2026-08-15 就是這樣——傳錯型別炸了 7 次，對外症狀只是「標題一直生不出來」，
    看起來像 Haiku 沒回應，實際上根本沒送出去。cc-bot 的 _autoname_channel
    一直是這樣包的，搬過來時漏掉了。
    """
    try:
        if state_mod.get_title(conv_id) is None:
            state_mod.set_title(conv_id, first_message.strip()[:20] or conv_id)
            await apply_title(conv_id, title_source(conv_id, first_message), frontend)
    except Exception as e:
        print(f"[AUTONAME] 自動命名失敗 conv={conv_id}: {e!r}", flush=True)
