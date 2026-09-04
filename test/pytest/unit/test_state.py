"""T2 — 狀態判定。測試介面：core/state 的 decide。

用語依 CONTEXT.md。純邏輯、不讀檔（ADR-00000011）：目標是否存在與兩邊的雜湊，
都由呼叫者算好傳入。「未納管」不由此判定——它由清單成員在呼叫前決定。
"""

from config_manager.core.state import State, decide


def test_missing_when_target_does_not_exist():
    # 目標不存在 → 未部署。
    assert decide(target_exists=False, target_hash=None, source_hash="abc") is State.MISSING


def test_in_sync_when_hashes_match():
    # 目標內容 == 來源內容（雜湊相同）→ 一致。
    assert decide(target_exists=True, target_hash="abc", source_hash="abc") is State.IN_SYNC


def test_drift_when_hashes_differ():
    # 目標內容 != 來源內容（雜湊不同）→ 偏離。
    assert decide(target_exists=True, target_hash="abc", source_hash="xyz") is State.DRIFT


def test_existence_is_decided_before_content():
    # 判定順序：先存在性、後內容。目標不存在時即使雜湊看似相同也回未部署。
    assert decide(target_exists=False, target_hash="abc", source_hash="abc") is State.MISSING
