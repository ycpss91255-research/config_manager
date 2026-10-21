"""T9 — HTTP 端點、T10 — CLI。以真實 HTTP 對一個真的在跑的服務測。

PDF §3.6.1 軸 2 的層級只有 Unit／Integration／System／Acceptance。先前這些規格放在
`test/bats/runtime/`——`runtime` 不是層級，是我發明的（#116）。而且測的是 Python，
依「測試工具對應被測的語言」該用 pytest 不是 bats。

搬到這裡解掉的是結構障礙：先前 `api/routes.py` 與 `api/cli.py` 在覆蓋率報告上是 0%，
不是沒測，是測它們的規格不由 pytest 執行（#97）。**還沒解掉的是量測範圍**——覆蓋率的
`source` 目前只有 `core/`，所以 `api/` 依然不在報告裡（見 `doc/TEST-PLAN.md`
「已知的量測缺口」）。
"""

import contextlib
import datetime
import gc
import http.server
import http.cookiejar
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import config_manager
from config_manager.api import drafts as drafts_api
from config_manager.core.allowed_roots import load as load_allowed_roots
from config_manager.api.lock import LockBox
from config_manager.core.errors import UidHorizonReached
from config_manager.io import promote as promote_io
from config_manager.io.errors import TargetNotWritable

_TIMEOUT = 5
# 422：輸入的形狀對、值不合法。端點刻意不用 400——那會把「你送錯格式」與
# 「你送的值不行」折成同一個回覆。
_UNPROCESSABLE = 422
# 409：與目前狀態相牴觸（已有同一 target 的條目、或尚未設定身分）。
_CONFLICT = 409
# 403：身分設了、但角色不夠（白名單維護僅開發者，#202）。
_FORBIDDEN = 403
# 500：伺服器層的錯（如 CM_HOSTNAME 部署誤設）——帶可行動訊息，不是裸 500。
_SERVER_ERROR = 500
# 404：要移除的白名單前綴不在檔裡（#15）——定位不到，不是衝突也不是輸入格式錯。
_NOT_FOUND = 404
# 410：續期的編輯階段已失效（逾時被回收或已釋放）——不是找不到資源，是曾經有、現在沒了（#33）。
_GONE = 410


# 這份檔案裡的請求都來自同一個「瀏覽器」：身分綁在後端發的瀏覽器票（cookie）上（#288），沒有
# cookie 罐的 urlopen 每一發都是「還沒說自己是誰」。要扮第二個瀏覽器的規格自己再開一罐
# （`_another_browser`）。


def _another_browser():
    """另一個瀏覽器：自己的 cookie 罐，後端會發它自己的票。"""
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


_browser = _another_browser()


def _get(api, path):
    with _browser.open(f"{api}{path}", timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


# 寫入只接受持有編輯權的那個分頁（F23）：輔助函式寫入時自己「取得 → 帶標頭送出 → 釋放」。不寫入的
# POST（身分、編輯階段本身、偵測）不帶。取不到編輯權（沒身分、別人正持有）就照樣送、不帶識別碼——
# 讓後端自己說為什麼擋（尚未設定身分、沒有編輯權）。
_EDITING_HEADER = "X-CM-Editing-Token"
_NOT_A_WRITE = ("/api/session", "/api/inspect")
_ACQUIRE = object()  # 預設：寫入時自己取得；測試要自帶（或刻意不帶）識別碼時傳字串或 None


def _writes(method, path):
    return method != "GET" and not path.startswith(_NOT_A_WRITE)


def _open_json(opener, url, method, payload, *, token=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json"}
    if token:
        headers[_EDITING_HEADER] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=_TIMEOUT) as response:
        body = response.read().decode("utf-8")
        return (json.loads(body) if body else None), response.headers


@contextlib.contextmanager
def _holding(opener, api):
    """這個瀏覽器取得編輯權、交出識別碼，做完就釋放；取不到交出 None（不吞掉寫入本身的錯）。"""
    try:
        held, _ = _open_json(opener, f"{api}/api/session/lock", "POST", None)
    except urllib.error.HTTPError:
        held = None
    try:
        yield held and held["token"]
    finally:
        if held:
            _open_json(opener, f"{api}/api/session/lock", "DELETE", {"token": held["token"]})


def _send(api, method, path, payload=None, *, token=_ACQUIRE):
    """這個瀏覽器（`_browser`）送一個請求、回解析後的 body；HTTP 錯誤照樣丟 HTTPError。寫入預設自己
    取得編輯權；測試要自帶識別碼（或刻意不帶：None）時傳 `token`。"""
    url = f"{api}{path}"
    if token is _ACQUIRE and _writes(method, path):
        with _holding(_browser, api) as held:
            return _open_json(_browser, url, method, payload, token=held)[0]
    return _open_json(_browser, url, method, payload, token=None if token is _ACQUIRE else token)[0]


def _post(api, path, payload, token=_ACQUIRE):
    return _send(api, "POST", path, payload, token=token)


def _delete(api, path, payload, token=_ACQUIRE):
    return _send(api, "DELETE", path, payload, token=token)


def _detail(error):
    # 422／409 的 body 是 {"detail": "..."}。斷言原因文字，才分得出「白名單外」與
    # 「不是目錄」——只斷言狀態碼的話，任何一種 422 都會讓規格過（資安審查 #185）。
    return json.loads(error.read().decode("utf-8"))["detail"]


# CLI 不是瀏覽器，納管的作者要在命令列明寫（#288）。
_AUTHOR_FLAGS = ("--name", "陳小明", "--email", "ming@example.com")


def _cli(*args):
    # 子行程不繼承 pytest 的 pythonpath 設定（那只作用在測試行程本身），所以自己
    # 供應 PYTHONPATH——與 runtime 映像的 ENV 是同一個值（Dockerfile 的 APP_ROOT/src）。
    # 這裡從套件的實際位置推導，才不會在兩種執行環境下各自寫死一份。
    environment = dict(os.environ)
    environment.setdefault("PYTHONPATH", str(pathlib.Path(config_manager.__file__).parent.parent))
    return subprocess.run(
        [sys.executable, "-m", "config_manager.api.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_configs_on_a_fresh_repo_is_an_empty_list(api, listing):
    # 什麼都還沒納管是合法狀態：回空清單，不是回錯誤，也不是起不來。
    # 空清單自己排出來，不靠「這則排在會寫入的那則前面」——後者在檔內順序被改動、
    # `-k` 過濾、或並行執行時，會把這條斷言變成一條不驗任何東西的規格。
    listing()

    assert _get(api, "/api/configs") == []


def test_session_records_the_identity_and_reads_it_back(api):
    # 這不是登入：沒有密碼、不驗證、角色是自我宣告（ADR-00000020）。
    # git_author 是它存在的理由——變更紀錄的作者。
    posted = _post(
        api,
        "/api/session",
        {"name": "陳小明", "email": "ming@example.com", "role": "developer"},
    )

    assert posted["git_author"] == "陳小明 <ming@example.com>"
    assert _get(api, "/api/session")["name"] == "陳小明"


def test_identity_that_would_break_the_author_string_is_refused(api):
    # 悄悄清洗會讓紀錄上的名字與輸入的不同，而紀錄的用途正是追溯到人。
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(
            api,
            "/api/session",
            {"name": "陳小明 <admin@example.com>", "email": "ming@example.com", "role": "user"},
        )

    assert exc.value.code == _UNPROCESSABLE


def test_configs_carries_the_state_of_every_entry(api, listing):
    # 三種狀態各一筆，走真實 HTTP 看端點吐出來的形狀。判定本身由 T21 逐條測過；
    # 這裡證明的是「端點真的把它接上了」，不是回一個寫死的欄位。
    listing("a", "b", "c")

    rows = _get(api, "/api/configs")

    assert [row["state"] for row in rows] == ["in_sync", "drift", "missing"]
    assert rows[0]["ref"] == "a@amr01-mfz3k9q1"


def test_configs_surfaces_a_pathological_target_as_a_row_error_not_a_bare_500(api, repo):
    # #214 Q3(ii)：某筆目標被換成 FIFO（digest 加固後會拒絕、不掛住）。GET /api/configs 不裸
    # 500，該列帶 error（state 為 null）、其餘列照常帶 state——一筆比不了不弄垮整份清單。
    root = pathlib.Path(repo)
    (root / "files").mkdir(exist_ok=True)
    (root / "deployed").mkdir(exist_ok=True)
    (root / "files" / "ok.yaml").write_text("ok: 1\n", encoding="utf-8")
    ok_target = root / "deployed" / "ok.yaml"
    ok_target.write_text("ok: 1\n", encoding="utf-8")
    (root / "files" / "bad.yaml").write_text("bad: 1\n", encoding="utf-8")
    fifo_target = root / "deployed" / "bad.pipe"
    fifo_target.unlink(missing_ok=True)
    os.mkfifo(fifo_target)
    header = (
        'list_version = 1\n\n[defaults.permissions]\n'
        'owner = "root"\ngroup = "root"\nmode = "0644"\n'
    )
    entry = (
        '\n[[files]]\nuid = "{uid}"\nname = "{name}"\nhostname = "amr01"\n'
        'source = "files/{name}.yaml"\ntarget = "{target}"\nformat = "yaml"\ngroups = []\n'
    )
    (root / "config-list.toml").write_text(
        header
        + entry.format(uid="aaaaaaaa", name="ok", target=ok_target)
        + entry.format(uid="bbbbbbbb", name="bad", target=fifo_target),
        encoding="utf-8",
    )

    rows = _get(api, "/api/configs")

    by_name = {row["name"]: row for row in rows}
    assert by_name["ok"]["state"] == "in_sync"
    assert by_name["ok"]["error"] is None
    assert by_name["bad"]["state"] is None
    assert str(fifo_target) in by_name["bad"]["error"]


def test_a_runtime_unparsable_config_list_is_a_structured_500_not_a_bare_500(api, repo):
    # #209 Q1／Q4：服務起來後清單檔被改壞（掛載漂移／手改）。GET /api/configs 回帶檔名與
    # 下一步的結構化 500，不是不指名檔、不可行動的裸 500（不變式 2）。
    root = pathlib.Path(repo)
    list_path = root / "config-list.toml"
    original = list_path.read_text(encoding="utf-8")
    list_path.write_text("this is not valid toml {[\n", encoding="utf-8")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(api, "/api/configs")
        assert exc.value.code == _SERVER_ERROR
        detail = json.loads(exc.value.read().decode("utf-8"))["detail"]
        assert str(list_path) in detail["file"]  # 結構化：機器可讀的檔名欄位
        assert "下一步" in detail["message"]  # 帶可行動訊息
    finally:
        list_path.write_text(original, encoding="utf-8")


def test_a_runtime_unparsable_allowed_roots_is_a_structured_500_not_a_bare_500(api, repo):
    # #209：白名單設定檔在執行期被改壞，走 root_prefixes 的端點（browse）回結構化 500。
    root = pathlib.Path(repo)
    roots_path = root / "allowed-roots.toml"
    original = roots_path.read_text(encoding="utf-8")
    roots_path.write_text("not valid toml {[\n", encoding="utf-8")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(api, "/api/browse?path=/tmp")
        assert exc.value.code == _SERVER_ERROR
        detail = json.loads(exc.value.read().decode("utf-8"))["detail"]
        assert str(roots_path) in detail["file"]
        assert "下一步" in detail["message"]
    finally:
        roots_path.write_text(original, encoding="utf-8")


def test_cli_list_goes_through_the_same_endpoint_as_the_page(api, listing):
    # ADR-00000009：不存在「CLI 能做但介面不能」或反之，因為根本是同一組端點。
    # 把關的不是輸出比對而是 --api：自己讀清單檔的實作根本用不到那個位址。
    #
    # 這三筆自己排。先前它讀的是同檔前一則寫進共用 repo 的清單，於是單獨執行時
    # 清單是空的、迴圈裡的斷言一條都不成立（#153）。
    listing("a", "b", "c")

    result = _cli("list", "--api", api)

    assert result.returncode == 0
    for state in ("一致", "偏離", "未部署"):
        assert state in result.stdout
    assert "a@amr01-mfz3k9q1" in result.stdout


def test_cli_list_marks_a_pathological_target_as_an_error_row(api, repo):
    # #214 Q3(ii)：病態目標那一列在 CLI 標「錯誤」並帶原因，不印一個偽裝的狀態，也不讓
    # 整個 list 崩掉——與 GET /api/configs 同一份資料。
    root = pathlib.Path(repo)
    (root / "files").mkdir(exist_ok=True)
    (root / "deployed").mkdir(exist_ok=True)
    (root / "files" / "bad.yaml").write_text("bad: 1\n", encoding="utf-8")
    fifo_target = root / "deployed" / "bad2.pipe"
    fifo_target.unlink(missing_ok=True)
    os.mkfifo(fifo_target)
    header = (
        'list_version = 1\n\n[defaults.permissions]\n'
        'owner = "root"\ngroup = "root"\nmode = "0644"\n'
    )
    entry = (
        '\n[[files]]\nuid = "cccccccc"\nname = "bad"\nhostname = "amr01"\n'
        f'source = "files/bad.yaml"\ntarget = "{fifo_target}"\nformat = "yaml"\ngroups = []\n'
    )
    (root / "config-list.toml").write_text(header + entry, encoding="utf-8")

    result = _cli("list", "--api", api)

    assert result.returncode == 0
    assert "錯誤" in result.stdout
    assert str(fifo_target) in result.stdout


def test_cli_fails_loudly_when_the_backend_is_not_up():
    # 「服務沒起來」與「什麼都還沒納管」看起來都是沒有東西，該做的處置卻完全不同。
    result = _cli("list", "--api", "http://127.0.0.1:9")

    assert result.returncode != 0
    assert "讀不到" in result.stderr


# ── POST /api/configs 納管、GET /api/browse（#185）──────────────────────────


def _set_session(api):
    # 納管要有作者。每則規格自己設，不靠「別則先設過」——那是順序相依（#153）。
    _post(api, "/api/session", {"name": "陳小明", "email": "ming@example.com", "role": "developer"})


def _write_source(sources_root, name, content=b"max_vel: 0.8\n"):
    # 白名單內的一份來源檔。名字各則不同，避免共用 session repo 時互相撞到 target。
    path = pathlib.Path(sources_root) / name
    path.write_bytes(content)
    return str(path)


_CONFIG_LIST_HEADER = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _write_config_list(repo, *entries):
    # 就地寫一份指名 target 的清單檔（比照 listing 夾具的整份覆寫），供受影響計算的測試安排
    # 「落在某前綴底下／不落在」的條目。entries：一串 (uid, name, target)。target 可任意絕對
    # 路徑（受影響計算的 read_config_list 不驗來源存在），但同時備妥來源檔——scan（/api/configs）
    # 對缺失來源會失敗，驗「條目仍在」時要讀得到。
    root = pathlib.Path(repo)
    (root / "files").mkdir(exist_ok=True)
    body = _CONFIG_LIST_HEADER
    for uid, name, target in entries:
        (root / "files" / f"{name}.yaml").write_text(f"{name}: 1\n", encoding="utf-8")
        body += (
            f'\n[[files]]\nuid = "{uid}"\nname = "{name}"\nhostname = "amr01"\n'
            f'source = "files/{name}.yaml"\ntarget = "{target}"\nformat = "yaml"\ngroups = []\n'
        )
    (root / "config-list.toml").write_text(body, encoding="utf-8")


def test_import_onboards_a_file_and_it_shows_in_configs(api, sources_root):
    _set_session(api)
    source = _write_source(sources_root, "happy_nav2.yaml")

    entry = _post(api, "/api/configs", {"source_path": source, "format": "yaml"})

    # 回傳剛建立的條目：target 是原始磁碟位置，source 是 repo 內的複本。
    assert entry["target"] == source
    assert entry["format"] == "yaml"
    assert entry["source"].startswith(f"files/{entry['hostname']}/")
    # 隨後重新 GET /api/configs 就看得到它（走的是同一份掃描）。
    refs = [row["ref"] for row in _get(api, "/api/configs")]
    assert entry["ref"] in refs


def test_the_import_commit_author_is_the_session_identity(api, sources_root, repo):
    # #114（#6 的第四條驗收條件）：輸入的身分成為變更紀錄的作者，不只是畫面右上角那行字。
    # 就地讀 config-repo 的 git log，驗這次納管產生的 commit 作者＝當前 session 身分（端到端
    # 接線：session → identity.git_author → onboard → io.git.record → commit）。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地讀 config-repo 的 git log 驗 commit 作者")
    _post(api, "/api/session", {"name": "林巡檢", "email": "lin@example.com", "role": "developer"})
    source = _write_source(sources_root, "authored_by_lin.yaml")

    _post(api, "/api/configs", {"source_path": source, "format": "yaml"})

    author = subprocess.run(
        ["git", "-C", repo, "log", "-1", "--format=%an <%ae>"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert author == "林巡檢 <lin@example.com>"


def test_import_a_source_outside_the_whitelist_is_refused(api, tmp_path):
    _set_session(api)
    # tmp_path 不在 sources_root（白名單）底下。
    outside = tmp_path / "secret.yaml"
    outside.write_bytes(b"stolen\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": str(outside), "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE
    assert "白名單之外" in _detail(exc.value)  # 被拒的原因是白名單，不是別種 422


def test_import_with_an_unknown_format_is_refused_as_a_bad_value(api, sources_root):
    # format 不是允許值是「送錯值」（422），不是「與既有狀態衝突」（409）——InvalidFormat
    # 雖屬 ConfigListError，端點特別先接它映 422（#175 的資安審查）。
    _set_session(api)
    source = _write_source(sources_root, "badfmt.yaml")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": source, "format": "notaformat"})

    assert exc.value.code == _UNPROCESSABLE
    assert "format" in _detail(exc.value)


def test_importing_the_same_target_twice_is_refused(api, sources_root):
    _set_session(api)
    source = _write_source(sources_root, "dup.yaml")
    _post(api, "/api/configs", {"source_path": source, "format": "yaml"})

    # 同一個 target 再納管一次：與既有條目衝突（409），不是輸入值不合法（422）。
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": source, "format": "yaml"})

    assert exc.value.code == _CONFLICT


@pytest.mark.skipif(
    os.getuid() == 0,
    reason="root 讀得穿 chmod 000；這條在非 root 的 test-tools 映像（uid 501）跑得到，"
    "映像系統測試以 root 執行故無法佈置這個前提",
)
def test_import_an_unreadable_source_is_refused_not_500(api, sources_root):
    # #175 AC4：檔案在、也到得了，但內容讀不出來（ContentUnreadable，不屬 SourceError 家族）
    # 早先漏接成 500。應回可行動的 422。
    _set_session(api)
    unreadable = pathlib.Path(sources_root) / "unreadable.yaml"
    unreadable.write_bytes(b"secret: 1\n")
    unreadable.chmod(0o000)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(api, "/api/configs", {"source_path": str(unreadable), "format": "yaml"})
        assert exc.value.code == _UNPROCESSABLE  # 422，不是 500
        assert "讀不出來" in _detail(exc.value)
    finally:
        unreadable.chmod(0o644)  # 讓 tmp 目錄清理得掉


def test_cli_import_goes_through_the_same_endpoint_as_the_page(api, sources_root):
    # ADR-00000009：CLI 納管走的是與畫面相同的 POST /api/configs。
    _set_session(api)
    source = _write_source(sources_root, "cli_import.yaml")

    result = _cli("import", "--api", api, "--source", source, "--format", "yaml", *_AUTHOR_FLAGS)

    assert result.returncode == 0
    assert "已納管" in result.stdout
    # 真的納管了：它的 target 出現在清單裡（走同一支端點，不是 CLI 自己讀清單檔）。
    targets = [row["target"] for row in _get(api, "/api/configs")]
    assert source in targets


def test_cli_import_is_refused_while_someone_edits_even_the_same_person(api, sources_root):
    # F23：一次只有一個編輯階段——同一個人在頁面上編輯時，CLI 也不繞過它寫進去；說出是誰。
    _set_session(api)
    held = _lock(api)
    source = _write_source(sources_root, "cli_held.yaml")
    try:
        result = _cli(
            "import", "--api", api, "--source", source, "--format", "yaml", *_AUTHOR_FLAGS
        )
    finally:
        _lock(api, "DELETE", payload={"token": held["token"]})

    targets = [row["target"] for row in _get(api, "/api/configs")]
    assert (result.returncode, "陳小明" in result.stderr, source in targets) == (1, True, False)


def test_browse_lists_a_directory_within_the_whitelist(api, sources_root):
    # 自己的子目錄，內容各則不同——不去列共用的 sources_root（別則也往那寫，會相依）。
    base = pathlib.Path(sources_root) / "browse_here"
    base.mkdir(exist_ok=True)
    (base / "sub").mkdir(exist_ok=True)
    (base / "conf.yaml").write_bytes(b"a: 1\n")

    query = urllib.parse.urlencode({"path": str(base)})
    listing = _get(api, f"/api/browse?{query}")

    names = [entry["name"] for entry in listing["entries"]]
    kinds = {entry["name"]: entry["kind"] for entry in listing["entries"]}
    assert kinds["sub"] == "dir"
    assert kinds["conf.yaml"] == "file"
    assert names == sorted(names)  # 依名字排序是契約——dict 比對驗不到，明確斷言順序


def test_browse_a_path_outside_the_whitelist_is_refused(api, tmp_path):
    # tmp_path 不在 sources_root（白名單）底下。
    query = urllib.parse.urlencode({"path": str(tmp_path)})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/browse?{query}")

    assert exc.value.code == _UNPROCESSABLE
    # detail 帶機器可讀的 kind，讓前端依原因＋角色分流（不靠比對中文字串，#13）。
    detail = _detail(exc.value)
    assert detail["kind"] == "outside_roots"
    assert "白名單之外" in detail["message"]
    # 也帶解析後路徑與建議加入的目錄前綴（tmp_path 是目錄 → suggested 即其 realpath），
    # 讓前端「加入白名單」預填解析後的目錄、而非使用者打的原字串。
    assert detail["resolved"] == os.path.realpath(str(tmp_path))
    assert detail["suggested"] == os.path.realpath(str(tmp_path))


def test_browse_a_symlink_that_escapes_the_whitelist_is_refused(api, sources_root, tmp_path):
    # io/browse 的整條 realpath 防線就是為了擋這個：白名單內放一個指向外面的符號連結，
    # 解析後落在白名單外→拒絕。沒有這條，一個把 realpath 拿掉的迴歸會靜靜地放行（#185）。
    escape = pathlib.Path(sources_root) / "escape"
    escape.mkdir(exist_ok=True)
    link = escape / "leak"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(tmp_path)  # tmp_path 在白名單（sources_root）之外
    query = urllib.parse.urlencode({"path": str(link)})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/browse?{query}")

    assert exc.value.code == _UNPROCESSABLE
    detail = _detail(exc.value)
    assert detail["kind"] == "outside_roots"
    assert "白名單之外" in detail["message"]


def test_browse_a_file_directly_in_a_whitelist_root_lists_the_root_and_selects_it(
    api, sources_root
):
    # 原本這一則斷言「指向檔案→not_a_directory」；F1（人工驗收 U05）起改成列它所在的目錄並選它。
    # 這裡是檔案就在白名單根底下的那一種：上層目錄就是根本身，仍在白名單內。
    a_file = pathlib.Path(sources_root) / "not_a_dir.yaml"
    a_file.write_bytes(b"x\n")
    query = urllib.parse.urlencode({"path": str(a_file)})

    listing = _get(api, f"/api/browse?{query}")

    assert (listing["path"], listing["selected"]) == (
        os.path.realpath(sources_root), os.path.realpath(a_file),
    )


# ── 瀏覽指向檔案（F1，人工驗收 U05、U10）────────────────────────────────────
# 貼一個檔案的完整路徑就找到它：回它所在目錄的清單，並標出那一個。白名單的檢查照舊在解析後的
# 真正路徑上做，上層目錄也要在白名單內；白名單外不透露檔案在不在。


def _browse_refused(api, path) -> dict:
    """瀏覽 `path` 預期被拒，回 detail。"""
    query = urllib.parse.urlencode({"path": str(path)})
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/browse?{query}")
    assert exc.value.code == _UNPROCESSABLE
    return _detail(exc.value)


def test_browsing_a_file_within_the_whitelist_lists_its_directory_and_selects_it(
    api, sources_root
):
    base = pathlib.Path(sources_root) / "browse_file"
    base.mkdir(exist_ok=True)
    (base / "picked.yaml").write_bytes(b"a: 1\n")
    (base / "other.yaml").write_bytes(b"b: 2\n")
    query = urllib.parse.urlencode({"path": str(base / "picked.yaml")})

    listing = _get(api, f"/api/browse?{query}")

    assert (listing["path"], listing.get("selected")) == (
        os.path.realpath(base), os.path.realpath(base / "picked.yaml"),
    )


def test_browsing_a_directory_selects_nothing(api, sources_root):
    # 只有指向檔案時才標「已選」；一般的目錄瀏覽仍是 null，不是缺這個欄位。
    base = pathlib.Path(sources_root) / "browse_dir_selects_nothing"
    base.mkdir(exist_ok=True)
    query = urllib.parse.urlencode({"path": str(base)})

    listing = _get(api, f"/api/browse?{query}")

    assert ("selected" in listing, listing.get("selected")) == (True, None)


def test_browsing_a_path_that_does_not_exist_is_not_found(api, sources_root):
    detail = _browse_refused(api, pathlib.Path(sources_root) / "no_such_file.yaml")

    assert detail["kind"] == "not_found"


def test_a_missing_path_says_to_check_it_rather_than_to_onboard_it(api, sources_root):
    # 人工驗收 U10 的觀察：不存在的路徑要說檢查路徑或確認檔案存在，不該建議直接納管。
    missing = pathlib.Path(sources_root) / "no_such_file.yaml"

    message = _browse_refused(api, missing)["message"]

    assert (str(missing) in message, "檢查" in message, "納管" in message) == (True, True, False)


def test_browsing_through_a_file_as_if_it_were_a_directory_is_not_found(api, sources_root):
    # 路徑中間一段是檔案（plain.yaml/inner）——開檔得到的也是「不是目錄」，但那不是「它是檔案」，
    # 不能拿 plain.yaml 去選。
    plain = pathlib.Path(sources_root) / "plain_middle.yaml"
    plain.write_bytes(b"x: 1\n")

    detail = _browse_refused(api, plain / "inner")

    assert detail["kind"] == "not_found"


def test_a_symlink_to_a_file_elsewhere_in_the_whitelist_selects_the_real_file(api, sources_root):
    # 連結解析後的真正路徑才是要選的那一個——納管記下的也是它。
    real = pathlib.Path(sources_root) / "browse_real"
    links = pathlib.Path(sources_root) / "browse_links"
    real.mkdir(exist_ok=True)
    links.mkdir(exist_ok=True)
    (real / "target.yaml").write_bytes(b"t: 1\n")
    alias = links / "alias.yaml"
    if alias.is_symlink() or alias.exists():
        alias.unlink()
    alias.symlink_to(real / "target.yaml")
    query = urllib.parse.urlencode({"path": str(alias)})

    listing = _get(api, f"/api/browse?{query}")

    assert (listing["path"], listing.get("selected")) == (
        os.path.realpath(real), os.path.realpath(real / "target.yaml"),
    )


def test_a_file_outside_the_whitelist_and_a_missing_one_there_are_refused_alike(api, tmp_path):
    # 白名單外不透露檔案在不在：有檔與沒檔，回的種類、建議加入的目錄都一樣，也都不選。
    (tmp_path / "exists.yaml").write_bytes(b"a: 1\n")

    names = ("exists.yaml", "missing.yaml")
    seen = [
        {key: str(value).replace(name, "<名字>") for key, value in _browse_refused(
            api, tmp_path / name
        ).items()}
        for name in names
    ]

    assert (seen[0], seen[0]["kind"]) == (seen[1], "outside_roots")


def test_a_whitelisted_symlink_to_a_file_outside_is_refused(api, sources_root, tmp_path):
    # 連結本身在白名單內，解析後的檔在外面——照白名單外拒絕，不選它。
    secret = tmp_path / "secret.yaml"
    secret.write_bytes(b"s: 1\n")
    escape = pathlib.Path(sources_root) / "escape_file"
    escape.mkdir(exist_ok=True)
    link = escape / "leak.yaml"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(secret)

    detail = _browse_refused(api, link)

    assert detail["kind"] == "outside_roots"


def test_a_whitelist_root_that_is_a_file_does_not_list_its_parent(api, repo, tmp_path):
    # 白名單的根本身是一個檔案（API 加不進去，但設定檔可以被手改）：它的上層目錄不在白名單內，
    # 不能因為「跳到檔案所在的目錄」就把它列出來。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的白名單設定檔")
    lone = tmp_path / "lone_root.yaml"
    lone.write_bytes(b"l: 1\n")
    (tmp_path / "neighbour.yaml").write_bytes(b"n: 1\n")
    roots_path = pathlib.Path(repo) / "allowed-roots.toml"
    original = roots_path.read_text(encoding="utf-8")
    roots_path.write_text(
        original + f'\n[[roots]]\nprefix = "{lone}"\nadded_by = "test"\n'
        'added_at = "2026-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    try:
        detail = _browse_refused(api, lone)
    finally:
        roots_path.write_text(original, encoding="utf-8")

    assert detail["kind"] == "not_a_directory"


def test_a_symlink_to_a_whitelist_root_that_is_a_file_does_not_list_its_parent(
    api, repo, sources_root, tmp_path
):
    # 同上，但經過連結：上層目錄要以**解析後的真正路徑**的上層判定——不是貼進來那條路徑的上層。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的白名單設定檔")
    lone = tmp_path / "lone_root_linked.yaml"
    lone.write_bytes(b"l: 1\n")
    link = pathlib.Path(sources_root) / "to_lone_root.yaml"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(lone)
    roots_path = pathlib.Path(repo) / "allowed-roots.toml"
    original = roots_path.read_text(encoding="utf-8")
    roots_path.write_text(
        original + f'\n[[roots]]\nprefix = "{lone}"\nadded_by = "test"\n'
        'added_at = "2026-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    try:
        detail = _browse_refused(api, link)
    finally:
        roots_path.write_text(original, encoding="utf-8")

    assert detail["kind"] == "not_a_directory"


def _behind_a_loop(sources_root, tmp_path) -> pathlib.Path:
    """白名單內放一個連結迴圈，旁邊一個指向白名單外目錄的連結；外面那個目錄有 sub/secret.yaml。"""
    base = pathlib.Path(sources_root) / "browse_unstable"
    base.mkdir(exist_ok=True)
    (tmp_path / "outdir" / "sub").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outdir" / "sub" / "secret.yaml").write_bytes(b"s: 1\n")
    for name, to in (("loop", base / "loop"), ("link_out", tmp_path / "outdir")):
        link = base / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(to)
    return base


def test_a_directory_hidden_behind_a_symlink_loop_and_an_outside_link_is_not_listed(
    api, sources_root, tmp_path
):
    # Python 3.11 的 realpath 遇到迴圈就不再解析後面那幾段：`loop/..` 讓字串看起來回到白名單內，
    # `link_out` 卻沒被解析——白名單判定看的是字串，核心開的卻是外面的目錄。不可以列出來。
    base = _behind_a_loop(sources_root, tmp_path)

    detail = _browse_refused(api, f"{base}/loop/../link_out/sub")

    assert "secret.yaml" not in json.dumps(detail, ensure_ascii=False)


def test_a_file_hidden_behind_a_symlink_loop_and_an_outside_link_is_not_selected(
    api, sources_root, tmp_path
):
    # 同上，指向外面的檔案：不可以因為「跳到檔案所在的目錄」而列出外面的目錄、選到外面的檔。
    base = _behind_a_loop(sources_root, tmp_path)

    detail = _browse_refused(api, f"{base}/loop/../link_out/sub/secret.yaml")

    assert "selected" not in detail


@pytest.mark.skipif(
    os.getuid() == 0,
    reason="root 讀得穿權限；這條在非 root 的 test-tools 映像跑得到",
)
def test_a_directory_on_the_way_that_cannot_be_entered_is_named(api, sources_root):
    # 路上某一層進不去（有 r 沒有 x）：指名擋住的那一層，不把整條路徑說成「目錄讀不出來」。
    closed = pathlib.Path(sources_root) / "browse_no_traverse"
    closed.mkdir(exist_ok=True)
    (closed / "inside.yaml").write_bytes(b"i: 1\n")
    closed.chmod(0o600)
    try:
        detail = _browse_refused(api, closed / "inside.yaml")
    finally:
        closed.chmod(0o755)

    assert f"擋住的是 {os.path.realpath(closed)}（" in detail["message"]


def test_a_named_pipe_is_refused_saying_only_regular_files_can_be_picked(api, sources_root):
    # 只有一般檔案能靠貼路徑選取；具名管道照樣拒絕，並說明為什麼。
    fifo = pathlib.Path(sources_root) / "browse_pipe"
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo)

    detail = _browse_refused(api, fifo)

    assert (detail["kind"], "一般檔案" in detail["message"]) == ("not_a_directory", True)


def test_a_symlink_loop_is_refused_not_selected(api, sources_root):
    # 連結迴圈解析不到任何檔案。Linux 上以 O_NOFOLLOW|O_DIRECTORY 開它得到的是 ENOTDIR，
    # 「不是目錄就去上層找」的寫法會誤選這個連結——它不是一般檔案，不能選。
    loop = pathlib.Path(sources_root) / "browse_loop"
    if loop.is_symlink() or loop.exists():
        loop.unlink()
    loop.symlink_to(loop)

    detail = _browse_refused(api, loop)

    assert detail["kind"] == "not_a_directory"


@pytest.mark.skipif(
    os.getuid() == 0,
    reason="root 讀得穿權限；這條在非 root 的 test-tools 映像跑得到",
)
def test_a_file_in_a_directory_that_can_be_entered_but_not_listed_names_that_directory(
    api, sources_root
):
    # 上層目錄只有 x（進得去、列不出來）：不硬選，回「讀不出來」並指名是哪個目錄。
    blind = pathlib.Path(sources_root) / "browse_blind"
    blind.mkdir(exist_ok=True)
    (blind / "hidden.yaml").write_bytes(b"h: 1\n")
    blind.chmod(0o111)
    try:
        detail = _browse_refused(api, blind / "hidden.yaml")
    finally:
        blind.chmod(0o755)

    assert (detail["kind"], detail.get("resolved")) == ("unreadable", os.path.realpath(blind))


def test_cli_browse_goes_through_the_same_endpoint_as_the_page(api, sources_root):
    base = pathlib.Path(sources_root) / "cli_browse"
    base.mkdir(exist_ok=True)
    (base / "picked.yaml").write_bytes(b"a: 1\n")

    result = _cli("browse", "--api", api, "--path", str(base))

    assert result.returncode == 0
    assert "picked.yaml" in result.stdout


def test_cli_browse_of_a_file_lists_its_directory_and_says_which_one_is_picked(api, sources_root):
    # F1：與畫面同一支端點——指向檔案時印出所在目錄的清單，並寫出已選的那一個。
    base = pathlib.Path(sources_root) / "cli_browse_file"
    base.mkdir(exist_ok=True)
    (base / "picked.yaml").write_bytes(b"a: 1\n")

    result = _cli("browse", "--api", api, "--path", str(base / "picked.yaml"))

    assert f"已選：{os.path.realpath(base / 'picked.yaml')}" in result.stdout


# ── POST /api/inspect 偵測（#195）──────────────────────────────────────────


def test_inspect_returns_format_ambiguities_and_summary(api, sources_root):
    source = _write_source(sources_root, "detect.yaml", b"enabled: no\nmax_vel: 0.8\n")

    result = _post(api, "/api/inspect", {"source_path": source, "format": "yaml"})

    assert result["format"] == "yaml"
    assert result["field_count"] == len(result["types"])
    assert "max_vel" in result["types"]
    # 「no」是 YAML 的歧義寫法：列出、指名行號與原樣的值（不自動修正）。
    lines = {a["value"]: a["line"] for a in result["ambiguities"]}
    assert lines["no"] == 1
    assert result["ambiguities"][0]["readings"]  # 有給可能的讀法
    # 原始權限一併帶回（§5.1 一行摘要的第三項）。
    assert result["permissions"]["mode"]


def test_inspect_previews_the_hostname_onboarding_will_use(api, sources_root):
    # 確認畫面要在納管前預覽 hostname（AC5：核對機器身分）。hostname 由後端決定
    # （CM_HOSTNAME 或 gethostname），納管當下寫進條目與 files/<hostname>/ 路徑。
    source = _write_source(sources_root, "hostname_preview.yaml", b"a: 1\n")

    result = _post(api, "/api/inspect", {"source_path": source, "format": "yaml"})

    assert isinstance(result["hostname"], str) and result["hostname"]


def test_inspect_unsafe_hostname_is_a_500_with_a_message(api, sources_root, monkeypatch):
    # CM_HOSTNAME 設成不安全值（含 /）→ HostnameInvalid。先前 onboard／inspect 都漏接成裸
    # 500；現在映成帶可行動訊息的 500（指名 CM_HOSTNAME）。就地測才控得了伺服器的 env。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("外部映像的 CM_HOSTNAME 由映像決定，就地起服務才控得了 env")
    monkeypatch.setenv("CM_HOSTNAME", "bad/host")
    source = _write_source(sources_root, "unsafe_host.yaml", b"a: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": source, "format": "yaml"})

    assert exc.value.code == _SERVER_ERROR
    assert "CM_HOSTNAME" in _detail(exc.value)  # 帶可行動訊息，不是裸 500


def test_onboard_unsafe_hostname_is_a_500_with_a_message(api, sources_root, monkeypatch):
    # TEST-PLAN 宣稱「inspect 與 onboard 皆然」——onboard 那半也要有測試釘住（#14 審查）。
    # UI 流程靠 inspect 先擋，但 POST /api/configs 對任何 client 開放，防禦碼不能無測試而腐化。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("外部映像的 CM_HOSTNAME 由映像決定，就地起服務才控得了 env")
    _set_session(api)
    monkeypatch.setenv("CM_HOSTNAME", "bad/host")
    source = _write_source(sources_root, "onboard_badhost.yaml", b"a: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": source, "format": "yaml"})

    assert exc.value.code == _SERVER_ERROR
    assert "CM_HOSTNAME" in _detail(exc.value)


def test_onboard_strips_control_chars_from_the_ambiguity_note(api, sources_root, repo):
    # ambiguity_note 進 commit 內文，io/git.history 以 \x1e/\x1f 當分隔符解析——含這些字元會把整庫
    # 變更紀錄切壞。端點在寫入前去掉控制字元（保留內容），#14 審查的縱深防禦。就地讀 git log 驗。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地讀 config-repo 的 git log 驗 commit 內文")
    _set_session(api)
    source = _write_source(sources_root, "note_clean.yaml", b"enabled: no\n")

    _post(api, "/api/configs",
          {"source_path": source, "format": "yaml", "ambiguity_note": "a\x1eb\x1fc"})

    body = subprocess.run(
        ["git", "-C", repo, "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "\x1e" not in body and "\x1f" not in body  # 分隔控制字元被去掉
    assert "abc" in body  # 內容保留、只去掉控制字元


def test_inspect_json_has_no_ambiguities_but_real_field_types(api, sources_root):
    # find_ambiguous 只有 yaml 會回非空；json 一律空。但型別／欄位數仍要是真的——json 的
    # Parsed.document 是原文（round-trip），若直接餵 infer_types 會一律回 0（#195 資安審查）。
    source = _write_source(sources_root, "detect.json", b'{"a": 1, "b": "x"}\n')

    result = _post(api, "/api/inspect", {"source_path": source, "format": "json"})

    assert result["ambiguities"] == []
    assert result["types"]["a"] == "int"
    assert result["types"]["b"] == "string"
    # 欄位數是真的（json 不再一律回 0）：與 types 一致，而 types 至少有 a、b 兩個欄位。
    assert result["field_count"] == len(result["types"])
    assert {"a", "b"} <= set(result["types"])


def test_inspect_a_pathologically_nested_file_is_refused_not_500(api, sources_root):
    # 刻意的深層巢狀會讓 parse／infer_types 遞迴爆掉 → 結構化 422，不是裸 500（#195 資安審查）。
    depth = 5000
    source = _write_source(sources_root, "deep.json", b"[" * depth + b"]" * depth)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": source, "format": "json"})

    assert exc.value.code == _UNPROCESSABLE


def test_inspect_a_syntax_error_is_a_structured_422_with_a_line(api, sources_root):
    # 語法錯誤直接拒絕，結構化 422 帶 file 與 line（供編輯器就地標示，§3.5.3）。
    source = _write_source(sources_root, "broken.yaml", b"a: [1, 2\nb: 3\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": source, "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE
    detail = _detail(exc.value)
    assert detail["file"] == source
    assert isinstance(detail["line"], int)


def test_inspect_a_source_outside_the_whitelist_is_refused(api, tmp_path):
    outside = tmp_path / "secret.yaml"
    outside.write_bytes(b"a: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": str(outside), "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE
    assert "白名單之外" in _detail(exc.value)["message"]


def _hidden_behind_a_loop(sources_root, tmp_path) -> str:
    """白名單內一個連結迴圈＋一個指向白名單外目錄的連結；回一條藏住後者的路徑（F18）。"""
    base = pathlib.Path(sources_root) / "source_unstable"
    base.mkdir(exist_ok=True)
    (tmp_path / "outdir" / "sub").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outdir" / "sub" / "secret.yaml").write_bytes(b"leaked_secret_key: 1\n")
    for name, to in (("loop", base / "loop"), ("link_out", tmp_path / "outdir")):
        link = base / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(to)
    return f"{base}/loop/../link_out/sub/secret.yaml"


def test_inspect_does_not_read_a_file_hidden_behind_a_symlink_loop(api, sources_root, tmp_path):
    # F18（安全）：偵測不需身分。藏在迴圈後面的白名單外檔案，不可以被讀進來、欄位也不可以回出來。
    path = _hidden_behind_a_loop(sources_root, tmp_path)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": path, "format": "yaml"})

    assert (exc.value.code, "leaked_secret_key" in exc.value.read().decode("utf-8")) == (
        _UNPROCESSABLE, False,
    )


def test_inspect_a_non_utf8_source_is_refused(api, sources_root):
    # 二進位／非 UTF-8 的檔案不是文字，無法以文字格式解析 → 結構化 422（不是 500）。
    source = _write_source(sources_root, "binary.yaml", b"\xff\xfe\x00 not utf-8\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": source, "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE
    assert "UTF-8" in _detail(exc.value)["message"]


def test_inspect_an_unknown_format_is_refused(api, sources_root):
    source = _write_source(sources_root, "inspect_badfmt.yaml", b"a: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": source, "format": "notaformat"})

    assert exc.value.code == _UNPROCESSABLE


def test_cli_inspect_goes_through_the_same_endpoint_as_the_page(api, sources_root):
    source = _write_source(sources_root, "cli_inspect.yaml", b"enabled: no\n")

    result = _cli("inspect", "--api", api, "--source", source, "--format", "yaml")

    assert result.returncode == 0
    assert "format：yaml" in result.stdout
    assert "no" in result.stdout  # 歧義列出來了


# ── POST /api/allowed-roots 白名單維護（#202）──────────────────────────────────


def test_get_allowed_roots_lists_the_whitelist_prefixes(api, sources_root):
    # 讀白名單根：browse 的起點、與一般使用者「檢視允許範圍」都靠它。唯讀、無角色門檻。
    result = _get(api, "/api/allowed-roots")

    assert isinstance(result["prefixes"], list)
    assert sources_root in result["prefixes"]  # api 夾具種進去的那個根


def test_a_developer_can_add_a_root_to_the_whitelist(api, tmp_path):
    # 開發者把一個真實可見的目錄加進白名單，回傳更新後的前綴清單、含剛加的那個。
    _set_session(api)  # developer
    new_root = tmp_path / "added_root"
    new_root.mkdir()

    result = _post(api, "/api/allowed-roots", {"prefix": str(new_root)})

    assert str(new_root) in result["prefixes"]


def test_adding_a_root_records_the_session_identity_and_a_server_timestamp(api, repo, tmp_path):
    # 是誰加的取自 session 身分（不由請求自報，否則紀錄可造假）、何時加的由伺服器蓋時間。
    # 端點只回前綴清單，who／when 記在設定檔裡，故讀回檔案驗證（用 core.load，不自己解析）。
    _post(api, "/api/session", {"name": "林工程", "email": "lin@example.com", "role": "developer"})
    recorded = tmp_path / "recorded_root"
    recorded.mkdir()

    _post(api, "/api/allowed-roots", {"prefix": str(recorded)})

    text = pathlib.Path(repo, "allowed-roots.toml").read_text(encoding="utf-8")
    added = next(r for r in load_allowed_roots(text).roots if r.prefix == str(recorded))
    assert added.added_by == "林工程 <lin@example.com>"
    assert added.added_at  # 伺服器蓋了時間戳，非空


def test_adding_a_root_already_in_the_whitelist_is_a_conflict(api, tmp_path):
    # 重複新增（存的是 realpath，尾斜線／symlink 都會解析到同一個）→ 與現狀衝突 409，
    # 不是裸 500。core 的訊息已可行動（指出是哪兩筆）。
    _set_session(api)  # developer
    dup = tmp_path / "dup_root"
    dup.mkdir()
    _post(api, "/api/allowed-roots", {"prefix": str(dup)})  # 第一次成功

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/allowed-roots", {"prefix": str(dup)})  # 第二次衝突

    assert exc.value.code == _CONFLICT


def test_a_normal_user_cannot_add_a_root(api, tmp_path):
    # 白名單維護僅開發者可用（§7.9、W2）：一般使用者被拒，角色不夠是 403。
    _post(api, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})
    new_root = tmp_path / "user_root"
    new_root.mkdir()

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/allowed-roots", {"prefix": str(new_root)})

    assert exc.value.code == _FORBIDDEN


def test_a_root_added_through_the_api_takes_effect_immediately(api, tmp_path):
    # AC9 的核心：新增的根不必重啟就生效——加之前瀏覽被拒，加之後同一個服務就瀏覽得進去。
    _set_session(api)  # developer
    fresh = tmp_path / "fresh_root"
    fresh.mkdir()
    (fresh / "a_file.yaml").write_text("k: 1\n", encoding="utf-8")
    query = urllib.parse.urlencode({"path": str(fresh)})

    # 加之前：白名單外 → 422。
    with pytest.raises(urllib.error.HTTPError) as before:
        _get(api, f"/api/browse?{query}")
    assert before.value.code == _UNPROCESSABLE

    _post(api, "/api/allowed-roots", {"prefix": str(fresh)})

    # 加之後：同一個服務、不重啟，就瀏覽得進去了。
    listing = _get(api, f"/api/browse?{query}")
    assert [entry["name"] for entry in listing["entries"]] == ["a_file.yaml"]


def test_adding_a_root_that_is_not_there_is_a_structured_422(api, tmp_path):
    # 指向不存在的目錄 → 422（AllowedRootUnreachable），不是 500。
    _set_session(api)  # developer
    ghost = tmp_path / "does-not-exist"

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/allowed-roots", {"prefix": str(ghost)})

    assert exc.value.code == _UNPROCESSABLE
    # 只斷言狀態碼分不出「到不了」與別種 422（本檔自訂標準）——斷言被拒原因。
    assert "到不了" in _detail(exc.value)


# ── GET 檢視擴充 + DELETE /api/allowed-roots 白名單維護（#15）──────────────────


def test_get_allowed_roots_also_lists_who_and_when_per_root(api, sources_root):
    # #15 檢視面板要顯示每個根是誰、何時加的。GET 加法式擴充：保留 prefixes（不破壞 #13 的
    # browse 消費），新增 roots——每筆帶原樣 prefix（移除用識別碼）、resolved（realpath，顯示
    # 用）、added_by、added_at。
    result = _get(api, "/api/allowed-roots")

    assert "prefixes" in result  # #13 的欄位還在（加法式，不破壞既有消費）
    seed = next(root for root in result["roots"] if root["resolved"] == sources_root)
    # 誰／何時都被列出且非空——不寫死值：本機夾具種 "seed"／固定時間，映像由 entrypoint 種
    # "部署設定 (CM_ALLOWED_ROOTS)"／蓋當下時間（`CM_SYSTEM_BASE_URL` 對外部映像跑時）。
    assert seed["added_by"] and seed["added_at"]


def test_a_developer_removes_a_root_after_confirming(api, tmp_path):
    # 帶 confirmed 才真刪；刪後回更新的清單、不再含它。
    _set_session(api)  # developer
    doomed = tmp_path / "doomed_root"
    doomed.mkdir()
    _post(api, "/api/allowed-roots", {"prefix": str(doomed)})

    result = _delete(api, "/api/allowed-roots", {"prefix": str(doomed), "confirmed": True})

    assert str(doomed) not in result["prefixes"]


def test_removing_a_root_unconfirmed_lists_the_affected_items_and_conflicts(api, repo, listing):
    # AC3：移除時若有受影響的納管項目，列出並要求確認、不靜默移除。未帶 confirmed → 回受影響
    # 清單 + 409。受影響＝清單檔中 target（realpath）落在被移除前綴（realpath）底下的條目。
    listing("a")  # 納管項目 a，target 在 <repo>/deployed/a.yaml
    _set_session(api)  # developer
    deployed = str(pathlib.Path(repo) / "deployed")
    _post(api, "/api/allowed-roots", {"prefix": deployed})  # 把 <repo>/deployed 加成一個根

    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": deployed, "confirmed": False})

    assert exc.value.code == _CONFLICT
    affected = _detail(exc.value)["affected"]
    assert any("a.yaml" in item["target"] for item in affected)
    assert any(item["ref"].startswith("a@") for item in affected)


def test_a_normal_user_cannot_remove_a_root(api, tmp_path):
    # 白名單維護僅開發者可用（§7.9、W2）：一般使用者被拒，角色不夠是 403。
    _set_session(api)  # developer 先加一個根
    protected = tmp_path / "protected_root"
    protected.mkdir()
    _post(api, "/api/allowed-roots", {"prefix": str(protected)})
    _post(api, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": str(protected), "confirmed": True})

    assert exc.value.code == _FORBIDDEN


def test_removing_a_prefix_that_is_not_in_the_whitelist_is_not_found(api):
    # 以檔案原樣 prefix 定位；定位不到就 404（不是衝突、不是輸入格式錯），不靜默成 no-op。
    _set_session(api)  # developer

    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": "/opt/nowhere-root", "confirmed": True})

    assert exc.value.code == _NOT_FOUND


def test_removing_an_unknown_prefix_unconfirmed_is_404_not_a_confirm_dialog(api):
    # 未確認 + 不存在的前綴：要 404，不是回一個「0 個受影響、請確認」的誤導對話框
    # （拼錯的前綴看起來像真的要刪某個東西）。以檔案原樣 prefix 定位，定位不到就大聲失敗。
    _set_session(api)  # developer

    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": "/opt/typo-root", "confirmed": False})

    assert exc.value.code == _NOT_FOUND


def test_confirmed_removal_deletes_despite_affected_items_which_stay_onboarded(api, repo, tmp_path):
    # AC3 的另一半：受影響清單是資訊性、不連動解除納管。帶 confirmed 就算底下有納管項目也照樣
    # 移除，且那些項目仍在（target 是絕對路徑、apply 不靠白名單）。少了這條，把「資訊性」誤讀成
    # 「阻擋性」（有受影響就一律 409）的回歸會靜默通過。
    root = tmp_path / "confirm_root"
    root.mkdir()
    _write_config_list(repo, ("mfz3k9qd", "underroot", str(root / "deployed.yaml")))
    _set_session(api)  # developer
    _post(api, "/api/allowed-roots", {"prefix": str(root)})

    # 未確認：先回受影響清單（含 underroot）＋409，不靜默移除。
    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": str(root), "confirmed": False})
    assert exc.value.code == _CONFLICT
    assert any(item["ref"].startswith("underroot@") for item in _detail(exc.value)["affected"])

    # 帶 confirmed：儘管底下有受影響項目仍真刪。
    result = _delete(api, "/api/allowed-roots", {"prefix": str(root), "confirmed": True})
    assert str(root) not in result["prefixes"]
    # 受影響的納管項目仍在——移除白名單根不連動解除納管。
    assert any(row["name"] == "underroot" for row in _get(api, "/api/configs"))


def test_affected_list_excludes_entries_outside_the_removed_prefix(api, repo, tmp_path):
    # 受影響＝target 落在被移除前綴底下的條目。單看「有沒有含到該筆」不夠——decide 過度涵蓋
    # （漏掉前綴比對、回傳全部）也會讓 any 斷言通過。放一筆落在前綴外的條目，釘住它不被誤列。
    scope_root = tmp_path / "scope_root"
    scope_root.mkdir()
    _write_config_list(
        repo,
        ("mfz3k9qi", "inside", str(scope_root / "in.yaml")),
        ("mfz3k9qo", "outside", str(tmp_path / "outside.yaml")),
    )
    _set_session(api)  # developer
    _post(api, "/api/allowed-roots", {"prefix": str(scope_root)})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": str(scope_root), "confirmed": False})

    refs = [item["ref"] for item in _detail(exc.value)["affected"]]
    assert any(ref.startswith("inside@") for ref in refs)  # 前綴內的被列出
    assert not any(ref.startswith("outside@") for ref in refs)  # 前綴外的不被誤列


def test_get_roots_resolves_symlink_prefixes_for_display_keeping_the_stored_prefix(
    api, repo, tmp_path
):
    # resolved 是 realpath（顯示用）、prefix 是檔案原樣（移除識別碼）。對一個以 symlink 字面值存入
    # 的根，兩者不同：交換它們（忘了 realpath、或 prefix 誤存 realpath）會讓 symlink 根顯示錯誤、
    # 或識別碼變 realpath 而對不回檔案原樣、刪不掉。現有測試的 prefix 與 resolved 恆等，抓不到對調。
    real = tmp_path / "real_target"
    real.mkdir()
    link = tmp_path / "link_alias"
    link.symlink_to(real)
    roots_path = pathlib.Path(repo, "allowed-roots.toml")
    original = roots_path.read_text(encoding="utf-8")
    roots_path.write_text(
        original + f'\n[[roots]]\nprefix = "{link}"\n'
        'added_by = "seed"\nadded_at = "2026-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    try:
        roots = _get(api, "/api/allowed-roots")["roots"]
        entry = next(r for r in roots if r["prefix"] == str(link))
        assert entry["resolved"] == str(real)  # realpath 解析後（顯示用）
        assert entry["prefix"] == str(link)  # 檔案原樣（移除識別碼），與 resolved 不同
    finally:
        roots_path.write_text(original, encoding="utf-8")  # 還原共享 session repo 的白名單


# ── GET /api/candidate-count（候選檔案數預覽，#206）─────────────────────────


def test_a_developer_gets_a_candidate_count_for_a_prefix_outside_the_whitelist(api, tmp_path):
    # 候選數預覽走白名單外——那正是新增流程的重點（要數的前綴此刻還沒加進白名單）。tmp_path
    # 不在 sources_root（白名單）底下，仍數得到；數的是遞迴的一般檔（含子目錄），不讀內容。
    _set_session(api)  # developer
    prefix = tmp_path / "candidates"
    prefix.mkdir()
    (prefix / "a.yaml").write_text("x")
    (prefix / "b.conf").write_text("y")
    (prefix / "sub").mkdir()
    (prefix / "sub" / "c.ini").write_text("z")

    query = urllib.parse.urlencode({"prefix": str(prefix)})
    result = _get(api, f"/api/candidate-count?{query}")

    assert result == {"count": 3, "capped": False}


def test_a_normal_user_cannot_get_a_candidate_count(api, tmp_path):
    # 走白名單外、探測主機目錄 metadata——套與白名單維護一致的開發者門檻，角色不夠是 403（#206）。
    _post(api, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})
    prefix = tmp_path / "user_prefix"
    prefix.mkdir()

    query = urllib.parse.urlencode({"prefix": str(prefix)})
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/candidate-count?{query}")

    assert exc.value.code == _FORBIDDEN


def test_a_candidate_count_prefix_with_a_literal_dotdot_is_unprocessable(api, tmp_path):
    # 字面 .. 是輸入的值不合法（可逃逸）→ 422，比照 browse 的 io 錯誤映射。
    _set_session(api)  # developer
    query = urllib.parse.urlencode({"prefix": str(tmp_path / "sub" / ".." / "x")})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/candidate-count?{query}")

    assert exc.value.code == _UNPROCESSABLE


def test_a_candidate_count_for_a_nonexistent_prefix_is_unprocessable(api, tmp_path):
    # 不存在／不是目錄 → 422（具名結果，不回 0——0 會把打錯路徑誤報成沒有可納管檔）。
    _set_session(api)  # developer
    query = urllib.parse.urlencode({"prefix": str(tmp_path / "does-not-exist")})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/candidate-count?{query}")

    assert exc.value.code == _UNPROCESSABLE


# ── T9／T17：開發／部署模式（GET /api/mode，#47）────────────────────────────────
# 模式由後端依 `CM_MODE` 判定、回報給前端；沒有改它的端點。角色（能做什麼）與模式（階段行為）
# 正交：同一張權限表在兩種模式下給同一個答案。

_METHOD_NOT_ALLOWED = 405


def test_the_backend_reports_the_mode_and_defaults_to_deployment(api):
    # 就地起的服務沒給 mode、映像裡的 entrypoint 沒設 CM_MODE——兩邊都該落在較嚴的部署模式。
    assert _get(api, "/api/mode") == {"mode": "deployment"}


def test_a_development_mode_server_reports_development(api_developing):
    assert _get(api_developing, "/api/mode") == {"mode": "development"}


def test_the_mode_cannot_be_changed_over_http(api):
    # 前端（或任何 client）改不了模式：沒有寫入端點，POST 是 405 而不是 404——路徑存在、只是不收。
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/mode", {"mode": "development"})

    assert exc.value.code == _METHOD_NOT_ALLOWED
    assert _get(api, "/api/mode") == {"mode": "deployment"}


@pytest.mark.parametrize("server", ["api", "api_developing"])
def test_a_developer_may_maintain_the_whitelist_in_either_mode(server, request, tmp_path):
    # 部署環境下仍可能需要開發者調整白名單（#47）：候選數預覽走開發者門檻，兩種模式都放行。
    base = request.getfixturevalue(server)
    _set_session(base)  # developer
    prefix = tmp_path / "either_mode"
    prefix.mkdir()
    (prefix / "a.yaml").write_text("x")

    query = urllib.parse.urlencode({"prefix": str(prefix)})

    assert _get(base, f"/api/candidate-count?{query}") == {"count": 1, "capped": False}


@pytest.mark.parametrize("server", ["api", "api_developing"])
def test_a_normal_user_may_not_maintain_the_whitelist_in_either_mode(server, request, tmp_path):
    # 反過來也一樣：開發模式不會順手放寬角色——一般使用者在兩種模式下都是 403。
    base = request.getfixturevalue(server)
    _post(base, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})
    prefix = tmp_path / "either_mode_user"
    prefix.mkdir()

    query = urllib.parse.urlencode({"prefix": str(prefix)})
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, f"/api/candidate-count?{query}")

    assert exc.value.code == _FORBIDDEN
    _set_session(base)


# ── #222：onboard／inspect 的例外映射缺口（與 #209 同類）─────────────────────


def test_onboard_write_path_failure_is_a_structured_500_not_a_bare_500(api, sources_root, repo):
    # 案1：寫入/回滾路徑失敗——這裡以 .git/index.lock 讓 git 動不了（回滾的 unstage 也失敗
    # → OnboardLeftBehind）。應映成帶可行動訊息（指名殘留、下一步）的 500，不是丟掉訊息的裸 500。
    _set_session(api)  # 納管要有作者
    src = _write_source(sources_root, "wf_fail.yaml")
    lock = pathlib.Path(repo) / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(api, "/api/configs", {"source_path": src, "format": "yaml"})
        assert exc.value.code == _SERVER_ERROR
        assert "下一步" in exc.value.read().decode("utf-8")  # 帶可行動訊息，非裸 500
    finally:
        lock.unlink()


def test_a_source_path_with_a_nul_byte_is_refused_not_a_bare_500(api):
    # 案2：NUL 字元讓 realpath 拋 ValueError（非 OSError），不接住會漏成裸 500。應回結構化 422。
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": "/tmp/a\x00b.yaml", "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE


def test_an_oversized_ambiguity_note_is_refused_not_a_bare_500(api, sources_root):
    # 案3：ambiguity_note 無上限時過大會讓 git commit 以 E2BIG 失敗→裸 500。設上限→422。
    _set_session(api)
    src = _write_source(sources_root, "bignote.yaml")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs",
              {"source_path": src, "format": "yaml", "ambiguity_note": "x" * 20_000})

    assert exc.value.code == _UNPROCESSABLE


def test_add_root_write_failure_is_a_structured_500_not_a_bare_500(api, repo, tmp_path):
    # #248：寫入/commit/回滾失敗（.git/index.lock → git 動不了、回滾 unstage 也失敗
    # → AllowedRootLeftBehind）應映成帶可行動訊息的 500，不裸 500（比照 #222 onboard）。
    _set_session(api)  # developer
    new_root = tmp_path / "wf_add_root"
    new_root.mkdir()
    lock = pathlib.Path(repo) / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(api, "/api/allowed-roots", {"prefix": str(new_root)})
        assert exc.value.code == _SERVER_ERROR
        assert "下一步" in exc.value.read().decode("utf-8")
    finally:
        lock.unlink()
        # 回滾失敗留下的擴張還原給後續 spec（session 共用 repo）。
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=False)


def test_a_source_with_a_control_char_in_its_path_is_refused_not_a_bare_500(api, sources_root):
    # #248：來源 realpath 最後兩段含控制字元 → derive_name → record → RecordFieldUnsafe
    # （ChangeError）。onboard 的 rollback 已還原 repo（無殘留），但錯誤映射漏 ChangeError
    # → 裸 500。分隔符守衛正確觸發、應回 422，不是被誤報成 Internal Server Error。
    _set_session(api)
    weird_dir = pathlib.Path(sources_root) / "ctl\x1fdir"
    weird_dir.mkdir()
    (weird_dir / "cfg.yaml").write_text("a: 1\n", encoding="utf-8")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": str(weird_dir / "cfg.yaml"), "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE


def test_remove_root_write_failure_is_a_structured_500_not_a_bare_500(api, repo, tmp_path):
    # #248：移除路徑的寫入/commit/回滾失敗也映成 500，不裸 500（與 add 對稱）。
    _set_session(api)  # developer
    doomed = tmp_path / "doomed_root"
    doomed.mkdir()
    _post(api, "/api/allowed-roots", {"prefix": str(doomed)})  # 先加（committed）才刪得到
    lock = pathlib.Path(repo) / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _delete(api, "/api/allowed-roots", {"prefix": str(doomed), "confirmed": True})
        assert exc.value.code == _SERVER_ERROR
    finally:
        lock.unlink()
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=False)


# ── #250：CLI（api/cli.py）錯誤呈現——裸 traceback／傾印，違反 T10 ────────────


@contextlib.contextmanager
def _foreign_server(body):
    """一個回 200＋指定 body 的假服務——模擬 --api 指到別的服務／代理落地頁。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        do_GET = _reply
        do_POST = _reply

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def test_cli_against_a_nonnumeric_port_is_a_readable_error_not_a_traceback():
    # #250：http://localhost:abc → http.client.InvalidURL（非 OSError／ValueError）先前逃出成
    # 裸 traceback。應回退成可讀訊息＋非零退出（T10）。
    result = _cli("list", "--api", "http://localhost:abc")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "config_manager:" in result.stderr


def test_cli_against_a_foreign_200_service_is_a_readable_error_not_a_traceback():
    # #250：外來服務回 200＋合法但異形 JSON → 成功路徑欄位存取（在 try 外）拋 KeyError/TypeError
    # → 裸 traceback。應回退成「非預期的回應」＋非零退出。
    with _foreign_server('[{"wrong": "shape"}]') as foreign:
        result = _cli("list", "--api", foreign)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "非預期的回應" in result.stderr


def test_cli_import_validation_error_is_readable_not_echoed_input(api, sources_root):
    # #250：FastAPI 的 request-validation 錯誤 detail 是 list 形；先前 _http_detail 直接 str() →
    # 把使用者送的超長 note 原樣回吐。應接每筆 msg 成可讀一行、不回吐 input。
    _set_session(api)
    src = _write_source(sources_root, "cli_note.yaml")
    huge = "x" * 20_000

    result = _cli(
        "import", "--api", api, "--source", src, "--format", "yaml", "--note", huge, *_AUTHOR_FLAGS
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert ("x" * 1000) not in result.stderr  # 沒有把超長輸入原樣回吐


def test_cli_serve_out_of_range_port_is_a_readable_error(monkeypatch, tmp_path):
    # #250：--port 99999 → uvicorn 綁 socket 時 OverflowError（非 OSError）裸 traceback。
    # serve_plan 應在計畫階段具名擋下 → 可讀訊息＋退出碼 2，不啟動也不崩。
    monkeypatch.setenv("CM_CONFIG_REPO", str(tmp_path))
    config_exit = 2  # 啟動接線錯（比照 ConfigRepoMissing 的退出碼）

    result = _cli("serve", "--port", "99999")

    assert result.returncode == config_exit
    assert "Traceback" not in result.stderr
    assert "65535" in result.stderr


def test_onboard_after_the_uid_horizon_is_a_500_with_a_message(api, sources_root, monkeypatch):
    # #257/#231：uid 的 8 碼 base36 空間用盡（2059 之後）→ new_uid 丟 UidHorizonReached。它直接
    # 繼承 Exception、不屬 _onboard_config 上面任何族，先前漏接成裸 500。應映成帶可行動訊息的 500。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地 monkeypatch new_uid，外部映像控不了 server 端模組")

    def _horizon(_now):
        raise UidHorizonReached("uid 空間用盡（horizon 2059）。下一步：見 ADR-00000012／#231")

    monkeypatch.setattr("config_manager.io.onboard.new_uid", _horizon)
    _set_session(api)
    source = _write_source(sources_root, "onboard_horizon.yaml", b"a: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": source, "format": "yaml"})

    assert exc.value.code == _SERVER_ERROR
    assert "2059" in _detail(exc.value)  # identity 的可行動訊息保住，不是裸 500


# ── T9：草稿與進版（#19；T18 移出的批次原子性在此）──────────────────────────


def _clear_drafts(api):
    # 階段掛在 app 上、跨規格存活；每則自己清，不靠「前一則有清」（#153）。捨棄也要編輯權（F23），
    # 取得要有身分：這個瀏覽器還沒說自己是誰才設——不改掉前一則刻意設的角色。
    if _get(api, "/api/session") is None:
        _set_session(api)
    _delete(api, "/api/drafts", {})


def _onboard(api, sources_root, name, content=b"max_vel: 0.8\n", note=""):
    _set_session(api)
    source = _write_source(sources_root, name, content)
    payload = {"source_path": source, "format": "yaml", "ambiguity_note": note}
    return _post(api, "/api/configs", payload)


def _head(repo):
    return subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_saving_a_draft_lists_it_and_leaves_the_target_untouched(api, sources_root):
    # 儲存草稿不寫到目標位置：草稿只存在編輯階段（T18）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "draft_keep.yaml")

    saved = _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"max_vel": 1.2}})

    assert [d["uid"] for d in saved["drafts"]] == [entry["uid"]]
    assert _get(api, "/api/drafts")["count"] == 1
    assert pathlib.Path(entry["target"]).read_bytes() == b"max_vel: 0.8\n"


def test_saving_a_draft_records_no_change(api, sources_root, repo):
    # 儲存草稿不產生變更紀錄（T18）——就地比 HEAD。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地讀 config-repo 的 HEAD")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "draft_norecord.yaml")
    before = _head(repo)

    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"max_vel": 1.2}})

    assert _head(repo) == before


def test_a_draft_failing_layer_one_is_a_structured_422_and_is_not_kept(api, sources_root):
    # 納管時確認過的歧義值（`no`）仍在來源複本裡；改別的參數存草稿時第 1 層擋下、逐條回
    # 行號／訊息／建議（結構化，不是純字串），且階段不變。
    _clear_drafts(api)
    entry = _onboard(
        api, sources_root, "draft_bad.yaml", b"enabled: no\nmax_vel: 0.8\n", note="no 讀作 false"
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"max_vel": 1.2}})

    detail = _detail(exc.value)
    assert exc.value.code == _UNPROCESSABLE
    assert (detail["uid"], detail["problems"][0]["line"]) == (entry["uid"], 1)
    assert detail["problems"][0]["suggestion"]
    assert _get(api, "/api/drafts")["count"] == 0


def test_saving_a_draft_with_an_unknown_path_is_unprocessable(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "draft_path.yaml")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"nope": 1}})

    assert exc.value.code == _UNPROCESSABLE
    assert "nope" in _detail(exc.value)


def test_saving_a_draft_for_an_unknown_uid_is_not_found(api):
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/drafts", {"uid": "zzzzzzz9", "edits": {"a": 1}})

    assert exc.value.code == _NOT_FOUND


def test_discarding_one_draft_leaves_the_other_and_an_unknown_uid_is_not_found(api, sources_root):
    _clear_drafts(api)
    first = _onboard(api, sources_root, "discard_a.yaml")
    second = _onboard(api, sources_root, "discard_b.yaml")
    _post(api, "/api/drafts", {"uid": first["uid"], "edits": {"max_vel": 1.2}})
    _post(api, "/api/drafts", {"uid": second["uid"], "edits": {"max_vel": 1.5}})

    left = _delete(api, f"/api/drafts/{first['uid']}", {})

    assert [d["uid"] for d in left["drafts"]] == [second["uid"]]
    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, f"/api/drafts/{first['uid']}", {})
    assert exc.value.code == _NOT_FOUND


def test_promote_writes_every_target_and_clears_the_drafts(api, sources_root):
    # 進版是全域動作：兩份草稿一起送出、各自的目標改變、進版後草稿清空（T18）。
    _clear_drafts(api)
    first = _onboard(api, sources_root, "promote_a.yaml")
    second = _onboard(api, sources_root, "promote_b.yaml", b"speed: 1\n")
    _post(api, "/api/drafts", {"uid": first["uid"], "edits": {"max_vel": 1.2}})
    _post(api, "/api/drafts", {"uid": second["uid"], "edits": {"speed": 2}})

    result = _post(api, "/api/promote", {})

    assert result["promoted"] == [first["uid"], second["uid"]]
    assert pathlib.Path(first["target"]).read_bytes() == b"max_vel: 1.2\n"
    assert pathlib.Path(second["target"]).read_bytes() == b"speed: 2\n"
    assert _get(api, "/api/drafts")["count"] == 0


def test_promote_records_one_cfg_change_per_config_authored_by_the_session(api, sources_root, repo):
    # 每份 config 各一筆變更紀錄、同一次操作、作者＝進版者（T18）。就地讀 git log 驗。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地讀 config-repo 的 git log")
    _clear_drafts(api)
    first = _onboard(api, sources_root, "record_a.yaml")
    second = _onboard(api, sources_root, "record_b.yaml")
    _post(api, "/api/drafts", {"uid": first["uid"], "edits": {"max_vel": 1.2}})
    _post(api, "/api/drafts", {"uid": second["uid"], "edits": {"max_vel": 1.5}})

    _post(api, "/api/promote", {})

    log = subprocess.run(
        ["git", "-C", repo, "log", "-2", "--format=%s|%an <%ae>"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert log == [
        f"cfg({second['uid']}): 修改參數（{second['name']}@{second['hostname']}）"
        "|陳小明 <ming@example.com>",
        f"cfg({first['uid']}): 修改參數（{first['name']}@{first['hostname']}）"
        "|陳小明 <ming@example.com>",
    ]


def test_promote_with_a_draft_whose_entry_left_the_list_writes_nothing(api, sources_root, listing):
    # 驗證失敗（草稿的條目已不在清單檔）→ 整批不進版：不寫出、不記錄，錯誤指名那一份。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "promote_gone.yaml")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"max_vel": 1.2}})
    listing()  # 清單檔換成空的：該 uid 不再受管

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/promote", {})

    assert exc.value.code == _UNPROCESSABLE
    assert _detail(exc.value)["uid"] == entry["uid"]
    assert pathlib.Path(entry["target"]).read_bytes() == b"max_vel: 0.8\n"


def test_promote_without_drafts_is_a_conflict(api):
    _clear_drafts(api)
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/promote", {})

    assert exc.value.code == _CONFLICT


def test_promote_kth_write_failure_restores_targets_and_withdraws_records(
    api, sources_root, repo, monkeypatch
):
    # T9：寫出 N 份、第 k 份失敗 → 前 k−1 份目標還原為進版前內容、已產生的紀錄一併撤銷，
    # 最終狀態與進版前逐位元組相同；回帶訊息的 500、草稿保留供重試。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地 monkeypatch 寫出並讀 HEAD，外部映像控不了 server 端模組")
    _clear_drafts(api)
    first = _onboard(api, sources_root, "rollback_a.yaml")
    second = _onboard(api, sources_root, "rollback_b.yaml", b"speed: 1\n")
    _post(api, "/api/drafts", {"uid": first["uid"], "edits": {"max_vel": 1.2}})
    _post(api, "/api/drafts", {"uid": second["uid"], "edits": {"speed": 2}})
    before = _head(repo)
    real_write = promote_io.write
    calls = []
    failing_call = 2  # 第 k 份（k=2）：第 1 份已寫出、已記錄，回滾才有東西可還原

    def _second_write_fails(target, content, permissions, roots):
        calls.append(target)
        if len(calls) == failing_call:
            raise TargetNotWritable(f"注入：{target} 寫不進去。下一步：無")
        real_write(target, content, permissions, roots)

    monkeypatch.setattr(promote_io, "write", _second_write_fails)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/promote", {})

    assert exc.value.code == _SERVER_ERROR
    assert "注入" in _detail(exc.value)
    assert pathlib.Path(first["target"]).read_bytes() == b"max_vel: 0.8\n"  # 第 1 份已寫出→還原
    assert pathlib.Path(second["target"]).read_bytes() == b"speed: 1\n"
    assert _head(repo) == before  # 第 1 份的紀錄撤銷
    assert _get(api, "/api/drafts")["count"] == failing_call  # 兩份草稿都保留，修好後可重試


# ── T9：單筆內容（GET /api/configs/{uid}，#20）────────────────────────────────


def test_config_detail_returns_the_types_and_values_of_the_source_copy(api, sources_root):
    # 欄位表據 types（路徑→型別）＋values（值樹）渲染；兩者都來自來源複本、不由前端猜。
    entry = _onboard(
        api, sources_root, "detail.yaml", b"max_vel: 0.8\nenabled: true\nnested:\n  n: 1\n"
    )

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert detail["types"] == {
        "max_vel": "float", "enabled": "bool", "nested": "dict", "nested.n": "int",
    }
    assert detail["values"] == {"max_vel": 0.8, "enabled": True, "nested": {"n": 1}}
    assert (detail["target"], detail["format"]) == (entry["target"], "yaml")
    assert detail["permissions"]["mode"]  # 條目自己的權限（納管時的快照）


def test_config_detail_for_an_unknown_uid_is_not_found(api):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, "/api/configs/zzzzzzz9")

    assert exc.value.code == _NOT_FOUND


def test_config_detail_of_a_raw_config_has_no_fields_rather_than_zero_fields(api, sources_root):
    # raw 不解析：types 空、values 為 null——介面顯示「未結構化」，不假裝有 0 個欄位（§7.5.4）。
    _set_session(api)
    source = _write_source(sources_root, "detail_raw.conf", b"whatever: [not parsed\n")
    entry = _post(api, "/api/configs", {"source_path": source, "format": "raw"})

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert (detail["types"], detail["values"]) == ({}, None)


def test_config_detail_carries_the_saved_draft_beside_the_source_values(api, sources_root):
    # #21：有草稿時 draft_values 是草稿的值樹、values 仍是來源值——介面據此並列「目前值／來源值」。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "detail_draft.yaml", b"count: 3\n")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 4}})

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert (detail["values"], detail["draft_values"]) == ({"count": 3}, {"count": 4})


def test_a_double_edited_through_json_still_promotes_with_a_decimal_point(api, sources_root):
    # JSON 分不出 5 與 5.0：介面送 5.0 到後端已是 int。型別由原值決定——原值是 double，寫出仍是
    # 5.0（ADR-00000013 的招牌保證，端到端釘住，#21）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "double_json.yaml", b"max_vel: 0.8\n")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"max_vel": 5}})

    _post(api, "/api/promote", {})

    assert pathlib.Path(entry["target"]).read_bytes() == b"max_vel: 5.0\n"


# ── T9：變更歷史（GET /api/configs/{uid}/history，#23）─────────────────────


def _history(api, uid, prefix=None):
    query = f"?prefix={prefix}" if prefix else ""
    return _get(api, f"/api/configs/{uid}/history{query}")


def test_history_defaults_to_content_changes_only_newest_first(api, sources_root):
    # 預設只看內容變更（cfg＋adopt）：納管的 import 不出現；兩次進版各一筆、最新在前、帶作者與時間。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "hist.yaml", b"count: 1\n")
    for value in (2, 3):
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": value}})
        _post(api, "/api/promote", {})

    changes = _history(api, entry["uid"])

    assert [change["kind"] for change in changes] == ["cfg", "cfg"]
    assert changes[0]["author"] == "陳小明 <ming@example.com>"
    assert changes[0]["at"] >= changes[1]["at"]  # ISO 8601 可直接比大小
    assert changes[0]["summary"].startswith("修改參數")


def test_history_prefix_picks_the_kinds_to_show(api, sources_root):
    # ?prefix=import,cfg 把納管那筆也列出來（「全部」就是六種都給）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "hist_prefix.yaml", b"count: 1\n")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})
    _post(api, "/api/promote", {})

    kinds = [change["kind"] for change in _history(api, entry["uid"], "import,cfg")]

    assert kinds == ["cfg", "import"]
    assert [c["kind"] for c in _history(api, entry["uid"], "import")] == ["import"]


def test_history_with_an_unknown_prefix_is_unprocessable_not_unfiltered(api, sources_root):
    entry = _onboard(api, sources_root, "hist_bad.yaml", b"count: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _history(api, entry["uid"], "cfg,bogus")

    assert exc.value.code == _UNPROCESSABLE
    assert "bogus" in _detail(exc.value)


def test_history_for_an_unknown_uid_is_not_found(api):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _history(api, "zzzzzzz9")

    assert exc.value.code == _NOT_FOUND


# ── T9：退版（POST /api/configs/{uid}/revert，#24）────────────────────────────


def _promote_value(api, uid, path, value):
    _post(api, "/api/drafts", {"uid": uid, "edits": {path: value}})
    _post(api, "/api/promote", {})


def test_revert_writes_the_old_content_back_as_a_new_record_without_rewriting_history(
    api, sources_root
):
    # ADR-00000005：退版是反向變更——目標內容回到那一版、歷史多一筆 revert（rollback to <sha>），
    # 先前的紀錄全部都在（未截斷）；預設的「只看內容變更」不顯示這筆 revert。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "revert.yaml", b"count: 1\n")
    _promote_value(api, entry["uid"], "count", 2)
    _promote_value(api, entry["uid"], "count", 3)
    first_cfg = _history(api, entry["uid"])[-1]["sha"]

    result = _post(api, f"/api/configs/{entry['uid']}/revert", {"version": first_cfg})

    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 2\n"
    assert result["record"]["summary"] == f"rollback to {first_cfg[:7]}"
    everything = _history(api, entry["uid"], "import,cfg,revert,adopt,meta,unmanage")
    assert [c["kind"] for c in everything] == ["revert", "cfg", "cfg", "import"]
    assert [c["kind"] for c in _history(api, entry["uid"])] == ["cfg", "cfg"]


def test_revert_to_a_version_outside_this_configs_history_is_refused(api, sources_root):
    # 拿別份 config 的 sha 會把別人的內容寫進來——擋成 422、什麼都不動。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "revert_a.yaml", b"count: 1\n")
    other = _onboard(api, sources_root, "revert_b.yaml", b"speed: 1\n")
    foreign = _history(api, other["uid"], "import")[0]["sha"]

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/revert", {"version": foreign})

    assert exc.value.code == _UNPROCESSABLE
    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 1\n"


def test_revert_while_a_draft_is_pending_is_a_conflict(api, sources_root):
    # 草稿以退版前的來源為底；退了就對不上——先進版或捨棄（預設落向安全）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "revert_draft.yaml", b"count: 1\n")
    version = _history(api, entry["uid"], "import")[0]["sha"]
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 9}})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/revert", {"version": version})

    assert exc.value.code == _CONFLICT
    _clear_drafts(api)


def test_revert_of_an_unknown_uid_is_not_found(api):
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs/zzzzzzz9/revert", {"version": "0123456"})

    assert exc.value.code == _NOT_FOUND


# ── T9：單一版本內容（GET /api/configs/{uid}/history/{sha}，#26）────────────


def test_version_detail_returns_the_values_of_that_version(api, sources_root):
    # 歷史檢視據此算參數層級差異：那一版的 values／types，形狀同單筆內容端點。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "version.yaml", b"count: 1\n")
    _promote_value(api, entry["uid"], "count", 2)
    import_sha = _history(api, entry["uid"], "import")[0]["sha"]

    version = _get(api, f"/api/configs/{entry['uid']}/history/{import_sha[:7]}")

    assert (version["sha"], version["values"], version["types"]) == (
        import_sha, {"count": 1}, {"count": "int"},
    )
    assert _get(api, f"/api/configs/{entry['uid']}")["values"] == {"count": 2}  # 目前版本另讀


def test_version_detail_of_another_configs_sha_is_refused(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "version_a.yaml", b"count: 1\n")
    other = _onboard(api, sources_root, "version_b.yaml", b"speed: 1\n")
    foreign = _history(api, other["uid"], "import")[0]["sha"]

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/configs/{entry['uid']}/history/{foreign}")

    assert exc.value.code == _UNPROCESSABLE


# ── T9：一筆紀錄改了什麼（人工驗收 U25 的回歸，F10）──────────────────────────
# 點一筆要看的是「那一次改了什麼」：版本內容另帶這一筆寫入之前（previous）、目前來源（current）、
# 緊鄰的前一筆紀錄（preceding）。


def _version(api, uid, sha):
    return _get(api, f"/api/configs/{uid}/history/{sha}")


def test_the_newest_change_carries_the_values_it_started_from(api, sources_root):
    # 1 → 2 → 3：最新那一筆（→3）寫入之前是 2——不是「和目前比」那樣什麼都沒有。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_newest.yaml", b"count: 1\n")
    _promote_value(api, entry["uid"], "count", 2)
    _promote_value(api, entry["uid"], "count", 3)
    newest = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], newest)

    assert (version["previous"]["values"], version["values"]) == ({"count": 2}, {"count": 3})


def test_onboarding_has_no_earlier_version_and_no_preceding_record(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_first.yaml", b"count: 1\n")
    imported = _history(api, entry["uid"], "import")[0]["sha"]

    version = _version(api, entry["uid"], imported)

    assert (version["previous"], version["previous_error"], version["preceding"]) == (
        None, None, None,
    )


def test_the_value_before_a_change_is_what_came_just_before_even_a_revert(api, sources_root):
    # 1 → 2 → 3 → 退回到 2 → 5：→5 那一筆寫入之前是退版後的 2，不是跳過退版、拿 3 來比；
    # 緊鄰的前一筆紀錄就是那筆退版。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_after_revert.yaml", b"count: 1\n")
    _promote_value(api, entry["uid"], "count", 2)
    second = _history(api, entry["uid"])[0]["sha"]
    _promote_value(api, entry["uid"], "count", 3)
    _post(api, f"/api/configs/{entry['uid']}/revert", {"version": second})
    _promote_value(api, entry["uid"], "count", 5)
    newest = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], newest)

    assert (version["previous"]["values"], version["preceding"]["kind"]) == ({"count": 2}, "revert")


def test_an_overwrite_record_has_the_same_values_before_and_after(api, sources_root):
    # 以來源覆蓋目標只留一筆空的修改參數紀錄：設定庫的內容沒變，前後相同。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_overwrite.yaml", b"count: 1\n")
    pathlib.Path(entry["target"]).write_bytes(b"count: 9\n")
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "overwrite"})
    overwrite = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], overwrite)

    assert version["previous"]["values"] == version["values"] == {"count": 1}


def test_a_manual_type_does_not_change_the_types_versions_are_compared_with(api, sources_root):
    # 人工指定只影響欄位表：720 指定成 double 之後，目前來源比較用的型別仍從內容推斷（int），
    # 和那一版同一套規則——否則會出現 720 → 720.0 這種假差異。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_manual.yaml", b"height: 720\n")
    _specify(api, entry["uid"], "height", "float")
    imported = _history(api, entry["uid"], "import")[0]["sha"]

    version = _version(api, entry["uid"], imported)

    assert (version["current"]["types"], version["types"]) == ({"height": "int"}, {"height": "int"})


def test_an_integer_turned_decimal_in_the_content_is_a_type_change(api, sources_root):
    # 內容裡的型別真的變了（目標被改成 720.0、再納入來源）：前後的型別不同，照樣算改了。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_decimal.yaml", b"height: 720\n")
    pathlib.Path(entry["target"]).write_bytes(b"height: 720.0\n")
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})
    adopted = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], adopted)

    assert (version["previous"]["types"], version["types"]) == (
        {"height": "int"}, {"height": "float"},
    )


def test_a_number_turned_string_in_the_content_is_a_type_change(api, sources_root):
    # 5 → "5"：顯示出來的字一樣，前後的型別不同（int 對 string），照樣算改了。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_string.yaml", b"count: 5\n")
    pathlib.Path(entry["target"]).write_bytes(b'count: "5"\n')
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})
    adopted = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], adopted)

    assert (version["previous"]["types"], version["types"]) == (
        {"count": "int"}, {"count": "string"},
    )


def test_only_the_literal_changed_is_the_same_value_and_type(api, sources_root):
    # TOML 的 1_000 與 1000 都是整數 1000：只有字面寫法不同，值與型別都相同
    # （字面寫法是 F9 那一類，不算參數差異）。
    _clear_drafts(api)
    entry = _onboard_as(api, sources_root, "f10_literal.toml", b"n = 1_000\n", "toml")
    pathlib.Path(entry["target"]).write_bytes(b"n = 1000\n")
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})
    adopted = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], adopted)

    assert (version["previous"]["values"], version["previous"]["types"]) == (
        version["values"], version["types"],
    )


def test_each_list_element_carries_its_own_type(api, sources_root):
    # 清單元素在 types 裡整條共用一個合併後的型別（w[]）；leaf_types 每一格各自算：
    # [1, 2.5] 的第 0 格是 int，改成 [1.0, 2.5] 後是 float——逐格比才看得出這一格真的改了。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_leaves.yaml", b"w: [1, 2.5]\n")
    pathlib.Path(entry["target"]).write_bytes(b"w: [1.0, 2.5]\n")
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})
    adopted = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], adopted)

    assert (version["previous"]["leaf_types"], version["leaf_types"]) == (
        {"w": "list", "w[0]": "int", "w[1]": "float"},
        {"w": "list", "w[0]": "float", "w[1]": "float"},
    )

def test_an_unreadable_previous_version_is_reported_not_called_the_first(api, sources_root, repo):
    # 前一版讀不到或解析不了（有人繞過工具把來源改壞）：這一版照常回，previous 為 null 並說原因與
    # 下一步——不能說成「這是第一筆」。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的來源複本並提交")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f10_broken.yaml", b"count: 1\n")
    source = _get(api, f"/api/configs/{entry['uid']}")["source"]
    (pathlib.Path(repo) / source).write_text("count: [1\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "--", source], check=True)
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=dev", "-c", "user.email=d@e.x", "commit", "-q",
         "-m", "手動改壞來源（不是本工具的紀錄）"],
        check=True,
    )
    pathlib.Path(entry["target"]).write_bytes(b"count: 7\n")
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})
    adopted = _history(api, entry["uid"])[0]["sha"]

    version = _version(api, entry["uid"], adopted)

    assert version["previous"] is None
    assert "下一步" in version["previous_error"] and version["values"] == {"count": 7}


# ── T9：解除納管（DELETE /api/configs/{uid}，#28）──────────────────────────────


def test_unmanaging_removes_the_config_from_the_list_and_keeps_the_target_file(api, sources_root):
    # §5.5：解除納管回到未納管、不刪 target；回被解除的條目，清單不再列它。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "unmanage.yaml", b"count: 1\n")

    removed = _send(api, "DELETE", f"/api/configs/{entry['uid']}")

    assert removed["uid"] == entry["uid"]
    assert entry["uid"] not in [row["uid"] for row in _get(api, "/api/configs")]
    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 1\n"


def test_unmanaging_records_an_unmanage_change_by_the_session_identity(api, sources_root, repo):
    # 解除納管本身留紀錄、作者＝身分。條目已不在清單、歷史端點以 404 擋，故就地讀 git log。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地讀 config-repo 的 git log")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "unmanage_rec.yaml", b"count: 1\n")

    _send(api, "DELETE", f"/api/configs/{entry['uid']}")

    latest = subprocess.run(
        ["git", "-C", repo, "log", "-1", "--format=%s|%an <%ae>"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subject = f"unmanage({entry['uid']}): 解除管理（{entry['name']}@{entry['hostname']}）"
    assert latest == f"{subject}|陳小明 <ming@example.com>"


def test_unmanaging_with_a_pending_draft_is_a_conflict(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "unmanage_draft.yaml", b"count: 1\n")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _send(api, "DELETE", f"/api/configs/{entry['uid']}")

    assert exc.value.code == _CONFLICT
    _clear_drafts(api)


def test_unmanaging_an_unknown_uid_is_not_found(api):
    _set_session(api)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _send(api, "DELETE", "/api/configs/zzzzzzz9")

    assert exc.value.code == _NOT_FOUND


# ── T9：偏離處置與寫出修復（POST /api/configs/{uid}/resolve、/apply，#29）──────


def _drifted(api, sources_root, name, content=b"count: 1\n", edited=b"count: 7\n"):
    """納管後在介面外改 target → 偏離。回條目。"""
    _clear_drafts(api)
    entry = _onboard(api, sources_root, name, content)
    pathlib.Path(entry["target"]).write_bytes(edited)
    return entry


def _state_of(api, uid):
    return next(row["state"] for row in _get(api, "/api/configs") if row["uid"] == uid)


def test_overwrite_puts_the_source_back_on_the_target_and_records_the_decision(api, sources_root):
    # 以來源覆蓋目標：目標內容回到來源、狀態回到一致；repo 沒變但記一筆（誰決定丟掉現場修改）。
    entry = _drifted(api, sources_root, "drift_overwrite.yaml")
    assert _state_of(api, entry["uid"]) == "drift"

    result = _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "overwrite"})

    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 1\n"
    assert _state_of(api, entry["uid"]) == "in_sync"
    assert result["record"]["kind"] == "cfg" and "覆蓋" in result["record"]["summary"]
    assert [c["kind"] for c in _history(api, entry["uid"])] == ["cfg"]


def test_adopt_makes_the_target_content_the_source_with_an_adopt_record(api, sources_root):
    # 將目標現況納入來源：走完整驗證 → adopt 紀錄 → 寫出；來源複本現在等於現場內容、狀態一致。
    entry = _drifted(api, sources_root, "drift_adopt.yaml")

    result = _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})

    assert _get(api, f"/api/configs/{entry['uid']}")["values"] == {"count": 7}
    assert _state_of(api, entry["uid"]) == "in_sync"
    assert result["record"]["kind"] == "adopt"
    assert [c["kind"] for c in _history(api, entry["uid"])] == ["adopt"]


def test_adopt_of_an_invalid_target_is_refused_with_the_line_and_nothing_changes(api, sources_root):
    # 含非法值則被拒並說明原因（A3）：壞內容不進來源、沒有 adopt 紀錄、狀態仍是偏離。
    entry = _drifted(api, sources_root, "drift_bad.yaml", edited=b"enabled: yes\ncount: 7\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})

    detail = _detail(exc.value)
    assert exc.value.code == _UNPROCESSABLE
    assert detail["problems"][0]["line"] == 1 and "先納入、待修正" in detail["message"]
    assert _get(api, f"/api/configs/{entry['uid']}")["values"] == {"count": 1}
    assert _history(api, entry["uid"], "adopt") == []
    assert _state_of(api, entry["uid"]) == "drift"


def test_adopt_draft_loads_the_target_into_a_draft_and_warns_instead_of_refusing(api, sources_root):
    # 先納入、待修正：壞內容不進來源、也不靜默丟棄——載入草稿並回警告，來源與目標都不動。
    entry = _drifted(
        api, sources_root, "drift_adopt_draft.yaml", edited=b"enabled: yes\ncount: 7\n"
    )

    result = _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt_draft"})

    assert result["warnings"][0]["line"] == 1 and result["warnings"][0]["suggestion"]
    assert [d["uid"] for d in result["drafts"]] == [entry["uid"]]
    detail = _get(api, f"/api/configs/{entry['uid']}")
    assert (detail["values"], detail["draft_values"]) == (
        {"count": 1}, {"enabled": "yes", "count": 7},
    )
    assert _state_of(api, entry["uid"]) == "drift"
    _clear_drafts(api)


def test_resolving_a_config_that_is_not_drifted_is_a_conflict(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "no_drift.yaml", b"count: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "overwrite"})

    assert exc.value.code == _CONFLICT


def test_apply_repairs_a_missing_target_from_the_source_without_a_record(api, sources_root):
    # 未部署（目標被刪）→ 一鍵寫出修復：目標回來、狀態一致；repo 沒變、不留紀錄。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "missing.yaml", b"count: 1\n")
    pathlib.Path(entry["target"]).unlink()
    assert _state_of(api, entry["uid"]) == "missing"
    every = "import,cfg,revert,adopt,meta,unmanage"
    before = [c["sha"] for c in _history(api, entry["uid"], every)]

    _post(api, f"/api/configs/{entry['uid']}/apply", {})

    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 1\n"
    assert _state_of(api, entry["uid"]) == "in_sync"
    assert [c["sha"] for c in _history(api, entry["uid"], every)] == before


def test_apply_refuses_a_target_that_exists_and_differs_and_leaves_it_alone(api, sources_root):
    # 寫出修復不留紀錄，所以它不能覆蓋任何既有內容：目標在、而且跟來源不同（偏離）時直接打這支
    # 端點，原本會無紀錄地把現場內容蓋掉。現在 409，檔案一個位元組都沒動。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "apply_drift.yaml", b"count: 1\n")
    target = pathlib.Path(entry["target"])
    target.write_bytes(b"count: 9  # changed on site\n")
    before = target.stat()
    every = "import,cfg,revert,adopt,meta,unmanage"
    history = [c["sha"] for c in _history(api, entry["uid"], every)]

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/apply", {})

    assert exc.value.code == _CONFLICT
    detail = _detail(exc.value)
    assert str(target) in detail and "以來源覆蓋目標" in detail and "下一步：" in detail
    assert target.read_bytes() == b"count: 9  # changed on site\n"
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert _state_of(api, entry["uid"]) == "drift"
    assert [c["sha"] for c in _history(api, entry["uid"], every)] == history


def test_apply_refuses_a_target_that_exists_even_when_it_matches_the_source(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "apply_in_sync.yaml", b"count: 1\n")
    target = pathlib.Path(entry["target"])
    before = target.stat()

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/apply", {})

    assert exc.value.code == _CONFLICT
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert _state_of(api, entry["uid"]) == "in_sync"


def test_overwriting_an_existing_target_goes_through_resolve_and_leaves_a_record(
    api, sources_root
):
    # 被寫出修復拒絕之後，正路還在：以來源覆蓋目標——要身分、留一筆有作者的紀錄。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "apply_then_overwrite.yaml", b"count: 1\n")
    target = pathlib.Path(entry["target"])
    target.write_bytes(b"count: 9\n")
    every = "import,cfg,revert,adopt,meta,unmanage"
    before = len(_history(api, entry["uid"], every))
    with pytest.raises(urllib.error.HTTPError):
        _post(api, f"/api/configs/{entry['uid']}/apply", {})

    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "overwrite"})

    assert target.read_bytes() == b"count: 1\n"
    records = _history(api, entry["uid"], every)
    assert len(records) == before + 1
    assert records[0]["author"] == "陳小明 <ming@example.com>"


def test_resolving_a_missing_target_points_to_apply_instead(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "missing_resolve.yaml", b"count: 1\n")
    pathlib.Path(entry["target"]).unlink()

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})

    assert exc.value.code == _CONFLICT
    assert "apply" in _detail(exc.value)


# ── T9：單筆內容帶目標現況（target_values，#30）──────────────────────────────


def test_config_detail_carries_the_target_values_when_the_target_drifted(api, sources_root):
    # 差異檢視據此把來源與現況以參數為單位並排；target 不存在→null、壞掉→null＋原因。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "detail_target.yaml", b"count: 1\n")
    pathlib.Path(entry["target"]).write_bytes(b"count: 9\nextra: 1\n")

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert (detail["values"], detail["target_values"]) == ({"count": 1}, {"count": 9, "extra": 1})
    assert detail["target_error"] is None


def test_config_detail_reports_a_missing_or_broken_target_without_failing(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "detail_target_bad.yaml", b"count: 1\n")
    pathlib.Path(entry["target"]).write_bytes(b"count: [1, 2\n")  # 語法壞掉

    broken = _get(api, f"/api/configs/{entry['uid']}")
    pathlib.Path(entry["target"]).unlink()
    missing = _get(api, f"/api/configs/{entry['uid']}")

    assert broken["target_values"] is None and "解析" in broken["target_error"]
    assert (missing["target_values"], missing["target_error"]) == (None, None)


# ── T9：搜尋（GET /api/search，#32）─────────────────────────────────────────────


def _search(api, query, scope=None):
    params = {"q": query} | ({"scope": scope} if scope else {})
    return _get(api, f"/api/search?{urllib.parse.urlencode(params)}")


def test_search_all_is_the_union_of_the_four_scopes(api, sources_root):
    # 搜尋 giraffe 在「全部」下同時命中 config 名稱／目標路徑（giraffe.yaml：名字由目標路徑推導，
    # 兩者都含這個字）與另一份的參數名稱（giraffe_fps）。
    _clear_drafts(api)
    cam = _onboard(api, sources_root, "giraffe.yaml", b"exposure: 0.4242\n")
    nav = _onboard(api, sources_root, "nav.yaml", b"giraffe_fps: 30\nmax_vel: 0.8\n")

    hits = {hit["uid"]: hit for hit in _search(api, "giraffe")["hits"]}

    assert set(hits[cam["uid"]]["matched"]) == {"config 名稱", "目標路徑"}
    assert hits[nav["uid"]]["matched"] == ["參數名稱"]
    assert hits[nav["uid"]]["params"] == [{"path": "giraffe_fps", "value": 30}]


def test_search_scope_limits_hits_to_that_scope(api, sources_root):
    _clear_drafts(api)
    cam = _onboard(api, sources_root, "zebra.yaml", b"exposure: 0.5577\n")
    _onboard(api, sources_root, "nav2.yaml", b"zebra_fps: 30\n")

    by_name = _search(api, "zebra", "config 名稱")["hits"]
    by_value = _search(api, "0.5577", "參數值")["hits"]
    by_target = _search(api, str(sources_root), "目標路徑")["hits"]

    assert [hit["uid"] for hit in by_name] == [cam["uid"]]
    assert by_value[0]["uid"] == cam["uid"] and by_value[0]["params"][0]["path"] == "exposure"
    assert cam["uid"] in [hit["uid"] for hit in by_target]


def test_search_reflects_promoted_changes_and_unmanaged_configs(api, sources_root):
    # 最容易忘記的兩處：修改後舊值不再命中、新值命中；解除納管後索引移除。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "search_changes.yaml", b"speed: 111\n")
    assert [h["uid"] for h in _search(api, "111", "參數值")["hits"]] == [entry["uid"]]

    _promote_value(api, entry["uid"], "speed", 222)
    assert _search(api, "111", "參數值")["hits"] == []
    assert [h["uid"] for h in _search(api, "222", "參數值")["hits"]] == [entry["uid"]]

    _send(api, "DELETE", f"/api/configs/{entry['uid']}")
    assert _search(api, "222", "參數值")["hits"] == []


def test_search_with_an_unknown_scope_is_unprocessable_not_empty(api):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _search(api, "x", "主機")

    assert exc.value.code == _UNPROCESSABLE
    assert "config 名稱／目標路徑／參數名稱／參數值／全部" in _detail(exc.value)


# ── T9：身分屬於各自的瀏覽器（#288）────────────────────────────────────────────
# 身分綁在後端發給該瀏覽器的票（cookie）上；另一個瀏覽器（另一罐 cookie）看不到、也沿用不到。


def _request_as(opener, api, path, payload=None, method="POST"):
    """以 `opener` 這個瀏覽器送；寫入時它自己取得編輯權（F23：識別碼只對取得它的瀏覽器有效）。"""
    if _writes(method, path):
        with _holding(opener, api) as held:
            return _open_json(opener, f"{api}{path}", method, payload, token=held)
    return _open_json(opener, f"{api}{path}", method, payload)


def test_an_identity_belongs_to_the_browser_that_entered_it(api):
    _set_session(api)  # 這一罐：陳小明
    other = _another_browser()

    mine, _ = _request_as(_browser, api, "/api/session", method="GET")
    theirs, _ = _request_as(other, api, "/api/session", method="GET")

    assert mine["name"] == "陳小明"
    assert theirs is None  # 另一個瀏覽器一律從「還沒說自己是誰」開始


def test_a_browser_without_an_identity_cannot_act_in_someone_elses_name(api, sources_root):
    entry = _onboard(api, sources_root, "other_browser.yaml", b"count: 1\n")
    other = _another_browser()

    with pytest.raises(urllib.error.HTTPError) as exc:
        _request_as(other, api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})

    assert exc.value.code == _CONFLICT
    assert "尚未設定身分" in _detail(exc.value)


def test_each_change_is_authored_by_the_browser_that_sent_it(api, sources_root):
    # 兩個瀏覽器各自納管一份：紀錄上的作者各是自己，不是「最後一個輸入身分的人」。
    _set_session(api)
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    mine = _write_source(sources_root, "authored_by_me.yaml")
    theirs = _write_source(sources_root, "authored_by_them.yaml")

    mine_entry = _post(api, "/api/configs", {"source_path": mine, "format": "yaml"})
    theirs_entry, _ = _request_as(
        other, api, "/api/configs", {"source_path": theirs, "format": "yaml"}
    )

    assert _history(api, mine_entry["uid"], "import")[0]["author"] == "陳小明 <ming@example.com>"
    assert _history(api, theirs_entry["uid"], "import")[0]["author"] == "林巡檢 <lin@example.com>"


def test_the_ticket_is_a_session_cookie_the_page_cannot_read(api):
    # HttpOnly：頁面的 JS 讀不到票；沒有 Max-Age／Expires：關閉瀏覽器即失效（記住裝置另外做，
    # 不靠它）。
    fresh = _another_browser()

    _, headers = _request_as(
        fresh, api, "/api/session", {"name": "王小美", "email": "mei@example.com"}
    )

    cookie = headers["set-cookie"]
    assert cookie.startswith("cm_browser=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age" not in cookie and "Expires" not in cookie


def test_a_malformed_ticket_is_replaced_not_trusted(api):
    # cookie 裡的任意內容不會變成後端字典的鍵：形狀不合就當沒帶、重新發一張。
    request = urllib.request.Request(
        f"{api}/api/session",
        data=json.dumps({"name": "王小美", "email": "mei@example.com"}).encode("utf-8"),
        headers={"content-type": "application/json", "cookie": "cm_browser=../../etc/passwd"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        issued = response.headers["set-cookie"]

    ticket = issued.split(";")[0].removeprefix("cm_browser=")
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,64}", ticket), ticket  # api/browser 收票的形狀
    # 身分記在新發的票上：拿它再問，得到的是剛宣告的人。
    follow_up = urllib.request.Request(
        f"{api}/api/session", headers={"cookie": issued.split(";")[0]}
    )
    with urllib.request.urlopen(follow_up, timeout=_TIMEOUT) as response:
        assert json.loads(response.read())["name"] == "王小美"


# ── T9／T13：編輯階段（POST /api/session/lock…，#33）───────────────────────────


def _lock(api, method="POST", path="/api/session/lock", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api}{path}", data=data, headers={"content-type": "application/json"}, method=method
    )
    with _browser.open(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


# ── 服務重啟（#51；驗證清單 U40）────────────────────────────────────────────
#
# 已進版的東西住在 config-repo，重啟後從檔讀回來；草稿、身分、編輯階段掛在程序上，重啟就沒了。
# 「重啟」＝同一份 config-repo 上重新起一個服務（conftest 的 `restarted`）。


def test_a_restarted_service_still_has_the_promoted_value_and_its_history(
    api, sources_root, restarted
):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "restart_promoted.yaml")
    _promote_value(api, entry["uid"], "max_vel", 1.2)

    after = restarted()

    values = _get(after, f"/api/configs/{entry['uid']}")["values"]
    kinds = [change["kind"] for change in _history(after, entry["uid"])]
    assert (values, kinds) == ({"max_vel": 1.2}, ["cfg"])


def test_a_restarted_service_lists_the_managed_configs_with_their_state_compared_again(
    api, sources_root, restarted
):
    kept = _onboard(api, sources_root, "restart_listed.yaml")
    drifted = _drifted(api, sources_root, "restart_drifted.yaml")

    after = restarted()

    states = {row["uid"]: row["state"] for row in _get(after, "/api/configs")}
    assert (states[kept["uid"]], states[drifted["uid"]]) == ("in_sync", "drift")


def test_a_restarted_service_still_has_the_roots_added_to_the_whitelist(api, restarted, tmp_path):
    _set_session(api)  # developer
    root = tmp_path / "restart_root"
    root.mkdir()
    _post(api, "/api/allowed-roots", {"prefix": str(root)})

    after = restarted()

    assert str(root) in _get(after, "/api/allowed-roots")["prefixes"]


def test_a_restarted_service_has_no_drafts(api, sources_root, restarted):
    # 草稿只存在編輯階段（T18），而階段掛在程序上：重啟後未進版的草稿不在了，來源仍是原值。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "restart_draft.yaml")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"max_vel": 1.2}})
    try:
        after = restarted()

        assert _get(after, "/api/drafts") == {"count": 0, "drafts": []}
    finally:
        _clear_drafts(api)


def test_a_restarted_service_knows_no_identity_and_holds_no_editing_session(api, restarted):
    # 同一個瀏覽器（同一張票）回來，重啟後的服務不認得它：要重新輸入身分，階段也沒有人持有。
    _set_session(api)
    held = _lock(api)
    try:
        after = restarted()

        assert (_get(after, "/api/session"), _lock(after, "GET")["held"]) == (None, False)
    finally:
        _lock(api, "DELETE", payload={"token": held["token"]})


def test_the_editing_session_can_be_acquired_once_and_names_the_holder_to_the_second(api):
    # 已被占用 → 409，含持有者姓名、email、開始時間；不提供強制接管。
    _set_session(api)
    first = _lock(api)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _lock(api)
        detail = _detail(exc.value)
        assert exc.value.code == _CONFLICT
        assert detail["holder"] == {"name": "陳小明", "email": "ming@example.com"}
        assert detail["started_at"] == first["started_at"]
        assert _lock(api, "GET")["held"] is True
    finally:
        _lock(api, "DELETE", payload={"token": first["token"]})
        _set_session(api)


def test_setting_another_identity_while_someone_holds_the_session_is_refused(api):
    # 換身分會把持有者接下來的變更紀錄掛到別人頭上 → 409 帶持有者資訊，前端轉唯讀。
    _set_session(api)
    session = _lock(api)
    try:
        other = {"name": "林巡檢", "email": "lin@example.com", "role": "user"}
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(api, "/api/session", other)
        assert exc.value.code == _CONFLICT and _detail(exc.value)["kind"] == "held"
        assert _get(api, "/api/session")["name"] == "陳小明"  # 身分沒被換掉
    finally:
        _lock(api, "DELETE", payload={"token": session["token"]})
        _set_session(api)


def test_after_release_someone_else_can_set_an_identity_and_acquire(api):
    _set_session(api)
    session = _lock(api)

    assert _lock(api, "DELETE", payload={"token": session["token"]}) == {"released": True}

    assert _get(api, "/api/session")["name"] == "陳小明"  # 身分留著：重新整理也會觸發釋放
    _post(api, "/api/session", {"name": "林巡檢", "email": "lin@example.com", "role": "user"})
    second = _lock(api)
    assert second["holder"]["name"] == "林巡檢"
    _lock(api, "DELETE", payload={"token": second["token"]})
    _set_session(api)


def test_renewing_with_a_stale_token_is_gone_not_a_silent_reacquire(api):
    _set_session(api)
    session = _lock(api)
    _lock(api, "DELETE", payload={"token": session["token"]})
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _lock(api, path="/api/session/lock/renew", payload={"token": session["token"]})

    assert exc.value.code == _GONE
    assert _lock(api, "GET")["held"] is False


def test_an_expired_session_is_swept_and_its_drafts_are_reported_as_cleared(api, sources_root):
    # 部署模式：閒置達逾時後回收，草稿一併清除，下一個取得者被告知清了 N 份。時鐘與逾時就地
    # 注入（T13：讀真實時間的實作測不了逾時）。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地注入時鐘與逾時，外部映像控不了 server 端模組")
    entry = _onboard(api, sources_root, "lock_drafts.yaml", b"count: 1\n")
    _clear_drafts(api)
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})
    session = _lock(api)
    box = _find_lock_box()
    real_clock, real_timeout = box.clock, box.lock.timeout
    try:
        box.lock.timeout = datetime.timedelta(minutes=10)
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=11)
        with pytest.raises(urllib.error.HTTPError) as exc:
            _lock(api, path="/api/session/lock/renew", payload={"token": session["token"]})
        assert exc.value.code == _GONE
        assert _get(api, "/api/drafts")["count"] == 0  # 逾時釋放時草稿一併清除
        _set_session(api)
        acquired = _lock(api)
        assert acquired["cleared_drafts"] == 1  # 回報「有 N 份草稿被清除」，不靜默丟棄
        _lock(api, "DELETE", payload={"token": acquired["token"]})
    finally:
        box.clock, box.lock.timeout = real_clock, real_timeout
        _set_session(api)


def _find_lock_box():
    """就地起的服務：從物件圖裡撈出 app 的 LockBox（測逾時要動它的時鐘與逾時）。

    撈的是**正持有編輯階段**的那一個：呼叫端在這之前都先 `_lock(api)` 取得了階段。物件圖裡可能
    還有別的 LockBox（開發模式那個對照服務的，#47；已收掉的 app 不一定立刻被回收），它們沒有
    人持有階段，所以不會被選到。
    """
    boxes = [obj for obj in gc.get_objects() if isinstance(obj, LockBox)]
    holding = [box for box in boxes if box.lock.current is not None]
    assert len(holding) == 1, f"預期恰好一個持有中的 LockBox，找到 {len(holding)}／{len(boxes)}"
    return holding[0]


def test_an_abandoned_session_is_released_once_its_renewals_stop(api):
    # U38 的回歸：持有分頁異常中斷（沒走到釋放）→ 心跳停了，預設的續期逾時就回收，下一個人能取得；
    # 不必設定任何逾時、也不必重啟服務。時鐘就地注入。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地注入時鐘，外部映像控不了 server 端模組")
    _set_session(api)
    _lock(api)  # 取得後就再也不續期、也不釋放
    box = _find_lock_box()
    real_clock = box.clock
    try:
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=10)
        other = {"name": "林巡檢", "email": "lin@example.com", "role": "user"}
        _post(api, "/api/session", other)  # 階段已回收：換身分不再被擋
        acquired = _lock(api)
        assert acquired["holder"]["name"] == "林巡檢"
        _lock(api, "DELETE", payload={"token": acquired["token"]})
    finally:
        box.clock = real_clock
        _set_session(api)


def test_the_page_can_release_its_session_with_a_plain_text_beacon(api):
    # 頁面關閉時用 sendBeacon 釋放：只能 POST、為免跨來源預檢用 text/plain——後端自己解析 body。
    _set_session(api)
    session = _lock(api)
    request = urllib.request.Request(
        f"{api}/api/session/lock/release",
        data=json.dumps({"token": session["token"]}).encode("utf-8"),
        headers={"content-type": "text/plain;charset=UTF-8"},
        method="POST",
    )

    with _browser.open(request, timeout=_TIMEOUT) as response:
        released = json.loads(response.read().decode("utf-8"))

    assert released == {"released": True}
    assert _lock(api, "GET")["held"] is False


# ── T9：寫入只接受持有編輯權的那個分頁（F23）─────────────────────────────────────
# 會寫入的 14 條路由要在 X-CM-Editing-Token 帶目前編輯階段的識別碼，而且送請求的瀏覽器就是取得
# 它的那一個。payload 挑「舊後端會因為別的原因拒絕、不留副作用」的值（不存在的 uid／路徑）。

_WRITES = [
    ("POST", "/api/configs", {"source_path": "/nonexistent/f23.yaml", "format": "yaml"}),
    ("DELETE", "/api/configs/zzzzzzz9", None),
    ("POST", "/api/allowed-roots", {"prefix": "/nonexistent/f23"}),
    ("DELETE", "/api/allowed-roots", {"prefix": "/nonexistent/f23"}),
    ("POST", "/api/drafts", {"uid": "zzzzzzz9", "edits": {}}),
    ("DELETE", "/api/drafts", None),
    ("DELETE", "/api/drafts/zzzzzzz9", None),
    ("POST", "/api/promote", {}),
    ("POST", "/api/configs/zzzzzzz9/revert", {"version": "0123456"}),
    ("POST", "/api/configs/zzzzzzz9/resolve", {"action": "overwrite"}),
    ("POST", "/api/configs/zzzzzzz9/apply", None),
    ("POST", "/api/configs/zzzzzzz9/schema", None),
    ("POST", "/api/configs/zzzzzzz9/types", {"path": "x", "type": "int"}),
    ("POST", "/api/configs/zzzzzzz9/attributes", {"name": "x", "hostname": "h"}),
]
_WRITE_ROUTES = {
    ("POST", "/api/configs"), ("DELETE", "/api/configs/{uid}"),
    ("POST", "/api/allowed-roots"), ("DELETE", "/api/allowed-roots"),
    ("POST", "/api/drafts"), ("DELETE", "/api/drafts"), ("DELETE", "/api/drafts/{uid}"),
    ("POST", "/api/promote"), ("POST", "/api/configs/{uid}/revert"),
    ("POST", "/api/configs/{uid}/resolve"), ("POST", "/api/configs/{uid}/apply"),
    ("POST", "/api/configs/{uid}/schema"), ("POST", "/api/configs/{uid}/types"),
    ("POST", "/api/configs/{uid}/attributes"),
}
# 不寫入的 POST、編輯階段自己的端點：不看識別碼。
_NOT_WRITE_ROUTES = {
    ("POST", "/api/inspect"), ("POST", "/api/session"), ("DELETE", "/api/session"),
    ("POST", "/api/session/activity"),
    ("POST", "/api/session/lock"), ("DELETE", "/api/session/lock"),
    ("POST", "/api/session/lock/renew"), ("POST", "/api/session/lock/release"),
}


def _refusal(call):
    """呼叫 `call`，回 (狀態碼, detail 的 kind)；沒被擋回 (200, None)。"""
    try:
        call()
    except urllib.error.HTTPError as error:
        detail = _detail(error)
        return error.code, detail.get("kind") if isinstance(detail, dict) else None
    return 200, None


def test_every_route_that_changes_something_is_a_write_or_explicitly_not_one(api):
    # 護欄：之後新增一條會寫入的路由卻沒歸類，這一則會紅（不分修正前後，一直成立）。
    paths = _get(api, "/api/openapi.json")["paths"]
    changing = {
        (method.upper(), path)
        for path, operations in paths.items() for method in operations if method != "get"
    }

    assert changing == _WRITE_ROUTES | _NOT_WRITE_ROUTES


@pytest.mark.parametrize(("method", "path", "payload"), _WRITES)
def test_a_write_without_the_editing_token_is_refused(api, method, path, payload):
    # 有身分、沒帶識別碼（唯讀分頁、還沒取得）：410 editing_missing，這次沒有寫進去。
    _clear_drafts(api)
    _set_session(api)

    refused = _refusal(lambda: _send(api, method, path, payload, token=None))

    assert refused == (_GONE, "editing_missing")


def test_a_released_token_cannot_write_and_nothing_changes(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f23_released.yaml", b"count: 1\n")
    session = _lock(api)
    _lock(api, "DELETE", payload={"token": session["token"]})

    refused = _refusal(lambda: _post(
        api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
    ))

    assert (refused, _get(api, "/api/drafts")["count"]) == ((_GONE, "editing_lost"), 0)


def test_after_a_takeover_the_old_token_cannot_write_into_the_new_holders_session(
    api, sources_root
):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f23_takeover.yaml", b"count: 1\n")
    session = _lock(api)
    _lock(api, "DELETE", payload={"token": session["token"]})
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    theirs, _ = _open_json(other, f"{api}/api/session/lock", "POST", None)
    try:
        refused = _refusal(lambda: _post(
            api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))
        in_their_session = _get(api, "/api/drafts")["count"]
    finally:
        _open_json(other, f"{api}/api/session/lock", "DELETE", {"token": theirs["token"]})
        _set_session(api)

    assert (refused, in_their_session) == ((_GONE, "editing_lost"), 0)


def test_another_browser_carrying_the_holders_token_cannot_write(api, sources_root):
    # 識別碼綁瀏覽器：另一個（有身分的）瀏覽器拿到持有者的識別碼來送，一樣擋——擋下的原因是票不對，
    # 不是沒身分。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f23_borrowed.yaml", b"count: 1\n")
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    session = _lock(api)
    try:
        refused = _refusal(lambda: _open_json(
            other, f"{api}/api/drafts", "POST", {"uid": entry["uid"], "edits": {"count": 2}},
            token=session["token"],
        ))
        drafts = _get(api, "/api/drafts")["count"]
    finally:
        _lock(api, "DELETE", payload={"token": session["token"]})

    assert (refused, drafts) == ((_GONE, "editing_lost"), 0)


@pytest.mark.parametrize("resume", [False, True])
def test_another_browser_cannot_renew_or_resume_the_holders_session(api, resume):
    _set_session(api)
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    session = _lock(api)
    current = session["token"]  # 收尾要放掉的是「現在有效」的那一個
    try:
        try:
            theirs, _ = _open_json(
                other, f"{api}/api/session/lock/renew", "POST",
                {"token": current, "resume": resume},
            )
            # 沒擋的話，別的瀏覽器接續會換掉識別碼：收尾用新的那一個——否則這一則的失敗會留下一個
            # 沒人放的編輯階段，把之後的規格一起拖下水。
            current = theirs["token"]
            refused = 200
        except urllib.error.HTTPError as error:
            refused = error.code
        still = _refusal(lambda: _lock(
            api, path="/api/session/lock/renew", payload={"token": session["token"]}
        ))
    finally:
        _lock(api, "DELETE", payload={"token": current})

    assert (refused, still) == (_GONE, (200, None))


@pytest.mark.parametrize("beacon", [False, True])
def test_another_browser_cannot_release_the_holders_session(api, beacon):
    _set_session(api)
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    session = _lock(api)
    body = json.dumps({"token": session["token"]}).encode("utf-8")
    request = (
        urllib.request.Request(
            f"{api}/api/session/lock/release", data=body,
            headers={"content-type": "text/plain;charset=UTF-8"}, method="POST",
        )
        if beacon else
        urllib.request.Request(
            f"{api}/api/session/lock", data=body,
            headers={"content-type": "application/json"}, method="DELETE",
        )
    )
    try:
        with other.open(request, timeout=_TIMEOUT) as response:
            released = json.loads(response.read().decode("utf-8"))
        held = _lock(api, "GET")["held"]
    finally:
        _lock(api, "DELETE", payload={"token": session["token"]})

    assert (released, held) == ({"released": False}, True)


def test_after_an_idle_timeout_the_old_token_is_refused_as_lost_editing(api, sources_root):
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f23_idle.yaml", b"count: 1\n")
    with _later(api) as (session, ahead):
        ahead(10)

        refused = _refusal(lambda: _post(
            api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))

    assert refused == (_GONE, "editing_lost")


def test_after_a_renew_timeout_the_old_token_cannot_write(api, sources_root):
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f23_renew_timeout.yaml", b"count: 1\n")
    session = _lock(api)
    box = _find_lock_box()
    real_clock = box.clock
    try:
        # 過了續期逾時（150 秒）、還沒到閒置逾時（10 分鐘）：收回的是續期逾時這一條。
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=3)
        refused = _refusal(lambda: _post(
            api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))
        drafts = _get(api, "/api/drafts")["count"]
    finally:
        box.clock = real_clock
        if box.lock.current is not None:
            box.lock.release(box.lock.current.token)
        _set_session(api)

    assert (refused, drafts) == ((_GONE, "editing_lost"), 0)


# ── T9：退出（DELETE /api/session，F3）─────────────────────────────────────────────
# 只影響送請求的這個瀏覽器；持有編輯權時當場釋放並清掉目前全部共用草稿；有東西會丟先確認。
# 每則收尾都把 `_browser` 的身分設回來（之後的規格要用）。


def _exit(api, opener=None, **body):
    """以 `opener`（預設 `_browser`）退出；回 (狀態碼, 回應或 detail)。"""
    request = urllib.request.Request(
        f"{api}/api/session", data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"}, method="DELETE",
    )
    try:
        with (opener or _browser).open(request, timeout=_TIMEOUT) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, _detail(error)


def test_exiting_ends_this_browsers_identity(api):
    _set_session(api)
    try:
        _exit(api)
        after = (_get(api, "/api/session"), _get(api, "/api/session/idle").get("left"))
    finally:
        _set_session(api)

    assert after == (None, True)


def test_exiting_while_holding_releases_and_clears_the_drafts_without_telling_the_next(
    api, sources_root
):
    # 暫行規則：持有者確認退出 → 釋放、清掉目前全部共用草稿；下一個取得的人不被告知清了幾份
    # （那句話只用在逾時）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f3_exit_holding.yaml", b"count: 1\n")
    session = _lock(api)
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"])
    try:
        code, left = _exit(api, confirmed=True, token=session["token"])
        state = (_lock(api, "GET")["held"], _get(api, "/api/drafts")["count"])
        # 下一個取得的人緊接著取得：中間不經過會先取得編輯權的輔助函式（它們會把待回報的份數領走）。
        _set_session(api)
        taken = _lock(api)
        _lock(api, "DELETE", payload={"token": taken["token"]})
    finally:
        # 修正前退出什麼都沒做：收尾把階段與草稿放掉，不留給之後的規格。
        _lock(api, "DELETE", payload={"token": session["token"]})
        _set_session(api)
        _clear_drafts(api)

    assert (code, left, state, taken["cleared_drafts"]) == (
        200, {"left": True, "released": True, "cleared_drafts": 1}, (False, 0), 0,
    )


@pytest.mark.parametrize("risk", ["drafts", "unsaved", "other_tab"])
def test_exiting_with_something_to_lose_asks_first_and_changes_nothing(
    api, sources_root, risk
):
    # 有東西會丟而沒帶 confirmed：持有時有草稿、區塊裡有沒存的改動、或持有的是同一個瀏覽器的另一個
    # 分頁（這個分頁沒有識別碼）→ 409 confirm_required，身分、編輯權、草稿都沒動。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, f"f3_exit_risk_{risk}.yaml", b"count: 1\n")
    session = _lock(api)
    body = {"token": session["token"]}
    if risk == "drafts":
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"])
    elif risk == "unsaved":
        body["unsaved"] = True
    else:
        body = {}
    try:
        code, detail = _exit(api, **body)
        kept = (
            _get(api, "/api/session") is not None,
            _lock(api, "GET")["held"],
            _get(api, "/api/drafts")["count"],
        )
    finally:
        _lock(api, "DELETE", payload={"token": session["token"]})
        _set_session(api)
        _clear_drafts(api)

    asked = isinstance(detail, dict) and detail.get("kind") == "confirm_required" and bool(
        detail.get(risk)
    )
    assert (code, asked, kept) == (_CONFLICT, True, (True, True, 1 if risk == "drafts" else 0))


def test_exiting_does_not_touch_another_browsers_editing_session(api, sources_root):
    # 只影響送請求的瀏覽器：另一個瀏覽器持有時，它的識別碼仍有效、草稿份數不變。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f3_exit_other.yaml", b"count: 1\n")
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    theirs, _ = _open_json(other, f"{api}/api/session/lock", "POST", None)
    _open_json(
        other, f"{api}/api/drafts", "POST", {"uid": entry["uid"], "edits": {"count": 2}},
        token=theirs["token"],
    )
    try:
        code, left = _exit(api, confirmed=True)
        renewed = _refusal(lambda: _open_json(
            other, f"{api}/api/session/lock/renew", "POST", {"token": theirs["token"]}
        ))
        drafts = _get(api, "/api/drafts")["count"]
    finally:
        _open_json(other, f"{api}/api/drafts", "DELETE", None, token=theirs["token"])
        _open_json(other, f"{api}/api/session/lock", "DELETE", {"token": theirs["token"]})
        _set_session(api)

    released = left.get("released") if isinstance(left, dict) else left
    assert (code, released, renewed, drafts) == (200, False, (200, None), 1)


def test_exiting_without_an_identity_still_succeeds(api):
    fresh = _another_browser()

    code, left = _exit(api, fresh)

    assert (code, left) == (200, {"left": True, "released": False, "cleared_drafts": 0})


def test_after_exiting_another_identity_takes_the_session_at_once(api):
    # 換人換角色：退出 → 以另一個身分進入 → 立刻取得編輯權，不必等、不必換瀏覽器。
    _set_session(api)
    session = _lock(api)
    try:
        _exit(api, confirmed=True, token=session["token"])
        _post(api, "/api/session", {"name": "林巡檢", "email": "lin@example.com", "role": "user"})
        taken = _lock(api)
        _lock(api, "DELETE", payload={"token": taken["token"]})
    finally:
        _lock(api, "DELETE", payload={"token": session["token"]})  # 修正前退出沒放掉
        _set_session(api)

    assert taken["holder"]["name"] == "林巡檢"


def test_the_idle_status_tells_an_exit_from_an_unknown_ticket(api):
    # 已退出（left）與「後端沒有這張票的紀錄」（例如服務重啟過）分得開；都不是閒置逾時。
    exited, unknown = _another_browser(), _another_browser()
    _request_as(exited, api, "/api/session", {"name": "王小美", "email": "mei@example.com"})
    _exit(api, exited)

    seen = [
        _request_as(browser, api, "/api/session/idle", method="GET")[0]
        for browser in (exited, unknown)
    ]

    assert [(view.get("left"), view["timed_out"]) for view in seen] == [
        (True, False), (False, False),
    ]


def _draft_write(api, entry, token):
    """以 `token` 存一份草稿；回 (狀態碼, kind)，沒被擋是 (200, None)。"""
    return _refusal(
        lambda: _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, token)
    )


def test_after_exiting_a_write_without_a_token_is_refused_for_lack_of_an_identity(
    api, sources_root
):
    # 票留著，但它上面已經沒有身分：需要作者的寫入被擋在「先設身分」。
    entry = _onboard(api, sources_root, "f3_exit_no_identity.yaml", b"count: 1\n")
    try:
        _exit(api)
        refused = _draft_write(api, entry, None)
    finally:
        _set_session(api)

    assert refused == (_CONFLICT, None)


def test_after_exiting_the_old_tabs_token_can_no_longer_write(api, sources_root):
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f3_exit_old_token.yaml", b"count: 1\n")
    session = _lock(api)
    try:
        _exit(api, confirmed=True, token=session["token"])
        refused = _draft_write(api, entry, session["token"])
        drafts = _get(api, "/api/drafts")["count"]
    finally:
        _lock(api, "DELETE", payload={"token": session["token"]})
        _set_session(api)
        _clear_drafts(api)

    assert (refused, drafts) == ((_GONE, "editing_lost"), 0)


def test_entering_again_in_the_same_browser_does_not_revive_the_old_tabs_token(
    api, sources_root
):
    # 同一個瀏覽器的分頁共用一張票：重新進入之後，舊分頁不會因此恢復寫入（新的識別碼才行）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f3_exit_reenter.yaml", b"count: 1\n")
    old = _lock(api)
    try:
        _exit(api, confirmed=True, token=old["token"])
        _set_session(api)
        new = _lock(api)
        try:
            outcome = tuple(_draft_write(api, entry, held["token"]) for held in (old, new))
        finally:
            _lock(api, "DELETE", payload={"token": new["token"]})
    finally:
        _lock(api, "DELETE", payload={"token": old["token"]})  # 修正前退出沒放掉
        _set_session(api)
        _clear_drafts(api)

    assert outcome == ((_GONE, "editing_lost"), (200, None))


def test_exiting_while_nobody_holds_the_session_leaves_the_drafts_alone(api, sources_root):
    # 草稿只有一份共用的、不記是誰存的：編輯權空著時退出不清、也不必確認（使用者 10/20 定案 b）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "f3_exit_free.yaml", b"count: 1\n")
    session = _lock(api)
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"])
    _lock(api, "DELETE", payload={"token": session["token"]})  # 正常釋放：草稿留著
    try:
        code, left = _exit(api)
        drafts = _get(api, "/api/drafts")["count"]
    finally:
        _set_session(api)
        _clear_drafts(api)

    assert (code, left, drafts) == (
        200, {"left": True, "released": False, "cleared_drafts": 0}, 1,
    )


def test_exiting_after_the_idle_timeout_says_it_timed_out(api, sources_root):
    # 這個瀏覽器其實已經閒置逾時（例如筆電睡過頭、頁面還沒問到）才按退出：逾時那一刻已釋放並清掉
    # 草稿（照逾時的規則）。退出照實說是逾時——不說成「原本沒有編輯權」，也不記成已退出。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    entry = _onboard(api, sources_root, "f3_exit_timed_out.yaml", b"count: 1\n")
    _clear_drafts(api)
    with _later(api) as (session, ahead):
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"])
        ahead(10)

        code, left = _exit(api, token=session["token"])
        idle = _get(api, "/api/session/idle")
    taken = _lock(api)  # 逾時清掉的份數照規則留給下一個取得的人；領走，不留給之後的規格
    _lock(api, "DELETE", payload={"token": taken["token"]})

    assert (code, left, (idle["timed_out"], idle.get("left"))) == (
        200, {"left": True, "released": False, "cleared_drafts": 0, "timed_out": True},
        (True, False),
    )


# ── T9：檢查編輯權與寫入是一段，和釋放、接手排隊；時間不倒退（F23）──────────────────────
# 每則自己起服務（`api_alone`）：伺服器那邊的執行緒會停在中間。停住的是「真正動手的那個函式」；
# 「有請求開始排隊」用鎖上的 on_wait 觀察（只觀察、不影響誰先誰後）。每個等待都有上限，finally 一律
# 放行。

_WAIT = 5


def _in_background(call):
    """在另一個執行緒裡送請求；回 (執行緒, 結果)。結果是 {"value": …}、{"refused": (碼, detail)}
    或 {"failed": 說明}。"""
    outcome = {}

    def run():
        try:
            outcome["value"] = call()
        except urllib.error.HTTPError as error:
            outcome["refused"] = (error.code, _detail(error))
        except (urllib.error.URLError, OSError, ValueError) as error:
            outcome["failed"] = repr(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _first(event, thread):
    """等到 `event` 發生或 `thread` 做完，先到先算，最多 `_WAIT` 秒。"""
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline and not event.is_set() and thread.is_alive():
        time.sleep(0.01)


def _outcome(result):
    """背景請求的結果寫成 (狀態碼, kind)：成功是 (200, None)。"""
    if "refused" in result:
        code, detail = result["refused"]
        return code, detail.get("kind") if isinstance(detail, dict) else None
    return (200, None) if "value" in result else (None, result.get("failed"))


def _pause(original):
    """`original` 的一個「先停在閘門前」的版本：回 (換上的函式, 進到閘門的事件, 閘門)。"""
    entered, gate = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        entered.set()
        gate.wait(_WAIT)
        return original(*args, **kwargs)

    return paused, entered, gate


def _ready(base, sources_root, name):
    """在這個服務上設身分、納管一份 count: 1、取得編輯權；回 (條目, 取得的回應)。"""
    _set_session(base)
    entry = _onboard(base, sources_root, name, b"count: 1\n")
    return entry, _lock(base)


def _beacon(base, token):
    request = urllib.request.Request(
        f"{base}/api/session/lock/release", data=json.dumps({"token": token}).encode("utf-8"),
        headers={"content-type": "text/plain;charset=UTF-8"}, method="POST",
    )
    with _browser.open(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _renew(base, token):
    return _lock(base, path="/api/session/lock/renew", payload={"token": token})


@pytest.mark.parametrize(("method", "path", "payload"), _WRITES)
def test_a_release_that_gets_the_lock_first_turns_the_queued_write_away(
    api_alone, sources_root, method, path, payload
):
    # ①釋放先到：DELETE 釋放停在真正動手之前（拿著鎖），寫入在排隊 → 放行後寫入 410、草稿份數不變。
    # 14 條寫入都一樣。
    base, box = api_alone
    name = f"f23_first_release_{method}_{path.strip('/').replace('/', '_')}.yaml"
    _, session = _ready(base, sources_root, name)
    token = session["token"]
    paused, entered, gate = _pause(box.lock.release)
    box.lock.release = paused
    queued = threading.Event()
    box.guard.on_wait = queued.set
    try:
        releasing, _ = _in_background(lambda: _lock(base, "DELETE", payload={"token": token}))
        entered.wait(_WAIT)
        writing, wrote = _in_background(lambda: _send(base, method, path, payload, token=token))
        _first(queued, writing)
    finally:
        gate.set()
        releasing.join(_WAIT)
        writing.join(_WAIT)
        del box.lock.release

    assert (_outcome(wrote), _get(base, "/api/drafts")["count"]) == ((_GONE, "editing_lost"), 0)


def test_a_release_that_arrives_during_a_write_waits_for_it(api_alone, sources_root, monkeypatch):
    # ②寫入先到：寫入停在檢查之後、寫入之前（拿著鎖），DELETE 釋放要排隊 → 放行後寫入 200、釋放
    # released:true；之後舊識別碼寫入 410、草稿份數不變。
    base, box = api_alone
    entry, session = _ready(base, sources_root, "f23_release_waits.yaml")
    token = session["token"]
    paused, entered, gate = _pause(drafts_api.save_draft)
    monkeypatch.setattr(drafts_api, "save_draft", paused)
    queued = threading.Event()
    box.guard.on_wait = queued.set
    try:
        writing, wrote = _in_background(lambda: _post(
            base, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, token
        ))
        entered.wait(_WAIT)
        releasing, released = _in_background(
            lambda: _lock(base, "DELETE", payload={"token": token})
        )
        _first(queued, releasing)
        waited = releasing.is_alive()
    finally:
        gate.set()
        writing.join(_WAIT)
        releasing.join(_WAIT)

    later = _refusal(lambda: _post(
        base, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 3}}, token
    ))
    drafts = _get(base, "/api/drafts")["count"]
    assert (waited, _outcome(wrote), released.get("value"), later, drafts) == (
        True, (200, None), {"released": True}, (_GONE, "editing_lost"), 1,
    )


def test_a_takeover_that_arrives_during_a_write_waits_and_is_told_of_the_draft(
    api_alone, sources_root, monkeypatch
):
    # ③寫入先到、逾時接手排隊：時鐘撥過續期逾時、另一個瀏覽器取得要排隊 → 放行後寫入 200，接手者
    # 被告知清了 1 份、他的階段裡 0 份（那份草稿不會悄悄進到接手者的階段）。
    base, box = api_alone
    other = _another_browser()
    _request_as(other, base, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    entry, session = _ready(base, sources_root, "f23_takeover_waits.yaml")
    paused, entered, gate = _pause(drafts_api.save_draft)
    monkeypatch.setattr(drafts_api, "save_draft", paused)
    queued = threading.Event()
    box.guard.on_wait = queued.set
    real_clock = box.clock
    try:
        writing, wrote = _in_background(lambda: _post(
            base, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))
        entered.wait(_WAIT)
        # 過了續期逾時（150 秒）、還沒到閒置逾時（10 分鐘）。
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=3)
        taking, taken = _in_background(
            lambda: _open_json(other, f"{base}/api/session/lock", "POST", None)[0]
        )
        _first(queued, taking)
    finally:
        gate.set()
        writing.join(_WAIT)
        taking.join(_WAIT)
        box.clock = real_clock

    drafts = _get(base, "/api/drafts")["count"]
    assert (_outcome(wrote), taken.get("value", {}).get("cleared_drafts"), drafts) == (
        (200, None), 1, 0,
    )


def test_a_queued_beacon_release_does_not_stall_other_requests(
    api_alone, sources_root, monkeypatch
):
    # 護欄：beacon 釋放在排隊時，事件迴圈沒被它卡住——寫入還拿著鎖的時候，其他請求就照常回應
    # （async 端點裡拿鎖就會卡住整個服務）。
    base, box = api_alone
    entry, session = _ready(base, sources_root, "f23_beacon_queued.yaml")
    paused, entered, gate = _pause(drafts_api.save_draft)
    monkeypatch.setattr(drafts_api, "save_draft", paused)
    queued = threading.Event()
    box.guard.on_wait = queued.set
    try:
        writing, _ = _in_background(lambda: _post(
            base, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))
        entered.wait(_WAIT)
        releasing, _ = _in_background(lambda: _beacon(base, session["token"]))
        _first(queued, releasing)
        with _browser.open(f"{base}/api/mode", timeout=1) as response:
            answered = json.loads(response.read().decode("utf-8"))
        still_held = writing.is_alive() and not gate.is_set()
    finally:
        gate.set()
        writing.join(_WAIT)
        releasing.join(_WAIT)

    assert ("mode" in answered, still_held) == (True, True)


def test_a_renew_never_moves_the_last_renewal_back(api_alone, sources_root):
    # 時間不倒退：最後續期時間只增不減——系統時間被調回去，晚一點的續期也不把它寫回過去。
    base, box = api_alone
    _, session = _ready(base, sources_root, "f23_renewal_forward.yaml")
    real_clock = box.clock
    try:
        box.clock = lambda: real_clock() + datetime.timedelta(seconds=60)
        ahead = datetime.datetime.fromisoformat(_renew(base, session["token"])["renewed_at"])
        box.clock = lambda: real_clock() + datetime.timedelta(seconds=30)
        later = datetime.datetime.fromisoformat(_renew(base, session["token"])["renewed_at"])
    finally:
        box.clock = real_clock

    assert later >= ahead


def test_queued_requests_run_in_the_order_they_arrived(api_alone, sources_root, monkeypatch):
    # 護欄（先到先處理）：長寫入期間排了兩發續期（第 30 秒、第 170 秒到）。照到達順序執行，兩發都
    # 成立；較晚到的那發若先執行，170 秒沒續期就被當成逾時、兩發都 410。
    base, box = api_alone
    entry, session = _ready(base, sources_root, "f23_fifo.yaml")
    paused, entered, gate = _pause(drafts_api.save_draft)
    monkeypatch.setattr(drafts_api, "save_draft", paused)
    waiting = threading.Event()
    box.guard.on_wait = waiting.set
    real_clock = box.clock
    try:
        writing, _ = _in_background(lambda: _post(
            base, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))
        entered.wait(_WAIT)
        renewals = []
        for seconds in (30, 170):
            box.clock = lambda later=seconds: real_clock() + datetime.timedelta(seconds=later)
            waiting.clear()
            renewing, renewed = _in_background(lambda: _renew(base, session["token"]))
            _first(waiting, renewing)
            renewals.append((renewing, renewed))
    finally:
        gate.set()
        writing.join(_WAIT)
        for renewing, _ in renewals:
            renewing.join(_WAIT)
        box.clock = real_clock

    assert [_outcome(renewed) for _, renewed in renewals] == [(200, None), (200, None)]


def test_a_session_already_taken_back_is_not_revived_by_an_earlier_time(api_alone, sources_root):
    # 護欄：已經被續期逾時收回的階段，之後的舊續期（時間較早也一樣）是 410，不會復活——收回是狀態。
    base, box = api_alone
    _, session = _ready(base, sources_root, "f23_not_revived.yaml")
    real_clock = box.clock
    try:
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=3)
        _lock(base, "GET")  # 問一次就回收（續期逾時）
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=2)
        refused = _refusal(lambda: _renew(base, session["token"]))
    finally:
        box.clock = real_clock

    assert refused[0] == _GONE


def test_the_holders_renew_queued_behind_its_own_long_write_is_not_counted_against_it(
    api_alone, sources_root, monkeypatch
):
    # 護欄：持有者的續期排在自己的長寫入後面，排隊等的時間不算進去——用的是它到達時的時間，不是
    # 拿到鎖時的時間，否則持有者會被自己的寫入收掉。
    base, box = api_alone
    entry, session = _ready(base, sources_root, "f23_queued_renew.yaml")
    paused, entered, gate = _pause(drafts_api.save_draft)
    monkeypatch.setattr(drafts_api, "save_draft", paused)
    queued = threading.Event()
    box.guard.on_wait = queued.set
    real_clock = box.clock
    try:
        writing, _ = _in_background(lambda: _post(
            base, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"]
        ))
        entered.wait(_WAIT)
        box.clock = lambda: real_clock() + datetime.timedelta(seconds=100)
        renewing, renewed = _in_background(lambda: _renew(base, session["token"]))
        _first(queued, renewing)
        box.clock = lambda: real_clock() + datetime.timedelta(seconds=200)
    finally:
        gate.set()
        writing.join(_WAIT)
        renewing.join(_WAIT)
        box.clock = real_clock

    assert _outcome(renewed) == (200, None)


# ── T9／T13：閒置逾時（GET /api/session/idle、POST /api/session/activity，#48）──────
# 部署模式：一個瀏覽器的人 10 分鐘沒操作就退出（身分清掉）、它持有的編輯階段釋放、草稿清除並回報。
# 逾時前 1 分鐘起介面提示，可延長。開發模式不逾時。判定在後端、時鐘就地注入（T13）。

_IN_PLACE_ONLY = "需就地注入時鐘，外部映像控不了 server 端模組"
_TEN_MINUTES = 600
_HALF_MINUTE = 30


@contextlib.contextmanager
def _later(api):
    """持有編輯階段、並把這個服務的時鐘往後撥：`ahead(分鐘)` 之後的請求都發生在那個時刻。

    續期逾時（分頁還在不在，兩種模式都有）在這裡調到一年：這幾則要看的是**閒置**逾時，不讓另一種
    逾時先把階段收走而看不出是誰收的。
    """
    _set_session(api)
    session = _lock(api)
    box = _find_lock_box()
    real_clock, real_timeout = box.clock, box.lock.timeout
    box.lock.timeout = datetime.timedelta(days=365)

    def ahead(minutes):
        box.clock = lambda: real_clock() + datetime.timedelta(minutes=minutes)

    try:
        yield session, ahead
    finally:
        box.clock, box.lock.timeout = real_clock, real_timeout
        # 階段不留給下一則：識別碼可能已被續期換過，直接從鎖上拿現在的那一個來釋放。
        if box.lock.current is not None:
            box.lock.release(box.lock.current.token)
        _set_session(api)


def test_the_idle_status_says_how_long_is_left_and_when_the_warning_starts(api):
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    with _later(api) as (_session, ahead):
        ahead(9.5)

        status = _get(api, "/api/session/idle")

    assert (status["timeout_seconds"], status["warn_seconds"]) == (_TEN_MINUTES, 60)
    assert _HALF_MINUTE - 5 <= status["remaining_seconds"] <= _HALF_MINUTE  # 9.5 分鐘後
    assert status["timed_out"] is False


def test_ten_idle_minutes_end_the_identity_and_release_the_session(api, sources_root):
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    entry = _onboard(api, sources_root, "idle_drafts.yaml", b"count: 1\n")
    _clear_drafts(api)
    with _later(api) as (session, ahead):
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}}, session["token"])
        ahead(10)

        assert _get(api, "/api/session") is None  # 自動退出：這個瀏覽器不再有身分
        assert _get(api, "/api/session/idle")["timed_out"] is True
        assert _lock(api, "GET")["held"] is False  # 編輯階段同時釋放
        assert _get(api, "/api/drafts")["count"] == 0  # 草稿不留給下一個人
        _set_session(api)
        assert _lock(api)["cleared_drafts"] == 1  # 回報清了幾份，不靜默丟棄


def test_the_heartbeat_of_an_open_page_does_not_count_as_activity(api):
    # 頁面開著就每 30 秒續期一次；那不是人在操作——算進去的話，開著頁面就永遠不逾時。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    with _later(api) as (session, ahead):
        token = session["token"]
        for minutes in (2, 4, 6, 8):
            ahead(minutes)
            token = _lock(api, path="/api/session/lock/renew", payload={"token": token})["token"]
        ahead(10)

        with pytest.raises(urllib.error.HTTPError) as exc:
            _lock(api, path="/api/session/lock/renew", payload={"token": token})

    assert exc.value.code == _GONE


def test_activity_extends_the_identity_and_the_session(api):
    # 提示出現後按「繼續使用」（或單純有在操作）：重新起算 10 分鐘。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    with _later(api) as (_session, ahead):
        ahead(9.5)
        extended = _post(api, "/api/session/activity", {})
        ahead(15)

        assert _TEN_MINUTES - 1 <= extended["remaining_seconds"] <= _TEN_MINUTES  # 重新起算
        assert _get(api, "/api/session")["name"] == "陳小明"
        assert _lock(api, "GET")["held"] is True


def test_activity_after_the_timeout_does_not_bring_the_identity_back(api):
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    with _later(api) as (_session, ahead):
        ahead(11)

        late = _post(api, "/api/session/activity", {})

        assert late["timed_out"] is True and late["remaining_seconds"] is None
        assert _get(api, "/api/session") is None


def test_someone_elses_idleness_does_not_end_my_identity(api):
    # 閒置是每個瀏覽器各算各的：另一個瀏覽器一直沒動，不會把有在操作的這一個踢出去。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip(_IN_PLACE_ONLY)
    other = _another_browser()
    _request_as(other, api, "/api/session", {"name": "林巡檢", "email": "lin@example.com"})
    with _later(api) as (_session, ahead):
        ahead(9)
        _post(api, "/api/session/activity", {})
        ahead(12)

        mine = _get(api, "/api/session")
        theirs, _ = _request_as(other, api, "/api/session", method="GET")
        held = _lock(api, "GET")

    assert mine["name"] == "陳小明"
    assert theirs is None
    # 退出的是沒在操作的那一個；編輯階段是我持有的，不因別人逾時而被釋放。
    assert held["held"] is True and held["holder"]["name"] == "陳小明"


def test_development_mode_never_idles_out(api_developing):
    # 開發模式不逾時：過了一個月身分還在、階段還在，閒置狀態說「不逾時」。
    with _later(api_developing) as (session, ahead):
        token = session["token"]
        ahead(60 * 24 * 30)

        status = _get(api_developing, "/api/session/idle")
        still = _get(api_developing, "/api/session")
        renewed = _lock(api_developing, path="/api/session/lock/renew", payload={"token": token})

    assert status == dict.fromkeys(("timeout_seconds", "warn_seconds", "remaining_seconds")) | {
        "timed_out": False, "left": False,
    }
    assert still["name"] == "陳小明"
    assert renewed["holder"]["name"] == "陳小明"


def test_a_browser_that_never_said_who_it_is_has_nothing_to_time_out(api):
    fresh = _another_browser()

    status, _ = _request_as(fresh, api, "/api/session/idle", method="GET")

    assert status["remaining_seconds"] is None and status["timed_out"] is False


# ── T9：產生 schema 骨架（POST /api/configs/{uid}/schema，#38）─────────────────


def test_a_developer_drafts_a_schema_skeleton_for_a_managed_config(api, sources_root):
    # 骨架存進 config-repo 的 `.schemas/`、條目記下路徑；記一筆 meta、作者＝身分。
    entry = _onboard(api, sources_root, "schema_ok.yaml", b"max_vel: 0.8\nretries: 3\n")
    assert _get(api, f"/api/configs/{entry['uid']}")["schema"] is None

    drafted = _post(api, f"/api/configs/{entry['uid']}/schema", {})

    assert drafted == {"uid": entry["uid"], "schema": f".schemas/{entry['uid']}.json"}
    assert _get(api, f"/api/configs/{entry['uid']}")["schema"] == drafted["schema"]
    latest = _history(api, entry["uid"], "meta")[0]
    assert (latest["kind"], latest["summary"]) == ("meta", "產生 schema 骨架")
    assert latest["author"] == "陳小明 <ming@example.com>"


def test_drafting_a_schema_is_for_developers_only(api, sources_root):
    # 骨架一產生就會擋人（#39 的硬擋依它檢查），所以由開發者決定要不要開（ADR-00000020）。
    entry = _onboard(api, sources_root, "schema_role.yaml", b"count: 1\n")
    _post(api, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/schema", {})

    assert exc.value.code == _FORBIDDEN
    assert _get(api, f"/api/configs/{entry['uid']}")["schema"] is None
    _set_session(api)


def test_drafting_twice_does_not_overwrite_the_existing_schema(api, sources_root):
    entry = _onboard(api, sources_root, "schema_twice.yaml", b"count: 1\n")
    _post(api, f"/api/configs/{entry['uid']}/schema", {})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/schema", {})

    assert exc.value.code == _CONFLICT
    assert f".schemas/{entry['uid']}.json" in json.loads(exc.value.read())["detail"]


def test_a_raw_config_cannot_get_a_schema(api, sources_root):
    # raw 不解析：沒有結構可推導，說出原因（422），不產生一份空的假裝有把關。
    _set_session(api)
    source = _write_source(sources_root, "schema_raw.bin", b"\x00\x01\x02")
    entry = _post(api, "/api/configs", {"source_path": source, "format": "raw",
                                        "ambiguity_note": ""})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/schema", {})

    assert exc.value.code == _UNPROCESSABLE
    assert "raw" in json.loads(exc.value.read())["detail"]


def test_drafting_a_schema_for_an_unknown_uid_is_not_found(api):
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs/zzzzzzz9/schema", {})

    assert exc.value.code == _NOT_FOUND


# ── T9：第 2 層驗證接在儲存、進版與納入現況上（#39）────────────────────────────


def _with_schema(api, sources_root, name, content=b"count: 3\n"):
    """納管一份並替它產生 schema 骨架，回條目（含 `schema` 路徑）。"""
    entry = _onboard(api, sources_root, name, content)
    drafted = _post(api, f"/api/configs/{entry['uid']}/schema", {})
    return {**entry, "schema": drafted["schema"]}


def test_saving_a_value_of_the_wrong_type_is_refused_naming_the_field(api, sources_root):
    # 第 2 層硬擋：型別不符 schema 的改動存不成草稿；問題帶欄位路徑、行號、建議。
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "layer2_type.yaml")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": "many"}})

    assert exc.value.code == _UNPROCESSABLE
    detail = json.loads(exc.value.read())["detail"]
    problem = detail["problems"][0]
    # 結構化的四個要素：檔案（repo 內的來源複本）、行號、欄位、修正建議。
    assert detail["file"] == entry["source"]
    assert (problem["path"], problem["line"]) == ("count", 1)
    assert problem["suggestion"] and "count" in problem["message"]
    assert _get(api, "/api/drafts")["count"] == 0


def test_a_value_that_fits_the_schema_still_saves(api, sources_root):
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "layer2_ok.yaml")

    saved = _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 4}})

    assert [draft["uid"] for draft in saved["drafts"]] == [entry["uid"]]
    _clear_drafts(api)


def test_promoting_rechecks_against_the_schema_as_it_is_now(api, sources_root, repo):
    # 草稿存下之後 schema 被收緊：進版時依此刻的 schema 重驗、整批不進版，目標不動。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的 schema 檔")
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "layer2_promote.yaml")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 9}})
    schema_file = pathlib.Path(repo) / entry["schema"]
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    schema["properties"]["count"]["maximum"] = 5
    schema_file.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/promote", {})

    assert exc.value.code == _UNPROCESSABLE
    detail = json.loads(exc.value.read())["detail"]
    assert (detail["uid"], detail["problems"][0]["path"]) == (entry["uid"], "count")
    assert detail["file"] == entry["source"]
    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 3\n"
    _clear_drafts(api)


def test_adopting_a_target_that_breaks_the_schema_is_refused(api, sources_root):
    # 「將目標現況納入來源」走完整驗證：現場把整數改成文字，不符 schema → 拒絕並指名欄位。
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "layer2_adopt.yaml")
    pathlib.Path(entry["target"]).write_bytes(b"count: many\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})

    assert exc.value.code == _UNPROCESSABLE
    detail = json.loads(exc.value.read())["detail"]
    # 驗的是目標現況，所以「檔案」是目標路徑——要改的是那一份。
    assert (detail["file"], detail["problems"][0]["path"]) == (entry["target"], "count")


def test_adopting_as_a_draft_lists_the_schema_problems_as_warnings(api, sources_root):
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "layer2_adopt_draft.yaml")
    pathlib.Path(entry["target"]).write_bytes(b"count: many\n")

    loaded = _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt_draft"})

    assert [warning["path"] for warning in loaded["warnings"]] == ["count"]
    _clear_drafts(api)


def test_a_broken_schema_file_blocks_saving_and_names_the_file(api, sources_root, repo):
    # schema 檔被改壞：不當成「沒有 schema」放行，回結構化的 500、指名那份檔案（不變式 2／4）。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的 schema 檔")
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "layer2_broken.yaml")
    schema_file = pathlib.Path(repo) / entry["schema"]
    schema_file.write_text("{ not json", encoding="utf-8")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 4}})

    assert exc.value.code == _SERVER_ERROR
    detail = json.loads(exc.value.read())["detail"]
    assert detail["file"] == str(schema_file) and "下一步" in detail["message"]
    assert _get(api, "/api/drafts")["count"] == 0


# ── T9：退版時 schema 跟著回到那一版（#39 的定案，ADR-00000021）────────────────


def test_reverting_to_a_version_from_before_the_schema_removes_the_schema(api, sources_root):
    # 那一版當時還沒有 schema：退回去之後這份就沒有 schema。版本內容端點先說會這樣（確認框要用）。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "revert_schema_gone.yaml", b"count: 3\n")
    imported = _history(api, entry["uid"], "import")[0]["sha"]
    _promote_value(api, entry["uid"], "count", 4)
    _post(api, f"/api/configs/{entry['uid']}/schema", {})
    before = _get(api, f"/api/configs/{entry['uid']}/history/{imported}")

    reverted = _post(api, f"/api/configs/{entry['uid']}/revert", {"version": imported})

    assert (before["schema_effect"], reverted["schema_effect"]) == ("remove", "remove")
    assert _get(api, f"/api/configs/{entry['uid']}")["schema"] is None
    # 沒有 schema 了，原本會被擋的值（整數欄位填文字）現在只過第 1 層。
    saved = _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": "many"}})
    assert [draft["uid"] for draft in saved["drafts"]] == [entry["uid"]]
    _clear_drafts(api)


def test_reverting_restores_the_schema_of_that_version_not_todays(api, sources_root, repo):
    # 開發者後來收緊了 schema（上限 5）；退回收緊之前的版本，schema 也回到當時——不拿今天的擋舊內容。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的 schema 檔並提交")
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "revert_schema_back.yaml", b"count: 9\n")
    _promote_value(api, entry["uid"], "count", 8)
    older = _history(api, entry["uid"])[0]["sha"]
    schema_file = pathlib.Path(repo) / entry["schema"]
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    schema["properties"]["count"]["maximum"] = 5
    schema_file.write_text(json.dumps(schema), encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "--", entry["schema"]], check=True)
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=dev", "-c", "user.email=d@e.x", "commit", "-q",
         "-m", f"meta({entry['uid']}): 收緊 count 的上限"],
        check=True,
    )
    _promote_value(api, entry["uid"], "count", 4)

    reverted = _post(api, f"/api/configs/{entry['uid']}/revert", {"version": older})

    assert reverted["schema_effect"] == "restore"
    restored = json.loads(schema_file.read_text(encoding="utf-8"))
    assert "maximum" not in restored["properties"]["count"]
    # 8 超過今天的上限 5，照樣退得回去。
    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 8\n"


def test_a_revert_that_does_not_touch_the_schema_says_so(api, sources_root):
    _clear_drafts(api)
    entry = _with_schema(api, sources_root, "revert_schema_same.yaml", b"count: 3\n")
    _promote_value(api, entry["uid"], "count", 4)
    older = _history(api, entry["uid"])[0]["sha"]
    _promote_value(api, entry["uid"], "count", 5)

    reverted = _post(api, f"/api/configs/{entry['uid']}/revert", {"version": older})

    assert reverted["schema_effect"] is None
    assert _get(api, f"/api/configs/{entry['uid']}")["schema"] == entry["schema"]


# ── T9：單筆內容帶 schema 的欄位提示（constraints，#40）────────────────────────


def test_the_detail_carries_the_schema_hints_for_each_field(api, sources_root, repo):
    # 欄位表據此設輸入框的範圍與選項。只鎖型別的骨架沒有東西要提示；收緊之後才有。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的 schema 檔")
    entry = _with_schema(api, sources_root, "hints.yaml")
    assert _get(api, f"/api/configs/{entry['uid']}")["constraints"] == {}
    schema_file = pathlib.Path(repo) / entry["schema"]
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    schema["properties"]["count"].update({"minimum": 1, "maximum": 5, "description": "重試次數"})
    schema_file.write_text(json.dumps(schema), encoding="utf-8")

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert detail["constraints"] == {
        "count": {"minimum": 1, "maximum": 5, "description": "重試次數"}
    }
    assert detail["schema_error"] is None


def test_the_detail_carries_the_string_length_hints_of_a_field(api, sources_root, repo):
    # F6（人工驗收 U34）：字串的長度限制也帶給介面——清空不能空白的欄位才能在輸入當下就標示。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的 schema 檔")
    entry = _with_schema(api, sources_root, "hints_length.yaml", b"name: amr01\n")
    schema_file = pathlib.Path(repo) / entry["schema"]
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    schema["properties"]["name"].update({"minLength": 1, "maxLength": 20})
    schema_file.write_text(json.dumps(schema), encoding="utf-8")

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert detail["constraints"] == {"name": {"minLength": 1, "maxLength": 20}}


def test_a_broken_schema_still_shows_the_values_and_says_what_is_wrong(api, sources_root, repo):
    # schema 壞了不讓整份內容跟著讀不到——值要看得到才能退版或解除納管；錯誤以文字帶回。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地改 config-repo 裡的 schema 檔")
    entry = _with_schema(api, sources_root, "hints_broken.yaml")
    (pathlib.Path(repo) / entry["schema"]).write_text("{ not json", encoding="utf-8")

    detail = _get(api, f"/api/configs/{entry['uid']}")

    assert detail["values"] == {"count": 3}
    assert detail["constraints"] == {}
    assert entry["schema"] in detail["schema_error"] and "下一步" in detail["schema_error"]


# ── T9：人工指定型別（POST /api/configs/{uid}/types，#285、ADR-00000021）───────


def _specify(api, uid, path, type_name):
    return _post(api, f"/api/configs/{uid}/types", {"path": path, "type": type_name})


def test_a_developer_specifies_a_type_and_the_detail_shows_it_over_the_inferred_one(
    api, sources_root
):
    # `timeout: 5` 被推斷成整數；指定成 double 後單筆內容回報的型別以指定為準、標出哪幾個是人工
    # 指定，記一筆 meta、作者＝身分。還沒有 schema 的 config，指定會建立只含這個欄位的 schema。
    entry = _onboard(api, sources_root, "types_ok.yaml", b"timeout: 5\nname: amr01\n")
    assert _get(api, f"/api/configs/{entry['uid']}")["types"]["timeout"] == "int"

    done = _specify(api, entry["uid"], "timeout", "float")

    assert done == {
        "uid": entry["uid"], "path": "timeout", "type": "float",
        "schema": f".schemas/{entry['uid']}.json",
    }
    detail = _get(api, f"/api/configs/{entry['uid']}")
    assert detail["types"] == {"timeout": "float", "name": "string"}
    assert detail["manual_types"] == {"timeout": "float"}
    latest = _history(api, entry["uid"], "meta")[0]
    assert latest["summary"] == "指定「timeout」的型別為 double"
    assert latest["author"] == "陳小明 <ming@example.com>"


def test_a_field_specified_as_double_is_written_with_a_decimal_point(api, sources_root):
    # 指定 double 的目的：之後改成整數樣子的值，寫出去仍是 `6.0` 不是 `6`（ROS 的 1 vs 1.0）；
    # 而 5.5 這種原本會被「推斷是整數」擋下的值現在存得成。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "types_double.yaml", b"timeout: 5\n")
    _specify(api, entry["uid"], "timeout", "float")

    _promote_value(api, entry["uid"], "timeout", 6)
    assert pathlib.Path(entry["target"]).read_bytes() == b"timeout: 6.0\n"

    _promote_value(api, entry["uid"], "timeout", 5.5)
    assert pathlib.Path(entry["target"]).read_bytes() == b"timeout: 5.5\n"


def test_clearing_one_specification_keeps_the_other_and_the_type_falls_back(api, sources_root):
    # 逐欄位清除：只影響那一欄，型別回到推斷值（不是清成空的）；清除也記一筆 meta。
    entry = _onboard(api, sources_root, "types_clear.yaml", b"timeout: 5\nretries: 3\n")
    _specify(api, entry["uid"], "timeout", "float")
    _specify(api, entry["uid"], "retries", "float")

    cleared = _specify(api, entry["uid"], "timeout", None)

    detail = _get(api, f"/api/configs/{entry['uid']}")
    assert cleared["type"] is None
    assert detail["types"] == {"timeout": "int", "retries": "float"}
    assert detail["manual_types"] == {"retries": "float"}
    assert _history(api, entry["uid"], "meta")[0]["summary"] == "清除「timeout」的型別指定"


def test_specifying_and_clearing_are_for_developers_only(api, sources_root):
    # 改錯型別的後果是靜默的（直到 node 啟動失敗才發現），所以不放給一般使用者（ADR-00000020）。
    entry = _onboard(api, sources_root, "types_role.yaml", b"timeout: 5\n")
    _specify(api, entry["uid"], "timeout", "float")
    _post(api, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})

    for type_name in ("int", None):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _specify(api, entry["uid"], "timeout", type_name)
        assert exc.value.code == _FORBIDDEN

    assert _get(api, f"/api/configs/{entry['uid']}")["manual_types"] == {"timeout": "float"}
    _set_session(api)


def test_a_type_the_current_value_does_not_fit_is_refused_with_the_reason(api, sources_root):
    entry = _onboard(api, sources_root, "types_bad.yaml", b"name: amr01\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _specify(api, entry["uid"], "name", "int")

    assert exc.value.code == _UNPROCESSABLE
    detail = json.loads(exc.value.read())["detail"]
    assert "name" in detail and "下一步" in detail
    assert _get(api, f"/api/configs/{entry['uid']}")["schema"] is None


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"path": "timeout", "type": "decimal"}, _UNPROCESSABLE),  # 不認得的型別
        ({"path": "nope", "type": "int"}, _UNPROCESSABLE),  # 這份沒有的參數
        ({"path": "timeout", "type": None}, _CONFLICT),  # 沒指定過，沒有東西可清
    ],
)
def test_requests_that_cannot_be_carried_out_say_why(api, sources_root, payload, status):
    _set_session(api)
    name = f"types_refuse_{payload['path']}_{payload['type']}.yaml"
    entry = _onboard(api, sources_root, name, b"timeout: 5\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, f"/api/configs/{entry['uid']}/types", payload)

    assert exc.value.code == status
    assert "下一步" in json.loads(exc.value.read())["detail"]


def test_specifying_for_an_unknown_uid_is_not_found(api):
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _specify(api, "zzzzzzz9", "timeout", "int")

    assert exc.value.code == _NOT_FOUND


# ── T9：修改 config 屬性（POST /api/configs/{uid}/attributes，#286）───────────


def _attributes(api, uid, **changes):
    detail = _get(api, f"/api/configs/{uid}")
    payload = {key: detail[key] for key in ("name", "hostname", "groups", "description")}
    return _post(api, f"/api/configs/{uid}/attributes", {**payload, **changes})


def test_a_developer_changes_the_attributes_and_the_list_shows_them(api, sources_root):
    # 名稱、群組、主機、說明都改得了；清單列（樹的資料來源）與單筆內容都看得到新的值。
    entry = _onboard(api, sources_root, "attr_ok.yaml", b"count: 1\n")

    updated = _attributes(
        api, entry["uid"], name="navigation", hostname="site-a",
        groups=["navigation", "safety"], description="Nav2 導航參數",
    )

    assert updated == {
        "uid": entry["uid"], "name": "navigation", "hostname": "site-a",
        "ref": f"navigation@site-a-{entry['uid']}",
        "groups": ["navigation", "safety"], "description": "Nav2 導航參數",
    }
    row = next(row for row in _get(api, "/api/configs") if row["uid"] == entry["uid"])
    assert (row["name"], row["hostname"], row["groups"], row["description"]) == (
        "navigation", "site-a", ["navigation", "safety"], "Nav2 導航參數",
    )
    assert _get(api, f"/api/configs/{entry['uid']}")["description"] == "Nav2 導航參數"


def test_renaming_keeps_the_history_the_draft_and_the_search_on_the_same_config(
    api, sources_root
):
    # 改名不影響 uid 與任何關聯：歷史還在同一份底下、存過的草稿還在、以新名稱搜得到同一個 uid。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "attr_rename.yaml", b"count: 1\n")
    _promote_value(api, entry["uid"], "count", 2)
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 3}})

    _attributes(api, entry["uid"], name="giraffe-renamed")

    assert [change["kind"] for change in _history(api, entry["uid"])] == ["cfg"]
    assert [draft["uid"] for draft in _get(api, "/api/drafts")["drafts"]] == [entry["uid"]]
    hits = _search(api, "giraffe-renamed", "config 名稱")["hits"]
    assert [hit["uid"] for hit in hits] == [entry["uid"]]
    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 2\n"  # 目標沒被動到
    _clear_drafts(api)


def test_an_attribute_change_is_a_meta_record_outside_the_default_history(api, sources_root):
    # 屬性變更與內容變更分開記：預設的歷史（只看內容變更）不出現它，點名 meta 才看得到。
    entry = _onboard(api, sources_root, "attr_meta.yaml", b"count: 1\n")

    _attributes(api, entry["uid"], groups=["perception"])

    assert _history(api, entry["uid"]) == []
    latest = _history(api, entry["uid"], "meta")[0]
    assert (latest["kind"], latest["summary"]) == ("meta", "群組改為 perception")
    assert latest["author"] == "陳小明 <ming@example.com>"


def test_changing_attributes_is_for_developers_only(api, sources_root):
    entry = _onboard(api, sources_root, "attr_role.yaml", b"count: 1\n")
    _post(api, "/api/session", {"name": "王小美", "email": "mei@example.com", "role": "user"})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _attributes(api, entry["uid"], name="hijacked")

    assert exc.value.code == _FORBIDDEN
    assert _get(api, f"/api/configs/{entry['uid']}")["name"] == entry["name"]
    _set_session(api)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "   "}, "name"),
        ({"hostname": "amr 01"}, "hostname"),
        ({"groups": ["nav", "nav"]}, "groups"),
        ({"description": "第一行\n第二行"}, "description"),
    ],
)
def test_an_invalid_attribute_is_refused_naming_the_field_and_nothing_changes(
    api, sources_root, changes, field
):
    entry = _onboard(api, sources_root, f"attr_bad_{field}.yaml", b"count: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _attributes(api, entry["uid"], **changes)

    assert exc.value.code == _UNPROCESSABLE
    detail = json.loads(exc.value.read())["detail"]
    assert detail["field"] == field and "下一步" in detail["message"]
    assert _history(api, entry["uid"], "meta") == []


def test_saving_unchanged_attributes_is_a_conflict_not_an_empty_record(api, sources_root):
    entry = _onboard(api, sources_root, "attr_same.yaml", b"count: 1\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _attributes(api, entry["uid"])

    assert exc.value.code == _CONFLICT
    assert _history(api, entry["uid"], "meta") == []


def test_changing_the_attributes_of_an_unknown_uid_is_not_found(api):
    _set_session(api)
    payload = {"name": "x", "hostname": "amr01", "groups": [], "description": None}

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs/zzzzzzz9/attributes", payload)

    assert exc.value.code == _NOT_FOUND


# ── 人工驗證 U06／U07：INI 分號註解、raw 的二進位檔 ────────────────────────────

_FIRMWARE = b"\x7fCMRAW\x00\x01\x02\xff\xfe binary-ish payload\n"  # 不是 UTF-8
_LEGACY_INI = "; 舊式 INI 設定（驗證用範例）\n[motion]\nmax_vel = 0.55\n".encode()


def _onboard_as(api, sources_root, name, content, fmt):
    _set_session(api)
    source = _write_source(sources_root, name, content)
    return _post(api, "/api/configs", {"source_path": source, "format": fmt, "ambiguity_note": ""})


def test_an_ini_file_with_a_semicolon_comment_can_be_previewed_and_onboarded(api, sources_root):
    # `;` 開頭的整行註解是合法的 INI：預覽不報語法錯誤、納管後來源複本逐位元組相同。
    source = _write_source(sources_root, "legacy.ini", _LEGACY_INI)

    preview = _post(api, "/api/inspect", {"source_path": source, "format": "ini"})
    entry = _onboard_as(api, sources_root, "legacy.ini", _LEGACY_INI, "ini")

    assert preview["types"] == {"motion": "dict", "motion.max_vel": "string"}
    detail = _get(api, f"/api/configs/{entry['uid']}")
    assert detail["values"] == {"motion": {"max_vel": "0.55"}}
    assert pathlib.Path(entry["target"]).read_bytes() == _LEGACY_INI


def test_a_binary_file_can_be_previewed_and_onboarded_as_raw(api, sources_root, repo):
    # raw＝只版控、不解析：內容是不是文字都不該擋。預覽沒有欄位；納管後來源複本逐位元組相同、
    # 狀態一致、單筆內容開得起來（沒有欄位表）。
    source = _write_source(sources_root, "firmware.bin", _FIRMWARE)

    preview = _post(api, "/api/inspect", {"source_path": source, "format": "raw"})
    entry = _onboard_as(api, sources_root, "firmware.bin", _FIRMWARE, "raw")

    assert (preview["format"], preview["field_count"], preview["ambiguities"]) == ("raw", 0, [])
    assert _state_of(api, entry["uid"]) == "in_sync"
    assert _get(api, f"/api/configs/{entry['uid']}")["values"] is None
    if not os.environ.get("CM_SYSTEM_BASE_URL"):
        assert (pathlib.Path(repo) / entry["source"]).read_bytes() == _FIRMWARE
    assert _search(api, "binary")["query"] == "binary"  # 搜尋不被這份二進位檔弄壞


def test_a_binary_file_previewed_as_a_text_format_is_told_to_use_raw(api, sources_root):
    # 選了要解析的格式而內容不是文字：拒絕，下一步指去 raw（此時這個建議才成立）。
    source = _write_source(sources_root, "firmware_as_yaml.bin", _FIRMWARE)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/inspect", {"source_path": source, "format": "yaml"})

    detail = json.loads(exc.value.read())["detail"]
    assert exc.value.code == _UNPROCESSABLE
    assert "yaml" in detail["message"] and "raw" in detail["message"]


def test_a_binary_raw_config_survives_drift_handling_and_revert_byte_for_byte(api, sources_root):
    # raw 的內容整塊搬、不經解碼：以來源覆蓋、納入現況、寫出修復、退版，位元組都不能走樣。
    _clear_drafts(api)
    entry = _onboard_as(api, sources_root, "firmware_ops.bin", _FIRMWARE, "raw")
    target = pathlib.Path(entry["target"])
    changed = b"\x00\xfe\xff changed on site \x80\n"

    target.write_bytes(changed)
    assert _state_of(api, entry["uid"]) == "drift"
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "overwrite"})
    assert target.read_bytes() == _FIRMWARE

    target.write_bytes(changed)
    _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})
    assert (target.read_bytes(), _state_of(api, entry["uid"])) == (changed, "in_sync")

    target.unlink()
    _post(api, f"/api/configs/{entry['uid']}/apply", {})
    assert target.read_bytes() == changed

    imported = _history(api, entry["uid"], "import")[0]["sha"]
    _post(api, f"/api/configs/{entry['uid']}/revert", {"version": imported})
    assert (target.read_bytes(), _state_of(api, entry["uid"])) == (_FIRMWARE, "in_sync")


# ── 人工驗證 U10／U13／U15：被拒絕時的文案要說「這次沒做什麼」，不是叫人去改既有的東西 ──


def test_onboarding_a_target_that_is_already_managed_points_to_the_existing_config(
    api, sources_root
):
    # 先前的說法叫人「改掉其中一筆的目標位置——寫出順序會決定最終內容」，容易誤導。
    # 實情是：這個檔案已經納管了，這次沒有再納管；該做的是去看既有的那一份。
    entry = _onboard(api, sources_root, "already_managed.yaml", b"count: 1\n")
    payload = {"source_path": entry["target"], "format": "yaml", "ambiguity_note": ""}

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", payload)

    detail = json.loads(exc.value.read())["detail"]
    assert exc.value.code == _CONFLICT
    assert "已經納管" in detail and entry["ref"] in detail and "沒有" in detail
    assert "寫出順序" not in detail and "改掉" not in detail


def test_adding_a_root_that_is_already_whitelisted_says_nothing_was_added(api, sources_root):
    # 先前的說法是「第 2 筆與第 3 筆重複，刪掉其中一筆」——但清單其實沒有多一筆，沒有東西要刪。
    _set_session(api)

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/allowed-roots", {"prefix": str(sources_root)})

    detail = json.loads(exc.value.read())["detail"]
    assert exc.value.code == _CONFLICT
    assert "已經在白名單" in detail and "沒有加入" in detail
    assert "刪掉" not in detail and "第 " not in detail


def test_the_removal_confirmation_is_worded_for_a_person_not_for_the_api(api, sources_root):
    _set_session(api)
    with pytest.raises(urllib.error.HTTPError) as missing:
        _delete(api, "/api/allowed-roots", {"prefix": "/nonexistent/never-added"})
    assert missing.value.code == _NOT_FOUND

    stored = _get(api, "/api/allowed-roots")["roots"][0]["prefix"]
    with pytest.raises(urllib.error.HTTPError) as exc:
        _delete(api, "/api/allowed-roots", {"prefix": stored, "confirmed": False})

    detail = json.loads(exc.value.read())["detail"]
    assert detail["kind"] == "confirm_required"
    assert "confirmed" not in detail["message"] and "確認移除" in detail["message"]


# ── T9：第 3 層規則與略過的理由（#41／#42）────────────────────────────────────


def _write_rules(repo, uid, rules):
    """替一份 config 寫規則檔（`.rules/<uid>.toml`，開發者手寫、這裡就地寫進 config-repo）。"""
    directory = pathlib.Path(repo) / ".rules"
    directory.mkdir(exist_ok=True)
    (directory / f"{uid}.toml").write_text(rules, encoding="utf-8")


def _with_rules(api, sources_root, repo, name, rules):
    """納管一份內容為 `lo: 1`／`hi: 5` 的 config，並替它寫規則檔。"""
    entry = _onboard(api, sources_root, name, b"lo: 1\nhi: 5\n")
    _write_rules(repo, entry["uid"], rules)
    return entry


_LO_BELOW_HI = '[[rules]]\nid = "lo-below-hi"\nleft = "lo"\nop = "<"\nright = "hi"\n'


def _refused(api, path, payload):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, path, payload)
    return exc.value.code, json.loads(exc.value.read())["detail"]


def test_breaking_a_rule_holds_the_save_back_until_a_reason_is_given(api, sources_root, repo):
    # 第 3 層不硬擋：違反規則時儲存先停下來（409、kind 說明是要理由）、指名規則與欄位；
    # 帶了理由再送就存得成，回應列出這份內容的警告。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    entry = _with_rules(api, sources_root, repo, "rule_hold.yaml", _LO_BELOW_HI)
    request = {"uid": entry["uid"], "edits": {"lo": 9}}

    status, detail = _refused(api, "/api/drafts", request)

    assert (status, detail["kind"]) == (_CONFLICT, "override_required")
    problem = detail["problems"][0]
    assert (problem["rule"], problem["path"]) == ("lo-below-hi", "lo")
    assert problem["severity"] == "warning"
    assert _get(api, "/api/drafts")["count"] == 0

    saved = _post(api, "/api/drafts", {**request, "overrides": {"lo-below-hi": "現場測試低速模式"}})

    assert [draft["uid"] for draft in saved["drafts"]] == [entry["uid"]]
    assert [warning["rule"] for warning in saved["warnings"]] == ["lo-below-hi"]
    assert _get(api, f"/api/configs/{entry['uid']}")["draft_overrides"] == {
        "lo-below-hi": "現場測試低速模式"
    }
    _clear_drafts(api)


def test_the_reason_is_written_into_the_change_record(api, sources_root, repo):
    # 理由記入變更紀錄：主旨照舊，內文一行 `override(<規則>): <理由>`；歷史另以結構化欄位回報。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    entry = _with_rules(api, sources_root, repo, "rule_rec.yaml", _LO_BELOW_HI)
    _post(api, "/api/drafts", {
        "uid": entry["uid"], "edits": {"lo": 9}, "overrides": {"lo-below-hi": "現場測試低速模式"},
    })

    _post(api, "/api/promote", {})

    latest = _history(api, entry["uid"])[0]
    assert latest["summary"] == f"修改參數（{entry['name']}@{entry['hostname']}）"
    assert latest["body"].strip() == "override(lo-below-hi): 現場測試低速模式"
    assert latest["overrides"] == [{"rule": "lo-below-hi", "reason": "現場測試低速模式"}]
    assert pathlib.Path(entry["target"]).read_bytes() == b"lo: 9\nhi: 5\n"


def test_content_that_satisfies_the_rules_saves_without_any_reason(api, sources_root, repo):
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    entry = _with_rules(api, sources_root, repo, "rule_ok.yaml", _LO_BELOW_HI)

    saved = _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"lo": 2}})

    assert saved["warnings"] == []
    _post(api, "/api/promote", {})
    assert _history(api, entry["uid"])[0]["overrides"] == []


def test_a_reason_does_not_get_past_a_hard_error(api, sources_root, repo):
    # override 後的修改仍走完整驗證：理由只略過第 3 層的警告，第 2 層（schema）照擋。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    entry = _with_rules(api, sources_root, repo, "rule_hard.yaml", _LO_BELOW_HI)
    _post(api, f"/api/configs/{entry['uid']}/schema", {})

    status, detail = _refused(api, "/api/drafts", {
        "uid": entry["uid"], "edits": {"lo": 9, "hi": "many"},
        "overrides": {"lo-below-hi": "理由"},
    })

    assert status == _UNPROCESSABLE and detail["problems"][0]["path"] == "hi"
    assert _get(api, "/api/drafts")["count"] == 0


def test_a_rule_added_after_saving_blocks_promotion_until_a_reason_is_given(
    api, sources_root, repo
):
    # 進版時重驗：草稿存下時還沒有規則。規則加上後進版被擋、指名那份與那條規則；回去帶理由
    # 重存就進得了版。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "rule_late.yaml", b"lo: 1\nhi: 5\n")
    _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"lo": 9}})
    _write_rules(repo, entry["uid"], _LO_BELOW_HI)

    status, detail = _refused(api, "/api/promote", {})

    assert status == _UNPROCESSABLE
    assert (detail["uid"], detail["problems"][0]["rule"]) == (entry["uid"], "lo-below-hi")
    assert pathlib.Path(entry["target"]).read_bytes() == b"lo: 1\nhi: 5\n"

    _post(api, "/api/drafts", {
        "uid": entry["uid"], "edits": {"lo": 9}, "overrides": {"lo-below-hi": "確認過"},
    })
    _post(api, "/api/promote", {})
    assert pathlib.Path(entry["target"]).read_bytes() == b"lo: 9\nhi: 5\n"


def test_a_value_missing_from_the_referenced_config_is_a_warning(api, sources_root, repo):
    # 外部一致性＝跨 config 對照：這份用到的 frame 必須是另一份定義過的。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    robot = _onboard(api, sources_root, "rule_robot.yaml", b"frames: [base_link, odom]\n")
    rules = (
        '[[rules]]\nid = "frame-is-defined"\nleft = "frame"\n'
        f'in = {{ config = "{robot["uid"]}", field = "frames" }}\n'
    )
    nav = _onboard(api, sources_root, "rule_nav.yaml", b"frame: odom\n")
    _write_rules(repo, nav["uid"], rules)

    status, detail = _refused(api, "/api/drafts", {"uid": nav["uid"], "edits": {"frame": "odm"}})

    assert (status, detail["problems"][0]["rule"]) == (_CONFLICT, "frame-is-defined")
    assert "base_link" in detail["problems"][0]["suggestion"]


def test_a_broken_rules_file_is_a_warning_to_override_not_a_dead_end(api, sources_root, repo):
    # 規則檔寫壞：不讓存檔整個失敗（那會成為路障），也不默默當成沒有規則——要填理由才略得過，
    # 留下紀錄讓開發者看得到。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    broken = '[[rules]]\nid = "r"\nleft = "lo"\nop = "<<"\nvalue = 1\n'
    entry = _with_rules(api, sources_root, repo, "rule_broken.yaml", broken)
    request = {"uid": entry["uid"], "edits": {"lo": 2}}

    status, detail = _refused(api, "/api/drafts", request)

    assert (status, detail["problems"][0]["rule"]) == (_CONFLICT, "rules-file")
    assert "<<" in detail["problems"][0]["message"]
    saved = _post(api, "/api/drafts", {**request, "overrides": {"rules-file": "規則檔待修"}})
    assert [draft["uid"] for draft in saved["drafts"]] == [entry["uid"]]
    _clear_drafts(api)


def test_adopting_a_target_that_breaks_a_rule_points_to_the_draft_route(api, sources_root, repo):
    # 「將目標現況納入來源」不另開填理由的入口：指去走「先納入、待修正」，在草稿上填。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的規則檔")
    _clear_drafts(api)
    entry = _with_rules(api, sources_root, repo, "rule_adopt.yaml", _LO_BELOW_HI)
    pathlib.Path(entry["target"]).write_bytes(b"lo: 9\nhi: 5\n")

    status, detail = _refused(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt"})

    assert (status, detail["kind"]) == (_CONFLICT, "override_required")
    assert "先納入、待修正" in detail["message"]
    loaded = _post(api, f"/api/configs/{entry['uid']}/resolve", {"action": "adopt_draft"})
    assert [warning["rule"] for warning in loaded["warnings"]] == ["lo-below-hi"]
    _clear_drafts(api)


# ── T9：第 1 層規則可配置（.lintrc.toml，#43）──────────────────────────────────


def _write_lintrc(repo, text):
    (pathlib.Path(repo) / ".lintrc.toml").write_text(text, encoding="utf-8")


def test_the_lintrc_in_the_repo_changes_what_the_first_layer_accepts(api, sources_root, repo):
    # 規則檔的修改實際改變驗證行為：預設擋 `yes`；設定檔放寬布林寫法後同一份內容存得成。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的 .lintrc.toml")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "lintrc_bool.yaml", b"enabled: yes\ncount: 1\n",
                     note="第 1 行「yes」已確認")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})
        assert exc.value.code == _UNPROCESSABLE

        _write_lintrc(repo, '[yaml]\nboolean_literals = ["true", "false", "yes", "no"]\n')

        saved = _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})
        assert [draft["uid"] for draft in saved["drafts"]] == [entry["uid"]]
    finally:
        (pathlib.Path(repo) / ".lintrc.toml").unlink(missing_ok=True)
        _clear_drafts(api)


def test_an_unusable_lintrc_blocks_saving_with_a_structured_error_naming_it(
    api, sources_root, repo
):
    # 它管的是硬擋的第 1 層：壞了不能當成預設值放行——結構化 500、指名那份檔。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地寫 config-repo 裡的 .lintrc.toml")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "lintrc_broken.yaml", b"count: 1\n")
    _write_lintrc(repo, '[yaml]\nduplicate_key = "ignore"\n')
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(api, "/api/drafts", {"uid": entry["uid"], "edits": {"count": 2}})

        detail = json.loads(exc.value.read())["detail"]
        assert exc.value.code == _SERVER_ERROR
        assert detail["file"].endswith(".lintrc.toml") and "ignore" in detail["message"]
    finally:
        (pathlib.Path(repo) / ".lintrc.toml").unlink(missing_ok=True)


# ── T9：整個清單替換（陣列編輯的後端，#46）────────────────────────────────────


def test_a_draft_can_replace_a_whole_list_and_keeps_the_rest_byte_for_byte(api, sources_root):
    # 介面的增刪調序送的是整個清單；其餘內容（註解、別的鍵）原樣不動，double 清單的新元素帶小數點。
    _clear_drafts(api)
    content = b"# nav\nrecovery:  # order matters\n  - spin\n  - wait\ngains: [0.5]\nspeed: 1\n"
    entry = _onboard(api, sources_root, "list_edit.yaml", content)

    _post(api, "/api/drafts", {
        "uid": entry["uid"], "edits": {"recovery": ["wait", "spin", "pause"], "gains": [0.5, 1]},
    })
    _post(api, "/api/promote", {})

    assert pathlib.Path(entry["target"]).read_bytes() == (
        b"# nav\nrecovery:  # order matters\n  - wait\n  - spin\n  - pause\n"
        b"gains: [0.5, 1.0]\nspeed: 1\n"
    )
