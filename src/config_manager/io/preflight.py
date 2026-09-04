"""io/preflight — 啟動前置檢查（設計文件 §3.3.3；測試介面 T15）。

契約在做實事之前先自我驗證（不變式 2）。掛載壞掉、清單檔不可解析、來源內容
不存在時立即以非零碼退出，而不是等容器起來後才在某個請求裡爆開——錯誤要發生
在離現場最近的地方。

清單檔的位置由設計固定為 <config-repo>/config-list.toml（§3.4），不由環境變數
指定：多一個可調的旋鈕只是多一個能掛錯的地方。

讀檔案系統，故在 io 層而非 core（ADR-00000011：核心層不碰檔案系統與 git）。
解析與完整性判定仍然交給 core.config_list.load——這支只負責把位元組拿給它。
"""

import os
import sys

from config_manager.core.config_list import load
from config_manager.core.models import ConfigList
from config_manager.io.errors import (
    ConfigListMissing,
    ConfigListUnparsable,
    PreflightError,
    SourceMissing,
)

CONFIG_LIST_NAME = "config-list.toml"

# argv 是 [模組名, config-repo 路徑]。
_ARGV_LEN = 2


def preflight(repo: str) -> None:
    """檢查 config-repo 可用。通過則正常返回，失敗則丟具名例外。"""
    config_list = read_config_list(repo)
    _check_sources_exist(repo, config_list)


def read_config_list(repo: str) -> ConfigList:
    """讀出並解析 repo 的清單檔。公開，因為 api 層也要讀同一份檔案。

    只有一個地方知道清單檔叫什麼、在哪裡、讀不出來時該說什麼——第二個實作
    遲早會與這個分歧，而分歧的那天不會有人發現。
    """
    list_path = os.path.join(repo, CONFIG_LIST_NAME)

    if not os.path.isfile(list_path):
        raise ConfigListMissing(
            f"config-repo 裡沒有清單檔：{list_path}。"
            f"下一步：確認 CM_CONFIG_REPO 指向正確的掛載，或從備份還原該檔"
        )

    try:
        text = _read(list_path)
        return load(text)
    except OSError as error:
        raise ConfigListUnparsable(
            f"清單檔讀不出來：{list_path}（{error.strerror}）。下一步：檢查該檔的權限與編碼"
        ) from error
    except Exception as error:
        raise ConfigListUnparsable(
            f"清單檔無法解析：{list_path}——{error}。下一步：依訊息指出的位置修正該檔"
        ) from error


def _check_sources_exist(repo: str, config_list: ConfigList) -> None:
    """來源側的內容都要在 repo 裡。

    只查來源，不查目標：目標尚未部署是「未部署」狀態，是 T2 的四種合法狀態之一，
    因為它拒絕啟動等於讓系統永遠完不成第一次 apply。
    """
    for entry in config_list.files:
        if not os.path.isfile(os.path.join(repo, entry.source)):
            raise SourceMissing(
                f"清單檔引用的來源內容不存在：{entry.ref} 的來源「{entry.source}」"
                f"不在 {repo} 裡。下一步：還原該檔，或從清單檔移除這筆條目"
            )


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def main(argv: list[str]) -> int:
    """entrypoint 的呼叫點：失敗時把原因印到 stderr 並回非零碼。"""
    if len(argv) != _ARGV_LEN:
        print("usage: python -m config_manager.io.preflight <config-repo>", file=sys.stderr)
        return 2

    # 只接前置檢查自己的例外。其餘任何例外都是這支程式的 bug，讓它帶著
    # traceback 炸開——那比一行摘要更大聲，而 entrypoint 一樣會以非零碼停住。
    try:
        preflight(argv[1])
    except PreflightError as error:
        print(f"preflight: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
