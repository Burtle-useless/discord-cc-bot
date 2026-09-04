"""SDK 選項組裝。

這支檔案裡每一個參數都是踩過坑換來的，改之前先讀註解。
"""
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

import config
from protocol import Frontend

from . import persona
from .safety import make_pretool_hook
from .state import ConvState, eff_effort, eff_model

# 疊加在 Claude Code 預設 prompt 之上的規則。
#
# ⚠️ **絕對不可含換行符**。實測（SDK 0.1.81 / Windows）：append 內只要有一個 "\n"，
# initialize 的控制訊息就會損毀，CC 永遠完成不了握手，卡滿 60 秒後拋
# "Control request timeout: initialize"。用三個字元的 "A\nB" 即可穩定重現。
# 長度不是問題——四千字的 prompt 照跑，只要一個換行都沒有。
# 同理也不要嵌入錢字號、反引號、罕見 Unicode 符號，一律用文字描述規則。
# 護欄在 sanitize_append()，但別依賴它——知道為什麼比被擋下來重要。
#
# ── 這支檔案裡的規則 vs personas/*.txt ────────────────────────────────────────
# 底下四條是**功能性**的：拿掉服務就會壞。標記協定是 turn.py 判斷回合結束的
# 依據，缺了會反覆誤觸發補跑；自製工具不明講模型不會用。
# 語氣、個性、示範對話那些屬於人格，全部在 personas/ 底下，隨便改都不會弄壞東西。

# 1. 進度與控制標記——turn.py 的自動續跑依賴這兩個標記。
#
# 標記一定要寫出**字面值**。早期版本用文字描述「兩個中括號包住 DONE」，
# 模型理解成單層的 [DONE]，而正規表示式只認 [[DONE]]：標記沒被清掉直接洩漏到畫面上，
# 續跑判定也跟著失效，於是每次都多補跑一輪、再吐一個標記出來。
# 中括號本身是安全字元，不需要為它繞路。
_RULE_MARKERS = (
    "每個階段開始時用一句話說你正要做什麼，讓他在手機上跟得上。"
    "整件事真的做完時，在回覆的最後面單獨加上這個標記：[[DONE]]"
    "如果你問了問題正在等他回答，就改加上這個標記：[[WAIT]]"
    "標記要用兩層中括號，一字不差。這是給系統判讀的，他看不到，不要解釋也不要提起它們。"
)

# 2. 要他做選擇時，把選項變成手機上的按鈕。
#
# 這條取代了 CC 內建的 AskUserQuestion——那個工具**不在 SDK session 的工具清單裡**
# （實測 36 個工具全都沒有它，環境變數也叫不出來），模型看不到就不可能呼叫，
# 所以先前那條攔工具的路徑從來沒有觸發過一次。改走文字標記，跟 [[DONE]] 同一套機制。
#
# 標記一定要寫出**字面值**，理由同 _RULE_MARKERS。
_RULE_ASK_MARKER = (
    "需要他從幾個做法裡挑一個時，先用白話把狀況和你的建議講清楚，"
    "然後在回覆的最後面加上這個標記：[[ASK:問題|選項一|選項二]]"
    "直線符號隔開，第一段是問題本身，後面每一段是一個選項，給二到四個選項，"
    "每個選項用簡短的名詞或短句，不要寫成長句子，手機按鈕放不下。"
    "他的手機上會跳出按鈕讓他直接點，點下去的那個選項會變成你的下一則輸入。"
    "標記要用兩層中括號、一字不差，他看不到標記本身，不要解釋也不要提起它。"
    "打了這個標記就停在那裡等他點，不要繼續往下做、也不要自己幫他選一個然後動手，"
    "而且不要再加 [[DONE]] 或 [[WAIT]]，這個標記本身就代表你在等他回答。"
)

# 3. 行事曆／鬧鐘／記帳／課表。
#
# 這些是自製工具（in-process MCP，見 agenda_tools.py），不是 CC 內建的，
# 模型不會憑空知道它們存在——工具清單裡有歸有，不明講的話它遇到「幫我記一下」
# 還是會去建一個 txt 檔。這條就是在指路：什麼情況該想到這組工具。
_RULE_AGENDA = (
    "他的行事曆、鬧鐘、記帳、課表都在你手上，"
    "用 calendar 開頭、alarm 開頭、ledger 開頭、course 開頭的那組工具操作，"
    "不要自己去建檔案記，也不要叫他自己打開 App 輸入。"
    "他隨口提到的時間與金錢就直接記下來：說明天幾點要幹嘛就開行程，"
    "說幾點叫我起床就設鬧鐘，說花了多少錢就記帳，分類你自己判斷不用問他。"
    "記完用一句話帶過就好，不要複誦整筆資料。"
    "行程與鬧鐘的差別是會不會吵他：鬧鐘會在手機上大聲響，只有他要求叫他的時候才設。"
    "課表是一週固定重複的課，用 course_add 加、course_now 看他現在有沒有在上課；"
    "第幾節是幾點到幾點每個地方不一樣，他講了就用 period_set 改，別自己假設。"
    "要判斷現在方不方便打擾他、或是幫他排事情避開上課時間，先看 course_now，"
    "不要拿課表自己去對時間。"
)

# 4. 傳檔給手機。
#
# 同樣是自製工具（file_tools.py）。這條的重點在**時機**而不是用法：
# 模型預設會把成品留在磁碟上、回一句「檔案在 D:\... 」就結束，
# 而使用者人不在電腦前，那個路徑對他毫無用處。
_RULE_SENDFILE = (
    "他在手機上時，你做出來的檔案他看不到，光報路徑等於沒給。"
    "圖片、報告、匯出的資料這種他會想直接看或存起來的東西，做完就用 send_file 傳給他，"
    "不用先問他要不要。純程式碼或設定檔就不必傳，貼在回覆裡比較快。"
    "他在電腦前面時，路徑講清楚就行，那台電腦就在他手上，不必再傳一次。"
)

# 5. 位置。
#
# 這條的重點也是時機。工具本身很好用（一句話就拿到地址），所以模型會很想用它——
# 但每呼叫一次就是他的手機開一次 GPS，而且他不會看到任何提示。
# 講清楚「需要才問、拿到就別重複問」比講用法重要。
_RULE_WHERE = (
    "要知道他人在哪就用 where_am_i，它會跟他手機要一次目前位置。"
    "只有在位置真的會改變你的回答時才用（附近有什麼、路上要多久、天氣如何），"
    "不要為了寒暄就去抓。同一件事問過一次就別再抓第二次。"
    "拿到的資料會告訴你那是幾分鐘前的，久到不合理就講出來，不要當成他現在的位置。"
)

# 6. 不要自己重啟服務。
#
# 這條是實際踩出來的。改完伺服器程式想讓它生效，最直覺的動作就是去跑
# restart_butler.ps1——但跑它的那個行程是伺服器的子孫，重啟會把自己一起收掉。
# 使用者那端看到的是「講到一半突然斷線」，而且不知道為什麼。
#
# 這條兩份 append 都要有：改伺服器程式這件事在工作分頁反而更常發生。
_RULE_NO_RESTART = (
    "**絕對不要自己重啟 butler 服務**：不要執行 restart_butler.ps1，"
    "不要去殺它的行程，也不要用任何方式讓它重新啟動。"
    "你就跑在那個服務裡面，重啟等於把你自己關掉，他只會看到你講到一半消失。"
    "伺服器的程式改完之後，直接告訴他「改好了，到工具頁按一下重新啟動才會生效」，"
    "然後這件事就交給他，不要自己動手。"
)

# 7. 看板。
#
# 結構是「企劃分組、卡片是底下的細分工作」。模型不會憑空知道這組工具存在，
# 也不會知道該用什麼粒度開卡——不講的話它要嘛整條線開一張、要嘛每個小步驟
# 都開一張，兩種都會讓板子沒法用。
_RULE_KANBAN = (
    "他的工作看板在你手上（kanban_list / kanban_add / kanban_update）。"
    "看板分待辦、進行中、完成三欄，一張卡片是一件細分工作。"
    "開始做一件事就把它換到 doing，做完換到 done，卡住或需要他拍板就標 urgent 並在 note 寫清楚卡在哪。"
    "他交代新工作就用 kanban_add 加上去，標題前面帶企劃名用「－」隔開（像「網站改版－看板拖放」），"
    "加之前先 kanban_list 看既有的企劃名怎麼寫的，同一條線的前綴要一致。"
    "粒度抓在「一件能單獨完成、講得出做完長什麼樣」的事，不要把整個專案開成一張卡，"
    "也不要把每個步驟都開一張。"
    "不要自己刪卡片，要拿掉他會自己在 App 上封存。"
)

# 8. 時間感。搭配 turn._stamp 蓋在每則使用者訊息前面的時間戳一起用。
#
# 模型手上只有 CLI system prompt 那句「今天幾號」，對話裡每一則訊息都沒有時間，
# 於是「隔了多久」全靠感覺編。實際症狀是它把同一天早上講過的事說成「我昨天說的」。
#
# 時間戳治得了新訊息，治不了已經存在的舊訊息（那些永遠不會有前綴），
# 所以規則要一起下：算不出來就不要講得像算得出來。
#
# 這條兩份 append 都要有——前綴是伺服器蓋的，不解釋的話模型會把它當成
# 使用者打的字複誦出來。
_RULE_TIME = (
    "每則使用者訊息前面的方括號，例如 [08/21 週四 11:04 手機]，是系統加的，他自己看不到。"
    "前半是那則訊息送出的時間。"
    "要講時間就照這個算，不要複誦它、不要在自己的回覆裡模仿這個格式。"
    "沒有這個時間的舊訊息就是算不出來，這種時候不要說昨天、上次、前幾天這種話，"
    "改說前面、稍早、剛才。寧可講得模糊，也不要講一個聽起來精確但其實是猜的時間。"
)

# 9. 來源：這則話是從哪一端送進來的。
#
# 前綴後半的「手機」／「電腦」由 transport 依請求的 `client` 欄位蓋上（見
# `transport.app._SRC_NAMES`）。附的 Android App 不送這個欄位，所以預設一律是
# 「手機」；自己另外寫一個桌面或網頁前端時送 `client: "desktop"` 就會變成「電腦」。
#
# 規則綁在**每則訊息**而不是連線上：他可能手機打到一半走到電腦前，
# 以最後一則的來源為準才是對的。
#
# 這條講的是**平台事實**（螢幕多大、路徑點不點得開），不是語氣。語氣在人格檔裡。
_RULE_SOURCE = (
    "方括號後半是他從哪一端送的。"
    "標「手機」＝小螢幕，話要短，長內容先給結論再展開，"
    "不要動不動就用條列或表格排版，檔案路徑跟程式碼貼給他也沒有用。"
    "標「電腦」＝他坐在電腦前面用大視窗看，話可以長、可以用條列跟表格、"
    "可以貼路徑跟程式碼區塊，也可以直接叫他去開某個檔案或按某個按鈕。"
    "沒標來源的是舊訊息或系統自動訊息，一律當手機處理。"
    "同一條對話裡這個標記會變，每次都看最新那則，不要用前面的印象當作他現在人在哪。"
)

# 10. 派子代理時要自己重述語言要求。
#
# **子代理讀不到這份 append。** 它拿的是 Claude Code 自己那份 system prompt，
# SDK 的 `system_prompt` append 傳不下去，所以人格檔裡寫的語言、語氣、回報格式
# 對子代理全部無效——它會用自己的預設語言回話，階段說明就混著兩種語言出現在
# 同一個畫面上。這是平台限制，只能靠派工時在 prompt 裡明講一次。
#
# 刻意**不寫死是哪一種語言**：語言由人格檔決定（kit 附的是繁體中文，換掉就換了），
# 這條只負責提醒「把你上面被要求的語言重述給子代理」。
_RULE_SUBAGENT = (
    "派子代理（Agent 工具）出去做事時，要在給它的 prompt 裡把語言要求重寫一遍，"
    "講明回覆、階段說明與程式碼註解要用哪一種語言。"
    "子代理讀不到你這份設定，不明講它就會用自己的預設語言回話，"
    "兩種語言混在同一個畫面上很突兀。"
)


def _amnesia_rule() -> str:
    """失憶自救。沒設 BUTLER_NOTES_FILE 就整條不加。

    伺服器重啟會殺掉 CC 進程，resume 回來時**最後一個回合接不回 context**：
    session id 沒變、逐字稿裡那段也寫進去了，但模型這邊就是沒有——
    檔案裡有，腦子裡沒有。

    觸發點刻意**不是**「重啟後」：失憶的人不知道自己失憶了，那個條件抓不到。
    唯一可靠的信號是使用者說「你剛剛才查過」。

    路徑在 prompt 裡一律轉成正斜線：反斜線要進 JSON 控制訊息，能少一層跳脫就少一層。
    """
    if not config.NOTES_FILE:
        return ""
    path = config.NOTES_FILE.replace("\\", "/")
    return (
        "你可能會忘記事情。這台電腦上的服務偶爾會讓你漏掉自己剛做完的一整段工作，"
        "而且你不會察覺，只會覺得那件事從來沒發生過。"
        "所以他說你剛剛才查過、你不是做完了、你又忘了這類話的時候，"
        "一律先假設他是對的，不要反駁、不要說自己沒忘、更不要回頭問他細節。"
        f"立刻去讀 {path}，最上面幾段就是最近幾輪做過的事，讀完再回話。"
        "要回報進度、要說某件事還沒做、或是要動這個專案的檔案之前也先讀它，"
        "那個檔比你的印象新。"
    )


# 助理對話：人格 + 全部核心規則。
SYSTEM_APPEND = (
    persona.load(config.PERSONA) + _amnesia_rule() + _RULE_TIME + _RULE_SOURCE
    + _RULE_SUBAGENT + _RULE_AGENDA + _RULE_KANBAN + _RULE_SENDFILE + _RULE_WHERE
    + _RULE_NO_RESTART + _RULE_ASK_MARKER + _RULE_MARKERS
)

# ── 工作區分頁：不套人格 ──────────────────────────────────────────────────────
#
# 助理與工作區是兩個用途不同的東西，共用同一份 append 會讓工作對話也被灌上
# 語氣與行事曆記帳規則。
#
# 這份刻意只留「不留就會壞掉」的：平台事實（他在手機上看）、進度標記、
# 一次一問（ask 通道一次只收得到一個答案）。行事曆規則不進來——那是生活資料，
# 工作對話開著只會讓模型在「幫我記一下這個 bug」的時候把東西寫進記帳本。
# 其餘一律交還給 Claude Code 的預設行為，這才是「純工作」該有的樣子。
WORK_APPEND = (
    persona.load(config.WORK_PERSONA) + _RULE_TIME + _RULE_SOURCE
    + _RULE_SUBAGENT + _RULE_SENDFILE + _RULE_NO_RESTART
    + _RULE_ASK_MARKER + _RULE_MARKERS
)


# PreToolUse hook 要攔哪些工具（工具名的 regex）。
#
# **抽成常數是為了讓測試能直接驗。** 這裡跟 `safety.needs_confirm` 是一組的：
# needs_confirm 寫對了但這裡漏掉工具名，整道確認防線就是空的——hook 根本不會被
# 呼叫，而且沒有任何錯誤訊息，表現出來只是「破壞性指令沒跳確認就跑了」。
PRETOOL_MATCHER = "Bash|PowerShell|AskUserQuestion"


def append_for(state: ConvState) -> str:
    """這條對話該套哪一份 system prompt。

    誰用哪一份由 `profiles` 決定：助理（全套）綁死一條固定對話，其餘依 conv_id
    前綴查註冊表，查不到就是預設的工作精簡版。
    """
    # 函式內 import：profiles 在載入時要拿這裡的兩份 append，頂端互相 import 會繞成一圈
    from . import profiles
    return profiles.resolve(state.conv_id).append


def servers_for(state: ConvState) -> dict:
    """這條對話該掛哪些自製工具。同樣交給 `profiles`，理由見 append_for。"""
    from . import profiles
    return profiles.resolve(state.conv_id).servers(state)


def sanitize_append(text: str) -> str:
    """清掉會弄壞 init 握手的字元。

    這是確定性護欄，不是禮貌提醒——人格檔與 plugin 都會貢獻
    system_prompt_append 文字，只靠註解叮嚀「不要換行」遲早會有人踩到，
    而症狀是「卡 60 秒後 timeout」，完全不指向換行符。
    """
    return " ".join(text.split())


def thinking_off(state: ConvState) -> dict:
    """關思考逃生門要送的 thinking 設定，依模型分流。純函式，供測試直接驗。

    非 Fable／Mythos 送 disabled——實測只有它能真的關掉思考（省略參數僅把 display
    退回 omitted，模型照樣思考）。
    Fable／Mythos 思考強制開啟、收到 disabled 會 400，只能退回 adaptive；
    保留 summarized 讓思考摘要仍拿得到，空回覆的第三層兜底才有素材可用。
    """
    m = (eff_model(state) or "").lower()
    if "fable" in m or "mythos" in m:
        return {"type": "adaptive", "display": "summarized"}
    return {"type": "disabled"}


def build_options(state: ConvState, frontend: Frontend) -> ClaudeAgentOptions:
    """依對話狀態組出 ClaudeAgentOptions（建立長駐 client 時用一次）。"""
    # cwd 防護：切換歷史 session 可能帶入已不存在的目錄（WinError 267），退回預設
    if not Path(state.cwd).is_dir():
        state.cwd = config.DEFAULT_CWD
    options = ClaudeAgentOptions(
        cwd=str(state.cwd),
        cli_path=config.CLAUDE_CLI,
        model=eff_model(state),
        effort=eff_effort(state),
        # 維持全放行（自動執行，不干擾工作流）。危險指令確認改掛 PreToolUse hook：
        # 實測 headless/SDK 下 can_use_tool 回呼不會被觸發，但 PreToolUse hook 即使在
        # bypassPermissions 也照樣觸發、且 permissionDecision="deny" 能真正擋下工具。
        permission_mode="bypassPermissions",
        hooks={"PreToolUse": [HookMatcher(
            matcher=PRETOOL_MATCHER,
            hooks=[make_pretool_hook(state, frontend)],
        )]},
        # 行事曆／鬧鐘／記帳走 in-process MCP：跟服務同一個進程，沒有 IPC 開銷，
        # 也不必再開一個子進程去守一份 JSON 檔。
        # 刻意不設 allowed_tools——那是白名單，一設下去 CC 內建工具全被擋掉。
        mcp_servers=servers_for(state),
        fallback_model=config.FALLBACK_MODEL,
        max_buffer_size=config.MAX_BUFFER_SIZE,
        # system_prompt 用 preset+append：自訂規則以「疊加」方式放在 Claude Code 完整
        # 預設 prompt 之上，保留預設行為框架與 CLAUDE.md 的開場注入。
        # 傳純字串會整份「取代」預設 prompt，CC 因此缺所有預設行為規則，
        # 只剩碰運氣的 nested-memory 附帶、壓縮後歸零。
        # append 一律過 sanitize_append：含換行會讓 init 握手卡死 60 秒（見上方說明）
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": sanitize_append(append_for(state)),
        },
        # 思考摘要：Opus 4.7+ 預設 display="omitted"（只回簽章、沒有文字），這是
        # 「思考中」永遠空白的根因。改 summarized 才拿得到思考文字。
        #
        # 必須用 adaptive，不能用 enabled：SDK 對 type=="enabled" 無條件讀
        # t["budget_tokens"] 組 --max-thinking-tokens，沒帶就 KeyError，每次建 client
        # 必炸；而 budget_tokens 自 Opus 4.7 起已從 API 移除，現代模型收到它一律 400
        # ——補上鍵也無解，enabled 就是死路。
        thinking=(thinking_off(state) if state._no_think
                  else {"type": "adaptive", "display": "summarized"}),
    )
    # 逐字串流：讓生成中的思考／回應能即時送出，也是回合仍活著的訊號
    options.include_partial_messages = True
    return options
