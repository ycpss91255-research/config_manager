"""系統層的共用夾具：一個真的在跑、以真實 HTTP 回話的服務。

同一份規格在兩個地方執行（#116、#97）：

1. `./script/test.sh` —— 在工具映像裡就地起服務。**由 pytest 執行**，所以
   `api/routes.py` 與 `api/cli.py` 的執行終於進得了覆蓋率工具的視野。
2. `docker build --target runtime-test` —— 對**建好的映像**跑，`CM_SYSTEM_BASE_URL`
   指向映像內已經起好的服務。那才是 PDF §3.6.1 軸 2 對 System 的定義：
   「整個建好的映像，端到端」。

**第 1 點還沒有變成一個數字。** 覆蓋率的 `source` 目前只有 `core/`（`pyproject.toml`），
所以 `api/` 仍然不出現在報告裡。搬過來解掉的是「規格不由 pytest 執行」這個結構障礙，
不是量測範圍本身——後者是另一件工作，記在 `doc/TEST-PLAN.md`「已知的量測缺口」。
把兩者說成同一件事，就是這個 repo 一直在抓的那個形狀。

兩者跑的是同一份規格，所以不會分歧；各自補上對方拿不到的東西——一個是（未來的）
數字，一個是保真度。只在其中一種環境會壞的缺陷，正是跑兩次才抓得到而跑一次抓不到的。
"""

import os
import pathlib
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

_ENTRY = """
[[files]]
uid      = "{uid}"
name     = "{name}"
hostname = "amr01"
source   = "files/{name}.yaml"
target   = "{target}"
format   = "yaml"
groups   = []
"""

# 三種狀態各一個樣本。目標與來源的關係決定狀態（CONTEXT.md）：相同→一致、
# 不同→偏離、不存在→未部署。
#
# uid 綁在名字上，不綁在呼叫時的位置：`a@amr01-mfz3k9q1` 這個參照因此不隨
# 「這則規格要了哪幾筆」而變，一則只要 b 的規格也不會把 b 的 uid 變成 1。
_SAMPLES = {
    "a": ("mfz3k9q1", "a: 1\n"),
    "b": ("mfz3k9q2", "b: 2\n"),
    "c": ("mfz3k9q3", None),
}

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


@pytest.fixture
def listing(repo):
    """把共用 config-repo 的清單檔換成指名的那幾筆，並備妥各自的來源與目標。

    **函式範圍，而且是整份覆寫。** `repo` 與 `api` 留在 session 範圍——起一個真的
    uvicorn 不便宜，而對著建好的映像跑時（`CM_SYSTEM_BASE_URL`）那個服務根本不是
    這裡起的，重啟不了。所以貴的東西留在 session 範圍，**真正造成順序相依的東西
    ——磁碟上的清單檔——改成每則規格自己安排**。`/api/configs` 每次請求重新掃描
    （`api/routes.list_configs`），所以換掉檔案就夠了，服務不必重起。

    覆寫而不是附加，一次解掉兩個方向的順序相依：不附加，後面的規格就看不見前面
    留下的條目；覆寫而不是只附加，前面的規格也不必先跑過。**一條在某些執行方式下
    必然為真的斷言不是斷言**（#153），而順序相依讓斷言在檔內順序被改動、`-k` 過濾、
    並行執行這三種情況下無聲地變成那種東西。

    `listing()` 不帶參數就是一份什麼都沒納管的清單——那是「還沒納管任何 config」
    這個合法狀態本身，不是「還沒有人動過這份 repo」。
    """

    def _write(*names: str) -> None:
        root = pathlib.Path(repo)
        (root / "files").mkdir(exist_ok=True)
        (root / "deployed").mkdir(exist_ok=True)

        entries = ""
        for name in names:
            uid, deployed = _SAMPLES[name]
            (root / "files" / f"{name}.yaml").write_text(f"{name}: 1\n", encoding="utf-8")
            target = root / "deployed" / f"{name}.yaml"
            # 未部署要的是目標「不存在」。前一則規格可能剛把它寫出來，所以這裡
            # 明講要刪掉——狀態由目標與來源的關係決定，不由執行順序決定。
            if deployed is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(deployed, encoding="utf-8")
            entries += _ENTRY.format(uid=uid, name=name, target=target)

        (root / "config-list.toml").write_text(_MINIMAL_LIST + entries, encoding="utf-8")

    return _write


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
