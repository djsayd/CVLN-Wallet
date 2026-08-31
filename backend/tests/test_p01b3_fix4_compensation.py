"""B3 FIX #4 regression: guard/ledger compensation consistency.

A post-guard failure is not triggerable through the public API, so this suite asserts
the observable invariants that a missing compensation would break:
  - transactions.refunded_cc == SUM(refunds.amount) for the same original_tx_id
  - transactions.reversed == True  <=>  a reversals record exists
  - rejected attempts (409 / 400 / 403) never move the guard counters
Run SERIALLY (-n 0).
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth, wallet_balance

IBAN = "FR7630006000011234567890189"


@pytest.fixture(scope="module")
def cadmin(mongo):
    uid, token = _mk_user(mongo, "b3cadm", is_admin=True, balance=10.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def cuser(mongo):
    uid, token = _mk_user(mongo, "b3cusr", balance=2000.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)
    mongo.refunds.delete_many({"user_id": uid})
    mongo.reversals.delete_many({"user_id": uid})


@pytest.fixture(scope="module", autouse=True)
def no_fees(api, cadmin):
    r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
                headers=auth(cadmin["token"]), timeout=30)
    assert r.status_code == 200, r.text[:300]
    yield


def _outflow(api, user, amount):
    r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": amount, "iban": IBAN},
                 headers=auth(user["token"]), timeout=60)
    assert r.status_code == 200, r.text[:400]
    txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(user["token"]), timeout=30).json()
    cand = [t for t in txs if t["category"] == "Retrait" and abs(t["amount"] + amount) < 1e-6]
    assert cand, f"no Retrait tx of -{amount}"
    return cand[0]["tx_id"]


def test_refunded_cc_matches_refund_records(api, mongo, cadmin, cuser):
    """Partial refunds + rejected over-refunds: counter must equal the sum of records."""
    tx_id = _outflow(api, cuser, 100)
    b0 = wallet_balance(api, cuser["token"])
    for amt in (25, 25, 10):
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id, "amount": amt},
                     headers=auth(cadmin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
    # rejected attempts must not touch the counter
    for bad in ({"original_tx_id": tx_id, "amount": 500}, {"original_tx_id": tx_id, "amount": -1}):
        r = api.post(f"{BASE_URL}/api/refunds", json=bad, headers=auth(cadmin["token"]), timeout=30)
        assert r.status_code in (400, 409), (bad, r.status_code, r.text[:200])

    tx = mongo.transactions.find_one({"tx_id": tx_id})
    recs = list(mongo.refunds.find({"original_tx_id": tx_id}))
    total = round(sum(x["amount"] for x in recs), 2)
    assert abs(round(tx.get("refunded_cc", 0), 2) - 60.0) < 1e-6, tx.get("refunded_cc")
    assert abs(total - round(tx.get("refunded_cc", 0), 2)) < 1e-6, (total, tx.get("refunded_cc"))
    assert abs(wallet_balance(api, cuser["token"]) - (b0 + 60)) < 1e-6


def test_reversed_flag_matches_reversal_record(api, mongo, cadmin, cuser):
    tx_id = _outflow(api, cuser, 40)
    b0 = wallet_balance(api, cuser["token"])
    r = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id, "reason": "TEST fix4"},
                 headers=auth(cadmin["token"]), timeout=60)
    assert r.status_code == 200, r.text[:300]
    rvid = r.json()["reversal_id"]
    tx = mongo.transactions.find_one({"tx_id": tx_id})
    assert tx.get("reversed") is True, tx
    assert mongo.reversals.count_documents({"reversal_id": rvid, "original_tx_id": tx_id}) == 1
    assert abs(wallet_balance(api, cuser["token"]) - (b0 + 40)) < 1e-6

    # failed second reversal must leave the flag set and no extra record
    r2 = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id},
                  headers=auth(cadmin["token"]), timeout=60)
    assert r2.status_code == 409, (r2.status_code, r2.text[:200])
    assert mongo.transactions.find_one({"tx_id": tx_id}).get("reversed") is True
    assert mongo.reversals.count_documents({"original_tx_id": tx_id}) == 1
    assert abs(wallet_balance(api, cuser["token"]) - (b0 + 40)) < 1e-6


def test_no_guard_drift_after_concurrent_mixed_traffic(api, mongo, cadmin, cuser):
    """20 concurrent refunds of 10 on a 100 principal: counter, records, balance all agree."""
    tx_id = _outflow(api, cuser, 100)
    b0 = wallet_balance(api, cuser["token"])
    hdrs = auth(cadmin["token"], {"Content-Type": "application/json"})

    def call(_):
        try:
            return requests.post(f"{BASE_URL}/api/refunds",
                                 json={"original_tx_id": tx_id, "amount": 10},
                                 headers=hdrs, timeout=90).status_code
        except Exception:
            return 0

    with ThreadPoolExecutor(max_workers=20) as ex:
        codes = list(ex.map(call, range(20)))
    ok = codes.count(200)
    print(f"mixed refunds codes={codes} winners={ok}")
    assert ok == 10, (ok, codes)
    tx = mongo.transactions.find_one({"tx_id": tx_id})
    recs = list(mongo.refunds.find({"original_tx_id": tx_id}))
    assert len(recs) == ok, (len(recs), ok)
    assert abs(round(tx.get("refunded_cc", 0), 2) - 100.0) < 1e-6, tx.get("refunded_cc")
    assert abs(wallet_balance(api, cuser["token"]) - (b0 + 100)) < 1e-6


def test_global_refund_counter_invariant(api, mongo, cadmin):
    """DB-wide: no tx may have refunded_cc != SUM(its refunds) (compensation drift detector)."""
    drift = []
    for tx in mongo.transactions.find({"refunded_cc": {"$gt": 0}}, {"tx_id": 1, "refunded_cc": 1}):
        s = round(sum(r["amount"] for r in mongo.refunds.find({"original_tx_id": tx["tx_id"]})), 2)
        if abs(s - round(tx["refunded_cc"], 2)) > 0.005:
            drift.append({"tx_id": tx["tx_id"], "refunded_cc": tx["refunded_cc"], "records_sum": s})
    assert not drift, f"refund guard drift detected: {drift}"


def test_health_balanced(api, cadmin):
    h = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(cadmin["token"]), timeout=60)
    assert h.status_code == 200, h.text[:300]
    d = h.json()
    assert d["ledger_balanced"] is True, d.get("per_asset_sum")
    assert d.get("jcc_supply_reconciled") is True, d
    assert d.get("severity") == "INFO", d
