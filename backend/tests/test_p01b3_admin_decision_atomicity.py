"""Probe: atomicity of admin withdrawal decision endpoints (approve / reject).

reject() must credit principal + fee EXACTLY ONCE even under concurrent calls, and
approve/reject must be mutually exclusive (a payout cannot be both paid and refunded).
Run SERIALLY (-n 0).
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth, wallet_balance

IBAN = "FR7630006000011234567890189"


@pytest.fixture(scope="module")
def radmin(mongo):
    uid, token = _mk_user(mongo, "b3radm", is_admin=True, balance=10.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module", autouse=True)
def fee_2pct(api, radmin):
    r = api.put(f"{BASE_URL}/api/admin/fees",
                json={"fee_policy": {"withdrawal": {"pct": 0.02, "flat": 0.0}}},
                headers=auth(radmin["token"]), timeout=30)
    assert r.status_code == 200, r.text[:300]
    yield
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
            headers=auth(radmin["token"]), timeout=30)


def _mk_pending(api, token, amount=100):
    r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": amount, "iban": IBAN},
                 headers=auth(token), timeout=60)
    assert r.status_code == 200, r.text[:300]
    return r.json()["withdrawal"]


@pytest.mark.parametrize("rnd", [1, 2, 3, 4, 5])
def test_concurrent_rejects_credit_only_once(api, mongo, radmin, rnd):
    uid, token = _mk_user(mongo, "b3rr", balance=300.0)
    try:
        wd = _mk_pending(api, token, 100)
        assert abs(wallet_balance(api, token) - 198.0) < 1e-6

        def call(_):
            try:
                return requests.post(f"{BASE_URL}/api/admin/withdrawals/{wd['wd_id']}/reject",
                                     headers=auth(radmin["token"]), timeout=60).status_code
            except Exception:
                return 0

        with ThreadPoolExecutor(max_workers=12) as ex:
            codes = list(ex.map(call, range(12)))
        bal = wallet_balance(api, token)
        print(f"round {rnd}: concurrent rejects codes={codes} balance={bal}")
        assert codes.count(200) == 1, f"reject is not atomic: {codes.count(200)} succeeded ({codes}), balance={bal}"
        assert abs(bal - 300.0) < 1e-6, f"double credit on concurrent reject: balance={bal} (expected 300)"
    finally:
        _cleanup(mongo, uid)


def test_approve_and_reject_mutually_exclusive(api, mongo, radmin):
    uid, token = _mk_user(mongo, "b3ar", balance=300.0)
    try:
        wd = _mk_pending(api, token, 100)

        def call(kind):
            try:
                return kind, requests.post(
                    f"{BASE_URL}/api/admin/withdrawals/{wd['wd_id']}/{kind}",
                    headers=auth(radmin["token"]), timeout=60).status_code
            except Exception:
                return kind, 0

        with ThreadPoolExecutor(max_workers=6) as ex:
            res = list(ex.map(call, ["approve", "reject", "approve", "reject", "approve", "reject"]))
        ok = [k for k, c in res if c == 200]
        bal = wallet_balance(api, token)
        final = mongo.withdrawals.find_one({"wd_id": wd["wd_id"]}, {"status": 1})
        print(f"approve/reject res={res} winners={ok} balance={bal} status={final}")
        assert len(ok) == 1, f"approve/reject not mutually exclusive: winners={ok}"
        if ok[0] == "reject":
            assert abs(bal - 300.0) < 1e-6, bal
        else:
            assert abs(bal - 198.0) < 1e-6, bal
    finally:
        _cleanup(mongo, uid)
