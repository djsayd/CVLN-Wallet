"""P0.1-B3 stress: repeated high-fanout concurrency on refunds/reversals.

Run SERIALLY (-n 0).
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth, wallet_balance
from test_p01b3_fees_refunds_reversals import _outflow_tx, _assert_balanced  # noqa


@pytest.fixture(scope="module")
def sadmin(mongo):
    uid, token = _mk_user(mongo, "b3sadm", is_admin=True, balance=50.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)
    mongo.refunds.delete_many({"user_id": uid})
    mongo.reversals.delete_many({"user_id": uid})


@pytest.fixture(scope="module")
def spayer(mongo):
    uid, token = _mk_user(mongo, "b3suser", balance=8000.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)
    mongo.refunds.delete_many({"user_id": uid})
    mongo.reversals.delete_many({"user_id": uid})


@pytest.fixture(scope="module", autouse=True)
def no_fees(api, sadmin):
    """No fee policy for the stress run (keeps principal math exact)."""
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
            headers=auth(sadmin["token"]), timeout=30)
    yield
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
            headers=auth(sadmin["token"]), timeout=30)


def _fanout(url, payloads, headers):
    def call(p):
        try:
            r = requests.post(url, json=p, headers=headers, timeout=120)
            return r.status_code
        except Exception as e:  # noqa
            print(f"request error: {e}")
            return 0
    with ThreadPoolExecutor(max_workers=len(payloads)) as ex:
        return list(ex.map(call, payloads))


@pytest.mark.parametrize("round_no", [1, 2, 3])
def test_stress_concurrent_partial_refunds(api, sadmin, spayer, mongo, round_no):
    principal = 100.0
    tx_id = _outflow_tx(api, spayer, principal)
    b0 = wallet_balance(api, spayer["token"])
    payloads = [{"original_tx_id": tx_id, "amount": 10, "reason": f"TEST stress {round_no}-{i}"}
                for i in range(24)]
    codes = _fanout(f"{BASE_URL}/api/refunds", payloads, auth(sadmin["token"]))
    ok = codes.count(200)
    print(f"round {round_no}: 200s={ok} 409s={codes.count(409)} other={[c for c in codes if c not in (200, 409)]}")
    assert all(c in (200, 409) for c in codes), codes
    assert ok == 10, f"expected exactly 10 accepted refunds of 10 on principal 100, got {ok}"
    orig = mongo.transactions.find_one({"tx_id": tx_id})
    assert round(orig.get("refunded_cc", 0), 2) == 100.0
    assert round(sum(r["amount"] for r in mongo.refunds.find({"original_tx_id": tx_id})), 2) == 100.0
    assert abs(wallet_balance(api, spayer["token"]) - (b0 + 100.0)) < 1e-6
    _assert_balanced(api, sadmin["token"])


@pytest.mark.parametrize("round_no", [1, 2, 3])
def test_stress_concurrent_reversals_single_winner(api, sadmin, spayer, mongo, round_no):
    tx_id = _outflow_tx(api, spayer, 70)
    b0 = wallet_balance(api, spayer["token"])
    payloads = [{"original_tx_id": tx_id, "reason": f"TEST stress rev {round_no}-{i}"} for i in range(16)]
    codes = _fanout(f"{BASE_URL}/api/reversals", payloads, auth(sadmin["token"]))
    print(f"rev round {round_no}: 200s={codes.count(200)} 409s={codes.count(409)}")
    assert all(c in (200, 409) for c in codes), codes
    assert codes.count(200) == 1, codes
    assert mongo.reversals.count_documents({"original_tx_id": tx_id}) == 1
    assert abs(wallet_balance(api, spayer["token"]) - (b0 + 70)) < 1e-6
    _assert_balanced(api, sadmin["token"])


def test_stress_mixed_refunds_and_reversals(api, sadmin, spayer, mongo):
    """8 refunds of 25 + 8 reversals on one 100 tx: either 1 reversal wins (nothing else),
    or only refunds win (total <= 100). Never both."""
    tx_id = _outflow_tx(api, spayer, 100)
    b0 = wallet_balance(api, spayer["token"])

    def call(item):
        kind, payload = item
        try:
            r = requests.post(f"{BASE_URL}/api/{kind}", json=payload,
                              headers=auth(sadmin["token"]), timeout=120)
            return kind, r.status_code
        except Exception as e:  # noqa
            return kind, 0

    items = [("refunds", {"original_tx_id": tx_id, "amount": 25, "reason": "TEST mix"}) for _ in range(8)]
    items += [("reversals", {"original_tx_id": tx_id, "reason": "TEST mix"}) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(call, items))
    rf_ok = sum(1 for k, c in res if k == "refunds" and c == 200)
    rv_ok = sum(1 for k, c in res if k == "reversals" and c == 200)
    print(f"mixed: refunds_ok={rf_ok} reversals_ok={rv_ok} codes={res}")
    assert all(c in (200, 409) for _, c in res), res
    assert rv_ok <= 1, "multiple reversals applied"
    assert not (rf_ok and rv_ok), "refund AND reversal both applied to the same tx"
    expected_credit = 70.0 if rv_ok else rf_ok * 25.0
    if rv_ok:
        expected_credit = 100.0
    assert rf_ok * 25.0 <= 100.0
    assert abs(wallet_balance(api, spayer["token"]) - (b0 + expected_credit)) < 1e-6
    _assert_balanced(api, sadmin["token"])
