"""Direct-mode tests for the Concord contract."""

import json

CONTRACT = "contracts/concord.py"

SOURCES_2 = ["https://a.example.com/price", "https://b.example.com/price"]
SOURCES_3 = SOURCES_2 + ["https://c.example.com/price"]
SOURCES_5 = SOURCES_3 + ["https://d.example.com/price", "https://e.example.com/price"]


def _mock_json(vm, url: str, body: dict, status: int = 200):
    vm.mock_web(
        url.replace(".", r"\."),
        {"method": "GET", "status": status, "body": json.dumps(body)},
    )


def _clear_and_mock_values(vm, sources, path_key, values):
    """values: list aligned with sources, may contain None to mean 'no usable value'."""
    vm.clear_mocks()
    for src, val in zip(sources, values):
        if val is None:
            _mock_json(vm, src, {"error": "not found"}, status=404)
        else:
            _mock_json(vm, src, {path_key: val})


# ---------------------------------------------------------------------------
# create_query
# ---------------------------------------------------------------------------


def test_create_query_stores_fields(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    contract.create_query("q1", SOURCES_3, "price", 100, 2)

    q = contract.get_query("q1")
    assert q["json_path"] == "price"
    assert q["tolerance_bps"] == 100
    assert q["threshold_count"] == 2
    assert q["source_count"] == 3
    assert q["resolved"] is False
    assert q["agreed_value"] == ""
    assert q["agreeing_count"] == 0
    assert contract.get_sources("q1") == SOURCES_3


def test_create_query_duplicate_id_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "price", 0, 2)

    with direct_vm.expect_revert("already exists"):
        contract.create_query("q1", SOURCES_2, "price", 0, 2)


def test_create_query_too_few_sources_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("between 2 and 8"):
        contract.create_query("q1", ["https://a.example.com"], "price", 0, 2)


def test_create_query_too_many_sources_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    nine = [f"https://s{i}.example.com" for i in range(9)]

    with direct_vm.expect_revert("between 2 and 8"):
        contract.create_query("q1", nine, "price", 0, 2)


def test_create_query_non_https_source_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("https://"):
        contract.create_query("q1", ["http://a.example.com", "https://b.example.com"], "price", 0, 2)


def test_create_query_empty_json_path_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("json_path cannot be empty"):
        contract.create_query("q1", SOURCES_2, "", 0, 2)


def test_create_query_tolerance_out_of_range_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("tolerance_bps must be"):
        contract.create_query("q1", SOURCES_2, "price", 10001, 2)

    with direct_vm.expect_revert("tolerance_bps must be"):
        contract.create_query("q2", SOURCES_2, "price", -1, 2)


def test_create_query_threshold_out_of_range_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("threshold_count must be"):
        contract.create_query("q1", SOURCES_2, "price", 0, 1)

    with direct_vm.expect_revert("threshold_count must be"):
        contract.create_query("q2", SOURCES_2, "price", 0, 3)


# ---------------------------------------------------------------------------
# resolve - numeric tolerance mode
# ---------------------------------------------------------------------------


def test_resolve_full_numeric_agreement(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "price", 100, 2)  # 1% tolerance

    _clear_and_mock_values(direct_vm, SOURCES_3, "price", [50000, 50010, 49995])
    contract.resolve("q1")

    q = contract.get_query("q1")
    assert q["resolved"] is True
    assert q["agreeing_count"] == 3
    assert q["agreed_value"] == str(50000 * 100_000_000)  # median of [49995, 50000, 50010]


def test_resolve_partial_agreement_meets_threshold(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "price", 50, 2)  # 0.5% tolerance

    # a,b agree closely; c is way off
    _clear_and_mock_values(direct_vm, SOURCES_3, "price", [50000, 50010, 90000])
    contract.resolve("q1")

    q = contract.get_query("q1")
    assert q["resolved"] is True
    assert q["agreeing_count"] == 2


def test_resolve_below_threshold_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "price", 10, 3)  # need all 3, tight tolerance

    _clear_and_mock_values(direct_vm, SOURCES_3, "price", [50000, 50010, 90000])
    with direct_vm.expect_revert("Quorum not reached"):
        contract.resolve("q1")

    assert contract.get_query("q1")["resolved"] is False


def test_resolve_excludes_dead_and_malformed_sources(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "price", 100, 2)

    direct_vm.clear_mocks()
    _mock_json(direct_vm, SOURCES_3[0], {"price": 50000})
    _mock_json(direct_vm, SOURCES_3[1], {"price": 50010})
    _mock_json(direct_vm, SOURCES_3[2], {"error": "not found"}, status=404)  # no 'price' key

    contract.resolve("q1")

    q = contract.get_query("q1")
    assert q["resolved"] is True
    assert q["agreeing_count"] == 2  # third source excluded, not counted as disagreement


def test_resolve_tolerance_boundary(direct_vm, direct_deploy, direct_alice):
    """tolerance_bps=100 (1%) - a value just inside the tolerance should
    cluster; just beyond it should not."""
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "price", 100, 2)

    # 50000 and 50500 differ by ~0.99% of 50500 -> within 1% tolerance
    _clear_and_mock_values(direct_vm, SOURCES_2, "price", [50000, 50500])
    contract.resolve("q1")
    assert contract.get_query("q1")["agreeing_count"] == 2

    contract.create_query("q2", SOURCES_2, "price", 100, 2)
    # 50000 and 50510 differ by just over 1% of 50510 -> outside tolerance
    _clear_and_mock_values(direct_vm, SOURCES_2, "price", [50000, 50510])
    with direct_vm.expect_revert("Quorum not reached"):
        contract.resolve("q2")


# ---------------------------------------------------------------------------
# resolve - exact match mode (tolerance_bps = 0)
# ---------------------------------------------------------------------------


def test_resolve_exact_numeric_mode(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "price", 0, 2)

    _clear_and_mock_values(direct_vm, SOURCES_3, "price", [50000, 50000, 50001])
    contract.resolve("q1")

    q = contract.get_query("q1")
    assert q["resolved"] is True
    assert q["agreeing_count"] == 2
    assert q["agreed_value"] == str(50000 * 100_000_000)


def test_resolve_exact_string_mode(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "status", 0, 2)

    _clear_and_mock_values(direct_vm, SOURCES_3, "status", ["active", "active", "paused"])
    contract.resolve("q1")

    q = contract.get_query("q1")
    assert q["resolved"] is True
    assert q["agreeing_count"] == 2
    assert q["agreed_value"] == "active"


# ---------------------------------------------------------------------------
# resolve - misc reverts / json_path shapes
# ---------------------------------------------------------------------------


def test_resolve_unknown_query_fails(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("not found"):
        contract.resolve("nonexistent")


def test_resolve_already_resolved_fails(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "price", 100, 2)

    _clear_and_mock_values(direct_vm, SOURCES_2, "price", [50000, 50010])
    contract.resolve("q1")

    with direct_vm.expect_revert("already been resolved"):
        contract.resolve("q1")


def test_get_query_unknown_fails(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("not found"):
        contract.get_query("nonexistent")


def test_get_sources_empty_for_unknown_query(direct_deploy):
    contract = direct_deploy(CONTRACT)
    assert contract.get_sources("nonexistent") == []


def test_resolve_nested_array_json_path(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "results[0].value", 100, 2)

    direct_vm.clear_mocks()
    for src, val in zip(SOURCES_2, [50000, 50010]):
        direct_vm.mock_web(
            src.replace(".", r"\."),
            {"method": "GET", "status": 200, "body": json.dumps({"results": [{"value": val}]})},
        )

    contract.resolve("q1")
    assert contract.get_query("q1")["agreeing_count"] == 2


def test_resolve_bare_top_level_array_json_path(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "[0].price", 100, 2)

    direct_vm.clear_mocks()
    for src, val in zip(SOURCES_2, [50000, 50010]):
        direct_vm.mock_web(
            src.replace(".", r"\."),
            {"method": "GET", "status": 200, "body": json.dumps([{"price": val}])},
        )

    contract.resolve("q1")
    assert contract.get_query("q1")["agreeing_count"] == 2


def test_resolve_boolean_leaf_excluded_not_treated_as_zero_one(direct_vm, direct_deploy, direct_alice):
    """A JSON `true`/`false` leaf must not be silently coerced to 1/0 and
    clustered as if it were a numeric price."""
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_3, "active", 100, 2)

    direct_vm.clear_mocks()
    _mock_json(direct_vm, SOURCES_3[0], {"active": True})
    _mock_json(direct_vm, SOURCES_3[1], {"active": True})
    _mock_json(direct_vm, SOURCES_3[2], {"active": 1})  # genuine int, not a bool

    with direct_vm.expect_revert("Quorum not reached"):
        contract.resolve("q1")


def test_resolve_quoted_numeric_string_clusters_with_bare_number(direct_vm, direct_deploy, direct_alice):
    """Some APIs quote numeric values as JSON strings to avoid float
    precision loss - that must still cluster with a bare-number source
    reporting the same fact, not be treated as a disagreeing string."""
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "price", 100, 2)

    direct_vm.clear_mocks()
    _mock_json(direct_vm, SOURCES_2[0], {"price": 50000})
    _mock_json(direct_vm, SOURCES_2[1], {"price": "50000.00"})

    contract.resolve("q1")
    q = contract.get_query("q1")
    assert q["agreeing_count"] == 2
    assert q["agreed_value"] == str(50000 * 100_000_000)


def test_resolve_records_creator(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "price", 0, 2)

    from tests.direct.conftest import to_hex

    assert contract.get_query("q1")["creator"] == to_hex(direct_alice)


def test_multiple_queries_are_independent(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.create_query("q1", SOURCES_2, "price", 100, 2)
    contract.create_query("q2", SOURCES_3, "status", 0, 2)

    assert contract.get_query("q1")["source_count"] == 2
    assert contract.get_query("q2")["source_count"] == 3
    assert contract.get_sources("q1") == SOURCES_2
    assert contract.get_sources("q2") == SOURCES_3
