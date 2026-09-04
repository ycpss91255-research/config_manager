"""T10 — CLI。測試介面：api/cli 的 main 與 serve_plan。

這一批與 `test/pytest/system/test_api.py` 的 T10 規格**測不同的東西**，不是同一批的
複製：系統層那幾則以子行程執行 CLI，證明它真的走了 `--api` 指向的那組 HTTP 端點
（ADR-00000009）。這一批在同一個行程裡呼叫，測的是**決定**——起服務前要跑哪個
config-repo、放行哪些來源、讀不到端點時的結束碼、清單怎麼呈現成中文狀態。

`serve` 的兩件事在 #97 被拆開：決定要跑什麼（serve_plan，純函式，environ 當參數
傳進來）與真的跑起來（create_app 加 uvicorn.run，兩行）。拆開之前那些決定與啟動擠在
同一個函式裡，而測試不會去跑 uvicorn.run，於是它們一起量不到。
"""

import json
import urllib.error

import pytest

from config_manager.api.cli import main, serve_plan
from config_manager.api.errors import ConfigRepoMissing
from config_manager.api.routes import DEFAULT_ORIGINS

# 2 是「用法錯誤／接線不對」，1 是「跑了但沒成功」。serve 少了 CM_CONFIG_REPO
# 屬於前者：不是服務起不來，是根本沒有東西可服務。
_MISCONFIGURED = 2

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


def _lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line]
