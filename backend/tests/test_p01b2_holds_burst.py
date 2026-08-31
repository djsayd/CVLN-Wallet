"""Focused burst: capture racing lazy-expiry reconciliation at the exact TTL boundary.

Checks that a capture and a concurrent expiry-reconcile can never both release the same
reserved amount from held_cc (over-release => other holds' funds freed => double-spend).
"""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, auth, _mk_user
from test_p01b2_holds import _cleanup_holds, reset_state, get_wallet, post_hold
from test_p01b2_holds_race import capture, sum_remaining_active


@pytest.fixture(scope="module")
def burst_user(mongo):
    uid, token = _mk_user(mongo, "burst", is_admin=True, balance=100.0)
    yield {"user_id": uid, "token": token, "mongo": mongo}
    _cleanup_holds(mongo, uid)


def test_capture_vs_expiry_burst(burst_user):
    uid, token, mongo = burst_user["user_id"], burst_user["token"], burst_user["mongo"]
    outcomes = []
    for i in range(8):
        reset_state(mongo, uid, 100.0)
        r = post_hold(token, 60, f"burst{i}", ttl=1)
        assert r.status_code == 200, r.text[:200]
        hid = r.json()["hold_id"]
        # sleep so that the hold expires ~while the capture request is in flight
        time.sleep(0.95)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_cap = ex.submit(capture, token, hid, 60)
            f_w = ex.submit(get_wallet, token)
            cap = f_cap.result()
            f_w.result()
        time.sleep(0.4)
        w = get_wallet(token)
        h = mongo.balance_holds.find_one({"hold_id": hid}, {"_id": 0, "status": 1, "captured": 1})
        expected_held = sum_remaining_active(mongo, uid)
        expected_balance = round(100.0 - h.get("captured", 0.0), 2)
        outcomes.append((cap.status_code, h["status"], w["held_cc"], w["balance_cc"]))
        assert w["available_balance_cc"] >= 0, w
        assert w["held_cc"] >= -1e-6, w
        assert w["held_cc"] == pytest.approx(expected_held, abs=0.01), (
            f"iter {i}: held_cc={w['held_cc']} != sum remaining {expected_held} (cap={cap.status_code}, {h})")
        assert w["balance_cc"] == pytest.approx(expected_balance, abs=0.01), (
            f"iter {i}: balance={w['balance_cc']} vs captured={h.get('captured')} (cap={cap.status_code})")
    print(f"burst outcomes (cap_code, status, held, balance): {outcomes}")
    assert any(o[0] == 200 for o in outcomes) or any(o[0] == 409 for o in outcomes)
    rep = requests.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(token), timeout=60).json()
    assert not [m for m in rep["negative_held"] if m["user_id"] == uid], rep["negative_held"]
    assert not [m for m in rep["over_reserved"] if m["user_id"] == uid], rep["over_reserved"]
