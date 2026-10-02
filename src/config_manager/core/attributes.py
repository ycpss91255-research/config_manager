"""core/attributes — T16 config 屬性（#286，設計 §7.4.4）。

一份 config 有四項可以改的屬性：名稱、群組、主機、說明。它們是給人看的與給樹分組用的，
**不是身分**——身分是 uid（永不變，ADR-00000012），所以改名不影響任何關聯：歷史、草稿、搜尋
都以 uid 找。來源複本在 repo 裡的位置與目標路徑也不跟著屬性變。

名稱與主機會被寫進參照形式（`<name>@<hostname>-<uid>`）與變更紀錄的主旨，所以擋掉會把它們
弄壞的字：名稱不能含 `@` 與控制字元，主機只收字母數字與 `. _ -`（與納管時讀入主機名的規則
同一條，`is_safe_hostname`）。群組不能有空的或重複的。說明限單行。

T16 的 `group_tree` 不在這裡：樹由介面依每筆條目的 `groups` 當場建出來，沒有快取，那些行為
在 T11 驗。核心層不做 I/O：清單以模型傳進來、回新的模型（不改傳入的那份）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config_manager.core.errors import AttributeInvalid, AttributesUnchanged, EntryNotFound
from config_manager.core.models import ConfigList, FileEntry

_MAX_NAME = 80
_MAX_GROUP = 40
_MAX_DESCRIPTION = 200
# 安全的 hostname：字母數字加 . _ -（涵蓋 FQDN 與容器 ID，#178）。
_SAFE_HOSTNAME = re.compile(r"[A-Za-z0-9._-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Attributes:
    """一份 config 可以改的四項屬性。介面每次送完整的四項（不是只送有改的）。"""

    name: str
    hostname: str
    groups: tuple[str, ...]
    description: str | None


def is_safe_hostname(hostname: str) -> bool:
    """`hostname` 能不能安全地寫進 `files/<hostname>/` 的路徑、參照形式與變更紀錄的主旨。"""
    return hostname not in (".", "..") and _SAFE_HOSTNAME.fullmatch(hostname) is not None


def update_attributes(config_list: ConfigList, uid: str, wanted: Attributes) -> ConfigList:
    """把 `uid` 那筆條目的屬性換成 `wanted`，回新的清單。其餘條目原封不動。

    值不合法丟 `AttributeInvalid`（指名是哪一項）；與現在一模一樣丟 `AttributesUnchanged`；
    uid 不在清單裡丟 `EntryNotFound`。
    """
    entry = next((item for item in config_list.files if item.uid == uid), None)
    if entry is None:
        raise EntryNotFound(
            f"清單檔裡沒有 uid「{uid}」的條目，沒有屬性可以修改。"
            "下一步：重新整理清單，確認該 config 仍在納管中"
        )
    updated = entry.model_copy(
        update={
            "name": _name(wanted.name),
            "hostname": _hostname(wanted.hostname),
            "groups": _groups(wanted.groups),
            "description": _description(wanted.description),
        }
    )
    if updated == entry:
        raise AttributesUnchanged(
            f"「{entry.name}@{entry.hostname}」的屬性與送來的一模一樣，沒有東西要改。"
            "下一步：改動至少一項再儲存，或直接關閉屬性面板"
        )
    files = [updated if item.uid == uid else item for item in config_list.files]
    return config_list.model_copy(update={"files": files})


def describe_change(before: FileEntry, after: FileEntry) -> str:
    """屬性改了哪幾項、改成什麼——變更紀錄（`meta`）的說明。沒動的不提。"""
    parts: list[str] = []
    if after.name != before.name:
        parts.append(f"名稱改為 {after.name}")
    if after.hostname != before.hostname:
        parts.append(f"主機改為 {after.hostname}")
    if after.groups != before.groups:
        parts.append(f"群組改為 {'、'.join(after.groups)}" if after.groups else "移出所有群組")
    if after.description != before.description:
        parts.append("清除說明" if after.description is None else "更新說明")
    return "；".join(parts)


def _name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > _MAX_NAME or "@" in name or _CONTROL.search(name):
        raise AttributeInvalid(
            f"名稱「{value}」不能用：不可空白、不可含 @ 或換行等控制字元、最長 {_MAX_NAME} 個字。"
            "下一步：改成一個看得懂的短名稱，例如 navigation-params",
            "name",
        )
    return name


def _hostname(value: str) -> str:
    hostname = value.strip()
    if not is_safe_hostname(hostname):
        raise AttributeInvalid(
            f"主機「{value}」不能用：只允許字母、數字與 . _ -，而且不可空白。"
            "下一步：改成這台機器或場域的代號，例如 amr01",
            "hostname",
        )
    return hostname


def _groups(values: tuple[str, ...]) -> list[str]:
    groups = [value.strip() for value in values]
    for group in groups:
        if not group or len(group) > _MAX_GROUP or _CONTROL.search(group):
            raise AttributeInvalid(
                f"群組「{group}」不能用：不可空白、不可含控制字元、最長 {_MAX_GROUP} 個字。"
                "下一步：拿掉這個群組，或改成一個有內容的名稱",
                "groups",
            )
    repeated = sorted({group for group in groups if groups.count(group) > 1})
    if repeated:
        raise AttributeInvalid(
            f"群組重複了：{'、'.join(repeated)}。下一步：每個群組只留一個",
            "groups",
        )
    return groups


def _description(value: str | None) -> str | None:
    description = (value or "").strip()
    if len(description) > _MAX_DESCRIPTION or _CONTROL.search(description):
        raise AttributeInvalid(
            f"說明不能用：限單行、不可含換行等控制字元、最長 {_MAX_DESCRIPTION} 個字。"
            "下一步：把說明縮成一行的用途描述",
            "description",
        )
    return description or None
