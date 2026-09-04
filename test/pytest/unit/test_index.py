"""T14 — 參數索引與搜尋。測試介面：core/index 的 index / search。

用語依 CONTEXT.md。純邏輯，不做 I/O（ADR-00000011）：index 收已解析的資料。
"""

from core.index import index


def test_index_flattens_nested_dict_to_dotted_paths():
    # 巢狀結構展平成正確的參數路徑；只索引葉值。
    data = {"nav": {"max_vel": 0.8, "min_vel": 0.1}, "name": "amr"}
    result = index("mfz3k9q1", data)
    assert set(result) == {
        ("mfz3k9q1", "nav.max_vel", 0.8),
        ("mfz3k9q1", "nav.min_vel", 0.1),
        ("mfz3k9q1", "name", "amr"),
    }
