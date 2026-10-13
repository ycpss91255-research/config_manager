"""io/backup — config-repo 的備份與還原（#52；設計 §8.2 發布檢查點）。

備份是 `git bundle`：一個檔案，裡面是**已提交**的全部——清單、來源複本、白名單、完整歷史。
草稿、身分、編輯階段不在 repo 裡，本來就不在範圍；改了沒提交、新加沒追蹤的檔案也不在
（bundle 只打包 commit）。服務不必停：bundle 讀的是某一刻的 refs，拿到的永遠是一個完整的版本。

還原是從 bundle `git clone` 進一個**空**目錄。三條規矩（維護者 2026-10-12 定案）：

1. **先驗證備份有效，再碰目的地。** 「有效」的定義是真的 clone 得出來、fsck 乾淨、而且裡面有
   清單檔——`git bundle verify` 對截斷的 bundle 會說 okay，它只看表頭，不算證據。
2. **只還原到空的目的地。** 有東西就拒絕、不清空。
3. **中途失敗就把目的地清回原狀。** 不留下半個 repo 被當成還原成功。

同一支驗證也用在備份完成的那一刻：做出來的 bundle 立刻 clone 進暫存目錄一次，clone 不出來的
不算備份。
"""

import datetime
import pathlib
import shutil
import subprocess
import tempfile
from typing import NamedTuple

from config_manager.io.errors import BackupUnverifiable, RestoreFailed, RestoreTargetOccupied

# 還原出來的目錄裡一定要有它，否則那不是 config-repo 的備份（T1 的清單檔）。
_LIST_NAME = "config-list.toml"
_PREFIX = "config-repo-"


# 讀來源 repo 的 upload-pack：自己帶 safe.directory（理由見 `_create`）。引號是給 git 起它時用的
# 那個 shell 看的，免得 `*` 被當成檔名樣式。
_UPLOAD_PACK = "git -c 'safe.directory=*' upload-pack"


class Backup(NamedTuple):
    """一份備份：檔案在哪、裡面的 HEAD 是哪一版。"""

    path: str
    head: str


def _git(*args: str, cwd: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, encoding="utf-8",
        errors="surrogateescape", cwd=cwd,
    )
    return completed.stdout


def _stderr(error: subprocess.CalledProcessError) -> str:
    text = error.stderr if isinstance(error.stderr, str) else ""
    return text.strip() or f"git 結束碼 {error.returncode}，沒有輸出"


def bundle(repo: str, destination_dir: str) -> Backup:
    """把 `repo` 已提交的全部打包成一個 bundle，放進 `destination_dir`，回傳檔案路徑與 HEAD。

    檔名 `config-repo-<UTC 時刻>-<HEAD 前七碼>.bundle`：一個目錄裡放很多份時，名字就說得出
    它是哪一刻、哪一版。先寫成暫存名，驗證過才改成正式名——半成品永遠不會頂著正式的名字。
    """
    folder = pathlib.Path(destination_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    partial = folder / f".{_PREFIX}{stamp}.partial"
    _create(repo, partial)
    try:
        head = _verified_head(str(partial))
    except BackupUnverifiable:
        partial.unlink(missing_ok=True)
        raise
    final = folder / f"{_PREFIX}{stamp}-{head[:7]}.bundle"
    partial.replace(final)
    return Backup(path=str(final), head=head)


def _create(repo: str, partial: pathlib.Path) -> None:
    """把 `repo` 打包成 `partial`。先拍一份快照，再從快照打包。

    直接對活著的 repo 跑 `git bundle create` 會撞上進版：它先讀 refs、再走歷史，ref 在中間動了
    那個 ref 就被丟掉，一個都不剩時說「Refusing to create empty bundle」。實測另一個程序連續
    commit 時 40 次裡壞 23 次。改成先 `git clone --no-local --bare`（走 fetch：refs 讀一次、物件照
    那一刻的 refs 打包，一致）到暫存目錄，再從那份沒有人在寫的快照打包——同一個實測 40 次裡 0 次壞。
    `--no-local` 不能省：本機 clone 預設直接複製 objects 目錄再抄 refs，中間落地的 commit 會讓 ref
    指到一個沒複製到的物件。

    讀來源的那個 upload-pack 子程序要**自己**帶 `safe.directory`。備份常常不是由設定庫的擁有者跑的
    （主機上的 script/backup.sh 以呼叫者的身分跑，設定庫屬於服務的使用者），git 對別人擁有的 repo
    預設拒絕；而它刻意**不把**呼叫端的 `-c` 與 `GIT_CONFIG_COUNT` 傳給那個子程序，所以在外面設什麼都
    沒有用——演練時備份就是這樣失敗的。`--upload-pack` 換掉的只有這一次、讀這一份 repo 的那個指令，
    放行的範圍因此就是「這次備份要讀的那一份」，不是整台機器。
    """
    with tempfile.TemporaryDirectory(prefix="config_manager-snapshot-") as scratch:
        snapshot = str(pathlib.Path(scratch, "snapshot.git"))
        try:
            _git(
                "clone", "-q", "--no-local", "--bare", "--upload-pack", _UPLOAD_PACK, repo, snapshot
            )
            _git("-C", snapshot, "bundle", "create", str(partial), "--all")
        except subprocess.CalledProcessError as error:
            partial.unlink(missing_ok=True)
            raise BackupUnverifiable(
                f"備份 {repo} 失敗：{_stderr(error)}。下一步：確認它是一份 git repo、至少有一筆 "
                "commit，且目的地目錄可寫"
            ) from error


def _verified_head(bundle_path: str) -> str:
    """驗證 `bundle_path` 真的還原得出來，回傳它的 HEAD。

    驗證＝clone 進暫存目錄、fsck、確認有清單檔。任何一步不過就 BackupUnverifiable，訊息帶 git
    的原話。
    """
    path = pathlib.Path(bundle_path)
    if not path.is_file():
        raise BackupUnverifiable(
            f"備份檔 {bundle_path} 不存在或不是一般檔案。下一步：確認路徑，備份檔是 .bundle"
        )
    with tempfile.TemporaryDirectory(prefix="config_manager-verify-") as scratch:
        probe = str(pathlib.Path(scratch, "probe"))
        try:
            _git("clone", "-q", bundle_path, probe)
            _git("-C", probe, "fsck", "--connectivity-only", "--no-progress")
            head = _git("-C", probe, "rev-parse", "HEAD").strip()
        except subprocess.CalledProcessError as error:
            raise BackupUnverifiable(
                f"備份檔 {bundle_path} 還原不出來：{_stderr(error)}。下一步：換一份完整的備份；"
                "這一份不要再用"
            ) from error
        if not pathlib.Path(probe, _LIST_NAME).is_file():
            raise BackupUnverifiable(
                f"備份檔 {bundle_path} 裡沒有 {_LIST_NAME}，不是 config-repo 的備份。"
                "下一步：確認拿的是 config_manager 做出來的備份檔"
            )
    return head


def restore(bundle_path: str, destination: str) -> Backup:
    """把備份還原到 `destination`（必須不存在或是空目錄）。回傳備份檔路徑與還原出來的 HEAD。

    順序就是三條規矩：先驗證（`_verified_head`，不碰目的地）→ 目的地要空 → clone；clone 或
    之後的核對失敗，把目的地清回原狀再把失敗往上丟。
    """
    head = _verified_head(bundle_path)
    target = pathlib.Path(destination)
    existed = target.exists()
    if existed and (not target.is_dir() or any(target.iterdir())):
        raise RestoreTargetOccupied(
            f"還原的目的地 {destination} 不是空目錄，不覆蓋。下一步：確認服務已停止；"
            "要換掉既有的設定庫，先自己把它移走（例如換一個 volume），再還原到空的位置"
        )
    try:
        _git("clone", "-q", bundle_path, str(target))
        _git("-C", str(target), "remote", "remove", "origin")
        restored = _git("-C", str(target), "rev-parse", "HEAD").strip()
        if restored != head:
            raise RestoreFailed(
                f"還原到 {destination} 的 HEAD 是 {restored}，備份裡的是 {head}。"
                "下一步：換一份備份重試；目的地已清空"
            )
    except (subprocess.CalledProcessError, RestoreFailed, OSError) as error:
        _clear(target, existed)
        if isinstance(error, RestoreFailed):
            raise
        reason = _stderr(error) if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise RestoreFailed(
            f"還原到 {destination} 中途失敗：{reason}。目的地已清回還原前的樣子。"
            "下一步：確認目的地可寫、磁碟有空間，再重試"
        ) from error
    return Backup(path=bundle_path, head=head)


def _clear(target: pathlib.Path, existed_before: bool) -> None:
    """把目的地清回還原前的樣子：原本不存在就整個移走，原本是空目錄就清空它（它可能是掛載點）。"""
    if not target.exists():
        return
    if not existed_before:
        shutil.rmtree(target, ignore_errors=True)
        return
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
