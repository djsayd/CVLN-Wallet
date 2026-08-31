"""P0.1-B2 adversarial: expiry-vs-capture race + mixed chaos load.

Goal: prove held_cc can never be OVER-released (which would free funds that are still
reserved/spent = double-spend) under interleaved capture / release / lazy-expiry.
"""
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, auth, _mk_user
from test_p01b2_holds import (_cleanup_holds, reset_state, get_wallet, post_hold,
                              assert_invariants)


@pytest.fixture(scope="module")
def chaos_user(mongo):
    uid, token = _mk_user(mongo, "chaos", is_admin=True, balance=100.0)
    yield {"user_id": uid, "token": token, "mongo": mongo}
    _cleanup_holds(mongo, uid)


def capture(token, hid, amount=None):
    body = {} if amount is None else {"amount": amount}
    return requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json=body,
                         headers=auth(token), timeout=60)


def release(token, hid):
    return requests.post(f"{BASE_URL}/api/holds/{hid}/release", headers=auth(token), timeout=60)


def sum_remaining_active(mongo, uid):
    total = 0.0
    for h in mongo.balance_holds.find({"user_id": uid,
                                       "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}},
                                      {"_id": 0, "amount": 1, "captured": 1}):
        total += h["amount"] - h.get("captured", 0.0)
    return round(total, 2)


class TestExpiryCaptureRace:
    @pytest.mark.parametrize("attempt", range(6))
    def test_capture_at_expiry_boundary(self, chaos_user, attempt):
        uid, token, mongo = chaos_user["user_id"], chaos_user["token"], chaos_user["mongo"]
        reset_state(mongo, uid, 100.0)
        h1 = post_hold(token, 50, "race", ttl=2)
        assert h1.status_code == 200, h1.text[:200]
        hid = h1.json()["hold_id"]
        h2 = post_hold(token, 50, "guard")  # long-lived guard hold, must stay reserved
        assert h2.status_code == 200, h2.text[:200]
        hid2 = h2.json()["hold_id"]

        time.sleep(1.95)
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(capture, token, hid, 20),
                    ex.submit(get_wallet, token),
                    ex.submit(release, token, hid)]
            res = [f.result() for f in futs]
        cap_code = res[0].status_code
        time.sleep(0.5)
        w = assert_invariants(token)
        expected_held = sum_remaining_active(mongo, uid)
        assert w["held_cc"] == pytest.approx(expected_held, abs=0.01), (
            f"held_cc drift after expiry/capture race: held={w['held_cc']} expected={expected_held} "
            f"capture_code={cap_code}")
        # the guard hold must still be reserving its 50
        g = mongo.balance_holds.find_one({"hold_id": hid2}, {"_id": 0, "status": 1})
        assert g["status"] == "ACTIVE", g
        assert w["held_cc"] >= 50.0 - 1e-6, f"guard hold funds were freed! {w}"
        # balance debited only if capture succeeded
        expected_balance = 80.0 if cap_code == 200 else 100.0
        assert w["balance_cc"] == pytest.approx(expected_balance, abs=0.01), (
            f"balance {w['balance_cc']} inconsistent with capture_code={cap_code}")
        rep = requests.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(token), timeout=60).json()
        mine = [m for m in rep["held_mismatch"] if m["user_id"] == uid]
        assert not mine, f"integrity mismatch for test user: {mine}"
        assert not [m for m in rep["negative_held"] if m["user_id"] == uid], rep["negative_held"]
        assert not [m for m in rep["over_reserved"] if m["user_id"] == uid], rep["over_reserved"]


class TestChaosLoad:
    def test_mixed_concurrent_operations_preserve_invariants(self, chaos_user):
        uid, token, mongo = chaos_user["user_id"], chaos_user["token"], chaos_user["mongo"]
        reset_state(mongo, uid, 100.0)
        rnd = random.Random(1234)
        hold_ids = []

        def create(amount, ttl, idem):
            r = post_hold(token, amount, "chaos", ttl, idem)
            if r.status_code == 200:
                hold_ids.append(r.json()["hold_id"])
            return ("create", r.status_code)

        def do_capture():
            if not hold_ids:
                return ("capture", "skip")
            hid = rnd.choice(hold_ids)
            return ("capture", capture(token, hid, 5).status_code)

        def do_release():
            if not hold_ids:
                return ("release", "skip")
            hid = rnd.choice(hold_ids)
            return ("release", release(token, hid).status_code)

        ops = []
        for i in range(12):
            ops.append(lambda i=i: create(rnd.choice([5, 10, 20]), rnd.choice([None, 1, 2]),
                                          rnd.choice([None, f"TEST_chaos_{uuid.uuid4().hex[:8]}"])))
        ops += [do_capture for _ in range(8)] + [do_release for _ in range(6)]
        ops += [lambda: ("wallet", get_wallet(token)["available_balance_cc"]) for _ in range(4)]
        rnd.shuffle(ops)

        with ThreadPoolExecutor(max_workers=10) as ex:
            results = [f.result() for f in [ex.submit(o) for o in ops]]
        codes = {}
        for name, code in results:
            codes[f"{name}:{code}"] = codes.get(f"{name}:{code}", 0) + 1
        print(f"chaos results: {codes}")
        for name, code in results:
            assert code not in (500, 502, 503), f"server error on {name}: {code}"

        time.sleep(2.5)  # let all TTLs lapse
        w = assert_invariants(token)
        expected = sum_remaining_active(mongo, uid)
        assert w["held_cc"] == pytest.approx(expected, abs=0.01), (
            f"held_cc {w['held_cc']} != sum remaining {expected}")
        assert w["balance_cc"] >= 0, w
        captured_total = sum(h.get("captured", 0.0) for h in
                             mongo.balance_holds.find({"user_id": uid}, {"_id": 0, "captured": 1}))
        assert w["balance_cc"] == pytest.approx(round(100.0 - captured_total, 2), abs=0.01), (
            f"balance {w['balance_cc']} vs captured {captured_total}")
        rep = requests.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(token), timeout=60).json()
        assert not [m for m in rep["held_mismatch"] if m["user_id"] == uid], rep["held_mismatch"]
        assert not [m for m in rep["negative_held"] if m["user_id"] == uid], rep["negative_held"]
        assert not [m for m in rep["over_reserved"] if m["user_id"] == uid], rep["over_reserved"]
        fh = requests.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(token), timeout=60).json()
        assert fh["ledger_balanced"] is True, fh
        mongo.idempotency_records.delete_many({"user_id": uid})


class TestLazyExpiryIntegrityFalsePositive:
    """Lazy-expiry is the documented design, but holds_integrity_report() does NOT reconcile
    before comparing: an expired hold that nobody has read yet is reported as held_mismatch +
    expired_still_active -> healthy=false -> financial-health severity CRITICAL, even though the
    funds are effectively free and the system self-heals on the next read."""

    def test_untouched_expired_hold_flags_global_health(self, mongo):
        uid, token = _mk_user(mongo, "lazyfp", is_admin=True, balance=100.0)
        try:
            r = post_hold(token, 40, "fp", ttl=1)
            assert r.status_code == 200, r.text[:200]
            time.sleep(2.5)
            rep = requests.get(f"{BASE_URL}/api/admin/holds/integrity",
                               headers=auth(token), timeout=60).json()
            mine_mm = [m for m in rep["held_mismatch"] if m["user_id"] == uid]
            mine_exp = [m for m in rep["expired_still_active"] if m["user_id"] == uid]
            print(f"untouched expired hold -> healthy={rep['healthy']} mismatch={mine_mm} expired={mine_exp}")
            fh = requests.get(f"{BASE_URL}/api/admin/financial-health",
                              headers=auth(token), timeout=60).json()
            print(f"financial-health severity={fh['severity']} holds_healthy={fh['holds_health']['healthy']}")
            if mine_mm or mine_exp:
                pytest.fail(
                    "FALSE POSITIVE: an expired-but-unreconciled hold makes the GLOBAL holds "
                    f"integrity report unhealthy (severity={fh['severity']}). mismatch={mine_mm} "
                    f"expired_still_active={mine_exp}. Integrity/financial-health should reconcile "
                    "expired holds (or treat lazy-expiry-pending as effective) before reporting.")
        finally:
            _cleanup_holds(mongo, uid)
