"""Preview-ticket lend flow: read-only preview, atomic confirm, stale-ticket races."""
import os, tempfile, threading

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="borrowboard-test-")

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.db import connect
from app.main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def fresh_db():
    c = connect()
    c.executescript("DELETE FROM loans; DELETE FROM items; DELETE FROM settings;")
    c.commit(); c.close()
    seed.init_db()
    yield

def item_by_title(title):
    c = connect()
    row = c.execute("SELECT * FROM items WHERE title=?", (title,)).fetchone()
    c.close()
    return dict(row) if row else None

def loans_of(item_id):
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM loans WHERE item_id=?", (item_id,))]
    c.close()
    return rows

def counts():
    return client.get("/api/board").get_json()["counts"]

def test_preview_returns_item_and_due_date_without_mutating():
    drill = item_by_title("电钻")
    before = counts()
    r = client.post(f"/api/items/{drill['id']}/lend/preview",
                    json={"borrower": "邻居乙", "due_date": "2026-12-31"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["item_id"] == drill["id"] and body["title"] == "电钻"
    assert body["due_date"] == "2026-12-31" and body["borrower"] == "邻居乙"
    # 可借栏与顶细条都不动：计数、物品状态、借出记录均无变化
    assert counts() == before
    assert item_by_title("电钻")["status"] == "available"
    assert loans_of(drill["id"]) == []

def test_preview_rejects_invalid_input_and_unavailable_item():
    drill = item_by_title("电钻")
    gone = item_by_title("已外借样例")
    r = client.post(f"/api/items/{drill['id']}/lend/preview", json={"borrower": "  ", "due_date": "2026-12-31"})
    assert r.status_code == 400 and r.get_json()["detail"] == "borrower_required"
    r = client.post(f"/api/items/{drill['id']}/lend/preview", json={"borrower": "邻居乙", "due_date": "2026-02-30"})
    assert r.status_code == 400 and r.get_json()["detail"] == "due_date_invalid"
    r = client.post(f"/api/items/{drill['id']}/lend/preview", json={"borrower": "邻居乙", "due_date": "not-a-date"})
    assert r.status_code == 400
    r = client.post(f"/api/items/{gone['id']}/lend/preview", json={"borrower": "邻居乙", "due_date": "2026-12-31"})
    assert r.status_code == 409
    r = client.post("/api/items/99999/lend/preview", json={"borrower": "邻居乙", "due_date": "2026-12-31"})
    assert r.status_code == 404

def test_confirm_success_flips_item_and_counts():
    drill = item_by_title("电钻")
    before = counts()
    t = client.post(f"/api/items/{drill['id']}/lend/preview",
                    json={"borrower": "邻居乙", "due_date": "2026-12-31"}).get_json()
    r = client.post(f"/api/items/{drill['id']}/lend/confirm",
                    json={"borrower": t["borrower"], "due_date": t["due_date"]})
    assert r.status_code == 200 and r.get_json()["loan_id"]
    assert item_by_title("电钻")["status"] == "on_loan"
    active = [l for l in loans_of(drill["id"]) if l["status"] == "active"]
    assert len(active) == 1 and active[0]["borrower"] == "邻居乙" and active[0]["due_date"] == "2026-12-31"
    after = counts()
    assert after["available"] == before["available"] - 1

def test_stale_ticket_after_another_lend_fails_wholesale():
    drill = item_by_title("电钻")
    t = client.post(f"/api/items/{drill['id']}/lend/preview",
                    json={"borrower": "邻居乙", "due_date": "2026-12-31"}).get_json()
    # 另一笔借出抢先落库
    r = client.post(f"/api/items/{drill['id']}/lend", json={"borrower": "别人", "due_date": "2026-11-01"})
    assert r.status_code == 200
    # 旧票整单失败：不得再插第二笔 active，也不得把 items.status 改坏
    r = client.post(f"/api/items/{drill['id']}/lend/confirm",
                    json={"borrower": t["borrower"], "due_date": t["due_date"]})
    assert r.status_code == 409
    loans = loans_of(drill["id"])
    assert len(loans) == 1 and loans[0]["borrower"] == "别人"
    assert item_by_title("电钻")["status"] == "on_loan"

def test_ticket_after_intervening_return_recomputes_and_succeeds():
    gone = item_by_title("已外借样例")
    r = client.post(f"/api/items/{gone['id']}/lend/preview", json={"borrower": "邻居乙", "due_date": "2026-12-31"})
    assert r.status_code == 409  # 预演时在借
    loan_id = [l for l in loans_of(gone["id"]) if l["status"] == "active"][0]["id"]
    assert client.post(f"/api/loans/{loan_id}/return").status_code == 200
    # 归还后重新预演再确认，按提交瞬间的状态通过
    t = client.post(f"/api/items/{gone['id']}/lend/preview",
                    json={"borrower": "邻居乙", "due_date": "2026-12-31"}).get_json()
    r = client.post(f"/api/items/{gone['id']}/lend/confirm",
                    json={"borrower": t["borrower"], "due_date": t["due_date"]})
    assert r.status_code == 200
    assert item_by_title("已外借样例")["status"] == "on_loan"

def test_confirm_invalid_input_fails_without_writes():
    drill = item_by_title("电钻")
    before = counts()
    for payload in ({"borrower": "", "due_date": "2026-12-31"},
                    {"borrower": "邻居乙", "due_date": "2026-13-40"}):
        r = client.post(f"/api/items/{drill['id']}/lend/confirm", json=payload)
        assert r.status_code == 400
    assert item_by_title("电钻")["status"] == "available"
    assert loans_of(drill["id"]) == []
    assert counts() == before

def test_concurrent_confirms_exactly_one_active_loan():
    drill = item_by_title("电钻")
    results = []
    def worker(n):
        r = client.post(f"/api/items/{drill['id']}/lend/confirm",
                        json={"borrower": f"邻居{n}", "due_date": "2026-12-31"})
        results.append(r.status_code)
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert results.count(200) == 1
    assert results.count(409) == 7
    active = [l for l in loans_of(drill["id"]) if l["status"] == "active"]
    assert len(active) == 1
    assert item_by_title("电钻")["status"] == "on_loan"
