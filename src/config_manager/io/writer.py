"""io/writer — 原子寫出 + 權限（設計 §5.2、ADR-00000006、ADR-00000003）。

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。
"""

import grp
import os
import pwd
from collections.abc import Iterable

from config_manager.core.models import Permissions
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import (
    OwnershipRefused,
    TargetOutsideRoots,
)


def _within_roots(resolved: str, allowed_roots: Iterable[str]) -> bool:
    """解析後的路徑是否落在某個允許的根目錄之內。根目錄本身也先解析。"""
    for root in allowed_roots:
        allowed = os.path.realpath(root)
        if resolved == allowed or resolved.startswith(allowed + os.sep):
            return True
    return False


def _resolve_owner(owner: str) -> int:
    """使用者名稱或數字 uid 都收。容器裡的使用者常常沒有 passwd 項目（實測
    uid 501 就查不到名字），所以數字形式不是取巧，是部署環境的需求。"""
    if owner.isdigit():
        return int(owner)
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError as error:
        raise OwnershipRefused(
            f"找不到使用者：{owner}。下一步：確認這是本機存在的使用者，"
            f"或改用數字 uid。"
        ) from error


def _resolve_group(group: str) -> int:
    """群組名稱或數字 gid 都收，理由同 _resolve_owner。"""
    if group.isdigit():
        return int(group)
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError as error:
        raise OwnershipRefused(
            f"找不到群組：{group}。下一步：確認這是本機存在的群組，"
            f"或改用數字 gid。"
        ) from error


def write(
    target: str,
    content: str,
    permissions: Permissions,
    allowed_roots: Iterable[str],
) -> None:
    """把內容寫到目標位置。要嘛完整寫入，要嘛完全不動。

    原子替換（暫存檔 → fsync → rename）由 `io/atomic.replace_atomically` 負責——那份
    實作 `io/repo` 也用（ADR-00000006 的原子寫出只該有一份）。這裡在它之上加的是**部署
    目標**特有的兩件事：白名單逃逸檢查，以及套用 owner／group／mode。

    失敗時暫存檔會被清掉；**清不掉時丟 TemporaryLeftBehind**（見 `io/atomic`）。
    """
    # 逃逸檢查在任何寫入動作之前。符號連結必須先解析：ADR-00000003 指出
    # 「寫暫存檔再改名」會把連結替換成一般檔案而靜默失效，等發現時連結已經沒了。
    resolved = os.path.realpath(target)
    if not _within_roots(resolved, allowed_roots):
        raise TargetOutsideRoots(
            f"目標解析後落在允許範圍之外：{target} → {resolved}。"
            f"下一步：確認該路徑或其父目錄不是指向範圍外的符號連結，"
            f"或把該位置納入允許的根目錄。"
        )

    # 先解析出 id：名字查不到就該在建立暫存檔之前失敗。
    owner_id = _resolve_owner(permissions.owner)
    group_id = _resolve_group(permissions.group)

    def _apply_ownership(temporary: str) -> None:
        # 設不上去就整個失敗。靜默跳過 chown 會讓目標以錯誤的擁有者上線，而且沒有人
        # 會知道（不變式 2）。chmod 在 rename 前套在暫存檔上，目標於是一出現就是對的權限。
        try:
            os.chown(temporary, owner_id, group_id)
        except PermissionError as error:
            raise OwnershipRefused(
                f"沒有權限把 {target} 設為 {permissions.owner}:{permissions.group}"
                f"（{error.strerror}）。下一步：這份 config 若真的需要別的擁有者，"
                f"標記 requires_privilege 走提權路徑；否則把 owner/group 改成"
                f"服務的執行身分。"
            ) from error
        os.chmod(temporary, int(permissions.mode, 8))

    replace_atomically(target, content.encode("utf-8"), finalize=_apply_ownership)
