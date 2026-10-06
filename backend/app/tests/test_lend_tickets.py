import threading

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as c:
        yield c


def _counts(client):
    return client.get("/api/board").json()["counts"]


def _item(client, iid):
    return next(i for i in client.get("/api/items").json() if i["id"] == iid)


def _active_loans(client, iid):
    loans = client.get("/api/loans").json()
    return [l for l in loans["active"] + loans["overdue"] if l["item_id"] == iid]


def _preview(client, iid=1, borrower="邻居乙", due_date="2026-12-31"):
    return client.post(f"/api/items/{iid}/preview", json={"borrower": borrower, "due_date": due_date})


def test_preview_rejects_bad_input(client):
    assert _preview(client, borrower="").status_code == 400
    assert _preview(client, borrower="   ").status_code == 400
    assert _preview(client, due_date="").status_code == 400
    assert _preview(client, due_date="2026-13-40").status_code == 400
    assert _preview(client, due_date="next friday").status_code == 400
    assert _preview(client, iid=999).status_code == 404
    assert _preview(client, iid=4).status_code == 409  # seed item already on_loan


def test_preview_leaves_board_untouched(client):
    before = _counts(client)
    r = _preview(client)
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "电钻" and body["due_date"] == "2026-12-31" and body["ticket_id"]
    assert _counts(client) == before
    assert _item(client, 1)["status"] == "available"


def test_confirm_success_and_replay(client):
    tid = _preview(client).json()["ticket_id"]
    before = _counts(client)
    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 200 and r.json()["loan_id"]
    after = _counts(client)
    assert after["available"] == before["available"] - 1
    assert after["active"] == before["active"] + 1
    assert _item(client, 1)["status"] == "on_loan"
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 409
    assert _counts(client) == after


def test_stale_ticket_fails_cleanly_after_other_lend(client):
    tid = _preview(client).json()["ticket_id"]
    assert _counts(client)["available"] == 3  # preview alone changed nothing
    # another borrow lands first and takes the drill
    r = client.post("/api/items/1/lend", json={"borrower": "别人", "due_date": "2026-11-11"})
    assert r.status_code == 200
    mid = _counts(client)
    # stale ticket fails as a whole: no second active loan, no items.status flip
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 409
    assert _counts(client) == mid
    actives = _active_loans(client, 1)
    assert len(actives) == 1 and actives[0]["borrower"] == "别人"
    assert _item(client, 1)["status"] == "on_loan"
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 409  # ticket now failed


def test_confirm_recalculates_at_submit(client):
    tid = _preview(client).json()["ticket_id"]
    lid = client.post("/api/items/1/lend", json={"borrower": "别人", "due_date": "2026-11-11"}).json()["loan_id"]
    assert client.post(f"/api/loans/{lid}/return").status_code == 200
    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 200
    actives = _active_loans(client, 1)
    assert len(actives) == 1 and actives[0]["borrower"] == "邻居乙"


def test_confirm_revalidates_stored_ticket(client):
    c = connect()
    cur = c.execute(
        "INSERT INTO lend_tickets(item_id,borrower,due_date,status,created_at)"
        " VALUES (1,'','2026-12-31','open','2026-01-01')")
    c.commit(); tid = cur.lastrowid; c.close()
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 400
    c = connect()
    status = c.execute("SELECT status FROM lend_tickets WHERE id=?", (tid,)).fetchone()["status"]
    c.close()
    assert status == "failed"
    assert _item(client, 1)["status"] == "available"


def test_concurrent_confirms_exactly_one_wins(client):
    tids = [_preview(client).json()["ticket_id"] for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []

    def confirm(tid):
        with TestClient(app) as c2:
            barrier.wait(timeout=10)
            results.append(c2.post(f"/api/tickets/{tid}/confirm").status_code)

    threads = [threading.Thread(target=confirm, args=(t,)) for t in tids]
    for th in threads: th.start()
    for th in threads: th.join(timeout=30)
    assert sorted(results) == [200, 409]
    assert len(_active_loans(client, 1)) == 1
