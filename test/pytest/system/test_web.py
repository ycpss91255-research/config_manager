"""T11 — 瀏覽器端到端行為，並順帶量出 `web/index.html` 的行覆蓋率。

T11 先前**沒有執行通路**：頁面裡的 `data-testid` 是給未來的測試準備的，沒有測試在用
它們（TEST-PLAN「已知的量測缺口」、#99）。這個檔案就是那條通路。

## 為什麼是 Playwright 的 V8 覆蓋率

行內 JS 不由 Python 執行，pytest-cov 看不到它。而 PDF §3.3 說前端「不需框架也不需
打包流程」，所以 c8／nyc 那條 npm 路線是被排除的——為了量一個數字而引進一整套 Node
工具鏈，代價比量到的東西貴。Chromium 自己會算：CDP 的 `Profiler` 領域直接給得出來，
翻成行的部分在 `v8_coverage.py`。

## 量的是真的在瀏覽器裡跑的那一份

不是靜態分析，也不是 jsdom 之類的模擬：一個真的 Chromium 打開真的 `index.html`，
對一個真的在跑的 FastAPI 服務發真的 fetch，前端與 backend 在兩個 port 上（設計文件
§3.1），所以跨來源這件事在測試裡沒有消失。這份數字說的是「使用者按下去時會執行的
那些行」，而不是「某個 JS 直譯器可以剖析的那些行」。

## 版面與 DOM 不在斷言裡

選取一律以語意屬性（`data-testid`、`aria-pressed`）為準，不以 CSS class 或 DOM 路徑
（ADR-00000019、`doc/UI-ELEMENTS.md`）。改樣式不該讓這些規格轉紅。

## 每則規格一個服務

身分記在 app 上（一次只有一個編輯階段，ADR-00000014），所以共用一個服務會讓「還沒
輸入身分」這件事取決於執行順序。一則一個，順序就不是變數。
"""

import functools
import http.server
import json
import pathlib
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright
from v8_coverage import line_coverage

from config_manager.api.routes import create_app

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_WEB_DIR = _ROOT / "src" / "config_manager" / "web"
_REPORT = _ROOT / ".coverage-web.json"

_STARTUP_TIMEOUT = 10.0
_POLL_INTERVAL = 0.05
_NAME = "陳小明"
_EMAIL = "ming@example.com"

_LIST_HEADER = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""

_ENTRY = """
[[files]]
uid      = "{uid}"
name     = "{name}"
hostname = "amr01"
source   = "files/{name}.yaml"
target   = "{target}"
format   = "yaml"
groups   = [{groups}]
"""

# 目標與來源的關係決定狀態（CONTEXT.md）：相同→一致，不同→偏離，不存在→未部署。
_DEPLOYED = {"a": "a: 1\n", "b": "b: 2\n", "c": None}


# ── 被測的東西：真的頁面、真的服務 ──────────────────────────────────────────


@pytest.fixture(scope="session")
def site():
    """供應 index.html 的靜態伺服器。

    與 backend 分屬兩個 port，因為部署起來就是兩個容器（設計文件 §3.1）。用同一個
    port 會讓跨來源這件事在測試裡消失，而 CORS 設錯正是使用者真的會撞到的失敗。
    """
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(_WEB_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def repo(tmp_path):
    """一份空的 config-repo。條目由 `listing` 夾具逐則寫進去。"""
    (tmp_path / "files").mkdir()
    (tmp_path / "deployed").mkdir()
    (tmp_path / "config-list.toml").write_text(_LIST_HEADER, encoding="utf-8")
    return tmp_path


@pytest.fixture
def listing(repo):
    """把清單檔換成指名的那幾筆條目，並準備各自的來源與目標檔案。"""

    def _write(*names: str) -> None:
        entries = ""
        for index, name in enumerate(names, start=1):
            (repo / "files" / f"{name}.yaml").write_text(f"{name}: 1\n", encoding="utf-8")
            target = repo / "deployed" / f"{name}.yaml"
            deployed = _DEPLOYED[name]
            if deployed is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(deployed, encoding="utf-8")
            entries += _ENTRY.format(
                uid=f"mfz3k9q{index}",
                name=name,
                target=target,
                groups='"navigation"' if name == "a" else "",
            )
        (repo / "config-list.toml").write_text(_LIST_HEADER + entries, encoding="utf-8")

    return _write


@pytest.fixture
def api(repo, site):
    """真的 FastAPI 服務，只放行頁面所在的那個來源。"""
    port = _free_port()
    config = uvicorn.Config(
        create_app(str(repo), (site,)), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    try:
        _wait_until_answering(base)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=_STARTUP_TIMEOUT)


# ── 覆蓋率的收集 ────────────────────────────────────────────────────────────


class _WebCoverage:
    """把每個頁面執行到的行聯集起來。

    一則規格一個頁面，同一份 script 於是被載入很多次。合併的正確做法是聯集
    「執行過的行」——先各自算成百分比再平均，得到的是一個沒有意義的數字。
    """

    def __init__(self) -> None:
        self.covered: set[int] = set()
        self.code: set[int] = set()

    def absorb(self, cdp, page_url: str) -> None:
        for entry in cdp.send("Profiler.takePreciseCoverage")["result"]:
            if entry.get("url") != page_url:
                continue
            fetched = cdp.send("Debugger.getScriptSource", {"scriptId": entry["scriptId"]})
            source = fetched["scriptSource"]
            covered, code = line_coverage(source, entry["functions"])
            # 報告上的行號指 index.html，不指行內 script 自己的第幾行：讀報告的人
            # 打開的是那個檔案，換算的那一步不該留給他做。
            offset = _html_line_offset(source)
            self.covered |= {number + offset for number in covered}
            self.code |= {number + offset for number in code}

    def write(self, path: pathlib.Path) -> None:
        # 一行都沒量到時仍然寫檔，而且寫 0：閘門讀不到檔會說「報告不見了」，讀到 0
        # 會說「這一層是 0%」。兩者都要擋，但它們是不同的失敗，訊息不該一樣。
        total = len(self.code)
        path.write_text(
            json.dumps(
                {
                    "area": "web",
                    "file": "src/config_manager/web/index.html",
                    "covered_lines": len(self.covered),
                    "code_lines": total,
                    "percent": round(100 * len(self.covered) / total, 2) if total else 0.0,
                    "uncovered_lines": sorted(self.code - self.covered),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.fixture(scope="session")
def web_coverage():
    report = _WebCoverage()
    yield report
    report.write(_REPORT)


@pytest.fixture(scope="session")
def browser():
    """Chromium。缺瀏覽器要大聲失敗而不是跳過——跳過的檢查不是檢查。"""
    with sync_playwright() as driver:
        try:
            launched = driver.chromium.launch(
                channel="chromium-headless-shell", args=["--no-sandbox"]
            )
        except BrowserError as error:
            raise RuntimeError(
                "起不了 Chromium，web/index.html 的覆蓋率量不到。"
                "下一步：改用 ./script/test.sh（docker/Dockerfile.test-tools 已經帶了它），"
                "或在這台主機上跑 `playwright install chromium-headless-shell`"
            ) from error
        yield launched
        launched.close()


@pytest.fixture
def open_page(browser, site, api, web_coverage):
    """開一個頁面，離開時把它執行到的行併進 web 的覆蓋率。"""
    opened = []

    def _open(unreachable: str | None = None):
        """`unreachable` 是一個端點樣式，配到的請求一律失敗——連載入時那一發也算。

        在 `goto` 之前就掛上，因為頁面一載入就會打 `/api/session`：載入後才掛的
        攔截放過了那一發，於是「backend 從頭到尾都不在」這件事根本沒被測到。
        """
        context = browser.new_context()
        page = context.new_page()
        page.add_init_script(f"window.CM_API_BASE = {json.dumps(api)};")
        if unreachable:
            page.route(unreachable, lambda route: route.abort())

        cdp = context.new_cdp_session(page)
        cdp.send("Debugger.enable")
        cdp.send("Profiler.enable")
        cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})

        url = f"{site}/index.html"
        opened.append((context, cdp, url))
        page.goto(url)
        return page

    yield _open

    for context, cdp, url in opened:
        web_coverage.absorb(cdp, url)
        context.close()


# ── T11 規格 ────────────────────────────────────────────────────────────────


def test_the_identity_form_is_what_you_see_before_you_have_said_who_you_are(open_page):
    # 這不是登入（ADR-00000020、CONTEXT.md「避免使用的說法」）。它是入口，因為
    # 變更紀錄要追溯得到人。
    page = open_page()

    assert page.is_visible("[data-testid='identity-form']")


def test_the_role_choice_is_visible_rather_than_hidden_in_a_menu(open_page):
    # 角色是兩個並排的選項按鈕，明顯可見（ADR-00000020、UI-ELEMENTS W1）。
    page = open_page()

    page.click("[data-testid='role-toggle'] button[data-role='developer']")

    assert page.get_attribute("[data-role='developer']", "aria-pressed") == "true"


def test_clicking_beside_the_role_buttons_changes_nothing(open_page):
    # 點在兩顆按鈕之間的空隙上不該改變宣告的角色。
    page = open_page()

    page.click("[data-testid='role-toggle']", position={"x": 1, "y": 1})

    assert page.get_attribute("[data-role='user']", "aria-pressed") == "true"


def test_the_list_appears_once_an_identity_has_been_entered(open_page, listing):
    listing("a")
    page = _enter_identity(open_page())

    assert page.is_visible("[data-testid='tree-item-mfz3k9q1']")


def test_every_entry_carries_the_state_the_backend_reported(open_page, listing):
    # 一致／偏離／未部署各一筆。判定本身由 T21 逐條測過；這裡證明頁面顯示了它。
    listing("a", "b", "c")
    page = _enter_identity(open_page())

    states = page.eval_on_selector_all(
        "[data-testid='status-dot']", "nodes => nodes.map(node => node.dataset.state)"
    )

    assert states == ["in_sync", "drift", "missing"]


def test_each_state_is_spelled_the_way_context_spells_it(open_page, listing):
    # 介面與 CLI 用同一組字：頁面說別的，等於憑介面決定術語（CONTEXT.md）。
    listing("a", "b", "c")
    page = _enter_identity(open_page())

    words = page.eval_on_selector_all(".state", "nodes => nodes.map(node => node.textContent)")

    assert words == ["一致", "偏離", "未部署"]


def test_an_entry_in_no_group_lands_under_the_ungrouped_heading(open_page, listing):
    listing("a", "b")
    page = _enter_identity(open_page())

    assert page.inner_text("[data-testid='tree-group-ungrouped'] h2") == "未分群"


def test_a_grouped_entry_lands_under_its_own_group(open_page, listing):
    listing("a", "b")
    page = _enter_identity(open_page())

    assert page.inner_text("[data-testid='tree-group-navigation'] h2") == "navigation"


def test_search_keeps_only_the_entries_that_match(open_page, listing):
    listing("a", "b", "c")
    page = _enter_identity(open_page())

    page.fill("[data-testid='search-input']", "b@amr01")

    assert page.locator("[data-testid='status-dot']").count() == 1


def test_search_says_so_when_nothing_matches(open_page, listing):
    # 一片空白會被讀成「壞了」。而且要與「還沒有納管任何 config」分開說——那兩件事
    # 該做的處置不同。
    listing("a")
    page = _enter_identity(open_page())

    page.fill("[data-testid='search-input']", "沒有這個東西")

    assert page.inner_text("[data-testid='no-matches']") == "沒有符合的項目。"


def test_an_empty_config_list_is_a_legal_state_not_an_error(open_page):
    page = _enter_identity(open_page())

    assert page.inner_text("[data-testid='empty-state']") == "還沒有納管任何 config。"


def test_rescanning_shows_a_target_that_was_changed_behind_the_interface(open_page, listing, repo):
    # 偏離代表有人繞過介面直接修改（CONTEXT.md）。按下「檢查差異」要看得到。
    listing("a")
    page = _enter_identity(open_page())
    page.wait_for_selector("[data-testid='status-dot'][data-state='in_sync']")

    (repo / "deployed" / "a.yaml").write_text("a: 999\n", encoding="utf-8")
    page.click("[data-testid='rescan']")

    page.wait_for_selector("[data-testid='status-dot'][data-state='drift']")


def test_the_page_says_it_cannot_read_the_list_instead_of_showing_an_empty_one(open_page, api):
    # 空清單與「讀不到清單」看起來一樣，該做的處置卻完全不同（不變式 2）。
    _remember_identity(api)

    page = open_page(unreachable="**/api/configs")

    page.wait_for_selector("[data-testid='load-error']")


def test_the_identity_page_is_still_there_when_the_backend_never_answers(open_page):
    # backend 連不上時仍顯示身分輸入頁：那是使用者唯一能操作的東西。載入時那一發
    # 打不出去也一樣——不是空白，也不是一個轉不完的圈。
    page = open_page(unreachable="**/api/session")

    assert page.is_visible("[data-testid='identity-form']")


def test_an_identity_that_cannot_be_sent_says_so_instead_of_looking_accepted(open_page):
    # 送出時的錯誤會說明真正的問題。
    page = open_page(unreachable="**/api/session")

    _fill_identity(page)

    assert "送不出去" in _identity_error(page)


def test_an_identity_that_would_break_the_author_string_is_shown_the_reason(open_page):
    # 端點的訊息已經寫成可行動的樣子（欄位＋原因＋下一步），原樣顯示，不在頁面上
    # 改寫成一句籠統的「輸入無效」（不變式 2）。
    page = open_page()

    _fill_identity(page, name=f"{_NAME} <admin@example.com>")

    assert "下一步：" in _identity_error(page)


def test_an_identity_already_entered_does_not_have_to_be_entered_again(open_page, listing, api):
    # 重新整理不必再填一次身分。
    listing("a")
    _remember_identity(api)

    page = open_page()

    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    assert page.is_hidden("[data-testid='identity-form']")


def test_the_header_says_who_is_looking_and_in_which_role(open_page, api):
    _remember_identity(api)

    page = open_page()

    page.wait_for_selector("[data-testid='current-role']:not(:empty)")
    assert page.inner_text("[data-testid='current-role']") == f"{_NAME}・一般使用者"


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _fill_identity(page, name: str = _NAME) -> None:
    page.fill("[data-testid='identity-name']", name)
    page.fill("[data-testid='identity-email']", _EMAIL)
    page.click("#identity button[type='submit']")


def _enter_identity(page):
    _fill_identity(page)
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    return page


def _identity_error(page) -> str:
    page.wait_for_selector("[data-testid='identity-error']:not([hidden])")
    return page.inner_text("[data-testid='identity-error']")


def _remember_identity(api: str) -> None:
    """走端點記下身分，不經由頁面——這幾則要觀察的是「已經輸入過之後」的樣子。"""
    request = urllib.request.Request(
        f"{api}/api/session",
        data=json.dumps({"name": _NAME, "email": _EMAIL}).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_STARTUP_TIMEOUT) as response:
        response.read()


def _html_line_offset(script_source: str) -> int:
    """行內 script 的第 1 行在 index.html 裡是第幾行，減一。

    以 script 的內容在檔案裡定位，不以 `<script>` 的行號——後者要再假設「內容從
    標籤的下一行開始」，而那個假設在 script 標籤與內容同一行時就錯了。
    """
    html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
    start = html.index(script_source)
    return html.count("\n", 0, start)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_until_answering(base: str) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/api/configs", timeout=1)
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(_POLL_INTERVAL)
        else:
            return
    raise RuntimeError(
        f"服務在 {_STARTUP_TIMEOUT} 秒內沒有回應：{base}。"
        f"下一步：看 uvicorn 的輸出，確認 create_app 起得來"
    )
