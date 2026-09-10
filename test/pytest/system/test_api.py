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

_TIMEOUT = 5
# 422：輸入的形狀對、值不合法。端點刻意不用 400——那會把「你送錯格式」與
# 「你送的值不行」折成同一個回覆。
_UNPROCESSABLE = 422
# 409：與目前狀態相牴觸（已有同一 target 的條目、或尚未設定身分）。
_CONFLICT = 409


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


def test_import_a_source_outside_the_whitelist_is_refused(api, tmp_path):
    _set_session(api)
    # tmp_path 不在 sources_root（白名單）底下。
    outside = tmp_path / "secret.yaml"
    outside.write_bytes(b"stolen\n")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(api, "/api/configs", {"source_path": str(outside), "format": "yaml"})

    assert exc.value.code == _UNPROCESSABLE
    assert "白名單之外" in _detail(exc.value)  # 被拒的原因是白名單，不是別種 422


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

    kinds = {entry["name"]: entry["kind"] for entry in listing["entries"]}
    assert kinds["sub"] == "dir"
    assert kinds["conf.yaml"] == "file"


def test_browse_a_path_outside_the_whitelist_is_refused(api, tmp_path):
    # tmp_path 不在 sources_root（白名單）底下。
    query = urllib.parse.urlencode({"path": str(tmp_path)})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/browse?{query}")

    assert exc.value.code == _UNPROCESSABLE
    assert "白名單之外" in _detail(exc.value)


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
    assert "白名單之外" in _detail(exc.value)


def test_browse_a_file_rather_than_a_directory_is_refused(api, sources_root):
    a_file = pathlib.Path(sources_root) / "not_a_dir.yaml"
    a_file.write_bytes(b"x\n")
    query = urllib.parse.urlencode({"path": str(a_file)})

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(api, f"/api/browse?{query}")

    assert exc.value.code == _UNPROCESSABLE
    assert "不是可列的目錄" in _detail(exc.value)  # 原因是「不是目錄」，與「白名單外」分得開


def test_cli_browse_goes_through_the_same_endpoint_as_the_page(api, sources_root):
    base = pathlib.Path(sources_root) / "cli_browse"
    base.mkdir(exist_ok=True)
    (base / "picked.yaml").write_bytes(b"a: 1\n")

    result = _cli("browse", "--api", api, "--path", str(base))

    assert result.returncode == 0
    assert "picked.yaml" in result.stdout
