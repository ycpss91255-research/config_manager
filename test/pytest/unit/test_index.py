"""T14 — 參數索引與搜尋。測試介面：core/index 的 index / search。

用語依 CONTEXT.md。純邏輯，不做 I/O（ADR-00000011）：index 收已解析的資料。
"""

from core.index import SCOPE_ALL, SCOPE_NAME, SCOPE_VALUE, index, reindex, search, unindex


def test_index_flattens_nested_dict_to_dotted_paths():
    # 巢狀結構展平成正確的參數路徑；只索引葉值。
    data = {"nav": {"max_vel": 0.8, "min_vel": 0.1}, "name": "amr"}
    result = index("mfz3k9q1", data)
    assert set(result) == {
        ("mfz3k9q1", "nav.max_vel", 0.8),
        ("mfz3k9q1", "nav.min_vel", 0.1),
        ("mfz3k9q1", "name", "amr"),
    }


def test_index_includes_array_indices_in_path():
    # 陣列元素以索引納入路徑；陣列內的物件繼續展平。
    data = {"speeds": [0.5, 1.0], "servers": [{"host": "a"}]}
    result = index("u1", data)
    assert set(result) == {
        ("u1", "speeds[0]", 0.5),
        ("u1", "speeds[1]", 1.0),
        ("u1", "servers[0].host", "a"),
    }


def test_search_by_name_returns_hits_with_uid_and_path():
    # 依參數名稱搜尋：命中含 uid 與路徑。
    idx = index("u1", {"nav": {"max_vel": 0.8, "min_vel": 0.1}})
    assert search(idx, "max_vel", SCOPE_NAME) == [("u1", "nav.max_vel", 0.8)]


def test_search_by_value_returns_matching_parameters():
    # 依參數值搜尋：比對的是值，不是名稱。
    idx = index("u1", {"nav": {"max_vel": 0.8}, "mode": "fast"})
    assert search(idx, "fast", SCOPE_VALUE) == [("u1", "mode", "fast")]


def test_search_all_scope_is_union_of_name_and_value():
    # 一個參數路徑含關鍵字、另一個參數的值含關鍵字；「全部」兩者都命中（聯集）。
    idx = index("u1", {"speed_limit": 30, "mode": "speed"})
    hits = search(idx, "speed", SCOPE_ALL)
    assert set(hits) == {("u1", "speed_limit", 30), ("u1", "mode", "speed")}


def test_specific_scope_excludes_other_dimension_matches():
    # 名稱含 "speed"、值不含；以「參數值」範圍搜尋不命中（釘住範圍排除，回歸測試）。
    idx = index("u1", {"speed_limit": 30})
    assert search(idx, "speed", SCOPE_VALUE) == []


def test_reindex_replaces_a_uids_entries_old_value_gone_new_present():
    # 修改一個參數後重新索引：舊值不再命中、新值命中（更新最容易漏）。
    idx = index("u1", {"max_vel": 0.8})
    idx = reindex(idx, "u1", {"max_vel": 1.2})
    assert search(idx, "0.8", SCOPE_VALUE) == []
    assert search(idx, "1.2", SCOPE_VALUE) == [("u1", "max_vel", 1.2)]


def test_unindex_removes_all_entries_of_a_uid():
    # 解除納管後，該 uid 的所有項目從索引移除，其他 uid 不受影響（移除最容易漏）。
    idx = index("u1", {"a": 1, "b": 2}) + index("u2", {"c": 3})
    idx = unindex(idx, "u1")
    assert set(idx) == {("u2", "c", 3)}
