from app.engines.borrow_rules import can_lend, is_overdue, classify_loans, parse_due_date, validate_lend_request

def test_mutex():
    assert can_lend("available", 0)["ok"]
    assert can_lend("available", 1)["reason"] == "already_on_loan"
    assert can_lend("retired", 0)["ok"] is False

def test_overdue():
    assert is_overdue("2020-01-01", "2026-01-01", "active")
    assert not is_overdue("2020-01-01", "2026-01-01", "returned")

def test_classify():
    r = classify_loans([
        {"id": 1, "status": "active", "due_date": "2020-01-01"},
        {"id": 2, "status": "active", "due_date": "2099-01-01"},
        {"id": 3, "status": "returned", "due_date": "2020-01-01"},
    ], "2026-01-01")
    assert len(r["overdue"]) == 1 and len(r["active"]) == 1 and len(r["returned"]) == 1

def test_parse_due_date():
    assert parse_due_date("2026-12-31") == "2026-12-31"
    assert parse_due_date(" 2026-12-31 ") == "2026-12-31"
    for bad in ("", None, "2026-13-01", "2026-02-30", "31-12-2026", "20261231", "abc"):
        assert parse_due_date(bad) is None

def test_validate_lend_request():
    assert validate_lend_request("", "2026-12-31")["reason"] == "borrower_required"
    assert validate_lend_request("   ", "2026-12-31")["reason"] == "borrower_required"
    assert validate_lend_request("邻居", "")["reason"] == "due_date_invalid"
    assert validate_lend_request("邻居", "2026-02-30")["reason"] == "due_date_invalid"
    ok = validate_lend_request("邻居", "2026-12-31")
    assert ok["ok"] and ok["due_date"] == "2026-12-31"
