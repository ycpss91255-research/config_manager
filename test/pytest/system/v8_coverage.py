"""把 Chromium 的 V8 精確覆蓋率翻成行覆蓋率。

**不是 spec，是 `test_web.py` 用的翻譯層。** 放在規格旁邊而不是 `script/` 底下，
因為它只有那一份規格會用，而它翻譯的是那份規格自己收集到的資料。

## 為什麼是 V8 而不是別的

`web/index.html` 的 JS 是行內腳本，pytest 量不到；而 PDF §3.3 說前端「不需框架也
不需打包流程」，所以 c8／nyc／istanbul 那條 npm 路線是被排除的——為了量覆蓋率而
引進一整套 Node 工具鏈，量到的東西會比它帶進來的東西便宜。瀏覽器自己就會算：
Chromium 的 CDP `Profiler` 領域直接給得出這份資料，唯一的相依是本來就要有的瀏覽器。

## V8 給的是位元組區間，不是行

每個函式一筆「整個函式的範圍 + 執行次數」，後面跟著巢狀的區塊範圍（`isBlockCoverage`）。
翻成行的規則與 c8 相同：

1. 所有區間依 `(起點升冪, 終點降冪)` 排序後**依序套用**，於是內層覆寫外層。
2. 一行只要有任何一個**非空白**字元落在 count > 0 的區間裡，那一行算執行過。

## 註解與空行不計

它們永遠落在最外層那個「整段 script」的區間裡，而 script 本身一定跑過（count >= 1）。
不扣掉的話，一個一行都沒被測到的檔案照樣會有一個好看的數字——那正是這個門檻要擋的
東西。所以分母是**程式碼行**：非空白、且不是純註解行。
"""

import re

_LINE_COMMENT = re.compile(r"^\s*//")
# 基本多文種平面的上界。超過它的字元在 UTF-16 佔兩個 code unit。
_BMP_MAX = 0xFFFF
_BLOCK_OPEN = "/*"
_BLOCK_CLOSE = "*/"


def line_coverage(source: str, functions: list[dict]) -> tuple[set[int], set[int]]:
    """回傳 (執行過的程式碼行, 全部程式碼行)，行號自 1 起算。

    兩個都回集合而不是直接回百分比，因為同一份 script 會在多個頁面各跑一次
    （一個 spec 一個頁面），而合併的正確做法是把「執行過的行」聯集起來——
    先各自算成百分比再平均，得到的是一個沒有意義的數字。
    """
    _refuse_astral(source)

    executed = _executed_offsets(source, functions)
    code = code_lines(source)

    covered = set()
    for number, start, end in _line_spans(source):
        if number not in code:
            continue
        if any(executed[offset] for offset in range(start, end) if not source[offset].isspace()):
            covered.add(number)
    return covered, code


def code_lines(source: str) -> set[int]:
    """有程式碼的行號。空行與純註解行不算——見模組說明。"""
    numbers = set()
    in_block = False
    for number, line in enumerate(source.splitlines(), start=1):
        text = line.strip()
        if in_block:
            in_block = _BLOCK_CLOSE not in text
            continue
        if text.startswith(_BLOCK_OPEN):
            in_block = _BLOCK_CLOSE not in text[len(_BLOCK_OPEN) :]
            continue
        if text and not _LINE_COMMENT.match(line):
            numbers.add(number)
    return numbers


def _executed_offsets(source: str, functions: list[dict]) -> list[bool]:
    """每個字元有沒有被執行到。外層先套、內層覆寫（與 c8 相同的順序）。"""
    executed = [False] * len(source)
    spans = [
        (span["startOffset"], span["endOffset"], span["count"] > 0)
        for function in functions
        for span in function["ranges"]
    ]
    for start, end, ran in sorted(spans, key=lambda span: (span[0], -span[1])):
        for offset in range(max(start, 0), min(end, len(source))):
            executed[offset] = ran
    return executed


def _line_spans(source: str) -> list[tuple[int, int, int]]:
    """(行號, 起點, 終點)，終點不含換行本身。"""
    spans = []
    offset = 0
    for number, line in enumerate(source.split("\n"), start=1):
        spans.append((number, offset, offset + len(line)))
        offset += len(line) + 1
    return spans


def _refuse_astral(source: str) -> None:
    """V8 的位移以 UTF-16 code unit 計，Python 的字串索引以 code point 計。

    兩者在基本平面內一致，遇到基本平面外的字元（emoji 之類）就會開始偏移，而偏移的
    症狀是「覆蓋率突然少了幾行」——一個看起來像測試退步、其實是量錯了的數字。與其
    讓它悄悄發生，不如在這裡大聲擋下。
    """
    astral = [character for character in source if ord(character) > _BMP_MAX]
    if astral:
        raise ValueError(
            f"index.html 的行內 script 含基本平面外的字元 {astral[:3]}，"
            f"V8 的位移與 Python 的字串索引會就此對不齊。"
            f"下一步：把那些字元移出 script（放進 HTML 或 CSS），"
            f"或改用 UTF-16 位移重寫 v8_coverage 的對位"
        )
