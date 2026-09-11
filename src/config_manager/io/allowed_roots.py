"""io/allowed_roots — 白名單設定檔（allowed-roots.toml）的讀取與新增（§7.9, #202）。

讀檔案系統與跑 git，故在 io 層而非 core（ADR-00000011）。解析與序列化交給
`core.allowed_roots`（T23）——這一支負責把位元組拿給它、把結果寫回並提交，並在新增當下
做 realpath 正規化與可見性檢查（realpath 是 I/O，比照 io/source）。

**代價寫明**（比照 io/preflight #120）：讀取這裡認得 `load()` 的失敗詞彙（tomlkit 的
ParseError、pydantic 的 ValidationError、core 的 AllowedRootsError），不用 `except Exception`
把 bug 藏成「你的白名單設定檔壞了」。
"""

import os
from subprocess import CalledProcessError

from pydantic import ValidationError
from tomlkit.exceptions import ParseError

from config_manager.core.allowed_roots import check_prefix, dump, load
from config_manager.core.errors import AllowedRootsError
from config_manager.core.models import AllowedRoot, AllowedRoots
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import (
    AllowedRootLeftBehind,
    AllowedRootsMissing,
    AllowedRootsUnparsable,
    AllowedRootUnreachable,
)
from config_manager.io.git import commit, stage, unstage

ALLOWED_ROOTS_NAME = "allowed-roots.toml"


def read_allowed_roots(repo: str) -> AllowedRoots:
    """讀出並解析 repo 的白名單設定檔。公開，因為 api 層每次請求也要讀同一份檔案。

    只有一個地方知道白名單設定檔叫什麼、在哪裡、讀不出來時該說什麼——第二個實作遲早會
    與這個分歧，而分歧的那天不會有人發現（比照 read_config_list）。
    """
    path = os.path.join(repo, ALLOWED_ROOTS_NAME)

    if not os.path.isfile(path):
        raise AllowedRootsMissing(
            f"config-repo 裡沒有白名單設定檔：{path}。"
            "下一步：確認 CM_CONFIG_REPO 指向正確的掛載，或讓 entrypoint 重新種下它"
        )

    try:
        text = _read(path)
        return load(text)
    except OSError as error:
        raise AllowedRootsUnparsable(
            f"白名單設定檔讀不出來：{path}（{error.strerror}）。下一步：檢查該檔的權限與編碼"
        ) from error
    except (ParseError, ValidationError, AllowedRootsError) as error:
        raise AllowedRootsUnparsable(
            f"白名單設定檔無法解析：{path}——{error}。下一步：依訊息指出的位置修正該檔"
        ) from error


def root_prefixes(repo: str) -> tuple[str, ...]:
    """白名單的前綴清單——browse／onboard／source 每次請求以這個取，不必自己解析檔案。

    每次請求都讀檔（而非啟動時凍結一份）：從介面新增的根要**立即生效、不必重啟**（#202）。
    """
    return tuple(root.prefix for root in read_allowed_roots(repo).roots)


def add_allowed_root(repo: str, prefix: str, added_by: str, added_at: str) -> None:
    """把 `prefix` 加進白名單設定檔並提交。`added_by` 是 `姓名 <email>`（同時作為 commit 署名）。

    先做字面檢查（core.check_prefix：絕對、無 ..）再 realpath 正規化，存的是解析後的目錄；
    指向到不了的目錄時大聲失敗、指名（不變式 2）。追加以 core.dump 保留原樣，原子寫回後
    以一筆一般 commit 記下（不是 T7 的變更紀錄——白名單根沒有 uid）。
    """
    check_prefix(prefix)
    resolved = os.path.realpath(prefix)
    if not os.path.isdir(resolved):
        raise AllowedRootUnreachable(
            f"要加入白名單的前綴指向到不了的目錄：{prefix}"
            f"（解析為 {resolved}）。下一步：改挑一個存在且可進入的目錄"
        )

    path = os.path.join(repo, ALLOWED_ROOTS_NAME)
    current = read_allowed_roots(repo)
    original = _read(path)
    current.roots.append(
        AllowedRoot(prefix=resolved, added_by=added_by, added_at=added_at)
    )

    replace_atomically(path, dump(current, original).encode("utf-8"))
    try:
        stage(repo, ALLOWED_ROOTS_NAME)
        commit(repo, f"白名單納入根目錄 {resolved}", added_by)
    except CalledProcessError as failure:
        # 白名單一被寫入該根就立即生效（每次請求從檔讀）；commit 沒成，就把設定檔還原到
        # 新增前，白名單不被靜默擴張而沒有 git 稽核（比照 onboard #173）。
        try:
            replace_atomically(path, original.encode("utf-8"))
            unstage(repo, ALLOWED_ROOTS_NAME)
        except (OSError, CalledProcessError) as cleanup:
            raise AllowedRootLeftBehind(
                f"新增白名單根 {resolved} 失敗後，回滾 {path} 也失敗了（{cleanup}）；"
                f"該根可能已生效卻未提交。下一步：手動檢視並還原 {path}"
            ) from failure
        raise


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()
