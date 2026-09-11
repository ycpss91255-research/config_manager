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
import subprocess
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


def test_the_confirm_screen_previews_the_hostname(open_page, browse_root):
    # AC5：納管前預覽 hostname（核對機器身分）。
    (browse_root / "h.yaml").write_text("a: 1\n", encoding="utf-8")
    page = _open_confirm(open_page(), browse_root, "h.yaml")

    assert page.inner_text("[data-testid='onboard-hostname']").strip()  # 非空、含 hostname


def test_an_onboarded_entry_survives_a_reload(open_page, browse_root):
    # AC5：hostname 寫進檔案、重開不變——落盤凍結，reload 後同一條目仍在。
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
