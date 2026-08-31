"""P0.1-B2: Transaction state machine + holds/reservations + available balance.

Focus: TRUE concurrency (ThreadPoolExecutor -> real parallel HTTP) anti-double-spend,
idempotency under concurrency, partial capture, release idempotency, lazy expiry,
illegal transitions, integrity engine + rebuild, capability honesty.

Run this file alone: `pytest tests/test_p01b2_holds.py -v` (integrity endpoints are GLOBAL).
"""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, auth, _mk_user, _cleanup


# ---------- helpers / fixtures ----------
def _cleanup_holds(mongo, uid):
    hold_ids = [h["hold_id"] for h in mongo.balance_holds.find({"user_id": uid}, {"hold_id": 1})]
    mongo.balance_holds.delete_many({"user_id": uid})
    if hold_ids:
        mongo.financial_state_history.delete_many({"entity_type": "hold", "entity_id": {"$in": hold_ids}})
    mongo.audit_logs.delete_many({"user_id": uid})
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def holds_user(mongo):
    """Admin-capable user with a controlled balance of 100 CC (ledger-backed)."""
    uid, token = _mk_user(mongo, "holds", is_admin=True, balance=100.0)
    yield {"user_id": uid, "token": token, "mongo": mongo}
    _cleanup_holds(mongo, uid)


def reset_state(mongo, uid, balance=100.0):
    """Wipe holds for the user and reset the held cache + balance to a known state."""
    hold_ids = [h["hold_id"] for h in mongo.balance_holds.find({"user_id": uid}, {"hold_id": 1})]
    mongo.balance_holds.delete_many({"user_id": uid})
    if hold_ids:
        mongo.financial_state_history.delete_many({"entity_type": "hold", "entity_id": {"$in": hold_ids}})
    mongo.users.update_one({"user_id": uid}, {"$set": {"held_cc": 0.0, "balance_cc": balance}})
    # keep the ledger consistent with the reset cache
    mongo.ledger_entries.delete_many({"postings.account_id": f"acct_cash_{uid}"})
    mongo.transactions.delete_many({"user_id": uid})
    mongo.ledger_entries.insert_one({
        "entry_id": f"le_TEST_{uuid.uuid4().hex[:10]}",
        "idempotency_key": f"TEST_reset_{uuid.uuid4().hex[:8]}",
        "description": "TEST reset funding", "category": "Reward", "asset": "JCC", "ref": None,
        "postings": [{"account_id": f"acct_cash_{uid}", "amount": balance},
                     {"account_id": "acct_sys_issuance", "amount": -balance}],
        "created_at": "2026-01-01T00:00:00+00:00",
    })


def get_wallet(token):
    r = requests.get(f"{BASE_URL}/api/wallet", headers=auth(token), timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def post_hold(token, amount, reason="TEST", ttl=None, idem=None):
    body = {"amount": amount, "reason": reason}
    if ttl is not None:
        body["ttl_seconds"] = ttl
    extra = {"Idempotency-Key": idem} if idem else None
    return requests.post(f"{BASE_URL}/api/holds", json=body, headers=auth(token, extra), timeout=60)


def assert_invariants(token):
    w = get_wallet(token)
    assert w["available_balance_cc"] >= 0, f"NEGATIVE AVAILABLE: {w}"
    assert w["held_cc"] <= w["balance_cc"] + 1e-6, f"held > balance: {w}"
    return w


# ---------- available balance ----------
class TestAvailableBalance:
    def test_wallet_exposes_available(self, holds_user):
        reset_state(holds_user["mongo"], holds_user["user_id"], 100.0)
        w = get_wallet(holds_user["token"])
        for k in ("balance_cc", "held_cc", "available_balance_cc"):
            assert k in w, f"missing {k} in {w}"
        assert w["balance_cc"] == 100.0
        assert w["held_cc"] == 0
        assert w["available_balance_cc"] == 100.0

    def test_create_hold_reserves_atomically(self, holds_user):
        reset_state(holds_user["mongo"], holds_user["user_id"], 100.0)
        r = post_hold(holds_user["token"], 30)
        assert r.status_code == 200, r.text[:300]
        h = r.json()
        assert h["status"] == "ACTIVE"
        assert h["amount"] == 30.0
        assert h["captured"] == 0.0
        assert "_id" not in h
        w = assert_invariants(holds_user["token"])
        assert w["held_cc"] == 30.0
        assert w["available_balance_cc"] == 70.0

    def test_hold_over_available_rejected(self, holds_user):
        reset_state(holds_user["mongo"], holds_user["user_id"], 100.0)
        assert post_hold(holds_user["token"], 60).status_code == 200
        r = post_hold(holds_user["token"], 60)
        assert r.status_code == 400, r.text[:300]
        assert "INSUFFICIENT_AVAILABLE_FUNDS" in r.text
        assert get_wallet(holds_user["token"])["held_cc"] == 60.0

    def test_invalid_amount_rejected(self, holds_user):
        r = post_hold(holds_user["token"], 0)
        assert r.status_code == 400, r.text[:300]


# ---------- CRITICAL: anti double-spend under TRUE concurrency ----------
class TestConcurrencyDoubleSpend:
    def test_10_concurrent_holds_of_20_on_balance_100(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(post_hold, token, 20, f"conc{i}") for i in range(10)]
            results = [f.result() for f in futs]
        codes = [r.status_code for r in results]
        ok = [r for r in results if r.status_code == 200]
        rejected = [r for r in results if r.status_code == 400]
        print(f"10x20 concurrent -> codes={codes}")
        assert len(ok) <= 5, f"MORE THAN 5 HOLDS ACCEPTED (double-spend): {codes}"
        assert len(ok) >= 1, f"no hold accepted: {[r.text[:120] for r in results]}"
        assert len(ok) + len(rejected) == 10, f"unexpected status codes: {codes}"
        for r in rejected:
            assert "INSUFFICIENT_AVAILABLE_FUNDS" in r.text, r.text[:200]
        w = assert_invariants(token)
        assert w["held_cc"] == pytest.approx(20.0 * len(ok)), w
        assert w["held_cc"] <= 100.0
        # rows must match the cache
        active = holds_user["mongo"].balance_holds.count_documents({"user_id": uid, "status": "ACTIVE"})
        assert active == len(ok), f"hold rows {active} != accepted {len(ok)}"

    def test_2_concurrent_holds_of_80_exactly_one_wins(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        with ThreadPoolExecutor(max_workers=2) as ex:
            results = [f.result() for f in [ex.submit(post_hold, token, 80, f"c80_{i}") for i in range(2)]]
        ok = [r for r in results if r.status_code == 200]
        assert len(ok) == 1, f"expected exactly 1 winner, got {[r.status_code for r in results]}"
        assert results[0].status_code != results[1].status_code
        w = assert_invariants(token)
        assert w["held_cc"] == 80.0
        assert w["available_balance_cc"] == 20.0

    def test_concurrent_release_no_double_credit(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        hid = post_hold(token, 50).json()["hold_id"]

        def rel():
            return requests.post(f"{BASE_URL}/api/holds/{hid}/release", headers=auth(token), timeout=60)

        with ThreadPoolExecutor(max_workers=5) as ex:
            results = [f.result() for f in [ex.submit(rel) for _ in range(5)]]
        assert all(r.status_code == 200 for r in results), [r.status_code for r in results]
        w = assert_invariants(token)
        assert w["held_cc"] == 0.0, f"double credit / negative held: {w}"
        assert w["available_balance_cc"] == 100.0

    def test_concurrent_capture_never_exceeds_hold(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        hid = post_hold(token, 50).json()["hold_id"]

        def cap(amount):
            return requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": amount},
                                 headers=auth(token), timeout=60)

        with ThreadPoolExecutor(max_workers=5) as ex:
            results = [f.result() for f in [ex.submit(cap, 30) for _ in range(5)]]
        ok = [r for r in results if r.status_code == 200]
        print(f"concurrent capture 5x30 on hold 50 -> {[r.status_code for r in results]}")
        assert len(ok) == 1, f"over-capture: {len(ok)} captures of 30 succeeded on a 50 hold"
        h = holds_user["mongo"].balance_holds.find_one({"hold_id": hid}, {"_id": 0})
        assert h["captured"] <= h["amount"] + 1e-6, h
        w = assert_invariants(token)
        assert w["balance_cc"] == 70.0, f"balance should be debited exactly once: {w}"


# ---------- idempotency under concurrency ----------
class TestIdempotency:
    def test_same_key_concurrent_single_reservation(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        key = f"TEST_idem_{uuid.uuid4().hex[:10]}"
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = [f.result() for f in
                       [ex.submit(post_hold, token, 25, "idem", None, key) for _ in range(5)]]
        codes = [r.status_code for r in results]
        ok = [r for r in results if r.status_code == 200]
        print(f"5x same idem key -> {codes}")
        hold_ids = {r.json()["hold_id"] for r in ok}
        assert len(hold_ids) == 1, f"multiple logical reservations created: {hold_ids}"
        for r in results:
            assert r.status_code in (200, 409), r.text[:200]
        w = assert_invariants(token)
        assert w["held_cc"] == 25.0, f"held must reflect ONE hold: {w}"
        rows = holds_user["mongo"].balance_holds.count_documents({"user_id": uid, "status": "ACTIVE"})
        assert rows == 1, f"{rows} hold rows for one idempotency key"
        # A later replay of the same key returns the same hold, no new reservation
        replay = post_hold(token, 25, "idem", None, key)
        assert replay.status_code == 200, replay.text[:200]
        assert replay.json()["hold_id"] == list(hold_ids)[0]
        assert get_wallet(token)["held_cc"] == 25.0
        holds_user["mongo"].idempotency_records.delete_many({"user_id": uid})

    def test_same_key_different_payload_conflict(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        key = f"TEST_idem_{uuid.uuid4().hex[:10]}"
        r1 = post_hold(token, 10, "a", None, key)
        assert r1.status_code == 200, r1.text[:200]
        r2 = post_hold(token, 40, "b", None, key)
        assert r2.status_code == 409, r2.text[:200]
        assert "IDEMPOTENCY_CONFLICT" in r2.text
        assert get_wallet(token)["held_cc"] == 10.0
        holds_user["mongo"].idempotency_records.delete_many({"user_id": uid})

    def test_failed_request_does_not_poison_key(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        key = f"TEST_idem_{uuid.uuid4().hex[:10]}"
        bad = post_hold(token, 500, "toobig", None, key)
        assert bad.status_code == 400, bad.text[:200]
        again = post_hold(token, 500, "toobig", None, key)
        assert again.status_code == 400, f"key poisoned after failure: {again.status_code} {again.text[:200]}"
        holds_user["mongo"].idempotency_records.delete_many({"user_id": uid})


# ---------- capture state machine ----------
class TestCaptureStateMachine:
    def test_partial_then_full_capture(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        hid = post_hold(token, 100, "cap").json()["hold_id"]

        r = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 40},
                          headers=auth(token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["status"] == "PARTIALLY_CAPTURED"
        assert d["captured"] == 40.0 and d["remaining"] == 60.0
        w = assert_invariants(token)
        assert w["balance_cc"] == 60.0, w
        assert w["held_cc"] == 60.0, w
        assert w["available_balance_cc"] == 0.0, w

        # over-capture on remaining 60
        over = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 61},
                             headers=auth(token), timeout=30)
        assert over.status_code == 409, over.text[:200]
        assert get_wallet(token)["held_cc"] == 60.0

        r2 = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 60},
                           headers=auth(token), timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json()["status"] == "CAPTURED"
        assert r2.json()["remaining"] == 0.0
        w = assert_invariants(token)
        assert w["balance_cc"] == 0.0 and w["held_cc"] == 0.0, w

        # re-capture on terminal state
        r3 = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 10},
                           headers=auth(token), timeout=30)
        assert r3.status_code == 409, r3.text[:200]

        hist = requests.get(f"{BASE_URL}/api/holds/{hid}/history", headers=auth(token), timeout=30)
        assert hist.status_code == 200
        states = [(h["previous_state"], h["new_state"]) for h in hist.json()]
        print(f"history: {states}")
        assert (None, "ACTIVE") in states
        assert ("ACTIVE", "PARTIALLY_CAPTURED") in states
        assert ("PARTIALLY_CAPTURED", "CAPTURED") in states

    def test_release_then_capture_and_double_release(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        hid = post_hold(token, 70, "rel").json()["hold_id"]
        assert get_wallet(token)["available_balance_cc"] == 30.0

        r = requests.post(f"{BASE_URL}/api/holds/{hid}/release", headers=auth(token), timeout=30)
        assert r.status_code == 200 and r.json()["status"] == "RELEASED", r.text[:200]
        w = assert_invariants(token)
        assert w["held_cc"] == 0.0 and w["available_balance_cc"] == 100.0

        r2 = requests.post(f"{BASE_URL}/api/holds/{hid}/release", headers=auth(token), timeout=30)
        assert r2.status_code == 200, r2.text[:200]
        assert r2.json().get("idempotent") is True
        assert r2.json()["status"] == "RELEASED"
        assert get_wallet(token)["held_cc"] == 0.0

        cap = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 10},
                            headers=auth(token), timeout=30)
        assert cap.status_code == 409, cap.text[:200]
        w = assert_invariants(token)
        assert w["balance_cc"] == 100.0, f"balance changed on illegal capture: {w}"

    def test_unknown_hold_404(self, holds_user):
        token = holds_user["token"]
        r = requests.post(f"{BASE_URL}/api/holds/hold_nope/capture", json={"amount": 1},
                          headers=auth(token), timeout=30)
        assert r.status_code == 404, r.text[:200]
        r2 = requests.post(f"{BASE_URL}/api/holds/hold_nope/release", headers=auth(token), timeout=30)
        assert r2.status_code == 404, r2.text[:200]

    def test_cross_user_hold_isolation(self, holds_user, mongo):
        uid2, tok2 = _mk_user(mongo, "holds2", is_admin=False, balance=50.0)
        try:
            reset_state(mongo, holds_user["user_id"], 100.0)
            hid = post_hold(holds_user["token"], 20).json()["hold_id"]
            r = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 5},
                              headers=auth(tok2), timeout=30)
            assert r.status_code == 404, f"cross-user capture allowed: {r.status_code}"
            r2 = requests.post(f"{BASE_URL}/api/holds/{hid}/release", headers=auth(tok2), timeout=30)
            assert r2.status_code == 404, f"cross-user release allowed: {r2.status_code}"
        finally:
            _cleanup_holds(mongo, uid2)


# ---------- lazy expiry ----------
class TestLazyExpiry:
    def test_ttl_expiry_without_worker(self, holds_user):
        import time
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        r = post_hold(token, 40, "ttl", ttl=1)
        assert r.status_code == 200, r.text[:300]
        hid = r.json()["hold_id"]
        w = get_wallet(token)
        assert w["available_balance_cc"] == 60.0, w
        time.sleep(2.5)
        w2 = assert_invariants(token)
        assert w2["held_cc"] == 0.0, f"expired hold still holding funds: {w2}"
        assert w2["available_balance_cc"] == 100.0, w2
        h = holds_user["mongo"].balance_holds.find_one({"hold_id": hid}, {"_id": 0, "status": 1})
        assert h["status"] == "EXPIRED", h
        cap = requests.post(f"{BASE_URL}/api/holds/{hid}/capture", json={"amount": 10},
                            headers=auth(token), timeout=30)
        assert cap.status_code == 409, cap.text[:200]
        hist = requests.get(f"{BASE_URL}/api/holds/{hid}/history", headers=auth(token), timeout=30).json()
        assert any(x["new_state"] == "EXPIRED" for x in hist), hist

    def test_expired_funds_immediately_reusable(self, holds_user):
        import time
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        assert post_hold(token, 100, "ttl2", ttl=1).status_code == 200
        time.sleep(2.0)
        r = post_hold(token, 100, "after-expiry")
        assert r.status_code == 200, f"expired funds not reusable: {r.status_code} {r.text[:200]}"
        w = assert_invariants(token)
        assert w["held_cc"] == 100.0, w


# ---------- integrity engine ----------
class TestIntegrity:
    def test_holds_integrity_healthy(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        post_hold(token, 30)
        post_hold(token, 20)
        r = requests.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(token), timeout=60)
        assert r.status_code == 200, r.text[:300]
        rep = r.json()
        print(f"holds integrity: {rep}")
        assert rep["healthy"] is True, rep

    def test_financial_health(self, holds_user):
        token = holds_user["token"]
        r = requests.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(token), timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        print(f"financial health: { {k: v for k, v in d.items() if k != 'holds_health'} }")
        assert d["holds_health"]["healthy"] is True, d["holds_health"]
        assert d["ledger_balanced"] is True, d
        assert d["jcc_supply_reconciled"] is True, d
        assert d["severity"] == "INFO", d

    def test_rebuild_repairs_drifted_cache(self, holds_user):
        uid, token = holds_user["user_id"], holds_user["token"]
        reset_state(holds_user["mongo"], uid, 100.0)
        post_hold(token, 40)
        # inject drift directly in the cache
        holds_user["mongo"].users.update_one({"user_id": uid}, {"$set": {"held_cc": 95.0}})
        bad = requests.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(token), timeout=60).json()
        assert bad["healthy"] is False, f"integrity engine did not detect drift: {bad}"
        rb = requests.post(f"{BASE_URL}/api/admin/holds/rebuild", headers=auth(token), timeout=60)
        assert rb.status_code == 200, rb.text[:300]
        w = assert_invariants(token)
        assert w["held_cc"] == 40.0, f"rebuild did not recompute held_cc: {w}"
        good = requests.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(token), timeout=60).json()
        assert good["healthy"] is True, good

    def test_admin_endpoints_require_admin(self, mongo):
        uid, tok = _mk_user(mongo, "nonadmin", is_admin=False, balance=10.0)
        try:
            for path, method in [("/api/admin/holds/integrity", "get"), ("/api/admin/holds/rebuild", "post")]:
                r = getattr(requests, method)(f"{BASE_URL}{path}", headers=auth(tok), timeout=30)
                assert r.status_code == 403, f"{path} -> {r.status_code}"
        finally:
            _cleanup_holds(mongo, uid)


# ---------- capability honesty ----------
class TestSystemStatus:
    def test_requires_auth(self):
        assert requests.get(f"{BASE_URL}/api/system/status", timeout=30).status_code in (401, 403)

    def test_capabilities_honest(self, holds_user):
        r = requests.get(f"{BASE_URL}/api/system/status", headers=auth(holds_user["token"]), timeout=30)
        assert r.status_code == 200, r.text[:300]
        caps = r.json()["capabilities"]
        expected = {"state_machines": "REAL", "holds": "REAL", "idempotency_api": "REAL",
                    "refund_engine": "PLANNED", "settlement_engine": "PARTIAL",
                    "payments_deposit_stripe": "SANDBOX", "card_issuing": "MOCK"}
        for k, v in expected.items():
            assert caps.get(k) == v, f"{k} = {caps.get(k)}, expected {v}"
