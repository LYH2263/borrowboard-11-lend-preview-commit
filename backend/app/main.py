from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.borrow_rules import can_lend, classify_loans, validate_lend_request

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/board")
def board():
    c = connect()
    available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
    loans = [dict(r) for r in c.execute(
        """SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id
           WHERE loans.status='active'""")]
    c.close()
    cls = classify_loans(loans, date.today().isoformat())
    return {
        "available": available,
        "active": cls["active"],
        "overdue": cls["overdue"],
        "counts": {"available": len(available), "active": len(cls["active"]), "overdue": len(cls["overdue"])},
    }

class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    c = connect()
    cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                    (body.title, body.owner, "available", "clean"))
    c.commit(); iid = cur.lastrowid; c.close(); return {"id": iid}

class LendIn(BaseModel):
    borrower: str
    due_date: str

def _run_lend(iid: int, borrower: str, due_date: str) -> dict:
    """Validate + lend in one IMMEDIATE transaction.

    The availability re-check and both writes (loan insert, item status flip) commit
    together or roll back together: a concurrent lend/return in between can never yield
    a second active loan on the item, nor a 409 that already changed items.status.
    """
    v = validate_lend_request(borrower, due_date)
    if not v["ok"]:
        raise HTTPException(400, v["reason"])
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        if not item: raise HTTPException(404, "item")
        active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
        check = can_lend(item["status"], active)
        if not check["ok"]: raise HTTPException(409, check["reason"])
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (iid, borrower.strip(), "active", v["due_date"], datetime.now(timezone.utc).isoformat()))
        flipped = c.execute("UPDATE items SET status='on_loan' WHERE id=? AND status='available'", (iid,))
        if flipped.rowcount != 1: raise HTTPException(409, "item_not_available")
        c.commit()
        return {"loan_id": cur.lastrowid}
    except HTTPException:
        c.rollback(); raise
    except Exception:
        c.rollback(); raise HTTPException(500, "lend_failed")
    finally:
        c.close()

@app.post("/api/items/{iid}/lend/preview")
def lend_preview(iid: int, body: LendIn):
    """Dry-run ticket: returns the item that would be occupied and the due_date.
    Read-only — board counts and item status are untouched."""
    v = validate_lend_request(body.borrower, body.due_date)
    if not v["ok"]: raise HTTPException(400, v["reason"])
    c = connect()
    item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
    check = can_lend(item["status"], active)
    if not check["ok"]:
        c.close(); raise HTTPException(409, check["reason"])
    c.close()
    return {"item_id": item["id"], "title": item["title"],
            "borrower": body.borrower.strip(), "due_date": v["due_date"]}

@app.post("/api/items/{iid}/lend/confirm")
def lend_confirm(iid: int, body: LendIn):
    """Confirm a preview ticket. Re-validates at submit instant: if another lend/return
    changed the item since the preview, the whole order fails (409) with zero writes."""
    return _run_lend(iid, body.borrower, body.due_date)

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    return _run_lend(iid, body.borrower, body.due_date)

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    c = connect()
    loan = c.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
    if not loan: c.close(); raise HTTPException(404, "loan")
    if loan["status"] != "active":
        c.close(); raise HTTPException(400, "not_active")
    c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=?",
              (datetime.now(timezone.utc).isoformat(), lid))
    c.execute("UPDATE items SET status='available' WHERE id=?", (loan["item_id"],))
    c.commit(); c.close(); return {"ok": True}

@app.get("/api/loans")
def loans():
    c = connect()
    rows = [dict(r) for r in c.execute(
        "SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id ORDER BY loans.id DESC")]
    c.close()
    return classify_loans(rows, date.today().isoformat())

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
