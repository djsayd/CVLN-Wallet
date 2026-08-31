"""B3 FIX #1: POST /api/withdrawals must enforce AVAILABLE balance (balance - held)
ATOMICALLY, so B2 holds can never be bypassed and concurrent withdrawals can never
together exceed the available balance.

Run SERIALLY (-n 0).
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth, wallet_balance

IBAN = "FR7630006000011234567890189"


@pytest.fixture(scope="module")
def wadmin(mongo):
    uid, token = _mk_user(mongo, "b3wadm", is_admin=True, balance=10.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module", autouse=True)
def no_fees(api, wadmin):
    """Withdrawal fees OFF so amounts in this module are exact. Reset at teardown."""
    r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
                headers=auth(wadmin["token"]), timeout=30)
    assert r.status_code == 200, r.text[:300]
    yield
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
            headers=auth(wadmin["token"]), timeout=30)


def _wallet(api, token):
    r = api.get(f"{BASE_URL}/api/wallet", headers=auth(token), timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _withdraw(token, amount):
    return requests.post(f"{BASE_URL}/api/withdrawals",
                         json={"amount_cc": amount, "iban": IBAN},
                         headers=auth(token), timeout=90)


def test_withdrawal_respects_active_hold(api, mongo, wadmin):
    """balance 100 fully locked by an ACTIVE hold of 100 -> withdraw 100 must be 400."""
    uid, token = _mk_user(mongo, "b3wh", balance=100.0)
    try:
        h = api.post(f"{BASE_URL}/api/holds",
                     json={"amount": 100, "reason": "TEST lock", "ttl_seconds": 600},
                     headers=auth(token), timeout=30)
        assert h.status_code == 200, h.text[:300]
        hold_id = h.json()["hold_id"]

        w = _withdraw(token, 100)
        assert w.status_code == 400, (w.status_code, w.text[:300])
        assert "disponible" in w.text.lower(), w.text[:300]

        wal = _wallet(api, token)
        assert abs(wal["balance_cc"] - 100.0) < 1e-6, wal
        assert abs(wal.get("held_cc", 0) - 100.0) < 1e-6, wal
        assert abs(wal.get("available_balance_cc", wal["balance_cc"] - wal.get("held_cc", 0))) < 1e-6, wal
        assert not list(mongo.withdrawals.find({"user_id": uid}))

        # partial availability: release the hold -> withdrawal of the freed amount succeeds
        rel = api.post(f"{BASE_URL}/api/holds/{hold_id}/release",
                       headers=auth(token), timeout=30)
        assert rel.status_code == 200, rel.text[:300]
        w = _withdraw(token, 100)
        assert w.status_code == 200, (w.status_code, w.text[:300])
        assert abs(wallet_balance(api, token) - 0.0) < 1e-6
    finally:
        _cleanup(mongo, uid)
        mongo.balance_holds.delete_many({"user_id": uid})


def test_withdrawal_partial_hold_limits_amount(api, mongo, wadmin):
    """balance 100, hold 60 -> available 40: withdraw 50 = 400, withdraw 40 = 200."""
    uid, token = _mk_user(mongo, "b3wph", balance=100.0)
    try:
        h = api.post(f"{BASE_URL}/api/holds",
                     json={"amount": 60, "reason": "TEST partial lock", "ttl_seconds": 600},
                     headers=auth(token), timeout=30)
        assert h.status_code == 200, h.text[:300]
        assert _withdraw(token, 50).status_code == 400
        assert abs(wallet_balance(api, token) - 100.0) < 1e-6
        w = _withdraw(token, 40)
        assert w.status_code == 200, (w.status_code, w.text[:300])
        wal = _wallet(api, token)
        assert abs(wal["balance_cc"] - 60.0) < 1e-6, wal
        assert abs(wal.get("held_cc", 0) - 60.0) < 1e-6, wal
    finally:
        _cleanup(mongo, uid)
        mongo.balance_holds.delete_many({"user_id": uid})


@pytest.mark.parametrize("rnd", [1, 2, 3])
def test_concurrent_withdrawals_atomic_guard(api, mongo, wadmin, rnd):
    """12 concurrent withdrawals of 25 on a 100 balance -> exactly 4 winners, balance 0."""
    uid, token = _mk_user(mongo, "b3wc", balance=100.0)
    try:
        with ThreadPoolExecutor(max_workers=12) as ex:
            results = list(ex.map(lambda _: _withdraw(token, 25).status_code, range(12)))
        ok = results.count(200)
        bal = wallet_balance(api, token)
        print(f"round {rnd}: codes={results} winners={ok} balance={bal}")
        assert all(c in (200, 400) for c in results), results
        assert ok == 4, f"expected exactly 4 winners, got {ok} ({results}), balance={bal}"
        assert abs(bal) < 1e-6, f"balance should be 0, got {bal}"
        assert mongo.withdrawals.count_documents({"user_id": uid}) == 4
    finally:
        _cleanup(mongo, uid)


def test_concurrent_withdrawals_with_hold(api, mongo, wadmin):
    """balance 100 with a 50 hold -> only 2 of 8 concurrent 25-withdrawals may win."""
    uid, token = _mk_user(mongo, "b3wch", balance=100.0)
    try:
        h = api.post(f"{BASE_URL}/api/holds",
                     json={"amount": 50, "reason": "TEST lock", "ttl_seconds": 600},
                     headers=auth(token), timeout=30)
        assert h.status_code == 200, h.text[:300]
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda _: _withdraw(token, 25).status_code, range(8)))
        wal = _wallet(api, token)
        print(f"with-hold codes={results} wallet={wal}")
        assert results.count(200) == 2, (results, wal)
        assert abs(wal["balance_cc"] - 50.0) < 1e-6, wal
        assert abs(wal.get("held_cc", 0) - 50.0) < 1e-6, wal
    finally:
        _cleanup(mongo, uid)
        mongo.balance_holds.delete_many({"user_id": uid})


def test_ledger_balanced_after_withdrawal_races(api, wadmin):
    h = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(wadmin["token"]), timeout=60)
    assert h.status_code == 200, h.text[:300]
    d = h.json()
    assert d["ledger_balanced"] is True, d.get("per_asset_sum")
    assert d.get("jcc_supply_reconciled") is True, d
    assert d.get("severity") == "INFO", d
