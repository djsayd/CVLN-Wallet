"""Backfill migration behaviour on a user with real cache/ledger drift.
Covers POST /api/admin/ledger/backfill (Migration opening entries) and the
re-drift case (static idempotency_key = 'backfill:<account>').
"""
import uuid

import pytest

from conftest import BASE_URL, auth, _mk_user, _cleanup


@pytest.fixture
def drifted_user(mongo):
    # balance_cc funded WITHOUT any ledger entry -> deliberate cache drift
    uid, token = _mk_user(mongo, "drift", is_admin=False, balance=300.0, with_ledger=False)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)
    mongo.ledger_entries.delete_many({"idempotency_key": f"backfill:acct_cash_{uid}"})


def _integrity(api, admin_token):
    r = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=auth(admin_token), timeout=60)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_backfill_creates_migration_entry_for_drifted_account(api, admin_user, drifted_user, mongo):
    h = admin_user["token"]
    uid = drifted_user["user_id"]

    pre = _integrity(api, h)
    assert any(m["user_id"] == uid for m in pre["cache_mismatches"]), \
        "integrity did not detect the seeded cache drift"

    b = api.post(f"{BASE_URL}/api/admin/ledger/backfill", headers=auth(h), timeout=120)
    assert b.status_code == 200, b.text[:300]
    assert b.json()["accounts_backfilled"] >= 1

    entry = mongo.ledger_entries.find_one({"idempotency_key": f"backfill:acct_cash_{uid}"})
    assert entry is not None, "no Migration ledger entry created for drifted account"
    assert entry["category"] == "Migration"
    assert "migration" in entry["description"].lower()
    assert abs(sum(p["amount"] for p in entry["postings"])) < 1e-6
    accounts = {p["account_id"]: p["amount"] for p in entry["postings"]}
    assert accounts[f"acct_cash_{uid}"] == 300.0
    assert accounts["acct_sys_issuance"] == -300.0

    post = _integrity(api, h)
    assert post["balanced"] is True
    assert post["cache_mismatches"] == [], post["cache_mismatches"]

    hh = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(h), timeout=60).json()
    assert hh["jcc_supply_reconciled"] is True, hh
    assert hh["severity"] == "INFO", hh


def test_backfill_cannot_fix_a_second_drift_on_same_account(api, admin_user, drifted_user, mongo):
    """Regression check: ledger_post uses a STATIC idempotency_key 'backfill:<acct>',
    so once an account was backfilled a later drift can never be corrected."""
    h = admin_user["token"]
    uid = drifted_user["user_id"]

    api.post(f"{BASE_URL}/api/admin/ledger/backfill", headers=auth(h), timeout=120)
    assert _integrity(api, h)["cache_mismatches"] == []

    # new drift appears on the same account
    mongo.users.update_one({"user_id": uid}, {"$set": {"balance_cc": 450.0}})
    mism = [m["user_id"] for m in _integrity(api, h)["cache_mismatches"]]
    assert uid in mism

    r = api.post(f"{BASE_URL}/api/admin/ledger/backfill", headers=auth(h), timeout=120)
    assert r.status_code == 200, r.text[:300]
    after = _integrity(api, h)
    assert after["cache_mismatches"] == [], (
        f"backfill reported accounts_backfilled={r.json()['accounts_backfilled']} "
        f"but drift persists (static idempotency key blocks a 2nd migration entry): "
        f"{after['cache_mismatches']}")
