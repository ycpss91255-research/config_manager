"""T10 — CLI。測試介面：api/cli 的 main 與 serve_plan。

這一批與 `test/pytest/system/test_api.py` 的 T10 規格**測不同的東西**，不是同一批的
複製：系統層那幾則以子行程執行 CLI，證明它真的走了 `--api` 指向的那組 HTTP 端點
（ADR-00000009）。這一批在同一個行程裡呼叫，測的是**決定**——起服務前要跑哪個
config-repo、放行哪些來源、讀不到端點時的結束碼、清單怎麼呈現成中文狀態。

`serve` 的兩件事在 #97 被拆開：決定要跑什麼（serve_plan，純函式，environ 當參數
傳進來）與真的跑起來（create_app 加 uvicorn.run，兩行）。拆開之前那些決定與啟動擠在
同一個函式裡，而測試不會去跑 uvicorn.run，於是它們一起量不到。
"""

import datetime
import io
import json
import urllib.error

import pytest

from config_manager.api.cli import main, serve_plan
from config_manager.api.errors import (
    ConfigRepoMissing,
    IdleTimeoutInvalid,
    ModeInvalid,
    SessionTimeoutInvalid,
)
from config_manager.api.routes import DEFAULT_ORIGINS, create_app

# 2 是「用法錯誤／接線不對」，1 是「跑了但沒成功」。serve 少了 CM_CONFIG_REPO
# 屬於前者：不是服務起不來，是根本沒有東西可服務。
_MISCONFIGURED = 2
# import 的作者（#288）：CLI 不是瀏覽器，沒有頁面上輸入過的身分可沿用。
_AUTHOR = ("--name", "陳小明", "--email", "ming@example.com")

_ROWS = [
    {"ref": "a@amr01-mfz3k9q1", "target": "/etc/a.yaml", "state": "in_sync"},
    {"ref": "bb@amr01-mfz3k9q2", "target": "/etc/b.yaml", "state": "drift"},
    {"ref": "c@amr01-mfz3k9q3", "target": "/etc/c.yaml", "state": "missing"},
]


class _Response:
    """urlopen 的回覆：一個當 context manager 用、read() 得出 bytes 的東西。"""

    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


@pytest.fixture
def answers(monkeypatch):
    """讓端點回一份指定的清單，並記下 CLI 打的是哪個位址。"""
    called: list[str] = []

    def _answer(rows):
        def _urlopen(url, timeout=None):
            called.append(url)
            return _Response(rows)

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        # import 走自己的 cookie 罐（#288：先設身分、再納管，兩個請求要接得上同一張票），所以
        # 不經 urlopen 而經 OpenerDirector.open——同一個記錄器兩邊都接。
        monkeypatch.setattr(
            "urllib.request.OpenerDirector.open", lambda _self, url, timeout=None: _urlopen(url)
        )
        return called

    return _answer


def test_serve_plan_serves_the_config_repo_named_by_the_environment():
    # 這一層是邊界，讀環境變數是它的工作；但「讀」與「決定」是兩件事，而只有後者
    # 需要被觀察，所以 environ 是參數不是隱含輸入。
    plan = serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/config-repo"})

    assert plan.repo == "/srv/config-repo"


def test_serve_plan_keeps_the_host_and_port_it_was_given():
    plan = serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/config-repo"})

    assert (plan.host, plan.port) == ("0.0.0.0", 9000)


def test_serve_plan_refuses_to_plan_a_service_with_no_config_repo():
    # 沒有 config-repo 就沒有東西可服務。大聲失敗，不是起一個服務空轉（不變式 2）。
    with pytest.raises(ConfigRepoMissing) as error:
        serve_plan("127.0.0.1", 8080, {})

    assert "CM_CONFIG_REPO" in str(error.value)


def test_serve_plan_says_what_to_do_about_the_missing_config_repo():
    # 三要素的第三項：該怎麼改（§0.4）。空字串與未設定是同一件事。
    with pytest.raises(ConfigRepoMissing) as error:
        serve_plan("127.0.0.1", 8080, {"CM_CONFIG_REPO": ""})

    assert "下一步：" in str(error.value)


def test_serve_plan_takes_the_allowed_origins_from_the_environment():
    # 非本機的部署來源不同，必須自己指定；逗號分隔，前後空白不算數。
    plan = serve_plan(
        "127.0.0.1",
        8080,
        {"CM_CONFIG_REPO": "/srv/r", "CM_ALLOWED_ORIGINS": " http://amr01:8081 , http://x:8081 "},
    )

    assert plan.allowed_origins == ("http://amr01:8081", "http://x:8081")


def test_serve_plan_falls_back_to_the_safe_default_origins_when_none_are_named():
    # 沒指定不是「放行全部」——預設值落向安全（不變式 4）。
    plan = serve_plan("127.0.0.1", 8080, {"CM_CONFIG_REPO": "/srv/r", "CM_ALLOWED_ORIGINS": " , "})

    assert plan.allowed_origins == DEFAULT_ORIGINS


def test_serve_hands_the_planned_address_to_the_server(monkeypatch):
    # 「決定要跑什麼」與「真的跑起來」拆開之後，這一則證明兩者仍然接得上。
    started: dict[str, object] = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **options: started.update(options))
    monkeypatch.setenv("CM_CONFIG_REPO", "/srv/config-repo")

    main(["config_manager", "serve", "--host", "0.0.0.0", "--port", "9000"])

    assert (started["host"], started["port"]) == ("0.0.0.0", 9000)


def test_serve_exits_nonzero_when_the_config_repo_is_not_set(monkeypatch):
    monkeypatch.delenv("CM_CONFIG_REPO", raising=False)

    assert main(["config_manager", "serve"]) == _MISCONFIGURED


def test_serve_names_the_variable_it_needs_before_it_gives_up(monkeypatch, capsys):
    monkeypatch.delenv("CM_CONFIG_REPO", raising=False)

    main(["config_manager", "serve"])

    assert "CM_CONFIG_REPO" in capsys.readouterr().err


def test_list_goes_to_the_configs_endpoint_of_the_named_api(answers):
    # 把關的是位址：自己讀清單檔的實作根本不會用到 --api（ADR-00000009）。
    called = answers(_ROWS)

    main(["config_manager", "list", "--api", "http://amr01:8080"])

    assert called == ["http://amr01:8080/api/configs"]


def test_list_shows_each_state_in_the_words_the_page_uses(answers, capsys):
    # CLI 說「偏離」而畫面說別的，等於憑介面決定術語（CONTEXT.md）。
    answers(_ROWS)

    main(["config_manager", "list"])

    assert [line.split()[0] for line in _lines(capsys)] == ["一致", "偏離", "未部署"]


def test_list_says_nothing_is_managed_yet_instead_of_printing_nothing(answers, capsys):
    # 什麼都還沒納管是合法狀態，不是錯誤——說出來，而不是留一片空白讓人以為壞了。
    answers([])

    main(["config_manager", "list"])

    assert _lines(capsys) == ["還沒有納管任何 config。"]


def test_list_fails_loudly_when_the_backend_is_not_answering(monkeypatch):
    # 「服務沒起來」與「什麼都還沒納管」看起來都是沒有東西，該做的處置卻完全不同。
    def _refuse(url, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)

    assert main(["config_manager", "list"]) == 1


def test_list_names_the_address_it_could_not_read(monkeypatch, capsys):
    def _refuse(url, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)

    main(["config_manager", "list", "--api", "http://127.0.0.1:9"])

    assert "http://127.0.0.1:9/api/configs" in capsys.readouterr().err


# ── import / browse 是 HTTP client：在同一行程呼叫並 mock urlopen，測「打對端點、
# 呈現對」。子行程那版（system/test_api.py）證明真的走了 --api，但子行程的執行量不進
# 覆蓋率，所以這些**決定與呈現**要在這裡就地跑（同 list 的處理，#97）。────────────


def _http_error(code, detail):
    return urllib.error.HTTPError(
        "http://x/api",
        code,
        "refused",
        {},
        io.BytesIO(json.dumps({"detail": detail}).encode("utf-8")),
    )


def _http_error_raw(code, body):
    # 一個 body 由呼叫端指定原始位元組的 HTTPError——用來餵非物件的 JSON body。
    return urllib.error.HTTPError("http://x/api", code, "refused", {}, io.BytesIO(body))


def test_browse_does_not_crash_on_a_non_object_error_body(monkeypatch, capsys):
    # --api 是使用者可任意指向的位址：指到別的服務／代理層時，4xx/5xx 的 body 可能是合法但
    # 非物件的 JSON（null、陣列、字面值）。_http_detail 該回退成狀態行，不是崩成裸 traceback
    # ——json.loads([]) 後 ["detail"] 下標對 list 拋 TypeError，而它不在 except 之列（不變式 2）。
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: (_ for _ in ()).throw(_http_error_raw(502, b"[]")),
    )

    code = main(["config_manager", "browse", "--api", "http://x", "--path", "/srv"])

    assert code == 1
    assert "HTTP 502" in capsys.readouterr().err


def test_list_relays_a_server_error_instead_of_saying_the_backend_is_down(monkeypatch, capsys):
    # 後端已啟動但回 5xx（如清單檔執行期讀取失敗）時，list 要指名伺服器的錯誤，不是誤報
    # 「確認 backend 已啟動」——HTTPError 是 OSError 子類，少了專屬分支就被連線失敗文案吞掉，
    # 把「起來了但出錯」說成「沒起來」（不變式 2）。與 import／browse／inspect 對齊。
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: (_ for _ in ()).throw(_http_error(500, "清單檔讀不出來")),
    )

    code = main(["config_manager", "list", "--api", "http://x"])

    assert code == 1
    err = capsys.readouterr().err
    assert "清單檔讀不出來" in err
    assert "確認 backend 已啟動" not in err


def test_import_posts_the_source_to_the_configs_endpoint(answers, capsys):
    # 把關的是位址與方法：自己另做一套的實作不會 POST 到 --api 指的 /api/configs。身分先設
    # （#288：CLI 不是瀏覽器，作者在命令列明寫），納管接著走同一支端點。
    called = answers({"ref": "nav2@amr01-abc", "target": "/etc/nav2.yaml"})

    code = main(
        ["config_manager", "import", "--api", "http://amr01:8080",
         "--source", "/etc/nav2.yaml", "--format", "yaml", *_AUTHOR]
    )

    assert code == 0
    assert [request.full_url for request in called] == [
        "http://amr01:8080/api/session", "http://amr01:8080/api/configs",
    ]
    assert called[1].method == "POST"
    assert json.loads(called[0].data)["name"] == "陳小明"
    assert "已納管" in capsys.readouterr().out


def test_import_without_an_author_is_a_usage_error(capsys):
    # 沒有 --name／--email 就沒有作者可署名：argparse 的用法錯誤（結束碼 2），不會打到端點。
    with pytest.raises(SystemExit) as exc:
        main(["config_manager", "import", "--source", "/a.yaml", "--format", "raw"])

    assert exc.value.code == _MISCONFIGURED
    assert "--name" in capsys.readouterr().err


def test_import_relays_the_endpoint_reason_and_fails_nonzero(monkeypatch, capsys):
    # 端點以結構化訊息回絕（白名單外／重複／未設身分）：把它的原因帶出來，非零結束。
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open",
        lambda _self, request, timeout=None: (_ for _ in ()).throw(
            _http_error(422, "來源在白名單外")
        ),
    )

    code = main(
        ["config_manager", "import", "--api", "http://x",
         "--source", "/outside.yaml", "--format", "yaml", *_AUTHOR]
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "來源在白名單外" in err
    assert "下一步：" in err


def test_browse_goes_to_the_browse_endpoint_of_the_named_api(answers):
    called = answers({"path": "/srv", "entries": []})

    main(["config_manager", "browse", "--api", "http://amr01:8080", "--path", "/srv"])

    assert called[0].startswith("http://amr01:8080/api/browse?")
    assert "path=%2Fsrv" in called[0]


def test_browse_lists_the_entries_marking_directories(answers, capsys):
    answers(
        {
            "path": "/srv",
            "entries": [
                {"name": "sub", "kind": "dir", "path": "/srv/sub"},
                {"name": "a.yaml", "kind": "file", "path": "/srv/a.yaml"},
            ],
        }
    )

    main(["config_manager", "browse", "--path", "/srv"])

    out = capsys.readouterr().out
    assert "sub/" in out  # 目錄尾端加 /
    assert "a.yaml" in out


def test_inspect_posts_the_source_and_shows_the_summary(answers, capsys):
    called = answers(
        {
            "format": "yaml",
            "field_count": 2,
            "ambiguities": [{"line": 1, "value": "no", "readings": ["布林", "字串"]}],
            "types": {"a": "bool", "b": "int"},
            "permissions": {"owner": "root", "group": "root", "mode": "0644"},
        }
    )

    code = main(
        ["config_manager", "inspect", "--api", "http://amr01:8080",
         "--source", "/etc/x.yaml", "--format", "yaml"]
    )

    assert code == 0
    assert called[0].full_url == "http://amr01:8080/api/inspect"
    assert called[0].method == "POST"
    out = capsys.readouterr().out
    assert "format：yaml" in out
    assert "no" in out  # 歧義印出來了


def test_inspect_relays_a_structured_error_with_its_line(monkeypatch, capsys):
    # 偵測端點的 detail 是 {message, file, line}——CLI 要挑出訊息與行號，不是印一坨 dict。
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: (_ for _ in ()).throw(
            _http_error(422, {"message": "語法錯誤", "file": "/etc/x.yaml", "line": 3})
        ),
    )

    code = main(
        ["config_manager", "inspect", "--api", "http://x",
         "--source", "/etc/x.yaml", "--format", "yaml"]
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "語法錯誤" in err
    assert "第 3 行" in err


def _lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line]


# ── CM_SESSION_TIMEOUT（#33）────────────────────────────────────────────────────


def test_serve_plan_reads_the_session_timeout_in_seconds():
    ten_minutes = "600"
    environ = {"CM_CONFIG_REPO": "/srv/r", "CM_SESSION_TIMEOUT": ten_minutes}
    plan = serve_plan("0.0.0.0", 9000, environ)

    assert plan.session_timeout == float(ten_minutes)


def test_serve_plan_without_a_session_timeout_leaves_the_default_in_charge():
    # 未設＝用預設的續期逾時（不是不逾時）：計畫上是 None，create_app 換成預設值。
    plan = serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r"})

    assert plan.session_timeout is None


def test_serve_plan_refuses_a_non_positive_session_timeout_by_name():
    # 寫錯的值不能靜默當成別的意思。
    with pytest.raises(SessionTimeoutInvalid, match="CM_SESSION_TIMEOUT"):
        serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r", "CM_SESSION_TIMEOUT": "abc"})


# ── CM_MODE（#47）───────────────────────────────────────────────────────────────
# 開發／部署模式由後端依環境變數判定（動工前定案：`.setup.conf` 目前是佔位檔）；沒設＝部署模式
# （無法判定時落向安全）；不認得的值大聲失敗，不靜默當成任何一種。


def test_serve_plan_reads_the_mode_from_the_environment():
    environ = {"CM_CONFIG_REPO": "/srv/r", "CM_MODE": "development"}

    assert serve_plan("0.0.0.0", 9000, environ).mode == "development"


def test_serve_plan_defaults_to_deployment_mode_when_unset():
    # 忘了設定的機器比較可能是現場機器：預設較嚴（有閒置逾時、沒有記住此裝置）不會造成損害。
    assert serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r"}).mode == "deployment"


def test_serve_plan_refuses_an_unknown_mode_by_name():
    with pytest.raises(ModeInvalid, match="CM_MODE"):
        serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r", "CM_MODE": "staging"})


def test_create_app_refuses_an_unknown_mode_even_when_called_directly():
    # create_app 不只從 serve_plan 來（測試、別的接線會直接呼叫），所以它自己也擋。
    with pytest.raises(ValueError, match="mode 必須是"):
        create_app("/srv/r", mode="staging")


# ── CM_IDLE_TIMEOUT（#48）───────────────────────────────────────────────────────
# 部署模式的閒置逾時：預設 10 分鐘（§7.2.3.2），可用環境變數調（人工驗證不必真的等 10 分鐘）。
# 寫錯的值大聲失敗——靜默當成不逾時正是部署模式要防的事。


def test_serve_plan_idles_out_after_ten_minutes_by_default():
    plan = serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r"})

    assert plan.timeouts.idle == datetime.timedelta(minutes=10)


def test_serve_plan_reads_the_idle_timeout_from_the_environment():
    plan = serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r", "CM_IDLE_TIMEOUT": "120"})

    assert plan.timeouts.idle == datetime.timedelta(minutes=2)


def test_the_idle_timeout_leaves_the_renew_timeout_alone():
    # 兩種逾時各管一件事：調閒置逾時不會動到續期逾時，反之亦然。
    environ = {"CM_CONFIG_REPO": "/srv/r", "CM_IDLE_TIMEOUT": "120", "CM_SESSION_TIMEOUT": "45"}

    plan = serve_plan("0.0.0.0", 9000, environ)

    assert plan.timeouts.renew == datetime.timedelta(seconds=45)


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "nan", "inf", "1e12"])
def test_serve_plan_refuses_an_idle_timeout_that_would_mean_never(raw):
    with pytest.raises(IdleTimeoutInvalid, match="CM_IDLE_TIMEOUT"):
        serve_plan("0.0.0.0", 9000, {"CM_CONFIG_REPO": "/srv/r", "CM_IDLE_TIMEOUT": raw})

