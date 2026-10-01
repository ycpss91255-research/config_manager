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
import json
import os
import pathlib
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

import config_manager
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


def _get(api, path):
    with urllib.request.urlopen(f"{api}{path}", timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(api, path, payload):
    request = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _delete(api, path, payload):
    request = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="DELETE",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _detail(error):
    # 422／409 的 body 是 {"detail": "..."}。斷言原因文字，才分得出「白名單外」與
    # 「不是目錄」——只斷言狀態碼的話，任何一種 422 都會讓規格過（資安審查 #185）。
    return json.loads(error.read().decode("utf-8"))["detail"]


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

    result = _cli("import", "--api", api, "--source", source, "--format", "yaml")

    assert result.returncode == 0
    assert "已納管" in result.stdout
    # 真的納管了：它的 target 出現在清單裡（走同一支端點，不是 CLI 自己讀清單檔）。
    targets = [row["target"] for row in _get(api, "/api/configs")]
    assert source in targets


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


def test_browse_a_file_rather_than_a_directory_is_refused(api, sources_root):
    a_file = pathlib.Path(sources_root) / "not_a_dir.yaml"
    a_file.write_bytes(b"x\n")
    query = urllib.parse.urlencode({"path": str(a_file)})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/browse?{query}")

    assert exc.value.code == _UNPROCESSABLE
    # 原因是「不是目錄」，kind 與「白名單外」分得開——前端據此不顯示加白名單入口。
    detail = _detail(exc.value)
    assert detail["kind"] == "not_a_directory"
    assert "不是可列的目錄" in detail["message"]


def test_cli_browse_goes_through_the_same_endpoint_as_the_page(api, sources_root):
    base = pathlib.Path(sources_root) / "cli_browse"
    base.mkdir(exist_ok=True)
    (base / "picked.yaml").write_bytes(b"a: 1\n")

    result = _cli("browse", "--api", api, "--path", str(base))

    assert result.returncode == 0
    assert "picked.yaml" in result.stdout


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

    result = _cli("import", "--api", api, "--source", src, "--format", "yaml", "--note", huge)

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
    # 階段掛在 app 上、跨規格存活；每則自己清，不靠「前一則有清」（#153）。
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


# ── T9：解除納管（DELETE /api/configs/{uid}，#28）──────────────────────────────


def test_unmanaging_removes_the_config_from_the_list_and_keeps_the_target_file(api, sources_root):
    # §5.5：解除納管回到未納管、不刪 target；回被解除的條目，清單不再列它。
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "unmanage.yaml", b"count: 1\n")

    request = urllib.request.Request(f"{api}/api/configs/{entry['uid']}", method="DELETE")
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        removed = json.loads(response.read().decode("utf-8"))

    assert removed["uid"] == entry["uid"]
    assert entry["uid"] not in [row["uid"] for row in _get(api, "/api/configs")]
    assert pathlib.Path(entry["target"]).read_bytes() == b"count: 1\n"


def test_unmanaging_records_an_unmanage_change_by_the_session_identity(api, sources_root, repo):
    # 解除納管本身留紀錄、作者＝身分。條目已不在清單、歷史端點以 404 擋，故就地讀 git log。
    if os.environ.get("CM_SYSTEM_BASE_URL"):
        pytest.skip("需就地讀 config-repo 的 git log")
    _clear_drafts(api)
    entry = _onboard(api, sources_root, "unmanage_rec.yaml", b"count: 1\n")

    request = urllib.request.Request(f"{api}/api/configs/{entry['uid']}", method="DELETE")
    with urllib.request.urlopen(request, timeout=_TIMEOUT):
        pass

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

    request = urllib.request.Request(f"{api}/api/configs/{entry['uid']}", method="DELETE")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=_TIMEOUT)

    assert exc.value.code == _CONFLICT
    _clear_drafts(api)


def test_unmanaging_an_unknown_uid_is_not_found(api):
    _set_session(api)
    request = urllib.request.Request(f"{api}/api/configs/zzzzzzz9", method="DELETE")

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=_TIMEOUT)

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

    request = urllib.request.Request(f"{api}/api/configs/{entry['uid']}", method="DELETE")
    with urllib.request.urlopen(request, timeout=_TIMEOUT):
        pass
    assert _search(api, "222", "參數值")["hits"] == []


def test_search_with_an_unknown_scope_is_unprocessable_not_empty(api):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _search(api, "x", "主機")

    assert exc.value.code == _UNPROCESSABLE
    assert "config 名稱／目標路徑／參數名稱／參數值／全部" in _detail(exc.value)


# ── T9／T13：編輯階段（POST /api/session/lock…，#33）───────────────────────────


def _lock(api, method="POST", path="/api/session/lock", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api}{path}", data=data, headers={"content-type": "application/json"}, method=method
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


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
    """就地起的服務：從物件圖裡撈出 app 的 LockBox（測逾時要動它的時鐘與逾時）。"""
    boxes = [obj for obj in gc.get_objects() if isinstance(obj, LockBox)]
    assert len(boxes) == 1, f"預期恰好一個 LockBox，找到 {len(boxes)}"
    return boxes[0]


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

    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        released = json.loads(response.read().decode("utf-8"))

    assert released == {"released": True}
    assert _lock(api, "GET")["held"] is False


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
