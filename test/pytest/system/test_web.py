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

import datetime
import functools
import gc
import http.server
import json
import os
import pathlib
import socket
import subprocess
import threading
import time
import tomllib
import http.cookiejar
import urllib.error
import urllib.request

import pytest
import uvicorn
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright
from v8_coverage import line_coverage

from config_manager.api.lock import LockBox
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
def browse_root(tmp_path):
    """白名單根（目標檔案系統的一段），與 config-repo 分開。browse 測試在這底下建目錄樹。"""
    root = tmp_path / "targets"
    root.mkdir()
    return root


@pytest.fixture
def repo(tmp_path, browse_root):
    """一份 config-repo（git repo），白名單種了 browse_root。條目由 `listing` 逐則寫進去。

    比照系統測試與 entrypoint：白名單以 `<repo>/allowed-roots.toml` 為準（#202），就地起
    服務沒有 entrypoint 種它，故這裡種好；git init＋提交，讓 browse 的「加入白名單」
    （會 commit）與任何寫入操作有 HEAD 可接。
    """
    (tmp_path / "files").mkdir()
    (tmp_path / "deployed").mkdir()
    (tmp_path / "config-list.toml").write_text(_LIST_HEADER, encoding="utf-8")
    (tmp_path / "allowed-roots.toml").write_text(
        "roots_version = 1\n\n[[roots]]\n"
        f'prefix = "{browse_root}"\n'
        'added_by = "seed"\nadded_at = "2026-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=seed", "-c", "user.email=s@e.x",
         "commit", "-q", "-m", "chore: 種子"],
        check=True,
    )
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
            try:
                fetched = cdp.send("Debugger.getScriptSource", {"scriptId": entry["scriptId"]})
            except BrowserError:
                # 頁面重新整理過：舊文件那份 script 已被回收、原始碼取不到，只剩它的計數。跳過它
                # ——同一份 index.html 的新副本仍在，行號對得回同一個檔案；漏掉的只有重載前那段
                # 執行（#33 起 pagehide 會送釋放請求，重載時舊文件多活一下，這種情況才出現）。
                continue
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
    contexts = []

    def _open(
        unreachable: str | None = None,
        identity: dict | None = None,
        same_browser_as=None,
        mode: str | None = None,
    ):
        """`unreachable` 是一個端點樣式，配到的請求一律失敗——連載入時那一發也算。

        在 `goto` 之前就掛上，因為頁面一載入就會打 `/api/session`：載入後才掛的
        攔截放過了那一發，於是「backend 從頭到尾都不在」這件事根本沒被測到。

        每次呼叫預設是**另一個瀏覽器**（新 context＝新的 cookie 罐，身分各自，#288）；
        `same_browser_as=<page>` 則是同一個瀏覽器的另一個分頁（共用 cookie，身分也共用）。
        `identity` 在開頁前先以這個瀏覽器的 cookie 罐走端點記下身分——觀察「已經輸入過之後」的樣子。
        `mode` 把 `GET /api/mode` 的回應換成指定的模式（就地起的服務是部署模式；開發模式那一邊的
        介面行為以攔截回應驗）。
        """
        if same_browser_as is not None:
            context = same_browser_as.context
        else:
            context = browser.new_context()
            contexts.append(context)
        if identity is not None:
            # context.request 與頁面共用 cookie 罐：這裡設的身分就是這個瀏覽器的。
            context.request.post(f"{api}/api/session", data=identity)
        page = context.new_page()
        page.add_init_script(f"window.CM_API_BASE = {json.dumps(api)};")
        if unreachable:
            page.route(unreachable, lambda route: route.abort())
        if mode:
            page.route(
                "**/api/mode",
                lambda route: route.fulfill(
                    status=200, content_type="application/json", body=json.dumps({"mode": mode})
                ),
            )

        cdp = context.new_cdp_session(page)
        cdp.send("Debugger.enable")
        cdp.send("Profiler.enable")
        cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})

        url = f"{site}/index.html"
        opened.append((cdp, url))
        page.goto(url)
        return page

    yield _open

    for cdp, url in opened:
        web_coverage.absorb(cdp, url)
    for context in contexts:
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


def test_a_pathological_target_shows_an_error_row_not_a_fake_state(open_page, repo):
    # #214 Q3(ii)：目標是 FIFO（判不出狀態）那一列標「錯誤」並把原因放進 title，不畫一個
    # 偽裝的狀態點——把「比不了」講成某個狀態就是靜默失敗（不變式 2）。
    (repo / "files").mkdir(exist_ok=True)
    (repo / "deployed").mkdir(exist_ok=True)
    (repo / "files" / "bad.yaml").write_text("bad: 1\n", encoding="utf-8")
    fifo = repo / "deployed" / "bad.pipe"
    fifo.unlink(missing_ok=True)
    os.mkfifo(fifo)
    entry = _ENTRY.format(uid="mfz3k9q1", name="bad", target=str(fifo), groups="")
    (repo / "config-list.toml").write_text(_LIST_HEADER + entry, encoding="utf-8")
    page = _enter_identity(open_page())

    page.wait_for_selector("[data-testid='tree-item-mfz3k9q1']")
    assert page.get_attribute("[data-testid='status-dot']", "data-state") == "error"
    assert page.inner_text("[data-testid='row-error']") == "錯誤"
    assert str(fifo) in page.get_attribute("[data-testid='row-error']", "title")


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

    page.select_option("[data-testid='search-scope']", "config 名稱")
    page.fill("[data-testid='search-input']", "b")

    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-testid='status-dot']\").length === 1"
    )
    assert page.is_visible("[data-testid='tree-item-mfz3k9q2']")


def test_search_says_so_when_nothing_matches(open_page, listing):
    # 一片空白會被讀成「壞了」。而且要與「還沒有納管任何 config」分開說——那兩件事
    # 該做的處置不同。
    listing("a")
    page = _enter_identity(open_page())

    page.fill("[data-testid='search-input']", "沒有這個東西")

    page.wait_for_selector("[data-testid='no-matches']")
    said = page.inner_text("[data-testid='no-matches']")
    # #50：說出在哪個範圍找什麼、接下來可以怎麼做——不只一句「沒有符合」。
    assert "在「全部」範圍內找不到含「沒有這個東西」的 config" in said
    assert "下一步：換個關鍵字，或清空搜尋框看全部。" in said


def test_an_empty_config_list_is_a_legal_state_not_an_error(open_page):
    page = _enter_identity(open_page())

    said = page.inner_text("[data-testid='empty-state']")
    assert said.startswith("還沒有納管任何 config。")
    assert "下一步：按上方的「納管」" in said  # #50：空狀態說得出接下來做什麼
    assert "納管第一份 config 之後" in page.inner_text("[data-testid='workspace-empty']")


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
    page = open_page(unreachable="**/api/configs", identity=_REMEMBERED)

    page.wait_for_selector("[data-testid='load-error']")


def test_typing_in_search_does_not_silently_clear_the_load_error(open_page, api):
    # render() 綁在搜尋輸入上、每次無條件從 rows 重畫。載入失敗時 rows 仍是空／過期的，
    # 一在錯誤態打字就會把 load-error 蓋成空清單或舊清單——把「讀不到」靜默改寫成「沒有」
    # 或「這是現況」（不變式 2）。錯誤要黏住。
    page = open_page(unreachable="**/api/configs", identity=_REMEMBERED)
    page.wait_for_selector("[data-testid='load-error']")

    page.fill("[data-testid='search-input']", "x")

    page.wait_for_selector("[data-testid='load-error']")  # 仍在，沒被 render 清掉
    assert page.query_selector("[data-testid='empty-state']") is None


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
    page = open_page(identity=_REMEMBERED)

    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    assert page.is_hidden("[data-testid='identity-form']")


def test_the_header_says_who_is_looking_and_in_which_role(open_page, api):
    page = open_page(identity=_REMEMBERED)

    page.wait_for_selector("[data-testid='current-role']:not(:empty)")
    assert page.inner_text("[data-testid='current-role']") == f"{_NAME}・一般使用者"


# ── 身分屬於各自的瀏覽器、記住此裝置（#288）─────────────────────────────────────
# 身分綁在後端發給該瀏覽器的票上；另一個瀏覽器開頁一律先看到身分輸入頁。「記住此裝置」只記姓名
# 與 Email、只存在這個瀏覽器、只在開發模式提供；角色每次重選。

_REMEMBER = "[data-testid='remember-device']"


def test_a_fresh_browser_is_asked_who_it_is_even_after_someone_else_entered(open_page, listing):
    listing("a")
    _enter_identity(open_page())

    other = open_page()

    other.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert other.is_hidden("[data-testid='config-tree']")


def test_refreshing_keeps_the_identity_whether_or_not_it_is_remembered(open_page, listing):
    # 部署模式（沒有記住此裝置可勾）也一樣：重新整理不掉身分，票在 cookie 裡。
    listing("a")
    page = _enter_identity(open_page())

    page.reload()

    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    assert page.is_hidden("[data-testid='identity-form']")
    assert page.inner_text("[data-testid='current-role']").startswith(_NAME)


def test_remember_device_is_offered_in_development_mode_only(open_page):
    deployed = open_page()
    deployed.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert deployed.is_hidden(_REMEMBER)

    developing = open_page(mode="development")
    developing.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert developing.is_visible(_REMEMBER)
    assert not developing.is_checked(f"{_REMEMBER} input")  # 預設不勾


def test_a_remembered_device_prefills_name_and_email_but_asks_the_role_again(open_page, listing):
    listing("a")
    page = open_page(mode="development")
    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    page.check(f"{_REMEMBER} input")
    page.click("[data-testid='role-toggle'] button[data-role='developer']")
    _enter_identity(page)

    page.context.clear_cookies()  # 關閉瀏覽器＝票沒了；localStorage 還在
    page.reload()

    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert page.input_value("[data-testid='identity-name']") == _NAME
    assert page.input_value("[data-testid='identity-email']") == _EMAIL
    assert page.is_checked(f"{_REMEMBER} input")
    role = "[data-testid='role-toggle'] button[data-role='user']"
    assert page.get_attribute(role, "aria-pressed") == "true"  # 角色沒被記住：回到預設


def test_without_remember_the_next_session_starts_blank(open_page, listing):
    listing("a")
    page = open_page(mode="development")
    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    _enter_identity(page)  # 沒勾

    page.context.clear_cookies()
    page.reload()

    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert page.input_value("[data-testid='identity-name']") == ""
    assert page.input_value("[data-testid='identity-email']") == ""


def test_unticking_remember_forgets_the_device(open_page, listing):
    listing("a")
    page = open_page(mode="development")
    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    page.check(f"{_REMEMBER} input")
    _enter_identity(page)
    page.context.clear_cookies()
    page.reload()
    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert page.input_value("[data-testid='identity-name']") == _NAME

    page.uncheck(f"{_REMEMBER} input")
    _enter_identity(page)
    page.context.clear_cookies()
    page.reload()

    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert page.input_value("[data-testid='identity-name']") == ""


# ── 閒置逾時的提示與退出（#48）──────────────────────────────────────────────────
# 判定在後端；頁面只回報操作、照後端說的剩餘時間顯示提示、後端說逾時就回身分輸入頁。這裡把後端的
# 回答換成指定的（後端真的數 10 分鐘的行為在 T9／T13 以注入時鐘驗）。

_IDLE_WARNING = "[data-testid='idle-warning']"
_TIMEOUT_NOTICE = "[data-testid='session-timeout']"


def _idle_answers(page, **overrides):
    """把閒置狀態兩支端點的回答換成指定的；回一個清單，記下頁面回報了幾次操作。"""
    view = {
        "timeout_seconds": 600, "warn_seconds": 60, "remaining_seconds": 600, "timed_out": False,
    }
    view.update(overrides)
    reported = []

    def answer(route):
        if route.request.method == "POST":
            reported.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(view))

    page.route("**/api/session/idle", answer)
    page.route("**/api/session/activity", answer)
    return reported


def _entered(open_page, listing):
    listing("a")
    page = _enter_identity(open_page())
    page.wait_for_selector("[data-testid='promote-all']", state="visible")
    return page


def test_no_warning_while_there_is_plenty_of_time_left(open_page, listing):
    page = _entered(open_page, listing)
    _idle_answers(page, remaining_seconds=300)

    page.evaluate("idleTick(false)")

    page.wait_for_timeout(200)
    assert page.is_hidden(_IDLE_WARNING)


def test_a_minute_before_the_timeout_a_warning_says_what_will_happen(open_page, listing):
    page = _entered(open_page, listing)
    _idle_answers(page, remaining_seconds=45)

    page.evaluate("idleTick(false)")

    page.wait_for_selector(_IDLE_WARNING, state="visible")
    text = page.inner_text("[data-testid='idle-warning-text']")
    assert "約 45 秒後將自動退出並釋放編輯階段" in text
    assert page.is_visible("[data-testid='idle-extend']")


def test_the_warning_counts_the_drafts_that_would_be_lost(open_page, listing, api):
    page = _entered(open_page, listing)
    page.evaluate("setDrafts(['mfz3k9q1'])")
    _idle_answers(page, remaining_seconds=30)

    page.evaluate("idleTick(false)")

    page.wait_for_selector(_IDLE_WARNING, state="visible")
    assert "1 份未進版的草稿會被清除" in page.inner_text("[data-testid='idle-warning-text']")


def test_continuing_reports_activity_and_dismisses_the_warning(open_page, listing):
    page = _entered(open_page, listing)
    _idle_answers(page, remaining_seconds=45)
    page.evaluate("idleTick(false)")
    page.wait_for_selector(_IDLE_WARNING, state="visible")
    page.unroute("**/api/session/idle")
    page.unroute("**/api/session/activity")
    reported = _idle_answers(page, remaining_seconds=600)
    # 剛回報過操作（節流期間內）：按鈕仍要立刻回報、立刻收掉提示，不等下一輪。
    page.evaluate("lastActivityAt = Date.now()")

    page.click("[data-testid='idle-extend']")

    page.wait_for_selector(_IDLE_WARNING, state="hidden")
    assert any(url.endswith("/api/session/activity") for url in reported)


def test_using_the_page_reports_activity_without_being_asked(open_page, listing):
    # 人回來動了一下：立刻回報，不必等提示出現、也不必按任何按鈕。
    page = _entered(open_page, listing)
    reported = _idle_answers(page)

    with page.expect_request("**/api/session/activity"):
        page.mouse.move(200, 200)
        page.keyboard.press("Shift")

    assert reported


def test_when_the_backend_says_timed_out_the_page_returns_to_the_identity_form(open_page, listing):
    page = _entered(open_page, listing)
    _idle_answers(page, remaining_seconds=None, timed_out=True)

    page.evaluate("idleTick(false)")

    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    assert "閒置超過 10 分鐘" in page.inner_text(_TIMEOUT_NOTICE)
    assert "編輯階段已釋放" in page.inner_text(_TIMEOUT_NOTICE)
    assert page.is_hidden("[data-testid='config-tree']")
    # 下一個坐下來的可能是別人：不留上一個人的姓名與 Email。
    assert page.input_value("[data-testid='identity-name']") == ""
    assert page.input_value("[data-testid='identity-email']") == ""


def test_after_timing_out_entering_an_identity_again_clears_the_notice(open_page, listing):
    page = _entered(open_page, listing)
    token = page.evaluate("sessionToken")
    _idle_answers(page, remaining_seconds=None, timed_out=True)
    page.evaluate("idleTick(false)")
    page.wait_for_selector(_TIMEOUT_NOTICE, state="visible")
    page.unroute("**/api/session/idle")
    page.unroute("**/api/session/activity")
    # 這裡的「逾時」是換掉回答演出來的；真的逾時時後端已釋放階段——補上那一步，重新進入才取得到。
    page.evaluate(
        "token => apiFetch(`${API_BASE}/api/session/lock`, {method: 'DELETE',"
        " headers: {'content-type': 'application/json'}, body: JSON.stringify({token})})",
        token,
    )

    _enter_identity(page)

    assert page.is_hidden(_TIMEOUT_NOTICE)
    page.wait_for_selector("[data-testid='promote-all']", state="visible")


def test_development_mode_starts_no_idle_watch(open_page, listing):
    # 後端說「不逾時」（開發模式）：頁面不監看、動了也不回報。
    listing("a")
    page = open_page()
    reported = _idle_answers(page, timeout_seconds=None, warn_seconds=None, remaining_seconds=None)
    _enter_identity(page)
    page.wait_for_selector("[data-testid='promote-all']", state="visible")

    page.mouse.move(200, 200)
    page.wait_for_timeout(300)

    assert reported == []
    assert page.evaluate("idleTimer") is None


def test_ten_idle_minutes_on_the_real_backend_end_the_page_session(open_page, listing):
    # 不換回答、真的把後端的時鐘撥過 10 分鐘：頁面下一次問就被退出；重新整理仍說明原因；重新輸入
    # 身分後取得得到編輯階段（後端已釋放）。
    page = _entered(open_page, listing)
    # 這個頁面的服務的那一個 LockBox：每則規格各起一個服務，先前的還留在物件圖裡，所以用「持有
    # 階段的是這個瀏覽器的票」來認。
    (ticket,) = [c["value"] for c in page.context.cookies() if c["name"] == "cm_browser"]
    (box,) = [
        found for found in gc.get_objects()
        if isinstance(found, LockBox) and found.holder_ticket == ticket
    ]
    real_clock = box.clock
    box.clock = lambda: real_clock() + datetime.timedelta(minutes=11)
    try:
        page.evaluate("idleTick(false)")
        page.wait_for_selector("[data-testid='identity-form']", state="visible")
        assert "閒置超過 10 分鐘" in page.inner_text(_TIMEOUT_NOTICE)

        page.reload()
        page.wait_for_selector(_TIMEOUT_NOTICE, state="visible")

        _enter_identity(page)
        page.wait_for_selector("[data-testid='promote-all']", state="visible")
        assert page.is_hidden(_TIMEOUT_NOTICE)
    finally:
        box.clock = real_clock


# ── 錯誤訊息的三要素（#50）─────────────────────────────────────────────────────
# 每則失敗訊息都要說得出發生什麼、在哪裡、該怎麼改。後端的訊息由 lint_messages 擋；這裡是頁面
# 自己產生的那一類——請求根本沒送到後端時，瀏覽器只給一句「Failed to fetch」。


def _failure_helper_source() -> tuple[str, str]:
    """index.html 切成（failureText 這個函式本身、其餘全部）。"""
    html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
    start = html.index("function failureText(")
    end = html.index("\n}\n", start) + len("\n}\n")
    return html[start:end], html[:start] + html[end:]


def test_every_failure_message_is_built_by_the_one_helper():
    # 各處自己拼一句「送不出去：Failed to fetch」正是這張 issue 要收掉的東西：錯誤物件的訊息只在
    # failureText 裡被讀，別處一律交給它。這一則擋的是之後新增的動作又繞過去。
    helper, rest = _failure_helper_source()

    code = "\n".join(line for line in rest.splitlines() if not line.lstrip().startswith("//"))
    assert "下一步：" in helper
    assert "error.message" not in code
    assert "送不出去" not in code


@pytest.mark.parametrize(
    ("endpoint", "trigger", "shown", "action"),
    [
        ("**/api/promote", "promoteAll()", "[data-testid='promote-error']", "進版"),
        ("**/api/drafts", "discardDrafts.call(null, null, null)", None, "捨棄變更"),
        ("**/api/session/lock", "acquireSession()", "[data-testid='readonly-banner']", "取得編輯階段"),
    ],
)
def test_an_unreachable_backend_names_the_action_and_the_next_step(
    open_page, listing, endpoint, trigger, shown, action
):
    listing("a")
    page = _enter_identity(open_page())
    page.wait_for_selector("[data-testid='promote-all']", state="visible")
    if shown is None:
        # 捨棄變更先過確認對話框：在背後按下確認。
        shown = "[data-testid='promote-error']"
        trigger = (
            "(discardDrafts(null, null),"
            " document.querySelector(\"[data-testid='confirm-ok']\").click())"
        )
    page.evaluate("rememberToken(null)")  # 取得編輯階段那一則：不走「接續」那條路
    page.route(endpoint, lambda route: route.abort())

    page.evaluate(trigger)

    page.wait_for_selector(f"{shown}:not([hidden])")
    text = page.inner_text(shown)
    assert f"「{action}」送不出去" in text
    assert "這個動作沒有完成" in text
    assert "下一步：確認後端服務在執行" in text


def test_a_refusal_from_the_backend_is_shown_as_the_backend_wrote_it(open_page, listing):
    # 後端回絕時訊息它已經寫好（含下一步）：原樣顯示，不套上「送不出去」那一套。
    listing("a")
    page = open_page(identity=_REMEMBERED)
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.route(
        "**/api/configs",
        lambda route: route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": "清單檔解析失敗。下一步：修正 config-list.toml 第 3 行"}),
        ),
    )

    page.click("[data-testid='rescan']")

    page.wait_for_selector("[data-testid='load-error']")
    text = page.inner_text("[data-testid='load-error']")
    assert "清單檔解析失敗。下一步：修正 config-list.toml 第 3 行" in text
    assert "送不出去" not in text


def test_an_error_without_a_reason_still_says_where_to_look(open_page, listing):
    # 回應不是後端寫的形狀（中間的代理、或後端崩了）：不只丟一個「502 Bad Gateway」。
    listing("a")
    page = open_page(identity=_REMEMBERED)
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.route(
        "**/api/configs",
        lambda route: route.fulfill(status=502, content_type="text/html", body="<h1>Bad Gateway</h1>"),
    )

    page.click("[data-testid='rescan']")

    page.wait_for_selector("[data-testid='load-error']")
    text = page.inner_text("[data-testid='load-error']")
    assert "後端回了 502" in text
    assert "下一步：查看後端服務的紀錄" in text


# ── 操作後的回饋與位置保持（#50）────────────────────────────────────────────────
# 做完一個動作：說一聲成了，而且人剛才的位置（折疊了什麼、捲到哪、焦點在哪）要留著。

_NESTED = "robot:\n  name: amr\n  speed: 1.5\ncamera:\n  fps: 30\n" + "".join(
    f"filler_{index}: {index}\n" for index in range(40)
)


def _collapsed(page, uid) -> list:
    return page.evaluate(
        "uid => [...document.querySelectorAll(`[data-testid='panel-${uid}']"
        " li[data-container='true'][data-collapsed='true']`)].map(node => node.dataset.name)",
        uid,
    )


def _open_nested(open_page, repo):
    _listing_many(repo, {"a": _NESTED})
    page = _enter_identity(open_page())
    page.set_viewport_size({"width": 1200, "height": 500})
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    return page


def test_saving_a_draft_says_it_was_saved_and_is_not_yet_promoted(open_page, repo):
    page = _open_nested(open_page, repo)
    page.fill(f"{_row('camera.fps')} input", "60")

    _save(page, _PARAM_UID)

    note = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-note']"
    page.wait_for_selector(note, state="visible")
    assert page.inner_text(note).startswith("已存為草稿——還沒進版")


def test_the_saved_note_goes_away_once_the_field_is_edited_again(open_page, repo):
    # 那句話說的是上一次儲存；又動了欄位還留著，就變成在描述一個已經不成立的狀態。
    page = _open_nested(open_page, repo)
    page.fill(f"{_row('camera.fps')} input", "60")
    _save(page, _PARAM_UID)
    note = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-note']"
    page.wait_for_selector(note, state="visible")

    page.fill(f"{_row('camera.fps')} input", "90")

    page.wait_for_selector(note, state="hidden")


def _row_top(page, path) -> float:
    """那一列離視窗頂端多遠——「人眼前的東西有沒有動」看的是這個，不是 scrollY。"""
    return page.evaluate(
        "name => [...document.querySelectorAll('li[data-name]')]"
        ".find(node => node.dataset.name === name).getBoundingClientRect().top",
        path,
    )


def _scrolled_to_an_edit_far_down(open_page, repo):
    """折疊 robot、改最底下附近的一格、把那一列放在視窗中段——接下來的動作不該把它移走。"""
    page = _open_nested(open_page, repo)
    page.click(f"{_row('robot')} .param-name")  # 折疊 robot
    page.fill(f"{_row('filler_35')} input", "350")
    page.evaluate(
        "name => { const row = [...document.querySelectorAll('li[data-name]')]"
        ".find(node => node.dataset.name === name);"
        " window.scrollBy(0, row.getBoundingClientRect().top - 200); }",
        "filler_35",
    )
    assert page.evaluate("window.scrollY") > 0
    return page


def test_saving_keeps_what_was_collapsed_and_where_the_page_was_scrolled(open_page, repo):
    page = _scrolled_to_an_edit_far_down(open_page, repo)
    before = _row_top(page, "filler_35")

    page.keyboard.press("Control+s")  # 焦點在剛改的那一格；用鍵盤存，不讓點擊自己去捲頁面
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="attached")

    assert _collapsed(page, _PARAM_UID) == ["robot"]
    assert abs(_row_top(page, "filler_35") - before) <= 1


def test_promoting_keeps_what_was_collapsed_and_where_the_page_was_scrolled(open_page, repo):
    # 進版後打開的區塊整個重畫（來源變了），上方還多出一條結果橫幅：眼前那一列不能因此跳走、
    # 折疊的不能全部展開。
    page = _scrolled_to_an_edit_far_down(open_page, repo)
    page.keyboard.press("Control+s")
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="attached")
    before = _row_top(page, "filler_35")

    page.evaluate("() => { promoteAll(); }")  # 按鈕在頁面頂端；直接觸發，不讓點擊把頁面捲回去
    page.wait_for_selector("[data-testid='promote-done']:not([hidden])", state="attached")
    page.wait_for_function(
        "uid => !document.querySelector(`[data-testid='panel-draft-${uid}']:not([hidden])`)"
        " && document.querySelector(`[data-testid='panel-${uid}'] li[data-name='filler_35']"
        " .param-source`).textContent.includes('350')",
        arg=_PARAM_UID,
    )

    assert _collapsed(page, _PARAM_UID) == ["robot"]
    assert abs(_row_top(page, "filler_35") - before) <= 1


def test_discarding_one_draft_keeps_the_focus_inside_that_panel(open_page, repo):
    page = _open_nested(open_page, repo)
    page.fill(f"{_row('camera.fps')} input", "60")
    _save(page, _PARAM_UID)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    page.focus(f"{_row('camera.fps')} input")

    # 不回傳那個 promise：它要等確認對話框被回答，而回答在下一行——回傳的話 evaluate 會一直等。
    page.evaluate("uid => { discardDrafts(uid, 'a', null); }", _PARAM_UID)
    page.click("[data-testid='confirm-ok']")
    page.wait_for_function(
        "uid => document.querySelector(`[data-testid='panel-${uid}'] li[data-name='camera.fps'] input`)"
        ".value === '30'",
        arg=_PARAM_UID,
    )

    assert "已捨棄「a」的草稿" in page.inner_text("[data-testid='promote-done']")
    focused = page.evaluate("document.activeElement.closest('li[data-name]')?.dataset.name")
    assert focused == "camera.fps"


def test_onboarding_says_it_worked_and_selects_the_new_config(open_page, browse_root):
    (browse_root / "fresh.yaml").write_text("count: 1\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "fresh.yaml")
    page.get_by_role("button", name="確認寫入").click()

    page.wait_for_selector("[data-testid='promote-done']:not([hidden])")
    said = page.inner_text("[data-testid='promote-done']")
    assert f"已納管「{browse_root.name}-fresh@" in said and "fresh.yaml" in said
    page.wait_for_selector("#tree li[aria-selected='true']")
    assert "fresh" in page.inner_text("#tree li[aria-selected='true']")


# ── 空狀態（#50）───────────────────────────────────────────────────────────────
# 每一種「這裡沒有東西」都說出是哪一種、接下來可以做什麼。


def test_a_narrowed_search_with_no_match_suggests_widening_the_scope(open_page, listing):
    listing("a")
    page = _enter_identity(open_page())
    page.select_option("[data-testid='search-scope']", "參數值")

    page.fill("[data-testid='search-input']", "zzz")

    page.wait_for_selector("[data-testid='no-matches']")
    said = page.inner_text("[data-testid='no-matches']")
    assert "在「參數值」範圍內找不到含「zzz」的 config" in said
    assert "把搜尋範圍改成「全部」" in said


def test_the_empty_list_does_not_point_a_read_only_viewer_at_a_button_they_lack(open_page):
    # 唯讀的人沒有「納管」可按：空狀態不叫他去按一顆不存在的按鈕。
    holder = _enter_identity(open_page())
    holder.wait_for_selector("[data-testid='promote-all']", state="visible")
    viewer = open_page(same_browser_as=holder)
    viewer.wait_for_selector("[data-testid='readonly-banner']", state="visible")

    viewer.evaluate("render()")

    assert viewer.inner_text("[data-testid='empty-state']") == "還沒有納管任何 config。"


def test_a_config_never_edited_explains_its_empty_history(open_page, browse_root, api):
    # 剛納管、還沒改過：預設（只看內容變更）是空的——說明原因與去哪裡看，不留一片空白。
    entry = _history_via_api(api, browse_root, ["count: 1\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"], expect_entries=False)

    page.wait_for_selector("[data-testid='history-empty']")

    said = page.inner_text("[data-testid='history-empty']")
    assert "納管後還沒有改過內容" in said and "下一步：切到「全部」" in said


def test_a_history_with_a_single_record_says_there_is_nothing_to_compare_yet(
    open_page, browse_root, api
):
    entry = _history_via_api(api, browse_root, ["count: 1\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"], expect_entries=False)

    page.click("[data-testid='history-filter'] button[data-filter='all']")

    page.wait_for_selector("[data-testid='history-single']")
    assert "還沒有更早的版本可以比較或退回" in page.inner_text("[data-testid='history-single']")


def test_a_history_with_several_records_has_no_single_record_note(open_page, browse_root, api):
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])

    page.click("[data-testid='history-filter'] button[data-filter='all']")

    page.wait_for_selector("[data-testid^='history-entry-']")
    page.wait_for_timeout(200)
    assert page.query_selector("[data-testid='history-single']") is None


def test_an_empty_directory_says_so_while_browsing(open_page, browse_root):
    (browse_root / "nothing_here").mkdir()
    page = _enter_identity(open_page())
    page.click("[data-testid='open-browse']")
    page.wait_for_selector(f"[data-testid='browse-root-{browse_root}']")
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-nothing_here']")

    page.click("[data-testid='browse-entry-nothing_here']")

    page.wait_for_selector("[data-testid='browse-empty']")
    assert "這個目錄是空的" in page.inner_text("[data-testid='browse-empty']")


# ── 鍵盤操作（#50、§7.10）──────────────────────────────────────────────────────
# 樹：↑／↓ 移動（Home／End 到頭尾）、Enter 展開、Esc 收合；Ctrl+S 儲存目前區塊。

_TREE_ITEMS = "[...document.querySelectorAll('#tree li[data-uid]')]"


def _tree_order(page) -> list:
    return page.evaluate(f"{_TREE_ITEMS}.map(node => node.dataset.uid)")


def _selected(page):
    return page.evaluate(
        "(document.querySelector(\"#tree li[aria-selected='true']\") || {dataset: {}}).dataset.uid"
    )


def _focused_uid(page):
    return page.evaluate("document.activeElement.dataset.uid")


def _three_configs(open_page, repo):
    _listing_many(repo, {"a": "count: 3\n", "b": "speed: 1.5\n", "c": "gain: 2\n"})
    page = _enter_identity(open_page())
    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}']")
    return page


def test_arrow_keys_move_through_the_tree_in_the_order_shown(open_page, repo):
    page = _three_configs(open_page, repo)
    first, second, third = _tree_order(page)
    page.click(f"[data-testid='tree-item-{first}']")

    page.keyboard.press("ArrowDown")
    after_down = (_selected(page), _focused_uid(page))
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")  # 已經在最後一個：停住，不繞回開頭
    at_end = _selected(page)
    page.keyboard.press("ArrowUp")

    assert after_down == (second, second)
    assert at_end == third
    assert _selected(page) == second


def test_home_and_end_jump_to_the_first_and_last_config(open_page, repo):
    page = _three_configs(open_page, repo)
    first, _second, third = _tree_order(page)
    page.click(f"[data-testid='tree-item-{first}']")

    page.keyboard.press("End")
    at_end = _selected(page)
    page.keyboard.press("Home")

    assert (at_end, _selected(page)) == (third, first)


def test_the_tree_is_one_tab_stop(open_page, repo):
    # Tab 只停在一個節點上、節點之間用方向鍵：不必 Tab 過整棵樹才到得了工作區。
    page = _three_configs(open_page, repo)

    stops = page.evaluate(f"{_TREE_ITEMS}.filter(node => node.tabIndex === 0).length")

    assert stops == 1


def test_enter_expands_the_selected_config_and_escape_collapses_it(open_page, repo):
    page = _three_configs(open_page, repo)
    page.click(f"[data-testid='tree-item-{_PARAM_UID}']")

    page.keyboard.press("Enter")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.keyboard.press("Escape")

    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="detached")
    assert _focused_uid(page) == _PARAM_UID  # 焦點還在樹上，可以繼續用方向鍵


def test_the_selection_and_focus_survive_a_rescan(open_page, repo):
    # 樹重畫時整棵換掉；選取與焦點要留著，否則方向鍵走到一半就掉回開頭。
    page = _three_configs(open_page, repo)
    _first, second, _third = _tree_order(page)
    page.click(f"[data-testid='tree-item-{second}']")

    page.evaluate("load()")
    page.wait_for_timeout(300)
    page.keyboard.press("ArrowDown")

    assert _selected(page) == _tree_order(page)[2]


def test_escape_inside_a_panel_collapses_it_and_returns_focus_to_the_tree(open_page, repo):
    page = _three_configs(open_page, repo)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.focus(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-history']")

    page.keyboard.press("Escape")

    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="detached")
    assert _focused_uid(page) == _PARAM_UID


def test_escape_while_typing_in_a_field_does_not_collapse_the_panel(open_page, repo):
    page = _three_configs(open_page, repo)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.fill(f"{_row('count')} input", "4")

    page.keyboard.press("Escape")

    page.wait_for_timeout(200)
    assert page.is_visible(f"[data-testid='panel-{_PARAM_UID}']")
    assert page.input_value(f"{_row('count')} input") == "4"


def test_collapsing_a_panel_with_unsaved_edits_asks_first(open_page, repo):
    # 關掉就沒了：先問。取消→區塊與改動都還在；確認→才收合。× 與 Esc 走同一條路。
    page = _three_configs(open_page, repo)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.fill(f"{_row('count')} input", "4")
    close = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-close']"

    page.click(close)
    page.wait_for_selector(f"{_CONFIRM}[open]")
    asked = page.inner_text("[data-testid='confirm-body']")
    label = page.inner_text("[data-testid='confirm-ok']")
    page.click("[data-testid='confirm-cancel']")
    kept = page.input_value(f"{_row('count')} input")
    page.click(close)
    page.click("[data-testid='confirm-ok']")

    assert "有還沒儲存的改動" in asked and label == "關閉並丟掉改動"
    assert kept == "4"
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="detached")


def test_collapsing_a_panel_without_edits_does_not_ask(open_page, repo):
    page = _three_configs(open_page, repo)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")

    page.click(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-close']")

    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="detached")
    assert page.is_hidden(_CONFIRM)


def test_ctrl_s_saves_the_panel_being_edited(open_page, repo):
    page = _three_configs(open_page, repo)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.fill(f"{_row('count')} input", "4")

    page.keyboard.press("Control+s")

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    assert page.inner_text("[data-testid='promote-all']") == "進版 (1)"


def test_ctrl_s_saves_only_the_panel_that_has_the_focus(open_page, repo):
    page = _three_configs(open_page, repo)
    for uid in (_PARAM_UID, _SECOND_UID):
        page.dblclick(f"[data-testid='tree-item-{uid}']")
        page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")
    page.fill(f"[data-testid='panel-{_PARAM_UID}'] li[data-name='count'] input", "4")
    page.fill(f"[data-testid='panel-{_SECOND_UID}'] li[data-name='speed'] input", "2.5")

    page.keyboard.press("Control+s")  # 焦點在第二份（剛填的那一格）

    page.wait_for_selector(_draft_dot(_SECOND_UID), state="visible")
    assert page.inner_text("[data-testid='promote-all']") == "進版 (1)"
    assert page.query_selector(_draft_dot(_PARAM_UID)) is None


def test_ctrl_s_from_the_tree_saves_the_selected_config(open_page, repo):
    page = _three_configs(open_page, repo)
    for uid in (_PARAM_UID, _SECOND_UID):
        page.dblclick(f"[data-testid='tree-item-{uid}']")
        page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")
    page.fill(f"[data-testid='panel-{_PARAM_UID}'] li[data-name='count'] input", "4")
    page.click(f"[data-testid='tree-item-{_PARAM_UID}']")

    page.keyboard.press("Control+s")

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")


def test_ctrl_s_with_nothing_to_save_says_so(open_page, repo):
    # 按了卻什麼都沒發生最讓人困惑：說出為什麼沒存。
    page = _three_configs(open_page, repo)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.focus(f"{_row('count')} input")

    page.keyboard.press("Control+s")

    note = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-note']"
    page.wait_for_selector(note, state="visible")
    assert page.inner_text(note) == "沒有還沒儲存的改動。"


# ── 確認對話框的一致性（#50）───────────────────────────────────────────────────
# 所有要先確認的動作共用一個對話框：確認鈕寫的是動作本身、開啟時焦點在取消、Esc 是取消。

_CONFIRM = "[data-testid='confirm-dialog']"


def _with_a_draft(open_page, repo):
    _listing_many(repo, {"a": "count: 3\n"})
    page = _enter_identity(open_page())
    _save_draft_for(page, _PARAM_UID, "count", "4")
    return page


def test_the_confirm_button_names_the_action_instead_of_a_generic_confirm(open_page, repo):
    page = _with_a_draft(open_page, repo)

    page.click("[data-testid='discard-all']")
    page.wait_for_selector(f"{_CONFIRM}[open]")
    discard = page.inner_text("[data-testid='confirm-ok']")
    page.click("[data-testid='confirm-cancel']")
    page.click(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-unmanage']")
    page.wait_for_selector(f"{_CONFIRM}[open]")
    unmanage = page.inner_text("[data-testid='confirm-ok']")

    assert (discard, unmanage) == ("捨棄變更", "解除納管")


def test_the_confirm_dialog_opens_with_focus_on_cancel(open_page, repo):
    # 手滑按到 Enter 不會把東西丟掉：預設落在安全的那一邊。
    page = _with_a_draft(open_page, repo)
    page.click("[data-testid='discard-all']")
    page.wait_for_selector(f"{_CONFIRM}[open]")

    focused = page.evaluate("document.activeElement.dataset.testid")
    page.keyboard.press("Enter")

    assert focused == "confirm-cancel"
    page.wait_for_selector(f"{_CONFIRM}:not([open])", state="attached")
    assert page.inner_text("[data-testid='promote-all']") == "進版 (1)"  # 草稿還在


def test_escape_cancels_the_confirm_dialog(open_page, repo):
    page = _with_a_draft(open_page, repo)
    page.click("[data-testid='discard-all']")
    page.wait_for_selector(f"{_CONFIRM}[open]")

    page.keyboard.press("Escape")

    page.wait_for_selector(f"{_CONFIRM}:not([open])", state="attached")
    assert page.inner_text("[data-testid='promote-all']") == "進版 (1)"


def test_a_confirmation_without_listed_items_shows_no_empty_list(open_page, repo):
    page = _with_a_draft(open_page, repo)

    page.click("[data-testid='discard-all']")
    page.wait_for_selector(f"{_CONFIRM}[open]")

    assert page.is_hidden("[data-testid='confirm-items']")


def test_removing_a_root_lists_the_affected_configs_in_the_same_dialog(open_page, browse_root):
    # 移除白名單的確認也走共用的對話框，受影響的納管項目列在裡面（資訊性：它們不會被解除納管）。
    page = _open_whitelist_as_developer(open_page())
    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']")
    affected = [{"ref": "nav@amr01-abc", "target": f"{browse_root}/nav.yaml"}]

    def answer(route):
        if route.request.method != "DELETE":
            route.continue_()
            return
        detail = {"kind": "confirm_required", "message": "需要確認", "affected": affected}
        route.fulfill(
            status=409, content_type="application/json", body=json.dumps({"detail": detail})
        )

    page.route("**/api/allowed-roots", answer)

    page.locator(
        f"[data-testid='whitelist-root-{browse_root}'] [data-testid='whitelist-remove']"
    ).click()

    page.wait_for_selector(f"{_CONFIRM}[open]")
    assert page.inner_text("[data-testid='confirm-items']") == f"nav@amr01-abc：{browse_root}/nav.yaml"
    assert "以下 1 份已納管的設定" in page.inner_text("[data-testid='confirm-body']")
    assert "不受影響" in page.inner_text("[data-testid='confirm-body']")


# ── 進行中的指示（#50）─────────────────────────────────────────────────────────
# 會寫入或要等一下的動作：按下後按鈕標成進行中（aria-busy、文字「…中」），做完還原；進行中再按
# 不會送出第二次。這裡把請求扣住不放，看那段期間的畫面。


def _hold(page, pattern, method="POST"):
    """把符合的請求扣住不回；回 (被扣住的清單, 放行函式)。其他方法照常放行。"""
    held = []

    def handler(route):
        if route.request.method == method:
            held.append(route)
        else:
            route.continue_()

    page.route(pattern, handler)

    def release():
        waiting, held[:] = list(held), []
        for route in waiting:
            route.continue_()
        page.unroute(pattern)

    return held, release


def _busy(page, selector) -> bool:
    return page.get_attribute(selector, "aria-busy") == "true"


def test_promote_shows_it_is_running_and_cannot_be_sent_twice(open_page, repo):
    _listing_many(repo, {"a": "count: 3\n"})
    page = _enter_identity(open_page())
    _save_draft_for(page, _PARAM_UID, "count", "4")
    held, release = _hold(page, "**/api/promote")
    button = "[data-testid='promote-all']"

    page.click(button)
    page.wait_for_selector(f"{button}[aria-busy='true']")
    assert page.inner_text(button) == "進版中…"
    page.evaluate("document.querySelector(\"[data-testid='promote-all']\").click()")  # 再按一下
    page.wait_for_timeout(200)
    assert len(held) == 1  # 沒有送出第二次

    release()

    page.wait_for_selector("[data-testid='promote-done']:not([hidden])")
    assert page.inner_text(button) == "進版 (0)"
    assert not _busy(page, button)


def test_saving_a_draft_shows_it_is_running(open_page, repo):
    _listing_many(repo, {"a": "count: 3\n"})
    page = _enter_identity(open_page())
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.fill(f"{_row('count')} input", "4")
    _held, release = _hold(page, "**/api/drafts")
    save = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save']"

    page.click(save)

    page.wait_for_selector(f"{save}[aria-busy='true']")
    assert page.inner_text(save) == "儲存中…"
    release()
    page.wait_for_selector(f"[data-testid='panel-draft-{_PARAM_UID}']")


def test_a_confirmed_action_is_busy_only_after_the_confirmation(open_page, repo):
    # 確認對話框還開著時動作還沒開始：按鈕不該已經寫著「捨棄中…」。
    _listing_many(repo, {"a": "count: 3\n"})
    page = _enter_identity(open_page())
    _save_draft_for(page, _PARAM_UID, "count", "4")
    held, release = _hold(page, "**/api/drafts", method="DELETE")
    button = "[data-testid='discard-all']"

    page.click(button)
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    assert not _busy(page, button) and page.inner_text(button) == "捨棄變更"
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(f"{button}[aria-busy='true']")
    assert page.inner_text(button) == "捨棄中…"
    assert len(held) == 1
    release()
    page.wait_for_function("document.querySelector(\"[data-testid='discard-all']\").disabled")
    assert page.inner_text(button) == "捨棄變更"


def test_entering_the_identity_shows_it_is_running(open_page):
    page = open_page()
    page.wait_for_selector("[data-testid='identity-form']", state="visible")
    _held, release = _hold(page, "**/api/session")

    _fill_identity(page)

    page.wait_for_selector("#identity button.enter[aria-busy='true']")
    assert page.inner_text("#identity button.enter") == "進入中…"
    release()
    page.wait_for_selector("[data-testid='config-tree']", state="visible")


def test_checking_for_differences_marks_the_button_busy(open_page, listing):
    listing("a")
    page = _enter_identity(open_page())
    page.wait_for_selector("[data-testid='tree-item-mfz3k9q1']")
    _held, release = _hold(page, "**/api/configs", method="GET")

    page.click("[data-testid='rescan']")

    page.wait_for_selector("[data-testid='rescan'][aria-busy='true']")
    assert page.inner_text("[data-testid='rescan']") == "檢查中…"
    release()
    page.wait_for_selector("[data-testid='scan-status'][data-state='done']")
    assert page.inner_text("[data-testid='rescan']") == "檢查差異"


# ── 開發／部署模式徽章（#47）──────────────────────────────────────────────────
# 模式由後端判定、前端只顯示（ADR-00000020：讓使用者清楚知道自己處於哪個模式，而非阻擋）。


def test_the_header_says_which_mode_the_backend_is_running_in(open_page, api):
    page = open_page(identity=_REMEMBERED)

    page.wait_for_selector("[data-testid='current-mode']:not(:empty)")
    assert page.inner_text("[data-testid='current-mode']") == "部署模式"
    assert page.get_attribute("body", "data-mode") == "deployment"


def test_a_development_mode_answer_shows_as_development(open_page, api):
    # 就地起的服務是部署模式；開發模式那一邊以攔截回應驗——文案對照表兩格都要真的走到。
    page = open_page(identity=_REMEMBERED)
    page.wait_for_selector("[data-testid='current-mode']:not(:empty)")
    page.route(
        "**/api/mode",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"mode": "development"})
        ),
    )

    page.evaluate("loadMode()")

    page.wait_for_selector("[data-testid='current-mode']:has-text('開發模式')")
    assert page.get_attribute("body", "data-mode") == "development"


def test_an_unknown_mode_is_shown_as_unknown_not_assumed(open_page, api):
    # 問不到模式時不假設任何一種：假設成開發模式會讓依模式開關的行為在部署機上開錯邊（不變式 4）。
    page = open_page(unreachable="**/api/mode", identity=_REMEMBERED)

    page.wait_for_selector("[data-testid='current-mode']:has-text('模式未知')")
    assert page.get_attribute("body", "data-mode") is None


# ── W7 檔案瀏覽（#13）────────────────────────────────────────────────────────


def test_opening_the_browser_lists_the_whitelist_roots(open_page, browse_root):
    page = _open_browser(open_page())

    # 根清單由 GET /api/allowed-roots 非同步填入——等它出現再讀，不然讀到空的。
    page.wait_for_selector("[data-testid^='browse-root-']")
    roots = page.eval_on_selector_all(
        "[data-testid='browse-roots'] [data-testid^='browse-root-']",
        "ns => ns.map(n => n.dataset.path)",
    )
    assert str(browse_root) in roots


def test_picking_a_root_lists_its_directory_contents(open_page, browse_root):
    (browse_root / "params").mkdir()
    (browse_root / "nav.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_browser(open_page())

    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-list']")

    # list（非 dict）比對，順序即後端排序：nav.yaml 在 params 之前。
    assert _browse_entries(page) == [["nav.yaml", "file"], ["params", "dir"]]


def test_clicking_a_folder_descends_and_updates_the_breadcrumb(open_page, browse_root):
    (browse_root / "params").mkdir()
    (browse_root / "params" / "deep.yaml").write_text("d: 1\n", encoding="utf-8")
    page = _open_browser(open_page())
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-params']")

    page.click("[data-testid='browse-entry-params']")
    page.wait_for_selector("[data-testid='browse-entry-deep.yaml']")

    # 下鑽到 params 的內容，麵包屑多了 params 這一段。
    assert _browse_entries(page) == [["deep.yaml", "file"]]
    segments = page.eval_on_selector_all(
        "[data-testid='breadcrumb'] [data-path]", "ns => ns.map(n => n.dataset.path)"
    )
    # 夾在白名單根為界：第一段就是根本身，且沒有任何段是根的嚴格上層（不讓人點到根之上）。
    assert segments[0] == str(browse_root)
    assert str(browse_root / "params") in segments
    assert all(seg == str(browse_root) or seg.startswith(f"{browse_root}/") for seg in segments)


def test_clicking_a_breadcrumb_segment_goes_back_up(open_page, browse_root):
    (browse_root / "params").mkdir()
    (browse_root / "params" / "deep.yaml").write_text("d: 1\n", encoding="utf-8")
    (browse_root / "nav.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_browser(open_page())
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-params']")
    page.click("[data-testid='browse-entry-params']")
    page.wait_for_selector("[data-testid='browse-entry-deep.yaml']")

    # 點麵包屑上的根那一段 → 回到根的內容。
    page.click(f"[data-testid='breadcrumb'] [data-path='{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-nav.yaml']")

    assert _browse_entries(page) == [["nav.yaml", "file"], ["params", "dir"]]


def test_a_manual_absolute_path_browses_that_directory(open_page, browse_root):
    (browse_root / "params").mkdir()
    (browse_root / "params" / "deep.yaml").write_text("d: 1\n", encoding="utf-8")
    page = _open_browser(open_page())

    page.fill("[data-testid='browse-path-input']", str(browse_root / "params"))
    page.click("text=前往")
    page.wait_for_selector("[data-testid='browse-entry-deep.yaml']")

    assert _browse_entries(page) == [["deep.yaml", "file"]]


def test_clicking_a_file_selects_it(open_page, browse_root):
    (browse_root / "nav.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_browser(open_page())
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-nav.yaml']")

    page.click("[data-testid='browse-entry-nav.yaml']")
    page.wait_for_selector("[data-testid='browse-selection']")

    assert str(browse_root / "nav.yaml") in page.inner_text("[data-testid='browse-selection']")


def test_a_general_user_sees_the_reason_and_the_allowed_range_not_an_add_entry(
    open_page, browse_root, tmp_path
):
    # 一般使用者手動打一個白名單外的路徑 → 看到原因與唯讀允許範圍，但沒有加入白名單入口。
    outside = tmp_path / "outside"
    outside.mkdir()
    page = _open_browser(open_page())  # 一般使用者

    page.fill("[data-testid='browse-path-input']", str(outside))
    page.get_by_role("button", name="前往").click()
    page.wait_for_selector("[data-testid='browse-rejected']")

    rejected = page.locator("[data-testid='browse-rejected']")
    assert rejected.get_attribute("data-kind") == "outside_roots"
    assert "白名單之外" in page.inner_text("[data-testid='browse-rejected']")
    assert page.get_by_role("button", name="加入白名單").count() == 0
    assert page.is_visible("[data-testid='allowed-range']")


def test_a_developer_sees_an_add_to_whitelist_entry_prefilled_with_the_rejected_path(
    open_page, browse_root, tmp_path
):
    outside = tmp_path / "outside_dev"
    outside.mkdir()
    page = _open_browser_as_developer(open_page())

    page.fill("[data-testid='browse-path-input']", str(outside))
    page.get_by_role("button", name="前往").click()
    page.wait_for_selector("[data-testid='whitelist-prefix-input']")

    assert page.get_by_role("button", name="加入白名單").count() == 1
    assert page.input_value("[data-testid='whitelist-prefix-input']") == str(outside)


def test_a_developer_adds_the_rejected_path_then_browsing_it_succeeds(
    open_page, browse_root, tmp_path
):
    # AC2 的完整迴路：白名單外被拒 → 開發者加入 → 同一畫面重跑瀏覽、現在列得出內容。
    outside = tmp_path / "onboarded"
    outside.mkdir()
    (outside / "cfg.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_browser_as_developer(open_page())
    page.fill("[data-testid='browse-path-input']", str(outside))
    page.get_by_role("button", name="前往").click()
    page.wait_for_selector("[data-testid='whitelist-prefix-input']")

    page.get_by_role("button", name="加入白名單").click()
    # 人工驗證 U13：這個入口原本一按就生效。現在與白名單維護同一套流程——先說即將開放的範圍與
    # 底下有幾個可納管檔，確認了才加入；取消什麼都不變。
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    said = page.inner_text("[data-testid='confirm-body']")
    assert str(outside) in said and "1 個可納管檔" in said
    page.click("[data-testid='confirm-cancel']")
    assert page.is_visible("[data-testid='whitelist-prefix-input']")
    assert page.query_selector("[data-testid='browse-entry-cfg.yaml']") is None

    page.get_by_role("button", name="加入白名單").click()
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='browse-entry-cfg.yaml']")
    assert _browse_entries(page) == [["cfg.yaml", "file"]]


def test_a_non_directory_rejection_offers_no_add_entry_even_to_a_developer(
    open_page, browse_root
):
    # 白名單內、但指向檔案（不是目錄）→ not_a_directory；加白名單無濟於事，
    # 即使開發者也沒有入口。
    (browse_root / "afile.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_browser_as_developer(open_page())

    page.fill("[data-testid='browse-path-input']", str(browse_root / "afile.yaml"))
    page.get_by_role("button", name="前往").click()
    page.wait_for_selector("[data-testid='browse-rejected']")

    rejected = page.locator("[data-testid='browse-rejected']")
    assert rejected.get_attribute("data-kind") == "not_a_directory"
    assert page.get_by_role("button", name="加入白名單").count() == 0


def test_the_add_entry_prefills_the_parent_directory_for_a_rejected_file(
    open_page, browse_root, tmp_path
):
    # 開發者手動打一個白名單外的**檔案**路徑 → 加入白名單入口預填該檔的父目錄，不是檔案本身
    # （白名單根必須是目錄；填檔案會被後端擋）。驗 suggested「檔案則取父目錄」這條契約。
    outside_file = tmp_path / "loose.yaml"
    outside_file.write_text("a: 1\n", encoding="utf-8")
    page = _open_browser_as_developer(open_page())

    page.fill("[data-testid='browse-path-input']", str(outside_file))
    page.get_by_role("button", name="前往").click()
    page.wait_for_selector("[data-testid='whitelist-prefix-input']")

    assert page.input_value("[data-testid='whitelist-prefix-input']") == str(tmp_path)


def test_navigating_to_another_directory_clears_a_previous_file_selection(open_page, browse_root):
    # 選取屬於某個目錄；離開它之後那個「已選」殘留會與目前位置不一致（#13 審查）。
    (browse_root / "params").mkdir()
    (browse_root / "params" / "inner.yaml").write_text("i: 1\n", encoding="utf-8")
    (browse_root / "nav.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_browser(open_page())
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-nav.yaml']")
    page.click("[data-testid='browse-entry-nav.yaml']")
    page.wait_for_selector("[data-testid='browse-selection']")

    page.click("[data-testid='browse-entry-params']")
    page.wait_for_selector("[data-testid='browse-entry-inner.yaml']")

    assert page.is_hidden("[data-testid='browse-selection']")


def test_returning_from_the_browser_shows_the_list_again(open_page):
    # W7「返回：關閉瀏覽、回到清單」——往返的另一半，不能只驗開啟。
    page = _open_browser(open_page())

    page.get_by_role("button", name="返回").click()
    page.wait_for_selector("[data-testid='config-tree']", state="visible")

    assert page.is_hidden("[data-testid='browse']")


# ── W8 納管確認（#14）────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, content, fmt",
    [
        ("nav.yaml", "max_vel_x: 0.8\n", "yaml"),
        ("conf.json", '{"a": 1}\n', "json"),
        ("app.toml", "k = 1\n", "toml"),
        ("s.ini", "[sec]\nk = 1\n", "ini"),
    ],
)
def test_onboarding_a_config_of_each_format_shows_it_in_the_list(
    open_page, browse_root, name, content, fmt
):
    # AC1：從介面納管至少三種格式的真實 config，納管後出現在左側樹。
    (browse_root / name).write_text(content, encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, name)

    assert page.input_value("[data-testid='onboard-format']") == fmt  # 副檔名預填的建議
    page.get_by_role("button", name="確認寫入").click()

    stem = name.rsplit(".", 1)[0]
    # 納管後回清單、新條目出現在左側樹（名字由目標路徑推導：<根名>-<檔名>）。
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.wait_for_selector(f"text={browse_root.name}-{stem}")


def test_the_confirm_screen_shows_each_value_type(open_page, browse_root):
    # AC3：顯示每個值的解析型別供確認（可折疊樹，葉節點帶正確 data-type）。
    (browse_root / "types.json").write_text('{"a": 1, "b": "x"}\n', encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "types.json")

    assert page.get_attribute("[data-testid='onboard-type-a']", "data-type") == "int"
    assert page.get_attribute("[data-testid='onboard-type-b']", "data-type") == "string"


def test_nested_types_render_as_a_collapsible_tree(open_page, browse_root):
    # AC3 的可折疊樹：巢狀 config 的容器節點帶 data-container，子節點型別正確，點容器折疊子節點。
    (browse_root / "nested.json").write_text(
        '{"outer": {"inner": 1}, "arr": [2]}\n', encoding="utf-8"
    )
    page = _open_confirm(open_page(), browse_root, "nested.json")

    assert page.get_attribute("[data-testid='onboard-type-outer']", "data-container") == "true"
    assert page.get_attribute("[data-testid='onboard-type-outer.inner']", "data-type") == "int"
    # 點容器折疊 → 子節點被標為折疊而隱藏（is-collapsed-child → display:none）。
    page.click("[data-testid='onboard-type-outer']")
    page.wait_for_selector("[data-testid='onboard-type-outer.inner']", state="hidden")


def test_a_key_with_a_dot_renders_without_colliding_with_a_nested_field(open_page, browse_root):
    # #219：key 含 . 與巢狀路徑撞號會靜默遺失型別；跳脫後兩者都在，含點 key 的葉名顯示為
    # 原樣 a.b（還原跳脫）、巢狀的顯示為 b（不被切半、不算錯層）。
    (browse_root / "dotkey.json").write_text(
        '{"a.b": "hello", "a": {"b": 123}}\n', encoding="utf-8"
    )
    page = _open_confirm(open_page(), browse_root, "dotkey.json")

    nodes = page.eval_on_selector_all(
        "[data-testid^='onboard-type-']",
        "els => els.map(e => ({name: e.dataset.name, type: e.dataset.type, "
        "leaf: e.querySelector('span').textContent.trim()}))",
    )
    by_name = {n["name"]: n for n in nodes}

    assert by_name["a\\.b"]["type"] == "string"  # 含點 key 的欄位還在
    assert by_name["a.b"]["type"] == "int"  # 巢狀欄位也還在（沒撞號遺失）
    assert by_name["a"]["type"] == "dict"
    assert by_name["a\\.b"]["leaf"] == "a.b"  # 葉名還原成 a.b，不是 a\.b 或被切成 b
    assert by_name["a.b"]["leaf"] == "b"


def test_ambiguous_yaml_gates_the_submit_until_each_is_acked(open_page, browse_root):
    # AC4：歧義以清單呈現（指名值與行號），逐條確認後才可寫入。
    (browse_root / "amb.yaml").write_text("enabled: no\nmode: 08\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "amb.yaml")
    submit = page.get_by_role("button", name="確認寫入")

    assert submit.is_disabled()  # 有未確認的歧義
    assert "no" in page.inner_text("[data-testid='onboard-ambiguity-1']")  # 指名值與行號
    page.check("[data-testid='onboard-ambiguity-ack-1']")
    assert submit.is_disabled()  # 還有一個未勾
    page.check("[data-testid='onboard-ambiguity-ack-2']")
    assert submit.is_enabled()  # 全部確認後才解鎖


def test_the_confirm_screen_previews_the_hostname_and_permissions(open_page, browse_root):
    # AC5：納管前預覽 hostname（核對機器身分），並顯示原始權限（§5.1 一行摘要的第三項）。
    (browse_root / "h.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "h.yaml")

    # 斷言「值」非空——_open_confirm 已 wait「將以 hostname」，只斷言該子字串會恆為真；
    # 空 hostname 也會產出「將以 hostname＝ 納管」，所以斷言它不等於那個空值形狀（#14 審查）。
    hostname_text = page.inner_text("[data-testid='onboard-hostname']")
    assert hostname_text.strip() not in ("將以 hostname＝ 納管", "將以 hostname= 納管")
    assert page.inner_text("[data-testid='onboard-permissions']").strip()  # 權限有顯示


def test_an_onboarded_entry_survives_a_reload(open_page, browse_root):
    # 納管後條目落盤：fresh page 重抓、伺服器每請求重掃 config-list.toml，reload 後仍在。
    # （hostname 本身「重開不因環境改變而變動」的凍結由 T22／test_api 的 API 測試守，不在這裡。）
    (browse_root / "persist.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "persist.yaml")
    page.get_by_role("button", name="確認寫入").click()
    page.wait_for_selector(f"text={browse_root.name}-persist")

    page.reload()

    page.wait_for_selector(f"text={browse_root.name}-persist")


def test_raw_format_shows_version_only_not_zero_fields(open_page, browse_root):
    # raw 不解析——確認畫面說「只版控、不解析」，不顯示「0 個欄位」假訊號。
    (browse_root / "blob.raw").write_text("anything\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "blob.raw")

    assert page.input_value("[data-testid='onboard-format']") == "raw"
    assert "只版控" in page.inner_text("[data-testid='onboard-summary']")


def test_changing_the_format_re_inspects(open_page, browse_root):
    # 契約：格式改則重新偵測（不變式 8：副檔名不是持續權威）。有歧義的 yaml 改成 raw →
    # 歧義清空、摘要變「只版控」。
    (browse_root / "reinspect.yaml").write_text("enabled: no\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "reinspect.yaml")
    page.wait_for_selector("[data-testid='onboard-ambiguity-1']")  # yaml 先有歧義

    page.select_option("[data-testid='onboard-format']", "raw")

    page.wait_for_selector("text=只版控")  # 重新偵測後 summary 變 raw 說明
    assert page.locator("[data-testid^='onboard-ambiguity-']").count() == 0  # 歧義清空


def test_an_inspect_error_is_shown_in_the_confirm_screen(open_page, browse_root):
    # inspect 的語法錯誤（帶行號）在確認畫面原樣顯示，行號只出現一次（後端訊息已內嵌，#14 審查）。
    (browse_root / "broken.json").write_text("{not: valid\n", encoding="utf-8")
    page = _open_browser(open_page())
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-broken.json']")
    page.click("[data-testid='browse-entry-broken.json']")
    page.get_by_role("button", name="檢視並納管").click()

    page.wait_for_selector("[data-testid='onboard-error']:not([hidden])")
    error_text = page.inner_text("[data-testid='onboard-error']")
    assert error_text.strip()
    assert error_text.count("（第") == 1  # 行號標記只出現一次（後端已內嵌，前端不再補）
    assert page.get_by_role("button", name="確認寫入").is_disabled()  # 出錯時不能寫入


def test_a_failed_inspect_clears_the_previous_files_ambiguities(open_page, browse_root):
    # 偵測失敗不清舊結果 → W8 殘留前一檔的歧義勾選框，勾滿就把「確認寫入」重新解鎖，於是對
    # 偵測失敗的檔案送出納管、還帶著另一檔的歧義確認，繞過 AC4 的確認閘門。清場後不殘留。
    (browse_root / "ambiguous.yaml").write_text("flag: yes\n", encoding="utf-8")
    (browse_root / "broken.json").write_text("{not: valid\n", encoding="utf-8")
    page = _open_browser(open_page())
    page.click(f"[data-testid='browse-root-{browse_root}']")

    # 檔案 A（有歧義）：偵測成功，歧義框出現。
    page.wait_for_selector("[data-testid='browse-entry-ambiguous.yaml']")
    page.click("[data-testid='browse-entry-ambiguous.yaml']")
    page.get_by_role("button", name="檢視並納管").click()
    page.wait_for_selector("[data-testid='onboard-ambiguity-1']")

    # 取消回瀏覽會回到選根狀態（清單讓位給根，見 showBrowse），故重新展開根，改選檔案 B。
    page.get_by_role("button", name="取消").click()
    page.wait_for_selector(f"[data-testid='browse-root-{browse_root}']")
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector("[data-testid='browse-entry-broken.json']")
    page.click("[data-testid='browse-entry-broken.json']")
    page.get_by_role("button", name="檢視並納管").click()
    page.wait_for_selector("[data-testid='onboard-error']:not([hidden])")

    # A 的歧義框不再殘留，且「確認寫入」停用（無法對失敗的 B 送出）。
    assert page.query_selector("[data-testid='onboard-ambiguity-1']") is None
    assert page.get_by_role("button", name="確認寫入").is_disabled()


def test_cancelling_returns_to_browse_and_writes_nothing(open_page, browse_root):
    # W8「取消：回到 W7 瀏覽（不寫入）」，且確認 view 要藏起（不殘留兩個 view 疊著，#14 審查）。
    (browse_root / "cancel.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "cancel.yaml")

    page.get_by_role("button", name="取消").click()

    page.wait_for_selector("[data-testid='browse']", state="visible")
    assert page.is_hidden("[data-testid='onboard-confirm']")  # 確認 view 藏起，沒疊著
    # 回到清單看：沒有任何新條目被寫入（取消不寫入）。
    page.get_by_role("button", name="返回").click()
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    assert page.locator("[data-testid^='tree-item-']").count() == 0


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _open_browser(page):
    """進入身分（一般使用者足以瀏覽）→ 點「納管」開檔案瀏覽 view。

    以角色＋名稱選按鈕，不用 `text=納管`：空清單提示「還沒有納管任何 config」也含「納管」，
    子字串選取會撞到兩個元素（strict mode）。
    """
    _fill_identity(page)
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.get_by_role("button", name="納管").click()
    page.wait_for_selector("[data-testid='browse']", state="visible")
    return page


def _open_browser_as_developer(page):
    """以開發者身分進入再開瀏覽（拒絕情境要驗開發者的加入白名單入口）。"""
    page.click("[data-testid='role-toggle'] button[data-role='developer']")
    return _open_browser(page)


def _open_confirm(page, browse_root, name):
    """開瀏覽 → 選 browse_root 底下的 name 檔 → 「檢視並納管」→ 到 W8 確認畫面。"""
    _open_browser(page)
    page.click(f"[data-testid='browse-root-{browse_root}']")
    page.wait_for_selector(f"[data-testid='browse-entry-{name}']")
    page.click(f"[data-testid='browse-entry-{name}']")
    page.get_by_role("button", name="檢視並納管").click()
    page.wait_for_selector("[data-testid='onboard-confirm']", state="visible")
    # 等 inspect 非同步回來、確認畫面填好——hostname 一定會被填（成功時），是「偵測完成」的訊號；
    # 不等的話讀 hostname／summary 會讀到還沒填的空字串。
    page.wait_for_selector("text=將以 hostname")
    return page


def _browse_entries(page) -> list:
    """目前目錄清單的 [[名字, 種類], …]，**依 DOM 順序**——後端依名字排序，回 list（不是
    dict）才驗得到排序這條契約（dict 比對忽略順序）。"""
    return page.eval_on_selector_all(
        "[data-testid='browse-list'] [data-testid^='browse-entry-']",
        "ns => ns.map(n => [n.dataset.name, n.dataset.kind])",
    )


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


# 「已經輸入過身分」的瀏覽器：交給 `open_page(identity=…)`，以該瀏覽器自己的 cookie 罐走端點記下。
_REMEMBERED = {"name": _NAME, "email": _EMAIL}


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


# ── W9 白名單維護（#15）──────────────────────────────────────────────────────


def _open_whitelist_as_developer(page):
    """以開發者身分進入 → 點「白名單」開維護面板（面板開發者專屬）。"""
    page.click("[data-testid='role-toggle'] button[data-role='developer']")
    _fill_identity(page)
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.get_by_role("button", name="白名單").click()
    page.wait_for_selector("[data-testid='whitelist']", state="visible")
    return page


def test_the_whitelist_button_is_absent_for_a_normal_user(open_page):
    # 白名單維護開發者專屬：一般使用者連按鈕都不存在於 DOM（角色表、ADR-00000020），非停用。
    page = _enter_identity(open_page())  # 預設一般使用者

    assert page.get_by_role("button", name="白名單").count() == 0


def test_a_developer_can_open_the_whitelist_panel(open_page):
    page = _open_whitelist_as_developer(open_page())

    assert page.is_visible("[data-testid='whitelist']")


def test_the_whitelist_lists_each_root_with_who_and_when(open_page, browse_root):
    # AC2：檢視時列出目前允許的根，以及各是誰、何時加入的（種子根 added_by=seed）。
    page = _open_whitelist_as_developer(open_page())

    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']")
    row = page.text_content(f"[data-testid='whitelist-root-{browse_root}']")
    assert "seed" in row and "2026-01-01" in row


def test_previewing_a_prefix_shows_its_candidate_file_count(open_page, tmp_path):
    # §7.9：加根前顯示「此路徑下有 N 個可納管檔」（GET /api/candidate-count，#206）。
    prefix = tmp_path / "preview_me"
    prefix.mkdir()
    (prefix / "a.yaml").write_text("x", encoding="utf-8")
    (prefix / "b.conf").write_text("y", encoding="utf-8")
    page = _open_whitelist_as_developer(open_page())

    page.fill("[data-testid='whitelist-add-input']", str(prefix))
    page.get_by_role("button", name="預覽").click()
    page.wait_for_selector("[data-testid='whitelist-preview']", state="visible")

    assert "2" in page.text_content("[data-testid='whitelist-preview']")


def test_previewing_an_invalid_prefix_shows_the_reason_not_a_count(open_page, tmp_path):
    # 前綴含 .. → 後端 422，預覽處顯示原樣錯誤、不顯示假的數字。
    page = _open_whitelist_as_developer(open_page())

    page.fill("[data-testid='whitelist-add-input']", str(tmp_path / "sub" / ".." / "x"))
    page.get_by_role("button", name="預覽").click()

    page.wait_for_selector("[data-testid='whitelist-error']", state="visible")


def test_adding_a_prefix_appends_it_to_the_whitelist(open_page, tmp_path):
    # AC1：可從介面新增路徑前綴。
    prefix = tmp_path / "to_add"
    prefix.mkdir()
    page = _open_whitelist_as_developer(open_page())

    (prefix / "a.yaml").write_text("x", encoding="utf-8")
    page.fill("[data-testid='whitelist-add-input']", str(prefix))
    page.get_by_role("button", name="加入白名單").click()
    # 加入前先確認：寫明即將開放的路徑與底下有幾個可納管檔（§7.9）。
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    said = page.inner_text("[data-testid='confirm-body']")
    assert str(prefix) in said and "1 個可納管檔" in said
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(f"[data-testid='whitelist-root-{prefix}']")


def test_adding_a_root_that_is_already_whitelisted_is_refused_before_the_confirmation(
    open_page, browse_root
):
    # 複驗回饋：已存在的路徑原本先跳確認框、按確認才被告知已存在。顯示確認框前先比對目前的
    # 白名單，直接說已存在、沒有加入；後端送出時的重複檢查仍保留。
    page = _open_whitelist_as_developer(open_page())
    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']")

    page.fill("[data-testid='whitelist-add-input']", str(browse_root))
    page.get_by_role("button", name="加入白名單").click()

    page.wait_for_selector("[data-testid='whitelist-error']", state="visible")
    assert "已經在白名單" in page.inner_text("[data-testid='whitelist-error']")
    assert page.query_selector("[data-testid='confirm-dialog'][open]") is None
    assert page.locator("[data-testid^='whitelist-root-']").count() == 1


def test_cancelling_the_add_confirmation_leaves_the_whitelist_unchanged(open_page, tmp_path):
    prefix = tmp_path / "not_added"
    prefix.mkdir()
    page = _open_whitelist_as_developer(open_page())

    page.fill("[data-testid='whitelist-add-input']", str(prefix))
    page.get_by_role("button", name="加入白名單").click()
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-cancel']")

    assert page.query_selector(f"[data-testid='whitelist-root-{prefix}']") is None
    assert page.input_value("[data-testid='whitelist-add-input']") == str(prefix)  # 輸入還在


def test_a_stale_preview_is_cleared_when_the_path_changes(open_page, tmp_path):
    # 人工驗證 U13：預覽某個路徑後把輸入改成別的，舊的「N 個可納管檔」還留著，會被誤認為是
    # 目前路徑的結果。路徑一改就清掉；按加入被拒時也不留著。
    prefix = tmp_path / "preview_then_change"
    prefix.mkdir()
    (prefix / "a.yaml").write_text("x", encoding="utf-8")
    page = _open_whitelist_as_developer(open_page())
    page.fill("[data-testid='whitelist-add-input']", str(prefix))
    page.get_by_role("button", name="預覽").click()
    page.wait_for_selector("[data-testid='whitelist-preview']", state="visible")

    page.fill("[data-testid='whitelist-add-input']", "relative/path")
    assert page.is_hidden("[data-testid='whitelist-preview']")

    page.get_by_role("button", name="加入白名單").click()
    page.wait_for_selector("[data-testid='whitelist-error']", state="visible")
    assert page.is_hidden("[data-testid='whitelist-preview']")


def test_adding_a_prefix_with_dotdot_is_rejected_with_the_reason_shown(open_page, tmp_path):
    # AC5：以 ../ 嘗試逃逸被攔截（後端 422），前端原樣呈現，不靜默加入。
    page = _open_whitelist_as_developer(open_page())

    page.fill("[data-testid='whitelist-add-input']", str(tmp_path / "sub" / ".." / "escape"))
    page.get_by_role("button", name="加入白名單").click()

    page.wait_for_selector("[data-testid='whitelist-error']", state="visible")


def test_removing_a_root_asks_for_confirmation_then_removes(open_page, browse_root):
    # AC3：移除前先要求確認（不靜默移除），確認後才真移除。
    page = _open_whitelist_as_developer(open_page())
    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']")

    page.locator(
        f"[data-testid='whitelist-root-{browse_root}'] [data-testid='whitelist-remove']"
    ).click()
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    # 人工驗證 U15：確認區是寫給按按鈕的人看的——說要移除哪一個、會怎樣，不出現 API 的參數名。
    said = page.inner_text("[data-testid='confirm-dialog']")
    assert str(browse_root) in said and "不能再瀏覽或納管" in said and "confirmed" not in said
    assert page.inner_text("[data-testid='confirm-ok']") == "從白名單移除"
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']", state="detached")


def test_cancelling_a_removal_keeps_the_root(open_page, browse_root):
    # 取消確認 → 不移除，根還在。
    page = _open_whitelist_as_developer(open_page())
    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']")

    page.locator(
        f"[data-testid='whitelist-root-{browse_root}'] [data-testid='whitelist-remove']"
    ).click()
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-cancel']")

    page.wait_for_selector("[data-testid='confirm-dialog']:not([open])", state="attached")
    assert page.is_visible(f"[data-testid='whitelist-root-{browse_root}']")


def test_returning_from_the_whitelist_panel_hides_it_rather_than_stacking_on_the_list(open_page):
    # #252 發現1：離開白名單面板的唯一出口是 showList，而它從不隱藏 whitelistView——返回後
    # 白名單面板與 config 清單會同時堆疊顯示。這裡驗「返回」後白名單面板確實收起、只剩清單。
    page = _open_whitelist_as_developer(open_page())

    page.click("[data-testid='whitelist-back']")
    page.wait_for_selector("[data-testid='config-tree']", state="visible")

    assert page.is_hidden("[data-testid='whitelist']")


def test_the_removal_confirmation_blocks_everything_else_until_answered(open_page, browse_root):
    # #252 發現1 的那個洞：舊的確認區是頁面裡的一塊，開著它按「返回」，那顆「確認移除」會跟著殘留
    # 在看似清單的畫面上。現在確認走共用的對話框（#50），是 modal——沒回答之前按不到後面的東西。
    page = _open_whitelist_as_developer(open_page())
    page.wait_for_selector(f"[data-testid='whitelist-root-{browse_root}']")
    page.locator(
        f"[data-testid='whitelist-root-{browse_root}'] [data-testid='whitelist-remove']"
    ).click()
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")

    covered = page.evaluate(
        """() => {
          const back = document.querySelector("[data-testid='whitelist-back']");
          const box = back.getBoundingClientRect();
          const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
          return hit !== back && !back.contains(hit);
        }"""
    )

    assert covered  # 「返回」被對話框擋著，點不到
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-testid='confirm-dialog']:not([open])", state="attached")
    assert page.is_visible(f"[data-testid='whitelist-root-{browse_root}']")  # Esc＝取消，根還在


def test_a_structured_load_error_shows_the_backend_message_not_a_bare_status(open_page):
    # #252 發現3：後端把清單檔執行期讀不了包成 {detail:{message,file}}，指名壞掉的檔與下一步
    # （不變式 2）。前端要原樣呈現該訊息，不能丟掉只顯示籠統的「500」。
    page = open_page()
    page.route(
        "**/api/configs",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps(
                {
                    "detail": {
                        "message": "清單檔解析失敗。下一步：修正該檔",
                        "file": "/repo/config-list.toml",
                    }
                }
            ),
        ),
    )
    _fill_identity(page)
    page.wait_for_selector("[data-testid='load-error']:not([hidden])")

    assert "下一步" in page.inner_text("[data-testid='load-error']")


# ── W3 參數欄位表（#20）──────────────────────────────────────────────────────
# 不是線上 YAML 編輯器（ADR-00000013）：一列一個參數，依型別給控制項。單擊左側樹節點開啟
# （#20 的 D1；雙擊與多開是 #35／#36）。型別與值來自 GET /api/configs/{uid}，前端不猜。

_PARAM_UID = "mfz3k9q1"


def _listing_with(repo, content: str, fmt: str = "yaml") -> None:
    """一筆條目、內容由規格自己給（`listing` 夾具的內容太簡單，驗不到型別）。目標＝來源→一致。"""
    suffix = "conf" if fmt == "raw" else fmt
    (repo / "files" / f"p.{suffix}").write_text(content, encoding="utf-8")
    target = repo / "deployed" / f"p.{suffix}"
    target.write_text(content, encoding="utf-8")
    entry = (
        f'\n[[files]]\nuid = "{_PARAM_UID}"\nname = "p"\nhostname = "amr01"\n'
        f'source = "files/p.{suffix}"\ntarget = "{target}"\nformat = "{fmt}"\ngroups = []\n'
    )
    (repo / "config-list.toml").write_text(_LIST_HEADER + entry, encoding="utf-8")


def _open_panel(page, developer: bool = False):
    """輸入身分 → 雙擊樹上那筆 → 等右側欄位表出現（單擊只是選取，#35）。"""
    if developer:
        page.click("[data-testid='role-toggle'] button[data-role='developer']")
    _enter_identity(page)
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    return page


def _param_value(path: str) -> str:
    return f"[data-testid='param-{path}'] [data-testid='param-value']"


def test_clicking_a_config_opens_a_table_with_one_row_per_parameter(open_page, repo):
    # AC1：一列一個參數——參數名、型別標示、輸入控制項、來源值、驗證狀態都在同一列。
    _listing_with(repo, "max_vel: 0.8\nenabled: true\nname: amr\ncount: 3\n")
    page = _open_panel(open_page())

    labels = page.eval_on_selector_all(
        "[data-testid='param-table'] li[data-name]",
        "els => Object.fromEntries(els.map(e => [e.dataset.name,"
        " e.querySelector('[data-testid=\"param-type\"]').textContent]))",
    )
    assert labels == {"max_vel": "double", "enabled": "bool", "name": "string", "count": "int"}
    assert page.inner_text("[data-testid='param-count'] [data-testid='param-source-value']") == "3"
    # 驗證狀態欄一開始就在（空的，因為值合法），該列標為合法。
    assert page.inner_text("[data-testid='param-count'] [data-testid='param-validation']") == ""
    assert page.get_attribute("[data-testid='param-count']", "data-valid") == "true"


def test_nested_parameters_render_as_a_collapsible_block_not_a_dotted_name(open_page, repo):
    # AC1：巢狀以可折疊區塊表示——子列以縮排呈現、顯示名是 inner 不是 outer.inner；點區塊折疊。
    _listing_with(repo, "outer:\n  inner: 1\n")
    page = _open_panel(open_page())

    assert page.get_attribute("[data-testid='param-outer']", "data-container") == "true"
    assert page.get_attribute("[data-testid='param-outer.inner']", "data-type") == "int"
    leaf = page.inner_text("[data-testid='param-outer.inner'] .param-name").strip()
    assert leaf == "inner"
    page.click("[data-testid='param-outer']")
    page.wait_for_selector("[data-testid='param-outer.inner']", state="hidden")


def test_a_bool_parameter_is_a_checkbox_reflecting_its_value(open_page, repo):
    # AC2：核取方塊——結構上輸入不了非布林值。
    _listing_with(repo, "enabled: true\nverbose: false\n")
    page = _open_panel(open_page())

    assert page.get_attribute(_param_value("enabled"), "type") == "checkbox"
    assert page.is_checked(_param_value("enabled"))
    assert not page.is_checked(_param_value("verbose"))


def test_an_int_parameter_rejects_a_decimal_and_says_why(open_page, repo):
    # AC3：整數步進的數字框；打小數→該列標錯並說原因，打整數→恢復。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())

    assert page.get_attribute(_param_value("count"), "step") == "1"
    page.fill(_param_value("count"), "1.5")
    assert page.get_attribute("[data-testid='param-count']", "data-valid") == "false"
    assert "整數" in page.inner_text("[data-testid='param-count'] [data-testid='param-validation']")
    page.fill(_param_value("count"), "4")
    assert page.get_attribute("[data-testid='param-count']", "data-valid") == "true"


def test_a_double_parameter_always_shows_a_decimal_point(open_page, repo):
    # AC4：允許小數的數字框；來源 `1.0` 顯示 1.0（不是 1），打 `5` 離開欄位後顯示 5.0。
    _listing_with(repo, "ratio: 1.0\nmax_vel: 0.8\n")
    page = _open_panel(open_page())

    assert page.get_attribute(_param_value("max_vel"), "step") == "any"
    assert page.input_value(_param_value("ratio")) == "1.0"
    page.fill(_param_value("max_vel"), "5")
    page.dispatch_event(_param_value("max_vel"), "change")
    assert page.input_value(_param_value("max_vel")) == "5.0"


def test_a_string_parameter_is_a_text_input_and_quotes_are_the_systems_business(open_page, repo):
    # AC6：文字框顯示的是值本身，來源檔的引號不出現在框裡。
    _listing_with(repo, 'name: "amr01"\n')
    page = _open_panel(open_page())

    assert page.get_attribute(_param_value("name"), "type") == "text"
    assert page.input_value(_param_value("name")) == "amr01"


def test_the_type_column_is_plain_text_for_a_normal_user(open_page, repo):
    # AC7（#20）＋#285：一般使用者看得到型別、改不了——欄位是純文字，選單根本不在 DOM 裡。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())

    tag = page.eval_on_selector(
        "[data-testid='param-count'] [data-testid='param-type']", "e => e.tagName"
    )
    assert tag == "SPAN"
    assert page.query_selector("[data-testid='param-count'] select") is None


def test_editing_a_value_keeps_the_source_value_beside_it_and_marks_the_change(open_page, repo):
    # §7.5.1：已修改未儲存時目前值與來源值並列，改動一眼可見。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())

    page.fill(_param_value("count"), "4")

    assert page.inner_text("[data-testid='param-count'] [data-testid='param-source-value']") == "3"
    assert page.get_attribute("[data-testid='param-count']", "data-changed") == "true"


def test_list_elements_are_editable_rows(open_page, repo):
    # #46：清單的元素是可編輯的列（先前唯讀，D3）；順序本身就是資料，所以每列有 ↑↓ 與移除。
    _listing_with(repo, "items:\n  - spin\n  - backup\n")
    page = _open_panel(open_page())

    assert page.get_attribute("[data-testid='param-items']", "data-type") == "list"
    assert page.input_value(_param_value("items[1]")) == "backup"
    assert page.get_attribute(_param_value("items[1]"), "readonly") is None
    assert page.is_visible("[data-testid='param-items[1]'] [data-testid='list-up']")


def test_a_raw_config_shows_an_unstructured_notice_not_an_empty_table(open_page, repo):
    # §7.5.4：沒有結構就明說「未結構化」，不假裝有 0 個欄位。
    _listing_with(repo, "whatever: [not parsed\n", fmt="raw")
    page = _open_panel(open_page())

    assert page.is_visible("[data-testid='panel-unstructured']")
    assert page.query_selector("[data-testid='param-table']") is None


# ── W3 陣列與巢狀編輯（#46，§7.5.2）────────────────────────────────────────
# 順序本身就是資料：只給新增／移除的話，要把第三項移到第一位得刪掉重打，重打會遺失原值。

_LIST_YAML = "recovery:\n  - spin\n  - backup\n  - wait\nspeed: 0.5\n"


def _list_values(page, path: str, count: int) -> list:
    return [page.input_value(_param_value(f"{path}[{index}]")) for index in range(count)]


def test_list_elements_can_be_reordered_edited_added_and_removed_then_saved(open_page, repo):
    targets = _listing_many(repo, {"p": _LIST_YAML})
    page = _open_panel(open_page())

    page.click(f"{_row('recovery[2]')} [data-testid='list-up']")  # wait 往上
    page.click(f"{_row('recovery[1]')} [data-testid='list-up']")  # 再往上到第一位
    assert _list_values(page, "recovery", 3) == ["wait", "spin", "backup"]
    page.fill(_param_value("recovery[2]"), "retreat")
    page.click(f"{_row('recovery')} [data-testid='list-add']")
    page.fill(_param_value("recovery[3]"), "pause")
    page.click(f"{_row('recovery[1]')} [data-testid='list-remove']")  # 移除 spin
    assert _list_values(page, "recovery", 3) == ["wait", "retreat", "pause"]
    assert page.get_attribute(_row("recovery"), "data-dirty") == "true"

    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")

    assert targets["p"].read_text(encoding="utf-8") == (
        "recovery:\n  - wait\n  - retreat\n  - pause\nspeed: 0.5\n"
    )


def test_the_first_element_cannot_move_up_and_the_last_cannot_move_down(open_page, repo):
    _listing_many(repo, {"p": _LIST_YAML})
    page = _open_panel(open_page())

    assert page.is_disabled(f"{_row('recovery[0]')} [data-testid='list-up']")
    assert page.is_disabled(f"{_row('recovery[2]')} [data-testid='list-down']")
    assert page.is_enabled(f"{_row('recovery[1]')} [data-testid='list-up']")


def test_moving_back_to_the_original_order_is_not_a_change(open_page, repo):
    _listing_many(repo, {"p": _LIST_YAML})
    page = _open_panel(open_page())

    page.click(f"{_row('recovery[0]')} [data-testid='list-down']")
    assert page.get_attribute(_row("recovery"), "data-dirty") == "true"
    page.click(f"{_row('recovery[1]')} [data-testid='list-up']")

    assert page.get_attribute(_row("recovery"), "data-dirty") == "false"
    assert page.is_disabled(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save']")


def test_a_list_of_objects_can_be_reordered_and_its_fields_edited(open_page, repo):
    # 元素是物件的清單：整塊一起搬（物件的欄位在它底下、可折疊），搬完欄位照樣可以改。
    targets = _listing_many(
        repo, {"p": "servers:\n  - host: a\n    port: 80\n  - host: b\n    port: 81\n"}
    )
    page = _open_panel(open_page())

    page.click(f"{_row('servers[1]')} [data-testid='list-up']")
    assert page.input_value(_param_value("servers[0].host")) == "b"
    page.fill(_param_value("servers[0].port"), "8080")
    page.click(f"{_row('servers')} [data-testid='list-add']")  # 以最後一項為樣板
    assert page.input_value(_param_value("servers[2].host")) == "a"
    page.fill(_param_value("servers[2].host"), "c")

    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")

    assert targets["p"].read_text(encoding="utf-8") == (
        "servers:\n  - host: b\n    port: 8080\n  - host: a\n    port: 80\n"
        "  - host: c\n    port: 80\n"
    )


def test_a_typed_list_keeps_its_element_type_when_edited(open_page, repo):
    # 元素型別一致：整數清單新增的是整數欄、double 清單寫出帶小數點。
    targets = _listing_many(repo, {"p": "ids:\n  - 1\n  - 2\ngains:\n  - 0.5\n"})
    page = _open_panel(open_page())

    page.click(f"{_row('ids')} [data-testid='list-add']")
    assert page.get_attribute(_param_value("ids[2]"), "type") == "number"
    page.fill(_param_value("ids[2]"), "3")
    page.click(f"{_row('gains')} [data-testid='list-add']")
    page.fill(_param_value("gains[1]"), "1")
    page.dispatch_event(_param_value("gains[1]"), "change")

    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")

    assert targets["p"].read_text(encoding="utf-8") == (
        "ids:\n  - 1\n  - 2\n  - 3\ngains:\n  - 0.5\n  - 1.0\n"
    )


def test_a_value_without_a_type_is_editable_as_text_and_marked_unstructured(open_page, repo):
    # 型別未知（null、空清單的元素）退回文字編輯，並明確標示未結構化、建議補 schema（§7.5.4）。
    targets = _listing_many(repo, {"p": "note: null\ncount: 3\n"})
    page = _open_panel(open_page())

    badge = f"{_row('note')} [data-testid='param-unstructured']"
    assert "未結構化" in page.inner_text(badge) and "schema" in page.get_attribute(badge, "title")
    assert page.query_selector(f"{_row('count')} [data-testid='param-unstructured']") is None
    page.fill(_param_value("note"), "filled in by hand")

    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")

    assert targets["p"].read_text(encoding="utf-8") == "note: filled in by hand\ncount: 3\n"


def test_list_controls_are_absent_while_read_only(open_page, repo):
    # 唯讀分頁：增刪調序鈕都是會寫入的控制項，不出現。
    _listing_many(repo, {"p": _LIST_YAML})
    holder = _enter_identity(open_page())
    holder.wait_for_selector("[data-testid='promote-all']", state="visible")
    second = open_page(same_browser_as=holder)
    second.wait_for_selector("[data-testid='readonly-banner']", state="visible")
    second.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    second.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")

    assert second.is_hidden(f"{_row('recovery')} [data-testid='list-add']")
    assert second.is_hidden(f"{_row('recovery[0]')} [data-testid='list-up']")
    assert holder.is_visible("[data-testid='promote-all']")


# ── W3 schema 骨架（#38）─────────────────────────────────────────────────────
# 骨架由開發者手動產生（納管時不自動產生）：推斷只看得到當下的值，由人看過再開啟把關。

_SCHEMA_DRAFT = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-schema-draft']"
_SCHEMA_BADGE = f"[data-testid='panel-schema-{_PARAM_UID}']"


def test_a_developer_drafts_a_schema_after_confirming_what_it_does(open_page, repo):
    # 先說後果再做：取消什麼都不變；確認後骨架存進 `.schemas/`、標頭標示已有 schema、按鈕消失。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page(), developer=True)
    stored = repo / ".schemas" / f"{_PARAM_UID}.json"

    page.click(_SCHEMA_DRAFT)
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    assert "型別" in page.inner_text("[data-testid='confirm-body']")
    page.click("[data-testid='confirm-cancel']")
    assert not stored.exists()

    page.click(_SCHEMA_DRAFT)
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(_SCHEMA_BADGE, state="visible")
    assert page.query_selector(_SCHEMA_DRAFT) is None
    assert f".schemas/{_PARAM_UID}.json" in page.inner_text("[data-testid='schema-drafted-notice']")
    assert json.loads(stored.read_text(encoding="utf-8"))["properties"] == {
        "count": {"type": "integer"}
    }


def test_a_config_that_already_has_a_schema_shows_the_badge_and_no_draft_button(open_page, repo):
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page(), developer=True)
    page.click(_SCHEMA_DRAFT)
    page.click("[data-testid='confirm-ok']")
    page.wait_for_selector(_SCHEMA_BADGE, state="visible")

    page.click(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-close']")
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")

    assert page.is_visible(_SCHEMA_BADGE)
    assert page.query_selector(_SCHEMA_DRAFT) is None


def test_drafting_a_schema_keeps_the_edits_not_yet_saved(open_page, repo):
    # 產生 schema 不重畫欄位表：還沒儲存的改動不能因此悄悄消失（不變式 2）。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page(), developer=True)
    page.fill(_param_value("count"), "4")

    page.click(_SCHEMA_DRAFT)
    page.click("[data-testid='confirm-ok']")
    page.wait_for_selector(_SCHEMA_BADGE, state="visible")

    assert page.input_value(_param_value("count")) == "4"
    assert page.get_attribute("[data-testid='param-count']", "data-changed") == "true"


def test_the_draft_schema_button_is_absent_for_a_normal_user(open_page, repo):
    # 僅開發者（ADR-00000020）：一般使用者模式下按鈕不存在於 DOM，不是停用。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page())

    assert page.query_selector("[data-testid='panel-schema-draft']") is None
    assert page.query_selector(_SCHEMA_BADGE) is None


def test_a_refused_schema_draft_shows_the_reason_in_the_panel(open_page, repo):
    # 面板開著的期間清單檔被別處加上了 schema：後端擋下（不覆寫），原因原樣顯示、不靜默。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page(), developer=True)
    listing = repo / "config-list.toml"
    listing.write_text(
        listing.read_text(encoding="utf-8").replace(
            'hostname = "amr01"\n', 'hostname = "amr01"\nschema = ".schemas/elsewhere.json"\n'
        ),
        encoding="utf-8",
    )

    page.click(_SCHEMA_DRAFT)
    page.click("[data-testid='confirm-ok']")

    error = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-error']"
    page.wait_for_selector(error, state="visible")
    assert ".schemas/elsewhere.json" in page.inner_text(error)
    assert page.query_selector(_SCHEMA_BADGE) is None


def test_a_raw_config_is_marked_unvalidated_and_offers_no_schema(open_page, repo):
    # raw 是唯一不受把關的格式——介面明確標示「未驗證」，使用者看得到自己放棄了什麼（§3.4）。
    _listing_with(repo, "whatever: [not parsed\n", fmt="raw")
    page = _open_panel(open_page(), developer=True)

    assert page.inner_text(f"[data-testid='panel-unvalidated-{_PARAM_UID}']") == "未驗證"
    assert page.query_selector("[data-testid='panel-schema-draft']") is None


def test_a_value_the_schema_rejects_is_refused_with_the_reason_in_the_panel(open_page, repo):
    # 第 2 層硬擋（#39）：schema 不收的值存不成草稿，面板說出原因（哪一行、哪個欄位、怎麼改）。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page(), developer=True)
    page.click(_SCHEMA_DRAFT)
    page.click("[data-testid='confirm-ok']")
    page.wait_for_selector(_SCHEMA_BADGE, state="visible")
    schema_file = repo / ".schemas" / f"{_PARAM_UID}.json"
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    schema["properties"]["count"]["maximum"] = 5
    schema_file.write_text(json.dumps(schema), encoding="utf-8")

    page.fill(_param_value("count"), "9")
    _save(page)

    error = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-error']"
    page.wait_for_selector(error, state="visible")
    shown = page.inner_text(error)
    assert "第 1 行" in shown and "count" in shown and "超出範圍" in shown
    assert not page.is_visible(_draft_dot(_PARAM_UID))


# ── W3 欄位表套用 schema（#40）───────────────────────────────────────────────
# 範圍、步進、列舉選項、說明都來自 schema。前端的即時回饋是體驗，不是保證——保證在後端（N-3）；
# 所以這裡也驗「介面沒擋到、後端擋下」時原因標在那一列。


def _listing_with_schema(repo, content: str, properties: dict) -> None:
    """一筆條目（uid＝_PARAM_UID）加上一份手寫的 schema：`properties` 是各欄位的規則。"""
    _listing_many(repo, {"p": content})
    listing = repo / "config-list.toml"
    listing.write_text(
        listing.read_text(encoding="utf-8").replace(
            'hostname = "amr01"\n',
            f'hostname = "amr01"\nschema = ".schemas/{_PARAM_UID}.json"\n',
        ),
        encoding="utf-8",
    )
    (repo / ".schemas").mkdir(exist_ok=True)
    (repo / ".schemas" / f"{_PARAM_UID}.json").write_text(
        json.dumps({"type": "object", "properties": properties}), encoding="utf-8"
    )


def _row(path: str) -> str:
    return f"[data-testid='param-{path}']"


def _validation(page, path: str) -> str:
    return page.inner_text(f"{_row(path)} [data-testid='param-validation']")


def test_a_number_with_a_range_gets_min_max_and_flags_a_value_outside_it(open_page, repo):
    # AC1：輸入框套用 min／max／step；超出範圍在輸入當下就標示，儲存停用；改回範圍內恢復。
    _listing_with_schema(repo, "max_vel: 0.8\n", {
        "max_vel": {"type": "number", "minimum": 0, "maximum": 2, "multipleOf": 0.1},
    })
    page = _open_panel(open_page())
    control = _param_value("max_vel")

    attributes = [page.get_attribute(control, name) for name in ("min", "max", "step")]
    assert attributes == ["0", "2", "0.1"]

    page.fill(control, "2.5")
    assert "不可大於 2" in _validation(page, "max_vel")
    assert page.get_attribute(_row("max_vel"), "data-valid") == "false"
    assert page.is_disabled(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save']")

    page.fill(control, "0.25")
    assert "0.1 的倍數" in _validation(page, "max_vel")

    page.fill(control, "0.3")
    assert _validation(page, "max_vel") == ""
    assert page.get_attribute(_row("max_vel"), "data-valid") == "true"


def test_an_integer_typed_into_a_double_field_stays_a_double(open_page, repo):
    # AC2：把 double 參數改成整數樣子的值，在輸入當下就被擋回 double——離開欄位即補上小數點，
    # 寫出去的是 `2.0` 不是 `2`（ROS 的 1 vs 1.0）。
    targets = _listing_many(repo, {"p": "max_vel: 0.8\n"})
    _listing_with_schema(repo, "max_vel: 0.8\n", {"max_vel": {"type": "number"}})
    page = _open_panel(open_page())

    page.fill(_param_value("max_vel"), "2")
    page.dispatch_event(_param_value("max_vel"), "change")
    assert page.input_value(_param_value("max_vel")) == "2.0"

    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")
    assert targets["p"].read_text(encoding="utf-8") == "max_vel: 2.0\n"


def test_an_enum_field_offers_only_the_choices_from_the_schema(open_page, repo):
    # AC3：選項來自 schema，結構上選不到不存在的值；選了之後存得成草稿、型別不變（整數仍是整數）。
    _listing_with_schema(repo, "mode: auto\nlevel: 1\n", {
        "mode": {"enum": ["auto", "manual", "off"]},
        "level": {"type": "integer", "enum": [1, 2, 3]},
    })
    page = _open_panel(open_page())

    options = page.eval_on_selector_all(
        f"{_param_value('mode')} option", "els => els.map(e => e.textContent)"
    )
    assert options == ["auto", "manual", "off"]
    assert page.inner_text(f"{_row('mode')} [data-testid='param-type']") == "enum"

    page.select_option(_param_value("mode"), label="manual")
    page.select_option(_param_value("level"), label="3")
    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")

    assert (repo / "files" / "p.yaml").read_text(encoding="utf-8") == "mode: auto\nlevel: 1\n"
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")
    assert (repo / "files" / "p.yaml").read_text(encoding="utf-8") == "mode: manual\nlevel: 3\n"


def test_a_current_value_outside_the_enum_is_shown_and_flagged_not_silently_replaced(
    open_page, repo
):
    # schema 是後來才收緊的：目前的值不在選項裡。照實顯示並標成有問題，不悄悄換成第一個選項。
    _listing_with_schema(repo, "mode: turbo\n", {"mode": {"enum": ["auto", "manual"]}})
    page = _open_panel(open_page())

    shown = page.eval_on_selector(_param_value("mode"), "e => e.selectedOptions[0].textContent")
    assert shown.startswith("turbo") and "不在允許的值裡" in shown
    assert page.get_attribute(_row("mode"), "data-valid") == "false"

    page.select_option(_param_value("mode"), label="auto")
    assert page.get_attribute(_row("mode"), "data-valid") == "true"


def test_the_schema_description_is_shown_beside_the_field(open_page, repo):
    # AC4：schema 的 description 顯示在欄位旁，滑鼠停留看得到全文。沒有說明的欄位沒有這個標記。
    _listing_with_schema(repo, "max_vel: 0.8\nretries: 3\n", {
        "max_vel": {"type": "number", "description": "最大線速度（m/s）"},
        "retries": {"type": "integer"},
    })
    page = _open_panel(open_page())

    marker = f"{_row('max_vel')} [data-testid='param-description']"
    assert page.get_attribute(marker, "title") == "最大線速度（m/s）"
    assert page.query_selector(f"{_row('retries')} [data-testid='param-description']") is None


def test_a_problem_the_backend_finds_is_marked_on_that_row_with_the_fix(open_page, repo):
    # AC5：介面沒擋到、後端擋下的（這裡是 schema 的 pattern）——那一列標紅並寫出原因與修正建議；
    # 再改動那一列時標記清掉。
    _listing_with_schema(repo, "name: amr01\ncount: 3\n", {
        "name": {"type": "string", "pattern": "^amr"},
    })
    page = _open_panel(open_page())

    page.fill(_param_value("name"), "robot7")
    _save(page)

    page.wait_for_selector(f"{_row('name')}[data-valid='false']")
    assert "pattern" in _validation(page, "name") and "建議" in _validation(page, "name")
    assert page.get_attribute(_row("count"), "data-valid") == "true"  # 別列不受牽連
    assert not page.is_visible(_draft_dot(_PARAM_UID))

    page.fill(_param_value("name"), "amr02")
    assert page.get_attribute(_row("name"), "data-valid") == "true"
    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")


def test_a_broken_schema_is_announced_in_the_panel_while_the_values_stay_visible(open_page, repo):
    # schema 讀不出來：值照樣看得到，面板明說修好之前存不了、指名那份檔案。
    _listing_with_schema(repo, "count: 3\n", {"count": {"type": "integer"}})
    (repo / ".schemas" / f"{_PARAM_UID}.json").write_text("{ not json", encoding="utf-8")
    page = _open_panel(open_page())

    banner = page.inner_text(
        f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-schema-error']"
    )
    assert "存不了" in banner and f".schemas/{_PARAM_UID}.json" in banner
    assert page.input_value(_param_value("count")) == "3"


# ── W3 人工指定型別（#285、ADR-00000021）──────────────────────────────────────
# 推斷只看得到當下的值；開發者在型別欄把它改對。指定寫進 schema，逐欄位清除。

_TYPE = "[data-testid='param-type']"


def test_a_developer_specifies_a_type_and_the_row_is_marked_specified(open_page, repo):
    # `timeout: 5` 推斷成 int；開發者選 double → 那一列換成 double 的輸入框、標「已指定」、
    # 有「清除」；指定寫進 schema 並留一筆紀錄。別列不受影響。
    _listing_many(repo, {"p": "timeout: 5\nretries: 3\n"})
    _commit_listing(repo)
    page = _open_panel(open_page(), developer=True)
    assert page.eval_on_selector(f"{_row('timeout')} {_TYPE}", "e => e.value") == "int"

    page.select_option(f"{_row('timeout')} {_TYPE}", "float")

    page.wait_for_selector(f"{_row('timeout')} [data-testid='type-overridden']")
    assert page.inner_text(f"{_row('timeout')} [data-testid='type-overridden']") == "已指定"
    assert page.is_visible(f"{_row('timeout')} [data-testid='type-clear']")
    assert page.input_value(_param_value("timeout")) == "5.0"  # double 的顯示帶小數點
    assert page.query_selector(f"{_row('retries')} [data-testid='type-overridden']") is None
    assert "double" in page.inner_text("[data-testid='type-specified-notice']")
    stored = json.loads((repo / ".schemas" / f"{_PARAM_UID}.json").read_text(encoding="utf-8"))
    assert stored["properties"]["timeout"]["type"] == "number"
    assert _git_log(repo, 1) == [f"meta({_PARAM_UID}): 指定「timeout」的型別為 double|陳小明"]


def test_clearing_one_field_returns_it_to_the_inferred_type_and_leaves_the_other(open_page, repo):
    # 逐欄位清除，不是全部重設：兩欄都指定過，清掉一欄，另一欄的指定還在。
    _listing_many(repo, {"p": "timeout: 5\nretries: 3\n"})
    _commit_listing(repo)
    page = _open_panel(open_page(), developer=True)
    for name in ("timeout", "retries"):
        page.select_option(f"{_row(name)} {_TYPE}", "float")
        page.wait_for_selector(f"{_row(name)} [data-testid='type-overridden']")

    page.click(f"{_row('timeout')} [data-testid='type-clear']")

    page.wait_for_selector(f"{_row('timeout')} [data-testid='type-overridden']", state="detached")
    assert page.eval_on_selector(f"{_row('timeout')} {_TYPE}", "e => e.value") == "int"
    assert page.input_value(_param_value("timeout")) == "5"
    assert page.is_visible(f"{_row('retries')} [data-testid='type-overridden']")


def test_a_normal_user_sees_the_specified_mark_but_cannot_change_or_clear(open_page, repo, api):
    # 僅開發者可改：一般使用者模式下選單與「清除」都不在 DOM 裡（不是停用）；標記看得到。
    _listing_many(repo, {"p": "timeout: 5\n"})
    _commit_listing(repo)
    _api_json(api, "POST", "/api/session", {"name": _NAME, "email": _EMAIL, "role": "developer"})
    _api_json(api, "POST", f"/api/configs/{_PARAM_UID}/types", {"path": "timeout", "type": "float"})
    # 頁面的瀏覽器以一般使用者身分開啟，直接進清單。
    page = open_page(identity={"name": _NAME, "email": _EMAIL, "role": "user"})
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")

    assert page.inner_text(f"{_row('timeout')} {_TYPE}") == "double"
    assert page.is_visible(f"{_row('timeout')} [data-testid='type-overridden']")
    assert page.query_selector(f"{_row('timeout')} select") is None
    assert page.query_selector("[data-testid='type-clear']") is None


def test_a_type_the_value_does_not_fit_is_refused_and_the_selector_goes_back(open_page, repo):
    # 把文字指定成 int：後端擋下，原因顯示在這一塊，選單回到原本的型別、沒有標成已指定。
    _listing_many(repo, {"p": "name: amr01\n"})
    _commit_listing(repo)
    page = _open_panel(open_page(), developer=True)

    page.select_option(f"{_row('name')} {_TYPE}", "int")

    error = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-error']"
    page.wait_for_selector(error, state="visible")
    assert "name" in page.inner_text(error) and "下一步" in page.inner_text(error)
    assert page.eval_on_selector(f"{_row('name')} {_TYPE}", "e => e.value") == "string"
    assert page.query_selector("[data-testid='type-overridden']") is None


def test_changing_a_type_with_unsaved_edits_is_held_back_so_they_are_not_lost(open_page, repo):
    # 改型別會重畫這張表；有還沒儲存的改動時先擋下、說清楚，不悄悄丟掉（不變式 2）。
    _listing_many(repo, {"p": "timeout: 5\nretries: 3\n"})
    _commit_listing(repo)
    page = _open_panel(open_page(), developer=True)
    page.fill(_param_value("retries"), "4")

    page.select_option(f"{_row('timeout')} {_TYPE}", "float")

    error = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-error']"
    page.wait_for_selector(error, state="visible")
    assert "還沒儲存" in page.inner_text(error)
    assert page.input_value(_param_value("retries")) == "4"
    assert page.eval_on_selector(f"{_row('timeout')} {_TYPE}", "e => e.value") == "int"
    assert not (repo / ".schemas" / f"{_PARAM_UID}.json").exists()


def test_list_elements_and_containers_have_no_type_selector(open_page, repo):
    _listing_many(repo, {"p": "frames:\n  - base\n  - odom\nrobot:\n  name: amr01\n"})
    page = _open_panel(open_page(), developer=True)

    assert page.query_selector(f"{_row('frames')} select") is None
    assert page.eval_on_selector(f"{_row('frames[0]')} {_TYPE}", "e => e.tagName") == "SPAN"
    assert page.query_selector(f"{_row('robot')} select") is None
    assert page.eval_on_selector(f"{_row('robot.name')} {_TYPE}", "e => e.tagName") == "SELECT"


def test_the_type_selector_is_hidden_while_read_only(open_page, repo):
    # 沒拿到編輯階段的分頁（即使身分是開發者）：選單不出現，型別以純文字顯示。
    _listing_many(repo, {"p": "timeout: 5\n"})
    holder = open_page()
    holder.click("[data-testid='role-toggle'] button[data-role='developer']")
    _enter_identity(holder)
    holder.wait_for_selector("[data-testid='promote-all']", state="visible")
    second = open_page(same_browser_as=holder)
    second.wait_for_selector("[data-testid='readonly-banner']", state="visible")

    second.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    second.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")

    assert second.is_hidden(f"{_row('timeout')} select")
    assert second.query_selector(f"{_row('timeout')} [data-testid='type-clear']") is None


# ── W3／W4 第 3 層規則的警告與略過理由（#44／#45）────────────────────────────
# 警告與硬擋在視覺上要分得開：硬擋阻止送出；警告允許帶理由通過，理由出現在變更紀錄。

_RULE_WARNINGS = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='rule-warnings']"
_LO_BELOW_HI = (
    '[[rules]]\nid = "lo-below-hi"\nleft = "lo"\nop = "<"\nright = "hi"\n'
    'message = "最小不能超過最大"\n'
)


def _listing_with_rules(repo, content: str, rules: str) -> dict:
    targets = _listing_many(repo, {"p": content})
    (repo / ".rules").mkdir(exist_ok=True)
    (repo / ".rules" / f"{_PARAM_UID}.toml").write_text(rules, encoding="utf-8")
    return targets


def test_breaking_a_rule_shows_a_warning_distinct_from_errors_and_asks_for_a_reason(
    open_page, repo
):
    # AC（#44）：警告不是紅色的硬擋——有自己的區塊、那一列標成警告；儲存停在那裡等理由。
    _listing_with_rules(repo, "lo: 1\nhi: 5\n", _LO_BELOW_HI)
    page = _open_panel(open_page())
    page.fill(_param_value("lo"), "9")

    _save(page)

    page.wait_for_selector(_RULE_WARNINGS, state="visible")
    said = page.inner_text(_RULE_WARNINGS)
    assert "lo-below-hi" in said and "最小不能超過最大" in said and "理由" in said
    assert page.get_attribute(_row("lo"), "data-warning") == "true"
    assert page.get_attribute(_row("lo"), "data-valid") == "true"  # 不是錯誤
    assert page.is_hidden(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-save-error']")
    assert not page.is_visible(_draft_dot(_PARAM_UID))  # 還沒存下


def test_a_reason_lets_the_draft_save_and_shows_in_the_history(open_page, repo):
    # AC（#45）：填了理由才能送出；理由出現在變更紀錄中。
    targets = _listing_with_rules(repo, "lo: 1\nhi: 5\n", _LO_BELOW_HI)
    page = _open_panel(open_page())
    page.fill(_param_value("lo"), "9")
    _save(page)
    page.wait_for_selector(_RULE_WARNINGS, state="visible")

    page.click("[data-testid='save-with-overrides']")  # 沒填理由
    page.wait_for_selector("[data-testid='rule-warnings'] [data-testid='override-error']")
    assert not page.is_visible(_draft_dot(_PARAM_UID))

    page.fill("[data-testid='override-reason-lo-below-hi']", "現場測試低速模式")
    page.click("[data-testid='save-with-overrides']")

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    assert "已略過" in page.inner_text(_RULE_WARNINGS)
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")
    assert targets["p"].read_text(encoding="utf-8") == "lo: 9\nhi: 5\n"

    page.click(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-history']")
    page.wait_for_selector("[data-testid='history-overrides']")
    assert "lo-below-hi" in page.inner_text("[data-testid='history-overrides']")
    assert "現場測試低速模式" in page.inner_text("[data-testid='history-overrides']")


def test_a_hard_error_stays_red_even_with_a_reason(open_page, repo):
    # override 後的修改仍走完整驗證：理由只略過警告，第 1 層的硬擋照樣是紅色錯誤。
    _listing_with_rules(repo, "lo: 1\nhi: 5\nflag: true\n", _LO_BELOW_HI)
    page = _open_panel(open_page())
    page.fill(_param_value("lo"), "9")
    _save(page)
    page.wait_for_selector(_RULE_WARNINGS, state="visible")
    page.fill("[data-testid='override-reason-lo-below-hi']", "理由")
    page.fill(_param_value("hi"), "")  # 空值是硬擋

    page.click("[data-testid='save-with-overrides']")

    page.wait_for_selector(f"{_row('hi')}[data-valid='false']")
    assert not page.is_visible(_draft_dot(_PARAM_UID))


def test_a_draft_adopted_with_a_rule_warning_asks_for_the_reason_when_reopened(
    open_page, repo, api
):
    # 「先納入、待修正」載入的草稿違反規則：重開面板就看到警告與理由欄，不必改值也能填理由儲存。
    _listing_with_rules(repo, "lo: 1\nhi: 5\n", _LO_BELOW_HI)
    _commit_listing(repo)
    (repo / "targets" / "p.yaml").write_text("lo: 9\nhi: 5\n", encoding="utf-8")
    _api_json(api, "POST", "/api/session", {"name": _NAME, "email": _EMAIL, "role": "user"})
    _api_json(api, "POST", f"/api/configs/{_PARAM_UID}/resolve", {"action": "adopt_draft"})
    page = open_page(identity={"name": _NAME, "email": _EMAIL, "role": "user"})
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(_RULE_WARNINGS, state="visible")

    page.fill("[data-testid='override-reason-lo-below-hi']", "現場就是這樣設的")
    page.click("[data-testid='save-with-overrides']")

    page.wait_for_function(
        "document.querySelector(\"[data-testid='rule-warnings']\").textContent.includes('已略過')"
    )
    page.click("[data-testid='promote-all']")
    page.wait_for_selector("[data-testid='promote-done']", state="visible")


# ── W3 儲存草稿（#21）────────────────────────────────────────────────────────
# 三段式：編輯 → 儲存（草稿）→ 進版。「儲存」只存為草稿：不記錄、不寫到目標（§7.4.3、T18）。

_SECOND_UID = "mfz3k9q2"


def _listing_many(repo, contents: dict) -> dict:
    """多筆條目（name → yaml 內容），uid 依序 mfz3k9q1、mfz3k9q2…；目標＝來源→一致。
    回 name → 目標路徑。"""
    # 目標放在白名單根（repo 夾具種的 `targets/`）底下、權限用目前的 uid／gid：進版會真的寫出到
    # 目標（白名單逃逸檢查＋chown），`deployed/`＋root:root 的樣本只夠比狀態、寫不出去。
    header = _LIST_HEADER.replace('owner = "root"', f'owner = "{os.getuid()}"').replace(
        'group = "root"', f'group = "{os.getgid()}"'
    )
    entries, targets = "", {}
    for index, (name, content) in enumerate(contents.items(), start=1):
        (repo / "files" / f"{name}.yaml").write_text(content, encoding="utf-8")
        target = repo / "targets" / f"{name}.yaml"
        target.write_text(content, encoding="utf-8")
        entries += _ENTRY.format(uid=f"mfz3k9q{index}", name=name, target=target, groups="")
        targets[name] = target
    (repo / "config-list.toml").write_text(header + entries, encoding="utf-8")
    return targets


def _save(page, uid: str = _PARAM_UID):
    # 多份可同時展開（#36），儲存要指名哪一塊——各區塊各自儲存。
    page.click(f"[data-testid='panel-{uid}'] [data-testid='panel-save']")


def _draft_dot(uid: str) -> str:
    return f"[data-testid='tree-item-{uid}'] [data-testid='draft-dot']"


def test_saving_stores_a_draft_marks_the_tree_and_leaves_the_target_alone(open_page, repo):
    # AC1／AC3／AC4：儲存 → 樹節點右側出現草稿標記（與左側狀態色點分開、狀態仍一致），目標檔未變。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())
    page.fill(_param_value("count"), "4")

    _save(page)

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    assert page.get_attribute(
        f"[data-testid='tree-item-{_PARAM_UID}'] [data-testid='status-dot']", "data-state"
    ) == "in_sync"
    assert page.is_visible(f"[data-testid='panel-draft-{_PARAM_UID}']")
    assert (repo / "deployed" / "p.yaml").read_text(encoding="utf-8") == "count: 3\n"


def test_two_configs_saved_as_drafts_leave_both_targets_unchanged(open_page, repo):
    # AC3：修改兩份 config 各存為草稿後，兩個目標檔案都沒改變；兩份都帶草稿標記。
    targets = _listing_many(repo, {"a": "count: 3\n", "b": "speed: 1.5\n"})
    page = _enter_identity(open_page())
    for uid, path, value in ((_PARAM_UID, "count", "4"), (_SECOND_UID, "speed", "2.5")):
        page.dblclick(f"[data-testid='tree-item-{uid}']")
        page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")
        page.fill(_param_value(path), value)
        _save(page, uid)
        page.wait_for_selector(_draft_dot(uid), state="visible")

    assert targets["a"].read_text(encoding="utf-8") == "count: 3\n"
    assert targets["b"].read_text(encoding="utf-8") == "speed: 1.5\n"


def test_a_draft_that_fails_layer_one_is_refused_with_the_line_and_the_fix(open_page, repo):
    # AC2：第 1 層驗證繞不過——來源裡納管時確認過的 `yes` 還在，存草稿時被擋、指出行號與建議，
    # 樹上不出現草稿標記。
    _listing_with(repo, "enabled: yes\ncount: 3\n")
    page = _open_panel(open_page())
    page.fill(_param_value("count"), "4")

    _save(page)

    page.wait_for_selector("[data-testid='panel-save-error']", state="visible")
    text = page.inner_text("[data-testid='panel-save-error']")
    assert "第 1 行" in text and "建議" in text
    assert page.query_selector(_draft_dot(_PARAM_UID)) is None


def test_save_is_disabled_until_something_valid_changed(open_page, repo):
    # W3：驗證未過時「儲存」停用；沒有改動也停用；改成合法值才啟用。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())

    assert page.is_disabled("[data-testid='panel-save']")
    page.fill(_param_value("count"), "1.5")
    assert page.is_disabled("[data-testid='panel-save']")
    page.fill(_param_value("count"), "4")
    assert page.is_enabled("[data-testid='panel-save']")


def test_reopening_a_config_shows_its_saved_draft_beside_the_source_value(open_page, repo):
    # 重開這份 config 看到的是存過的草稿（目前值 4），來源值仍是 3、該列標示已改動。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())
    page.fill(_param_value("count"), "4")
    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")

    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector(_param_value("count"))

    assert page.input_value(_param_value("count")) == "4"
    assert page.inner_text("[data-testid='param-count'] [data-testid='param-source-value']") == "3"
    assert page.get_attribute("[data-testid='param-count']", "data-changed") == "true"


def test_the_draft_marker_survives_a_reload(open_page, repo):
    # 草稿掛在 app 的階段上，不在瀏覽器：重新整理後樹上的標記還在（ADR-00000014）。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())
    page.fill(_param_value("count"), "4")
    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")

    page.reload()

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")


# ── 進版與捨棄變更（#22）─────────────────────────────────────────────────────
# 三段式的第三段：工具列「進版 (N)」把全部草稿一次驗證、記錄、寫出（整批原子）；「捨棄變更」清草稿、
# 回到來源內容（全域／單一），經確認對話框。


def _save_draft_for(page, uid: str, path: str, value: str) -> None:
    page.dblclick(f"[data-testid='tree-item-{uid}']")
    page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")
    page.fill(_param_value(path), value)
    _save(page, uid)
    page.wait_for_selector(_draft_dot(uid), state="visible")


def _git_log(repo, count: int) -> list:
    return subprocess.run(
        ["git", "-C", str(repo), "log", f"-{count}", "--format=%s|%an"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()


def test_the_promote_button_shows_the_pending_draft_count(open_page, repo):
    # AC1：工具列全域按鈕顯示待進版草稿數；無草稿時停用。
    _listing_many(repo, {"a": "count: 3\n", "b": "speed: 1.5\n"})
    page = _enter_identity(open_page())

    assert page.inner_text("[data-testid='promote-all']") == "進版 (0)"
    assert page.is_disabled("[data-testid='promote-all']")
    _save_draft_for(page, _PARAM_UID, "count", "4")
    _save_draft_for(page, _SECOND_UID, "speed", "2.5")

    assert page.inner_text("[data-testid='promote-all']") == "進版 (2)"
    assert page.is_enabled("[data-testid='promote-all']")


def test_pressing_promote_sends_every_draft_at_once_and_records_one_change_each(open_page, repo):
    # AC2：按一次兩份一起送出——兩個目標都改變、各一筆變更紀錄（作者＝身分）、草稿清空。
    targets = _listing_many(repo, {"a": "count: 3\n", "b": "speed: 1.5\n"})
    page = _enter_identity(open_page())
    _save_draft_for(page, _PARAM_UID, "count", "4")
    _save_draft_for(page, _SECOND_UID, "speed", "2.5")

    page.click("[data-testid='promote-all']")

    page.wait_for_selector("[data-testid='promote-done']", state="visible")
    assert page.inner_text("[data-testid='promote-all']") == "進版 (0)"
    assert page.query_selector(_draft_dot(_PARAM_UID)) is None
    assert targets["a"].read_text(encoding="utf-8") == "count: 4\n"
    assert targets["b"].read_text(encoding="utf-8") == "speed: 2.5\n"
    assert _git_log(repo, 2) == [
        f"cfg({_SECOND_UID}): 修改參數（b@amr01）|{_NAME}",
        f"cfg({_PARAM_UID}): 修改參數（a@amr01）|{_NAME}",
    ]


def test_a_promote_that_fails_names_the_config_and_writes_nothing(open_page, repo):
    # AC3：任一份沒過整批不進版，橫幅指名是哪一份（這裡：進版期間該 config 已被解除管理——後端
    # 唯一能在儲存後才變壞的路徑；參數層級的指名走同一個橫幅、同一種結構化錯誤形），
    # 目標不變、草稿保留。
    targets = _listing_many(repo, {"a": "count: 3\n", "b": "speed: 1.5\n"})
    page = _enter_identity(open_page())
    _save_draft_for(page, _PARAM_UID, "count", "4")
    _save_draft_for(page, _SECOND_UID, "speed", "2.5")
    _listing_many(repo, {"a": "count: 3\n"})  # b 不再受管

    page.click("[data-testid='promote-all']")

    page.wait_for_selector("[data-testid='promote-error']", state="visible")
    assert _SECOND_UID in page.inner_text("[data-testid='promote-error']")
    assert targets["a"].read_text(encoding="utf-8") == "count: 3\n"  # 沒過的那份擋住了全部
    assert page.inner_text("[data-testid='promote-all']") == "進版 (2)"


def test_discarding_all_drafts_asks_first_then_clears_them_leaving_the_target(open_page, repo):
    # AC4（全域）：捨棄變更先確認；取消什麼都不變，確認後草稿消失、來源與目標皆未改變。
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())
    page.fill(_param_value("count"), "4")
    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")

    page.click("[data-testid='discard-all']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-cancel']")
    assert page.is_visible(_draft_dot(_PARAM_UID))

    page.click("[data-testid='discard-all']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="detached")
    assert page.inner_text("[data-testid='promote-all']") == "進版 (0)"
    assert (repo / "files" / "p.yaml").read_text(encoding="utf-8") == "count: 3\n"
    assert (repo / "deployed" / "p.yaml").read_text(encoding="utf-8") == "count: 3\n"


def test_discarding_one_config_leaves_the_other_draft_and_shows_the_source_again(open_page, repo):
    # AC4（單一）：面板的「捨棄變更」只清這份；另一份的草稿還在；畫面回到來源內容。
    _listing_many(repo, {"a": "count: 3\n", "b": "speed: 1.5\n"})
    page = _enter_identity(open_page())
    _save_draft_for(page, _SECOND_UID, "speed", "2.5")
    _save_draft_for(page, _PARAM_UID, "count", "4")

    page.click("[data-testid='panel-discard']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(_draft_dot(_PARAM_UID), state="detached")
    assert page.is_visible(_draft_dot(_SECOND_UID))
    page.wait_for_function(
        "() => document.querySelector(\"[data-testid='param-count'] [data-testid='param-value']\")"
        "?.value === '3'"
    )
    assert page.inner_text("[data-testid='promote-all']") == "進版 (1)"


# ── W4 歷史（#25／#26）──────────────────────────────────────────────────────
# 使用者看到的是行為，不是代號（§7.6.1）；差異以參數為單位（§7.6.2）。歷史用真的納管＋進版造出來
# （走端點、不經頁面——這幾則要觀察的是歷史檢視，不是納管與進版的畫面）。


# 以端點種資料用的「瀏覽器」：自己一罐 cookie，與頁面的瀏覽器各自有身分（#288）。
_API_BROWSER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def _api_json(api, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api}{path}", data=data, headers={"content-type": "application/json"}, method=method
    )
    with _API_BROWSER.open(request, timeout=_STARTUP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _history_via_api(api, browse_root, contents: list) -> dict:
    """納管 `contents[0]`、再依序把每個後續內容以草稿→進版寫進歷史；回納管的條目。"""
    _api_json(api, "POST", "/api/session", {"name": _NAME, "email": _EMAIL, "role": "developer"})
    source = browse_root / "hist.yaml"
    source.write_text(contents[0], encoding="utf-8")
    entry = _api_json(api, "POST", "/api/configs", {"source_path": str(source), "format": "yaml"})
    for content in contents[1:]:
        pairs = [line.split(":") for line in content.strip().split("\n")]
        edits = {key: int(value) for key, value in pairs}
        _api_json(api, "POST", "/api/drafts", {"uid": entry["uid"], "edits": edits})
        _api_json(api, "POST", "/api/promote", {})
    return entry


def _open_history(page, uid: str, expect_entries: bool = True):
    page.wait_for_selector("[data-testid='config-tree']", state="visible")
    page.dblclick(f"[data-testid='tree-item-{uid}']")
    page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")
    page.get_by_role("button", name="歷史").click()
    page.wait_for_selector(f"[data-testid='history-{uid}']", state="visible")
    if expect_entries:  # 剛納管、沒改過的那一份，預設檢視是空的（見空狀態的規格）
        page.wait_for_selector("[data-testid^='history-entry-']")
    return page


def test_history_lists_each_change_as_a_behaviour_with_author_and_time(open_page, browse_root, api):
    # #25：每筆顯示行為描述（修改參數）、作者與時間；列表上不出現 cfg／revert／import 這些代號。
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n", "count: 3\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])

    entries = page.eval_on_selector_all(
        "[data-testid^='history-entry-']",
        "els => els.map(e => ({kind: e.querySelector('.history-kind').textContent,"
        " author: e.querySelector('[data-testid=\"history-author\"]').textContent,"
        " time: e.querySelector('[data-testid=\"history-time\"]').textContent}))",
    )
    # 預設只看內容變更，納管那筆不列。
    assert [e["kind"] for e in entries] == ["修改參數", "修改參數"]
    assert all(e["author"] == _NAME and e["time"] for e in entries)
    text = page.inner_text("[data-testid='history-list']")
    assert not any(code in text for code in ("cfg", "revert", "import", "adopt", "meta"))


def test_history_filter_all_shows_onboarding_as_a_behaviour(open_page, browse_root, api):
    # #25：篩選器以行為描述呈現：「全部」列出納入管理那筆，仍不出現代號。
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])

    page.click("[data-testid='history-filter'] button[data-filter='all']")
    page.wait_for_selector("text=納入管理")

    kinds = page.eval_on_selector_all(
        "[data-testid^='history-entry-'] .history-kind", "els => els.map(e => e.textContent)"
    )
    assert kinds == ["修改參數", "納入管理"]
    assert "import" not in page.inner_text("[data-testid='history-list']")


def test_selecting_a_change_shows_which_parameters_differ_from_the_current_version(
    open_page, browse_root, api
):
    # #26：差異以參數為單位——哪個參數從什麼變成什麼；沒變的標為相同，不是文字行差異。
    entry = _history_via_api(
        api, browse_root, ["count: 1\nspeed: 5\n", "count: 2\nspeed: 5\n", "count: 3\nspeed: 5\n"]
    )
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])
    older = page.eval_on_selector_all(
        "[data-testid^='history-entry-']", "els => els[els.length - 1].dataset.testid"
    )

    page.click(f"[data-testid='{older}']")
    page.wait_for_selector("[data-testid='diff-row-count']")

    assert page.get_attribute("[data-testid='diff-row-count']", "data-change") == "changed"
    assert page.inner_text("[data-testid='diff-row-count'] [data-testid='diff-from']") == "2"
    assert page.inner_text("[data-testid='diff-row-count'] [data-testid='diff-to']") == "3"
    assert page.get_attribute("[data-testid='diff-row-speed']", "data-change") == "same"
    assert "1 個參數不同" in page.inner_text("[data-testid='history-diff-summary']")


def test_returning_from_history_shows_the_parameter_table_again(open_page, browse_root, api):
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])

    page.get_by_role("button", name="返回欄位表").click()

    page.wait_for_selector(f"[data-testid='panel-{entry['uid']}']", state="visible")
    assert page.query_selector(f"[data-testid='history-{entry['uid']}']") is None


# ── 退版按鈕（#27）───────────────────────────────────────────────────────────
# 列出歷史 → 選版本 → 看差異 → 「退回此版本」→ 確認對話框 → 執行（§5.3 圖 7）。


def _select_first_version(page):
    """切到「全部」、點最舊那筆（納入管理＝第一版）、等差異出現。"""
    page.click("[data-testid='history-filter'] button[data-filter='all']")
    page.wait_for_selector("text=納入管理")
    oldest = page.eval_on_selector_all(
        "[data-testid^='history-entry-']", "els => els[els.length - 1].dataset.testid"
    )
    page.click(f"[data-testid='{oldest}']")
    page.wait_for_selector("[data-testid='history-revert']")


def test_reverting_from_history_puts_the_target_back_to_the_first_version(
    open_page, browse_root, api
):
    # AC2／AC3：連續修改三次後可退回第一版，目標內容確實回到第一版；歷史多一筆「退回舊版本」。
    versions = ["count: 1\n", "count: 2\n", "count: 3\n", "count: 4\n"]
    entry = _history_via_api(api, browse_root, versions)
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])
    _select_first_version(page)

    page.click("[data-testid='history-revert']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='history-notice']:not([hidden])")
    assert pathlib.Path(entry["target"]).read_text(encoding="utf-8") == "count: 1\n"
    page.click("[data-testid='history-filter'] button[data-filter='all']")
    page.wait_for_selector("text=退回舊版本")
    assert "退回到版本" in page.inner_text("[data-testid='history-list']")


def test_cancelling_the_revert_confirmation_changes_nothing(open_page, browse_root, api):
    # AC4：破壞性操作二次確認——取消什麼都不變（與捨棄變更共用同一個對話框）。
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])
    _select_first_version(page)

    page.click("[data-testid='history-revert']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    assert "退回此版本" in page.inner_text("[data-testid='confirm-title']")
    page.click("[data-testid='confirm-cancel']")

    assert pathlib.Path(entry["target"]).read_text(encoding="utf-8") == "count: 2\n"
    assert page.query_selector("[data-testid='history-notice']:not([hidden])") is None


def test_reverting_while_a_draft_is_pending_shows_the_reason(open_page, browse_root, api):
    # 後端擋（409：草稿以退版前的來源為底）→ 原樣顯示原因與下一步，目標不動。
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n"])
    _api_json(api, "POST", "/api/drafts", {"uid": entry["uid"], "edits": {"count": 9}})
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])
    _select_first_version(page)

    page.click("[data-testid='history-revert']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='history-revert-error']")
    assert "草稿" in page.inner_text("[data-testid='history-revert-error']")
    assert pathlib.Path(entry["target"]).read_text(encoding="utf-8") == "count: 2\n"


def test_reverting_says_the_schema_goes_back_too_before_doing_it(open_page, browse_root, api):
    # #39 的定案：退版時 schema 跟著回到那一版。這會動到開發者設的限制，所以確認框先說、不悄悄發生。
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n"])
    _api_json(api, "POST", f"/api/configs/{entry['uid']}/schema")
    _api_json(api, "POST", "/api/drafts", {"uid": entry["uid"], "edits": {"count": 3}})
    _api_json(api, "POST", "/api/promote", {})
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])
    # 最舊的那一筆內容變更（count: 2）是在產生 schema 之前——那一版當時還沒有 schema。
    page.click("[data-testid='history-list'] li[data-testid^='history-entry-']:last-child")
    page.wait_for_selector("[data-testid='history-revert']")

    page.click("[data-testid='history-revert']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    assert "不再有 schema" in page.inner_text("[data-testid='confirm-body']")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='history-notice']:not([hidden])")
    assert "沒有 schema" in page.inner_text("[data-testid='history-notice']")
    assert _api_json(api, "GET", f"/api/configs/{entry['uid']}")["schema"] is None


def test_a_revert_that_leaves_the_schema_alone_does_not_mention_it(open_page, browse_root, api):
    entry = _history_via_api(api, browse_root, ["count: 1\n", "count: 2\n", "count: 3\n"])
    page = _open_history(open_page(identity=_REMEMBERED), entry["uid"])
    _select_first_version(page)

    page.click("[data-testid='history-revert']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")

    assert "schema" not in page.inner_text("[data-testid='confirm-body']")
    page.click("[data-testid='confirm-cancel']")


# ── W5 差異檢視（#30）────────────────────────────────────────────────────────
# 只在偏離時出現：橫幅說明目標被介面外修改、沒有紀錄與作者；來源與目標以參數為單位並排。


def _drift(repo, source: str, target: str) -> None:
    """一筆條目：來源複本是 source、目標檔在介面外被改成 target → 偏離。"""
    _listing_with(repo, source)
    (repo / "deployed" / "p.yaml").write_text(target, encoding="utf-8")


def test_a_drifted_config_shows_a_banner_saying_the_change_bypassed_the_interface(open_page, repo):
    # AC1：橫幅說明該修改未經介面進行、沒有對應的變更紀錄與作者資訊；一致的 config 沒有橫幅。
    _drift(repo, "count: 3\n", "count: 9\n")
    page = _open_panel(open_page())

    banner = page.inner_text("[data-testid='panel-mfz3k9q1'] [data-testid='drift-banner']")
    assert "未經介面" in banner and "作者" in banner


def test_an_in_sync_config_has_no_drift_banner(open_page, repo):
    _listing_with(repo, "count: 3\n")
    page = _open_panel(open_page())

    assert page.query_selector("[data-testid='drift-banner']") is None


def test_the_diff_view_compares_source_and_target_side_by_side_per_parameter(open_page, repo):
    # AC2／AC3／AC4：左來源、右目標，逐參數一列；沒變的標 same、改了的標 changed、目標多出的標
    # added——與 W4 歷史差異同一套 data-change 與顏色語言。
    _drift(repo, "count: 3\nspeed: 1.5\n", "count: 9\nspeed: 1.5\nextra: 1\n")
    page = _open_panel(open_page())

    page.click("[data-testid='panel-diff']")
    page.wait_for_selector("[data-testid='diff-mfz3k9q1']", state="visible")

    assert page.is_visible("[data-testid='diff-source']")
    assert page.is_visible("[data-testid='diff-target']")
    count = "[data-testid='diff-row-count']"
    assert page.get_attribute(count, "data-change") == "changed"
    assert page.inner_text(f"{count} [data-testid='diff-source-value']") == "3"
    assert page.inner_text(f"{count} [data-testid='diff-target-value']") == "9"
    assert page.get_attribute("[data-testid='diff-row-speed']", "data-change") == "same"
    assert page.get_attribute("[data-testid='diff-row-extra']", "data-change") == "added"
    assert "2 個參數不同" in page.inner_text("[data-testid='diff-summary']")
    assert page.is_visible("[data-testid='diff-mfz3k9q1'] [data-testid='drift-banner']")


def test_returning_from_the_diff_view_shows_the_parameter_table(open_page, repo):
    _drift(repo, "count: 3\n", "count: 9\n")
    page = _open_panel(open_page())
    page.click("[data-testid='panel-diff']")
    page.wait_for_selector("[data-testid='diff-mfz3k9q1']", state="visible")

    page.get_by_role("button", name="返回欄位表").click()

    page.wait_for_selector("[data-testid='panel-mfz3k9q1']", state="visible")
    assert page.query_selector("[data-testid='diff-mfz3k9q1']") is None


# ── W5 三種處置（#31）────────────────────────────────────────────────────────
# 偏離不自動處置：三個出口由人選、後果先說；覆蓋後回到一致、納入後來源更新且有採納紀錄、先納入
# 載成草稿並警告。目標放在白名單根底下、權限用目前 uid／gid（寫出要過得了白名單與 chown）。


def _drift_in_root(repo, source: str, target_text: str):
    """一筆條目（uid mfz3k9q1，目標在白名單根 targets/ 底下）；目標在介面外被改成 target_text。"""
    targets = _listing_many(repo, {"a": source})
    targets["a"].write_text(target_text, encoding="utf-8")
    return targets["a"]


def _open_diff(page):
    _open_panel(page)
    page.click("[data-testid='panel-diff']")
    page.wait_for_selector(f"[data-testid='diff-{_PARAM_UID}']", state="visible")
    return page


def test_the_three_outlets_state_their_consequences_before_being_pressed(open_page, repo):
    # A3：三個選項的後果在按下之前就說明清楚。
    _drift_in_root(repo, "count: 3\n", "count: 9\n")
    page = _open_diff(open_page())

    text = page.inner_text(".resolve-actions")
    assert "以來源覆蓋目標" in text and "捨棄現場修改" in text
    assert "將目標現況納入來源" in text and "含非法值會被拒" in text
    assert "先納入、待修正" in text and "進版前須改正" in text


def test_overwrite_from_the_diff_view_puts_the_source_back_and_returns_to_in_sync(open_page, repo):
    # AC1／AC5：以來源覆蓋目標（經確認）→ 目標回到來源、樹上狀態回到一致。
    target = _drift_in_root(repo, "count: 3\n", "count: 9\n")
    page = _open_diff(open_page())

    page.click("[data-testid='resolve-overwrite']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='promote-done']", state="visible")
    assert target.read_text(encoding="utf-8") == "count: 3\n"
    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}'] [data-state='in_sync']")


def test_adopt_from_the_diff_view_updates_the_source_and_records_an_adoption(open_page, repo):
    # AC2／AC5：將目標現況納入來源 → 來源複本更新、狀態一致、歷史多一筆「採納現場調整」。
    _drift_in_root(repo, "count: 3\n", "count: 9\n")
    page = _open_diff(open_page())

    page.click("[data-testid='resolve-adopt']")

    page.wait_for_selector("[data-testid='promote-done']", state="visible")
    assert (repo / "files" / "a.yaml").read_text(encoding="utf-8") == "count: 9\n"
    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}'] [data-state='in_sync']")
    page.wait_for_selector(f"[data-testid='panel-{_PARAM_UID}']", state="visible")
    page.get_by_role("button", name="歷史").click()
    page.wait_for_selector("text=採納現場調整")


def test_adopting_an_invalid_target_is_refused_with_the_reason(open_page, repo):
    # AC2：含非法值則被拒並說明原因（行號＋建議＋改走先納入待修正）；來源不動。
    _drift_in_root(repo, "count: 3\n", "enabled: yes\ncount: 9\n")
    page = _open_diff(open_page())

    page.click("[data-testid='resolve-adopt']")

    page.wait_for_selector("[data-testid='resolve-error']", state="visible")
    text = page.inner_text("[data-testid='resolve-error']")
    assert "第 1 行" in text and "先納入、待修正" in text
    assert (repo / "files" / "a.yaml").read_text(encoding="utf-8") == "count: 3\n"


def test_adopt_draft_loads_the_target_as_a_draft_and_warns(open_page, repo):
    # AC3：先納入、待修正 → 目標現況成為草稿（樹上有標記、欄位表顯示現況值）、警告列出問題。
    _drift_in_root(repo, "count: 3\n", "enabled: yes\ncount: 9\n")
    page = _open_diff(open_page())

    page.click("[data-testid='resolve-adopt-draft']")
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='adopt-draft-notice']", state="visible")
    assert "進版前須改正" in page.inner_text("[data-testid='adopt-draft-notice']")
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")
    assert page.input_value(_param_value("count")) == "9"
    assert (repo / "files" / "a.yaml").read_text(encoding="utf-8") == "count: 3\n"


def test_editing_a_target_behind_the_interface_shows_the_right_diff(open_page, repo):
    # AC4：以 vim 直接改目標檔 → 「檢查差異」後判定為偏離，且差異內容正確。
    targets = _listing_many(repo, {"a": "count: 3\n"})
    page = _open_panel(open_page())
    assert page.query_selector("[data-testid='drift-banner']") is None

    targets["a"].write_text("count: 4\n", encoding="utf-8")  # 介面外的修改
    page.get_by_role("button", name="檢查差異").click()

    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}'] [data-state='drift']")
    page.dblclick(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.wait_for_selector("[data-testid='panel-diff']")
    page.click("[data-testid='panel-diff']")
    page.wait_for_selector("[data-testid='diff-row-count']")
    target_value = "[data-testid='diff-row-count'] [data-testid='diff-target-value']"
    assert page.inner_text(target_value) == "4"


def test_group_nodes_roll_up_the_most_severe_state_of_their_children(open_page, listing):
    # AC6：父節點彙總子節點最嚴重的狀態（偏離 > 未部署 > 一致）：navigation 只有 a（一致）→ 一致；
    # 未分群有 b（偏離）與 c（未部署）→ 偏離。
    listing("a", "b", "c")
    page = _enter_identity(open_page())

    dot = "[data-testid='group-status-dot']"
    nav = page.get_attribute(f"[data-testid='tree-group-navigation'] {dot}", "data-state")
    ungrouped = page.get_attribute(f"[data-testid='tree-group-ungrouped'] {dot}", "data-state")
    assert (nav, ungrouped) == ("in_sync", "drift")


# ── W6 單一編輯階段（#33）────────────────────────────────────────────────────
# 一次只允許一個編輯階段（§7.2.2、ADR-00000014）：第二個分頁唯讀、指名持有者與開始時間；會寫入的
# 控制項不出現（不是停用）；不提供強制接管。


def test_a_second_tab_is_read_only_and_names_the_holder(open_page, listing):
    # 同一個人開第二個分頁：第一個持有編輯階段，第二個唯讀、橫幅指名持有者與開始時間。
    listing("a")
    holder = _enter_identity(open_page())
    holder.wait_for_selector("[data-testid='promote-all']", state="visible")

    second = open_page(same_browser_as=holder)  # 同一瀏覽器的分頁：有身分 → 進清單 → 取階段被拒
    second.wait_for_selector("[data-testid='readonly-banner']", state="visible")

    banner = second.inner_text("[data-testid='readonly-banner']")
    assert "陳小明" in banner and "ming@example.com" in banner and "起編輯中" in banner
    assert second.is_hidden("[data-testid='promote-all']")
    second.dblclick("[data-testid='tree-item-mfz3k9q1']")
    second.wait_for_selector("[data-testid='panel-mfz3k9q1']", state="visible")
    assert second.is_hidden("[data-testid='panel-save']")
    assert holder.is_visible("[data-testid='promote-all']")  # 持有者不受影響


def test_entering_another_identity_while_someone_holds_the_session_is_read_only(open_page, listing):
    # 另一個瀏覽器（另一個人）在有人持有階段時進來：先看到身分輸入頁（身分不沿用別人的，#288），
    # 填了自己的身分後後端擋下進入編輯（409），前端以唯讀進清單並說明是誰；重新整理仍回身分表單。
    listing("a")
    _enter_identity(open_page())
    second = open_page()
    second.wait_for_selector("[data-testid='identity-form']", state="visible")
    _fill_identity(second, "林巡檢")

    second.wait_for_selector("[data-testid='readonly-banner']", state="visible")
    assert "陳小明" in second.inner_text("[data-testid='readonly-banner']")
    assert second.inner_text("[data-testid='current-role']") == "唯讀"
    assert second.is_hidden("[data-testid='promote-all']")

    second.reload()
    second.wait_for_selector("[data-testid='identity-form']", state="visible")


def test_when_the_holder_leaves_the_next_person_can_edit(open_page, listing):
    # 正常關閉頁面主動釋放（pagehide）→ 下一個人重新整理、填自己的身分即可取得編輯階段。
    listing("a")
    holder = _enter_identity(open_page())
    holder.wait_for_selector("[data-testid='promote-all']", state="visible")
    second = open_page()
    second.wait_for_selector("[data-testid='identity-form']", state="visible")
    _fill_identity(second, "林巡檢")
    second.wait_for_selector("[data-testid='readonly-banner']", state="visible")

    holder.evaluate("window.dispatchEvent(new Event('pagehide'))")
    second.wait_for_timeout(300)
    second.reload()

    second.wait_for_selector("[data-testid='identity-form']", state="visible")
    _fill_identity(second, "林巡檢")
    second.wait_for_selector("[data-testid='config-tree']", state="visible")
    second.wait_for_selector("[data-testid='promote-all']", state="visible")
    assert second.is_hidden("[data-testid='readonly-banner']")
    assert second.inner_text("[data-testid='current-role']").startswith("林巡檢")


# ── W2 解除納管的介面入口（#287）────────────────────────────────────────────
# 後端端點是 #28；這裡是介面上的入口。不可逆操作先二次確認、寫出實際會發生的事（設計原則 2）。

_UNMANAGE = f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-unmanage']"


def _commit_listing(repo) -> None:
    """把夾具直接寫進 repo 的清單與來源複本提交進 git——真實流程裡納管一定有這一筆，
    解除納管要從索引拿掉來源複本，沒被追蹤的檔案拿不掉。"""
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=seed", "-c", "user.email=s@e.x",
         "commit", "-q", "-m", "chore: 種下夾具"],
        check=True,
    )


def test_unmanaging_asks_first_then_removes_the_config_and_keeps_the_target(open_page, repo):
    targets = _listing_many(repo, {"p": "count: 3\n", "q": "count: 5\n"})
    _commit_listing(repo)
    page = _open_panel(open_page())

    page.click(_UNMANAGE)
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    said = page.inner_text("[data-testid='confirm-body']")
    assert "目標檔案" in said and "保留" in said and "歷史" in said
    page.click("[data-testid='confirm-cancel']")
    assert page.is_visible(f"[data-testid='tree-item-{_PARAM_UID}']")

    page.click(_UNMANAGE)
    page.wait_for_selector("[data-testid='confirm-dialog'][open]")
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}']", state="detached")
    assert page.query_selector(f"[data-testid='panel-{_PARAM_UID}']") is None
    assert page.is_visible(f"[data-testid='tree-item-{_SECOND_UID}']")  # 另一份不受影響
    assert str(targets["p"]) in page.inner_text("[data-testid='promote-done']")
    assert targets["p"].read_text(encoding="utf-8") == "count: 3\n"  # 目標一個位元組都不動
    assert _PARAM_UID not in (repo / "config-list.toml").read_text(encoding="utf-8")
    assert _git_log(repo, 1) == [f"unmanage({_PARAM_UID}): 解除管理（p@amr01）|陳小明"]


def test_unmanaging_a_config_with_a_pending_draft_is_refused_with_the_reason(open_page, repo):
    # 有未進版的草稿時擋下、說明要先進版或捨棄；項目仍在樹上。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page())
    page.fill(_param_value("count"), "4")
    _save(page)
    page.wait_for_selector(_draft_dot(_PARAM_UID), state="visible")

    page.click(_UNMANAGE)
    page.click("[data-testid='confirm-ok']")

    page.wait_for_selector("[data-testid='promote-error']", state="visible")
    assert "草稿" in page.inner_text("[data-testid='promote-error']")
    assert page.is_visible(f"[data-testid='tree-item-{_PARAM_UID}']")
    assert _PARAM_UID in (repo / "config-list.toml").read_text(encoding="utf-8")


def test_a_raw_config_can_be_unmanaged_too(open_page, repo):
    # 入口在區塊標頭：沒有欄位表的 raw 也拿得到。
    _listing_with(repo, "whatever: [not parsed\n", fmt="raw")
    page = _open_panel(open_page())

    assert page.is_visible(_UNMANAGE)


def test_the_unmanage_entry_is_absent_while_read_only(open_page, listing):
    # 沒拿到編輯階段的分頁是唯讀：會寫入的控制項不出現（不是停用）。
    listing("a")
    holder = _enter_identity(open_page())
    holder.wait_for_selector("[data-testid='promote-all']", state="visible")
    second = open_page(same_browser_as=holder)
    second.wait_for_selector("[data-testid='readonly-banner']", state="visible")

    second.dblclick("[data-testid='tree-item-mfz3k9q1']")
    second.wait_for_selector("[data-testid='panel-mfz3k9q1']", state="visible")

    assert second.is_hidden("[data-testid='panel-mfz3k9q1'] [data-testid='panel-unmanage']")
    assert holder.is_hidden("[data-testid='readonly-banner']")


def test_reloading_the_holders_own_tab_keeps_editing_and_shows_who_it_is(open_page, listing):
    # 人工驗證 U38：重新整理時舊頁面先釋放、新頁面再取回。取回後標頭要是身分與角色、不是「唯讀」，
    # 沒有唯讀橫幅，會寫入的入口還在。再重新整理一次也一樣。
    listing("a")
    page = open_page()
    page.click("[data-testid='role-toggle'] button[data-role='developer']")
    _enter_identity(page)
    page.wait_for_selector("[data-testid='promote-all']", state="visible")

    for _ in range(2):
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")  # 卸載時的釋放先送達
        page.wait_for_timeout(300)
        page.reload()
        page.wait_for_selector("[data-testid='config-tree']", state="visible")
        page.wait_for_selector("[data-testid='promote-all']", state="visible")

        assert page.inner_text("[data-testid='current-role']") == f"{_NAME}・開發者"
        assert page.is_hidden("[data-testid='readonly-banner']")
        assert page.get_attribute("body", "data-readonly") == "false"
        assert page.is_visible("[data-testid='open-whitelist']")


def test_getting_the_session_back_puts_the_identity_back_in_the_header(open_page, listing):
    # 轉唯讀時標頭改成「唯讀」；之後只要又拿到編輯階段，標頭就要改回身分——否則畫面說唯讀、
    # 實際上卻可以編輯（人工驗證 U38 看到的就是這個）。
    listing("a")
    page = _enter_identity(open_page())
    page.wait_for_selector("[data-testid='promote-all']", state="visible")

    page.evaluate("setReadonly(null, null, '暫時取不到')")
    assert page.inner_text("[data-testid='current-role']") == "唯讀"
    page.evaluate("setEditing()")

    assert page.inner_text("[data-testid='current-role']") == f"{_NAME}・一般使用者"
    assert page.is_hidden("[data-testid='readonly-banner']")


def test_a_release_that_arrives_after_the_reload_does_not_take_the_session_away(
    open_page, listing, api
):
    # 釋放晚到的那一種順序：新頁面先拿存著的識別碼接續成功，舊頁面的釋放才到。接續時識別碼已換新，
    # 晚到的釋放帶的是舊的——階段還在，這個分頁繼續可以編輯。
    listing("a")
    page = _enter_identity(open_page())
    page.wait_for_selector("[data-testid='promote-all']", state="visible")
    old_token = page.evaluate("sessionStorage.getItem('cm.session.token')")
    # 讓卸載時的釋放「還在路上」：頁面不送（記憶體裡的識別碼清掉，存著的那份還在），稍後由
    # 這裡代它送出——等於它晚到。
    page.evaluate("sessionToken = null")

    page.reload()
    # 等新頁面**接續完成**（存著的識別碼換成新的）再讓釋放「到」：進版鈕在接續的回應回來之前就
    # 看得到，只等它的話，讀識別碼時頁面可能還沒收到新的那一個（CI 上實際撞到過）。
    page.wait_for_function(
        "old => sessionStorage.getItem('cm.session.token') !== old", arg=old_token
    )
    late = _api_json(api, "POST", "/api/session/lock/release", {"token": old_token})

    assert late == {"released": False}
    assert _api_json(api, "GET", "/api/session/lock")["held"] is True
    assert page.evaluate("sessionStorage.getItem('cm.session.token')") != old_token
    assert page.evaluate("renewSession(sessionToken)") is True
    assert page.get_attribute("body", "data-readonly") == "false"


# ── 人工驗證：檢查差異的回饋、窄視窗排版 ─────────────────────────────────────

_NARROW = 390  # 手機直立的寬度；工具列在這個寬度下不該拆字、不該讓頁面左右捲


def test_checking_for_differences_says_when_it_ran_and_what_it_found(open_page, repo):
    # 全部一致時按「檢查差異」原本什麼都沒變，看不出有沒有執行。現在說出檢查了幾份、結果如何。
    targets = _listing_many(repo, {"p": "count: 3\n", "q": "count: 5\n"})
    page = _enter_identity(open_page())
    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}']")

    page.click("[data-testid='rescan']")
    page.wait_for_selector("[data-testid='scan-status'][data-state='done']")
    said = page.inner_text("[data-testid='scan-status']")
    assert "2 份" in said and "全部一致" in said

    targets["p"].write_text("count: 9\n", encoding="utf-8")
    targets["q"].unlink()
    page.click("[data-testid='rescan']")
    page.wait_for_function(
        "document.querySelector(\"[data-testid='scan-status']\").textContent.includes('偏離')"
    )
    said = page.inner_text("[data-testid='scan-status']")
    assert "1 份偏離" in said and "1 份未部署" in said and "全部一致" not in said


def test_a_failed_check_says_it_failed_instead_of_looking_finished(open_page, repo):
    _listing_many(repo, {"p": "count: 3\n"})
    page = _enter_identity(open_page())
    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}']")
    page.route("**/api/configs", lambda route: route.abort())

    page.click("[data-testid='rescan']")

    page.wait_for_selector("[data-testid='scan-status'][data-state='failed']")
    assert "失敗" in page.inner_text("[data-testid='scan-status']")


def test_the_toolbar_fits_a_narrow_window_without_sideways_scrolling(open_page, repo):
    # 窄視窗下工具列原本逐字換行、按鈕超出畫面要橫向捲動。現在整列換行、按鈕文字不拆字、
    # 頁面不必左右捲。
    _listing_many(repo, {"p": "count: 3\n"})
    page = open_page()
    page.set_viewport_size({"width": _NARROW, "height": 800})
    page.click("[data-testid='role-toggle'] button[data-role='developer']")
    _enter_identity(page)
    page.wait_for_selector("[data-testid='promote-all']", state="visible")

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    heights = page.eval_on_selector_all(
        "header button", "els => els.filter(e => e.offsetParent).map(e => e.offsetHeight)"
    )
    right_edges = page.eval_on_selector_all(
        "header button",
        "els => els.filter(e => e.offsetParent).map(e => e.getBoundingClientRect().right)",
    )

    assert overflow <= 0
    assert len(set(heights)) == 1  # 每顆按鈕都是一行高：文字沒有被拆成好幾行
    assert max(right_edges) <= _NARROW


# ── W2 屬性面板（#286，§7.4.4）──────────────────────────────────────────────
# 群組不是自動產生的，要有地方改。面板從區塊標頭展開，僅開發者；儲存後樹立即依新屬性重建。

_ATTRIBUTES = f"[data-testid='attributes-{_PARAM_UID}']"


def _open_attributes(page):
    page.click(f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-attributes']")
    page.wait_for_selector(_ATTRIBUTES, state="visible")
    return page.locator(_ATTRIBUTES)


def _grouped(group: str, uid: str = _PARAM_UID) -> str:
    return f"[data-testid='tree-group-{group}'] [data-testid='tree-item-{uid}']"


def _in_group(page, group: str, uid: str = _PARAM_UID):
    return page.query_selector(_grouped(group, uid))


def test_a_developer_regroups_a_config_and_the_tree_rebuilds_at_once(open_page, repo):
    # AC1／AC2／AC4：改群組（可多個）→ 樹立即依新群組重建，那份出現在每個群組底下；留一筆 meta。
    _listing_many(repo, {"p": "count: 3\n", "q": "count: 5\n"})
    page = _open_panel(open_page(), developer=True)
    assert _in_group(page, "navigation") is None
    form = _open_attributes(page)

    form.get_by_label("群組").fill("navigation, safety")
    page.click("[data-testid='attributes-save']")

    page.wait_for_selector(_grouped("navigation"))
    assert _in_group(page, "safety") is not None
    assert _in_group(page, "navigation", _SECOND_UID) is None  # 另一份沒被動到
    assert page.is_visible("[data-testid='attributes-saved-notice']")
    listed = tomllib.loads((repo / "config-list.toml").read_text(encoding="utf-8"))["files"]
    assert [entry["groups"] for entry in listed] == [["navigation", "safety"], []]
    assert _git_log(repo, 1) == [f"meta({_PARAM_UID}): 群組改為 navigation、safety|陳小明"]


def test_renaming_shows_the_new_name_in_the_tree_and_the_panel_under_the_same_uid(open_page, repo):
    # AC3：改名不影響 uid——同一個樹節點、同一個區塊，只是名字換了。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page(), developer=True)
    form = _open_attributes(page)

    form.get_by_label("名稱").fill("navigation-params")
    form.get_by_label("主機").fill("site-a")
    page.click("[data-testid='attributes-save']")

    page.wait_for_selector("[data-testid='attributes-saved-notice']")
    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}'] >> text=navigation-params")
    assert "navigation-params" in page.inner_text(
        f"[data-testid='panel-{_PARAM_UID}'] [data-testid='panel-head']"
    )


def test_the_description_shows_when_hovering_the_tree_node(open_page, repo):
    # AC5：說明於樹節點滑鼠停留時顯示（title）。沒有說明的節點沒有這個提示。
    _listing_many(repo, {"p": "count: 3\n", "q": "count: 5\n"})
    page = _open_panel(open_page(), developer=True)
    form = _open_attributes(page)

    form.get_by_label("說明").fill("Nav2 導航參數")
    page.click("[data-testid='attributes-save']")

    page.wait_for_selector(f"[data-testid='tree-item-{_PARAM_UID}'][title='Nav2 導航參數']")
    assert page.get_attribute(f"[data-testid='tree-item-{_SECOND_UID}']", "title") is None


def test_the_attributes_button_is_absent_for_a_normal_user(open_page, repo):
    # AC6：僅開發者——一般使用者模式下按鈕不存在於 DOM，不是停用。
    _listing_many(repo, {"p": "count: 3\n"})
    page = _open_panel(open_page())

    assert page.query_selector("[data-testid='panel-attributes']") is None


def test_an_invalid_attribute_is_marked_on_its_input_and_nothing_is_saved(open_page, repo):
    # AC7：重複的群組被擋下——原因與下一步顯示在面板、標在那個輸入框；清單檔沒被改。
    _listing_many(repo, {"p": "count: 3\n"})
    before = (repo / "config-list.toml").read_text(encoding="utf-8")
    page = _open_panel(open_page(), developer=True)
    form = _open_attributes(page)

    form.get_by_label("群組").fill("nav, nav")
    page.click("[data-testid='attributes-save']")

    page.wait_for_selector("[data-testid='attributes-error']", state="visible")
    assert "重複" in page.inner_text("[data-testid='attributes-error']")
    assert form.get_by_label("群組").get_attribute("aria-invalid") == "true"
    assert form.get_by_label("名稱").get_attribute("aria-invalid") is None
    assert (repo / "config-list.toml").read_text(encoding="utf-8") == before


def test_saving_attributes_with_unsaved_parameter_edits_is_held_back(open_page, repo):
    # 儲存屬性會重畫這一塊；有還沒儲存的參數改動時先擋下，不悄悄丟掉（不變式 2）。
    _listing_many(repo, {"p": "count: 3\n"})
    before = (repo / "config-list.toml").read_text(encoding="utf-8")
    page = _open_panel(open_page(), developer=True)
    page.fill(_param_value("count"), "4")
    form = _open_attributes(page)

    form.get_by_label("名稱").fill("renamed")
    page.click("[data-testid='attributes-save']")

    page.wait_for_selector("[data-testid='attributes-error']", state="visible")
    assert "還沒儲存" in page.inner_text("[data-testid='attributes-error']")
    assert page.input_value(_param_value("count")) == "4"
    assert (repo / "config-list.toml").read_text(encoding="utf-8") == before


def test_cancelling_closes_the_attributes_panel_without_changing_anything(open_page, repo):
    _listing_many(repo, {"p": "count: 3\n"})
    before = (repo / "config-list.toml").read_text(encoding="utf-8")
    page = _open_panel(open_page(), developer=True)
    form = _open_attributes(page)

    form.get_by_label("名稱").fill("renamed")
    page.click("[data-testid='attributes-cancel']")

    assert page.query_selector(_ATTRIBUTES) is None
    assert (repo / "config-list.toml").read_text(encoding="utf-8") == before


def test_a_raw_config_has_the_attributes_panel_too(open_page, repo):
    # 入口在區塊標頭：沒有欄位表的 raw 也分得了群組。
    _listing_with(repo, "whatever: [not parsed\n", fmt="raw")
    page = _open_panel(open_page(), developer=True)
    form = _open_attributes(page)

    form.get_by_label("群組").fill("firmware")
    page.click("[data-testid='attributes-save']")

    page.wait_for_selector(_grouped("firmware"))


# ── W2 左側樹依主機分層（#34）───────────────────────────────────────────────


def _listing_two_hosts(repo) -> None:
    """兩台主機各一份：a@amr01（navigation）、b@amr02（未分群），目標＝來源→一致。"""
    entries = ""
    hosts = (("mfz3k9q1", "a", "amr01", '"navigation"'), ("mfz3k9q2", "b", "amr02", ""))
    for uid, name, hostname, groups in hosts:
        (repo / "files" / f"{name}.yaml").write_text(f"{name}: 1\n", encoding="utf-8")
        target = repo / "deployed" / f"{name}.yaml"
        target.write_text(f"{name}: 1\n", encoding="utf-8")
        entries += (
            f'\n[[files]]\nuid = "{uid}"\nname = "{name}"\nhostname = "{hostname}"\n'
            f'source = "files/{name}.yaml"\ntarget = "{target}"\nformat = "yaml"\n'
            f"groups = [{groups}]\n"
        )
    (repo / "config-list.toml").write_text(_LIST_HEADER + entries, encoding="utf-8")


def test_the_tree_is_grouped_by_default_with_no_host_layer(open_page, repo):
    # 預設依群組：階層來自 groups，不是機器（§7.4.1）。
    _listing_two_hosts(repo)
    page = _enter_identity(open_page())

    assert page.input_value("[data-testid='tree-layout']") == "group"
    assert page.query_selector("[data-testid^='tree-host-']") is None
    nav_a = "[data-testid='tree-group-navigation'] [data-testid='tree-item-mfz3k9q1']"
    assert page.is_visible(nav_a)


def test_switching_to_host_layout_puts_hostname_first_then_groups(open_page, repo):
    # 依主機：第一層 hostname、第二層群組；每份 config 落在自己主機底下的群組節點。
    _listing_two_hosts(repo)
    page = _enter_identity(open_page())

    page.select_option("[data-testid='tree-layout']", "host")

    page.wait_for_selector("[data-testid='tree-host-amr01']")
    host1 = "[data-testid='tree-host-amr01']"
    host2 = "[data-testid='tree-host-amr02']"
    nav_a = "[data-testid='tree-group-navigation'] [data-testid='tree-item-mfz3k9q1']"
    ungrouped_b = "[data-testid='tree-group-ungrouped'] [data-testid='tree-item-mfz3k9q2']"
    assert page.is_visible(f"{host1} {nav_a}")
    assert page.is_visible(f"{host2} {ungrouped_b}")
    hosts = page.eval_on_selector_all(
        "[data-testid^='tree-host-']", "els => els.map(e => e.dataset.testid)"
    )
    assert hosts == ["tree-host-amr01", "tree-host-amr02"]
    assert page.get_attribute(
        "[data-testid='tree-host-amr01'] > h2 [data-testid='group-status-dot']", "data-state"
    ) == "in_sync"


def test_switching_back_to_group_layout_removes_the_host_layer(open_page, repo):
    _listing_two_hosts(repo)
    page = _enter_identity(open_page())
    page.select_option("[data-testid='tree-layout']", "host")
    page.wait_for_selector("[data-testid='tree-host-amr01']")

    page.select_option("[data-testid='tree-layout']", "group")

    page.wait_for_selector("[data-testid^='tree-host-']", state="detached")
    ungrouped_b = "[data-testid='tree-group-ungrouped'] [data-testid='tree-item-mfz3k9q2']"
    assert page.is_visible(ungrouped_b)


# ── W2 雙擊展開與多開堆疊（#35／#36）─────────────────────────────────────────
# 單擊＝選取（高亮），雙擊＝展開到右側；右側可同時堆疊多份、各自儲存／捨棄；折疊與關閉逐塊。


def test_a_single_click_selects_and_only_a_double_click_expands(open_page, listing):
    listing("a", "b")
    page = _enter_identity(open_page())

    page.click("[data-testid='tree-item-mfz3k9q1']")

    assert page.get_attribute("[data-testid='tree-item-mfz3k9q1']", "aria-selected") == "true"
    assert page.query_selector("[data-testid='panel-mfz3k9q1']") is None
    page.dblclick("[data-testid='tree-item-mfz3k9q1']")
    page.wait_for_selector("[data-testid='panel-mfz3k9q1']", state="visible")


def test_three_configs_can_be_expanded_at_once_and_saved_independently(open_page, repo):
    # #36：三份以上同時展開、各自儲存；新開的在最上面。
    _listing_many(repo, {"a": "count: 1\n", "b": "speed: 2\n", "c": "ratio: 0.5\n"})
    page = _enter_identity(open_page())
    for uid in ("mfz3k9q1", "mfz3k9q2", "mfz3k9q3"):
        page.dblclick(f"[data-testid='tree-item-{uid}']")
        page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")

    order = page.eval_on_selector_all(
        "[data-testid^='panel-mfz']", "els => els.map(e => e.dataset.uid)"
    )
    assert order == ["mfz3k9q3", "mfz3k9q2", "mfz3k9q1"]

    speed = "[data-testid='panel-mfz3k9q2'] [data-testid='param-speed'] [data-testid='param-value']"
    page.fill(speed, "3")
    page.click("[data-testid='panel-mfz3k9q2'] [data-testid='panel-save']")
    page.wait_for_selector(_draft_dot("mfz3k9q2"), state="visible")

    assert page.query_selector(_draft_dot("mfz3k9q1")) is None  # 其他區塊不受影響
    assert page.is_visible("[data-testid='panel-mfz3k9q1']")
    assert page.is_visible("[data-testid='panel-mfz3k9q3']")


def test_a_panel_collapses_on_its_header_and_can_be_closed(open_page, listing):
    listing("a", "b")
    page = _enter_identity(open_page())
    for uid in ("mfz3k9q1", "mfz3k9q2"):
        page.dblclick(f"[data-testid='tree-item-{uid}']")
        page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")

    page.click("[data-testid='panel-mfz3k9q1'] [data-testid='panel-head']")
    assert page.get_attribute("[data-testid='panel-mfz3k9q1']", "data-collapsed") == "true"
    assert page.is_hidden("[data-testid='panel-mfz3k9q1'] [data-testid='param-table']")
    assert page.is_visible("[data-testid='panel-mfz3k9q2'] [data-testid='param-table']")

    page.click("[data-testid='panel-mfz3k9q2'] [data-testid='panel-close']")
    page.wait_for_selector("[data-testid='panel-mfz3k9q2']", state="detached")
    assert page.is_visible("[data-testid='panel-mfz3k9q1']")


def test_returning_from_history_keeps_the_other_expanded_panels(open_page, listing):
    # #35：返回時保留已展開的區塊——歷史只換自己那一槽。
    listing("a", "b")
    page = _enter_identity(open_page())
    for uid in ("mfz3k9q1", "mfz3k9q2"):
        page.dblclick(f"[data-testid='tree-item-{uid}']")
        page.wait_for_selector(f"[data-testid='panel-{uid}']", state="visible")

    page.click("[data-testid='panel-mfz3k9q1'] [data-testid='panel-history']")
    page.wait_for_selector("[data-testid='history-mfz3k9q1']", state="visible")
    assert page.is_visible("[data-testid='panel-mfz3k9q2']")

    page.get_by_role("button", name="返回欄位表").click()

    page.wait_for_selector("[data-testid='panel-mfz3k9q1']", state="visible")
    assert page.is_visible("[data-testid='panel-mfz3k9q2']")
    assert page.query_selector("[data-testid='history-mfz3k9q1']") is None


# ── W2 參數層級搜尋（#37）───────────────────────────────────────────────────
# 搜尋框左側有範圍下拉，預設「全部」（四種範圍的聯集），指定範圍是為了降噪（§7.4.2）。
# 走 GET /api/search（#32）：命中的 config 留在樹上，命中的參數在展開時標示並定位。


def _search_three(repo):
    return _listing_many(repo, {
        "nav2": "max_vel: 0.8\nrate: 10\n",
        "camera": "exposure: 0.55\nnav2_topic: x\n",
        "plain": "count: 1\n",
    })


def test_searching_a_parameter_name_lists_the_configs_that_have_it_and_locates_the_row(
    open_page, repo
):
    # AC1：搜尋 max_vel 列出含該參數的 config，展開時定位到那一列（標示 data-hit）。
    _search_three(repo)
    page = _enter_identity(open_page())

    page.fill("[data-testid='search-input']", "max_vel")

    page.wait_for_selector("[data-testid='tree-item-mfz3k9q2']", state="detached")
    assert page.is_visible("[data-testid='tree-item-mfz3k9q1']")
    page.dblclick("[data-testid='tree-item-mfz3k9q1']")
    page.wait_for_selector("[data-testid='param-max_vel'][data-hit='true']")
    assert page.get_attribute("[data-testid='param-rate']", "data-hit") is None


def test_searching_a_value_finds_the_parameter_holding_it(open_page, repo):
    # AC2：搜尋 0.55 找到值符合的參數（camera 的 exposure）。
    _search_three(repo)
    page = _enter_identity(open_page())

    page.fill("[data-testid='search-input']", "0.55")

    page.wait_for_selector("[data-testid='tree-item-mfz3k9q1']", state="detached")
    assert page.is_visible("[data-testid='tree-item-mfz3k9q2']")
    assert page.query_selector("[data-testid='tree-item-mfz3k9q3']") is None


def test_the_config_name_scope_only_hits_names(open_page, repo):
    # AC3：nav2 在「全部」同時命中 nav2（名稱）與 camera（參數 nav2_topic）；指定「config 名稱」
    # 只剩 nav2。
    _search_three(repo)
    page = _enter_identity(open_page())
    page.fill("[data-testid='search-input']", "nav2")
    page.wait_for_selector("[data-testid='tree-item-mfz3k9q3']", state="detached")
    assert page.is_visible("[data-testid='tree-item-mfz3k9q2']")

    page.select_option("[data-testid='search-scope']", "config 名稱")

    page.wait_for_selector("[data-testid='tree-item-mfz3k9q2']", state="detached")
    assert page.is_visible("[data-testid='tree-item-mfz3k9q1']")


def test_the_target_path_scope_only_hits_paths(open_page, repo):
    # AC4：「目標路徑」範圍以路徑命中——搜檔名只剩那一份；同一個字在「config 名稱」範圍下不中。
    _search_three(repo)
    page = _enter_identity(open_page())

    page.select_option("[data-testid='search-scope']", "目標路徑")
    page.fill("[data-testid='search-input']", "plain.yaml")

    page.wait_for_selector("[data-testid='tree-item-mfz3k9q1']", state="detached")
    assert page.is_visible("[data-testid='tree-item-mfz3k9q3']")
    page.select_option("[data-testid='search-scope']", "config 名稱")
    page.wait_for_selector("[data-testid='no-matches']")


def test_search_with_no_hits_shows_the_empty_state(open_page, repo):
    # AC5：搜尋無結果有明確提示，不是一片空白。
    _search_three(repo)
    page = _enter_identity(open_page())

    page.fill("[data-testid='search-input']", "zzz-nothing")

    page.wait_for_selector("[data-testid='no-matches']")
    assert "沒有符合" in page.inner_text("[data-testid='no-matches']")
