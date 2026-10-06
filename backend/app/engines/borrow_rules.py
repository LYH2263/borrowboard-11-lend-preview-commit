"""One active loan per item + overdue detection + lend-request validation."""
import re
from datetime import date

_DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def parse_due_date(raw: str) -> str | None:
    """Normalize a strict YYYY-MM-DD date; None if malformed or not a real calendar date."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not _DUE_RE.match(s):
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None

def validate_lend_request(borrower: str, due_date: str) -> dict:
    """Empty borrower or illegal due date fails outright, before any state is touched."""
    if not borrower or not borrower.strip():
        return {"ok": False, "reason": "borrower_required"}
    norm = parse_due_date(due_date)
    if norm is None:
        return {"ok": False, "reason": "due_date_invalid"}
    return {"ok": True, "reason": "", "due_date": norm}

def can_lend(item_status: str, active_loans: int) -> dict:
    if item_status != "available":
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}

def is_overdue(due_date: str, today: str, loan_status: str) -> bool:
    if loan_status != "active":
        return False
    return bool(due_date) and due_date < today

def classify_loans(loans: list[dict], today: str) -> dict:
    active, overdue, returned = [], [], []
    for L in loans:
        st = L.get("status")
        if st == "returned":
            returned.append(L)
        elif is_overdue(L.get("due_date"), today, st):
            overdue.append({**L, "overdue": True})
        elif st == "active":
            active.append({**L, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}
