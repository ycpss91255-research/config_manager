"""T9 — HTTP 端點、T10 — CLI。以真實 HTTP 對一個真的在跑的服務測。

PDF §3.6.1 軸 2 的層級只有 Unit／Integration／System／Acceptance。先前這些規格放在
`test/bats/runtime/`——`runtime` 不是層級，是我發明的（#116）。而且測的是 Python，
依「測試工具對應被測的語言」該用 pytest 不是 bats。

搬到這裡解掉的是結構障礙：先前 `api/routes.py` 與 `api/cli.py` 在覆蓋率報告上是 0%，
不是沒測，是測它們的規格不由 pytest 執行（#97）。**還沒解掉的是量測範圍**——覆蓋率的
`source` 目前只有 `core/`，所以 `api/` 依然不在報告裡（見 `doc/TEST-PLAN.md`
「已知的量測缺口」）。
"""

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import pytest

import config_manager
from config_manager.core.allowed_roots import load as load_allowed_roots

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
