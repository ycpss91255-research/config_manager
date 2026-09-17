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
from config_manager.core.errors import AllowedRootsError, PrefixNotFound
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
            "下一步：確認 CM_CONFIG_REPO 指向正確的掛載，或讓 entrypoint 重新種下它",
            file=path,
        )

    try:
        text = _read(path)
        return load(text)
    except OSError as error:
        raise AllowedRootsUnparsable(
            f"白名單設定檔讀不出來：{path}（{error.strerror}）。下一步：檢查該檔的權限與編碼",
            file=path,
        ) from error
    except (ParseError, ValidationError, AllowedRootsError) as error:
        raise AllowedRootsUnparsable(
            f"白名單設定檔無法解析：{path}——{error}。下一步：依訊息指出的位置修正該檔",
            file=path,
        ) from error


def root_prefixes(repo: str) -> tuple[str, ...]:
    """白名單的前綴清單（**realpath 正規化後**）——browse／onboard／source 每次請求以這個取。

    每次請求都讀檔（而非啟動時凍結一份）：從介面新增的根要**立即生效、不必重啟**（#202）。

    正規化（realpath）的理由：entrypoint 把 `CM_ALLOWED_ROOTS` 逐字種進檔案（可能帶尾斜線、
    或在 symlink 掛載下字面值 != realpath，如 macOS 的 /tmp→/private/tmp）；而 browse 回給前端的
    路徑一律是 realpath。兩邊不一致會讓前端「麵包屑夾在根為界」的判定落空、把根以上的祖先也
    當成可點（#13 的資安審查）。在這裡統一成 realpath，前端拿到的根與 browse 的路徑就對得起來。
    後端 browse 本就對根與路徑各自 realpath 再比對，故這裡正規化是冪等的、不改變放行範圍。
    """
    return tuple(os.path.realpath(root.prefix) for root in read_allowed_roots(repo).roots)


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

    _write_roots_and_commit(repo, dump(current, original), original,
                            f"白名單納入根目錄 {resolved}", added_by)


def remove_allowed_root(repo: str, prefix: str, removed_by: str) -> None:
    """把 `prefix` 從白名單設定檔移除並提交。`removed_by` 是 `姓名 <email>`（同時作為署名）。

    以**檔案原樣儲存的 prefix** 定位（core.dump 也以原樣定位）：從介面新增的根存的是
    realpath、種子的根存的是字面值，兩者都以「檔裡長什麼樣」為識別碼——不能先 realpath
    再比，否則種子字面值對不回去、刪不掉（靜默 bug）。定位不到丟 `PrefixNotFound`。

    移除以 core.dump 保留其餘原樣，原子寫回後以一筆一般 commit 記下；commit 沒成就把設定檔
    還原到移除前（白名單每次請求從檔讀，檔一改就生效，回滾避免靜默縮減而無稽核，比照新增）。
    """
    path = os.path.join(repo, ALLOWED_ROOTS_NAME)
    current = read_allowed_roots(repo)
    kept = [root for root in current.roots if root.prefix != prefix]
    if len(kept) == len(current.roots):
        raise PrefixNotFound(
            f"要移除的前綴不在白名單設定檔裡：{prefix}。"
            "下一步：以檢視清單列出的原樣前綴為準，確認拼寫與尾斜線"
        )

    original = _read(path)
    current.roots = kept

    _write_roots_and_commit(repo, dump(current, original), original,
                            f"白名單移除根目錄 {prefix}", removed_by)


def _write_roots_and_commit(
    repo: str, new_text: str, original: str, subject: str, author: str
) -> None:
    """原子寫回 new_text 並以一筆一般 commit（subject／author 署名）記下；commit 沒成就把
    設定檔還原到 original。

    白名單一被寫入就立即生效（每次請求從檔讀）；commit 沒成時回滾，白名單不被靜默增減而
    沒有 git 稽核（比照 onboard #173）。回滾本身也失敗時丟 `AllowedRootLeftBehind`，同時
    說出原本的失敗與清理的失敗、指名殘留了什麼。新增與移除共用這條寫回路徑。
    """
    path = os.path.join(repo, ALLOWED_ROOTS_NAME)
    replace_atomically(path, new_text.encode("utf-8"))
    try:
        stage(repo, ALLOWED_ROOTS_NAME)
        commit(repo, subject, author)
    except CalledProcessError as failure:
        try:
            replace_atomically(path, original.encode("utf-8"))
            unstage(repo, ALLOWED_ROOTS_NAME)
        except (OSError, CalledProcessError) as cleanup:
            raise AllowedRootLeftBehind(
                f"「{subject}」失敗後，回滾 {path} 也失敗了（{cleanup}）；"
                f"該變更可能已生效卻未提交。下一步：手動檢視並還原 {path}"
            ) from failure
        raise


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()
