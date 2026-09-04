"""系統層的共用夾具：一個真的在跑、以真實 HTTP 回話的服務。

同一份規格在兩個地方執行（#116、#97）：

1. `./script/test.sh` —— 在工具映像裡就地起服務。**覆蓋率算得進報告**，
   `api/` 因此量得到；先前 T9／T10 的規格在 bats，那兩個模組是 0%。
2. `docker build --target runtime-test` —— 對**建好的映像**跑，`CM_SYSTEM_BASE_URL`
   指向映像內已經起好的服務。那才是 PDF §3.6.1 軸 2 對 System 的定義：
   「整個建好的映像，端到端」。

兩者跑的是同一份規格，所以不會分歧；各自補上對方拿不到的東西——一個是數字，
一個是保真度。只在其中一種環境會壞的缺陷，正是跑兩次才抓得到而跑一次抓不到的。
"""

import os
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from config_manager.api.routes import create_app

_MINIMAL_LIST = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""

_STARTUP_TIMEOUT = 10.0
_POLL_INTERVAL = 0.05


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
            return  # 有回應就算起來了，狀態碼不重要
        except OSError:
            time.sleep(_POLL_INTERVAL)
        else:
            return
    raise RuntimeError(f"服務在 {_STARTUP_TIMEOUT} 秒內沒有回應：{base}")


@pytest.fixture(scope="session")
def repo(tmp_path_factory):
    """config-repo。對著建好的映像跑時，用映像裡那一份。"""
    external = os.environ.get("CM_SYSTEM_CONFIG_REPO")
    if external:
        return external

    path = tmp_path_factory.mktemp("config-repo")
    (path / "config-list.toml").write_text(_MINIMAL_LIST, encoding="utf-8")
    return str(path)


@pytest.fixture(scope="session")
def api(repo):
    """服務的位址。外部已經有一個就用它，否則就地起一個。"""
    external = os.environ.get("CM_SYSTEM_BASE_URL")
    if external:
        _wait_until_answering(external)
        yield external
        return

    port = _free_port()
    config = uvicorn.Config(create_app(repo), host="127.0.0.1", port=port, log_level="error")
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
