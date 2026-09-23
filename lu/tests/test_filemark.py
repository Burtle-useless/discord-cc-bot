"""舊的 `[[FILE:]]` 標記要能拆出來。

重寫成 lu 之後這個標記一度沒人處理，於是原樣印在頻道上，
使用者看到一行路徑以為檔案給了，實際什麼都沒送。**漏送是靜默的**，
所以這幾條要釘住。
"""
from __future__ import annotations

import sys
from pathlib import Path

# lu 的上一層（cc-bot）要在 path 上才 import 得到 lu。這支是純函式測試，
# 不碰 engine 也不需要暫存資料目錄
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lu.filemark import split_file_marks  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


def main() -> int:
    print("\n[基本]")
    clean, paths = split_file_marks("看這個 [[FILE: C:/tmp/a.pdf]] 給你")
    check("路徑抽得出來", paths == ["C:/tmp/a.pdf"], str(paths))
    check("標記從文字裡拿掉", "[[FILE" not in clean, clean)
    check("其餘文字留著", "看這個" in clean and "給你" in clean, clean)

    print("\n[真實案例：Windows 路徑含中文與空格]")
    real = (
        "清單如下\n\n"
        "[[FILE: C:\\Users\\you\\Documents\\期末 報告\\第二版"
        "\\Report_final_v2_reviewed.docx]]\n"
        "[[FILE: C:\\Users\\you\\Documents\\期末 報告\\第二版"
        "\\04_繳交前檢查清單.docx]]\n\n"
        "每一項都寫了要補什麼。"
    )
    clean, paths = split_file_marks(real)
    check("兩個檔都抓到", len(paths) == 2, str(len(paths)))
    check("中文路徑完整", paths[0].endswith("Report_final_v2_reviewed.docx"),
          paths[0][-40:])
    check("順序保留", "04_繳交前" in paths[1], paths[1][-30:])
    check("文字乾淨", "[[FILE" not in clean, clean[:40])
    check("沒留下一堆空行", "\n\n\n" not in clean, repr(clean))

    print("\n[邊界]")
    check("沒有標記就原樣回", split_file_marks("普通訊息") == ("普通訊息", []))
    check("空字串安全", split_file_marks("") == ("", []))
    check("同一個檔標兩次只送一次",
          split_file_marks("[[FILE: a.txt]] [[FILE: a.txt]]")[1] == ["a.txt"])
    check("引號會剝掉",
          split_file_marks('[[FILE: "C:/a b.txt"]]')[1] == ["C:/a b.txt"])
    # 只有標記、沒有其他文字：清乾淨之後是空字串，呼叫端才知道不必發訊息
    check("整則只有標記 → 文字變空",
          split_file_marks("[[FILE: a.txt]]")[0] == "")
    check("沒有結尾的壞標記不當成路徑",
          split_file_marks("[[FILE: 沒收尾")[1] == [])

    print(f"\n{'=' * 50}")
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
