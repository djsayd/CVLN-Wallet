# P0.1-B4 (Settlement / Webhook inbox / Reconciliation / Outbox)
# P0.1-B5 (Asset registry / Monetary precision / Maker-Checker / Recovery)
# Independent re-verification with TRUE concurrency (threads + barrier).
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth

TIMEOUT = 45


# ---------------- fixtures ----------------
@pytest.fixture(scope="module")
def maker(mongo):
    uid, tok = _mk_user(mongo, "b45maker", is_admin=True, balance=1000.0)
    yield {"user_id": uid, "token": tok}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def checker(mongo):
    uid, tok = _mk_user(mongo, "b45checker", is_admin=True, balance=0.0)
    yield {"user_id": uid, "token": tok}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def payer(mongo):
    uid, tok = _mk_user(mongo, "b45payer", is_admin=False, balance=2000.0)
    yield {"user_id": uid, "token": tok}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module", autouse=True)
def zero_fees(api, maker):
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}}, headers=auth(maker["token"]), timeout=TIMEOUT)
    yield
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}}, headers=auth(maker["token"]), timeout=TIMEOUT)


@pytest.fixture(scope="module")
def tracker(mongo):
    """Track B4/B5 docs created by this module so financial-health stays clean."""
    t = {"settlements": [], "cases": [], "approvals": [], "webhooks": []}
    yield t
    if t["settlements"]:
        mongo.settlements.delete_many({"settlement_id": {"$in": t["settlements"]}})
        mongo.financial_state_history.delete_many({"entity_type": "settlement", "entity_id": {"$in": t["settlements"]}})
    if t["cases"]:
        mongo.reconciliation_cases.delete_many({"case_id": {"$in": t["cases"]}})
    if t["approvals"]:
        mongo.approval_requests.delete_many({"approval_id": {"$in": t["approvals"]}})
    if t["webhooks"]:
        mongo.webhook_inbox.delete_many({"provider_event_id": {"$in": t["webhooks"]}})


def _make_tx(api, user_token, amount=50.0):
    r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": amount, "iban": "FR76TEST"},
                 headers=auth(user_token), timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(user_token), timeout=TIMEOUT).json()
    out = [t for t in txs if t["category"] == "Retrait" and abs(t["amount"] + amount) < 0.001]
    assert out, "no withdrawal tx found"
    return out[0]["tx_id"]


def _new_settlement(api, admin_token, tx_id, tracker, provider="mock_bank"):
    r = api.post(f"{BASE_URL}/api/admin/settlements",
                 json={"transaction_id": tx_id, "provider": provider, "direction": "payout"},
                 headers=auth(admin_token), timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    s = r.json()
    tracker["settlements"].append(s["settlement_id"])
    return s


def fire(fn, n):
    """Fire n truly-concurrent calls, synchronised with a barrier."""
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(worker, range(n)))


# ---------------- B5: asset registry + precision ----------------
class TestAssetsAndPrecision:
    def test_assets_registry(self, api, maker):
        r = api.get(f"{BASE_URL}/api/assets", headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:200]
        assets = {a["asset_code"]: a for a in r.json()}
        assert "JCC" in assets and "EUR" in assets
        for a in assets.values():
            assert a["decimals"] == 2 and a["minor_unit"] == 100
            assert a["rounding"] == "HALF_UP" and a["enabled"] is True

    def test_assets_requires_auth(self, api):
        r = api.get(f"{BASE_URL}/api/assets", timeout=TIMEOUT)
        assert r.status_code in (401, 403), r.status_code

    def test_precision_dry_run_report(self, api, maker):
        r = api.post(f"{BASE_URL}/api/admin/precision/migrate?dry_run=true",
                     headers=auth(maker["token"]), timeout=120)
        assert r.status_code == 200, r.text[:300]
        rep = r.json()
        assert rep["dry_run"] is True
        assert rep["ledger_postings_checked"] > 0
        assert rep["balances_checked"] > 0
        assert isinstance(rep["non_representable_postings"], list)
        assert "representable" in rep
        if not rep["representable"]:
            print("NON-REPRESENTABLE:", rep["non_representable_postings"][:5],
                  rep["non_representable_balances"][:5])

    def test_precision_requires_admin(self, api, payer):
        r = api.post(f"{BASE_URL}/api/admin/precision/migrate?dry_run=true",
                     headers=auth(payer["token"]), timeout=TIMEOUT)
        assert r.status_code == 403, r.status_code

    def test_minor_exactness_after_decimal_ops(self, api, mongo, maker):
        """0.1 + 0.2 style: repeated fractional debits must stay minor-exact."""
        uid, tok = _mk_user(mongo, "b45prec", balance=100.0)
        try:
            for amt in (0.1, 0.2, 0.05):
                r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": amt, "iban": "X"},
                             headers=auth(tok), timeout=TIMEOUT)
                assert r.status_code == 200, r.text[:200]
            u = mongo.users.find_one({"user_id": uid}, {"balance_cc": 1})
            bal = u["balance_cc"]
            from decimal import Decimal
            exact = (Decimal(str(bal)) * 100) == (Decimal(str(bal)) * 100).to_integral_value()
            print(f"balance after 0.1/0.2/0.05 debits = {bal!r} minor_exact={exact}")
            assert abs(bal - 99.65) < 1e-9, bal
            assert exact, f"BALANCE NOT MINOR-EXACT: {bal!r}"
        finally:
            _cleanup(mongo, uid)


# ---------------- B4: settlement lifecycle ----------------
class TestSettlementLifecycle:
    def test_create_submit_history(self, api, maker, payer, tracker):
        tx = _make_tx(api, payer["token"], 50.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        assert s["internal_status"] == "PENDING"
        assert s["amount"] == 50.0 and s["amount_minor"] == 5000
        assert s["provider_reference"] is None
        assert s["reconciliation_status"] == "UNRECONCILED"

        entries_before = api.get(f"{BASE_URL}/api/admin/financial-health",
                                 headers=auth(maker["token"]), timeout=TIMEOUT).json()["ledger_entries"]

        r = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                     headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        sub = r.json()
        assert sub["internal_status"] == "SUBMITTED"
        assert sub["provider_reference"] and sub["provider_reference"].startswith("mock_bank_ref_")
        assert sub["external_status"] == "processing"

        # Settlement must NOT re-post ledger value
        entries_after = api.get(f"{BASE_URL}/api/admin/financial-health",
                                headers=auth(maker["token"]), timeout=TIMEOUT).json()["ledger_entries"]
        assert entries_after == entries_before, "settlement re-posted ledger value!"

        g = api.get(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}",
                    headers=auth(maker["token"]), timeout=TIMEOUT)
        assert g.status_code == 200
        hist = g.json()["history"]
        assert [h["new_state"] for h in hist] == ["PENDING", "SUBMITTED"], hist

    def test_double_submit_is_409(self, api, maker, payer, tracker):
        tx = _make_tx(api, payer["token"], 12.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        r1 = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                      headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r1.status_code == 200
        r2 = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                      headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r2.status_code == 409, r2.text[:200]
        assert "INVALID_TRANSITION" in r2.text

    def test_duplicate_settlement_for_same_tx(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 9.0)
        s1 = _new_settlement(api, maker["token"], tx, tracker)
        r2 = api.post(f"{BASE_URL}/api/admin/settlements",
                      json={"transaction_id": tx, "provider": "mock_bank", "direction": "payout"},
                      headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json()["settlement_id"] == s1["settlement_id"], "duplicate settlement created for same tx"
        assert mongo.settlements.count_documents({"transaction_id": tx}) == 1
        mongo.settlements.update_one({"settlement_id": s1["settlement_id"]},
                                     {"$set": {"internal_status": "CANCELLED"}})

    def test_cross_provider_webhook_scope(self, api, maker, payer, tracker, mongo):
        """A webhook from provider B must not be able to drive provider A's settlement."""
        tx = _make_tx(api, payer["token"], 8.0)
        s = _new_settlement(api, maker["token"], tx, tracker, provider="mock_bank")
        sub = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                       headers=auth(maker["token"]), timeout=TIMEOUT).json()
        ref = sub["provider_reference"]
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"].append(ev)
        r = requests.post(f"{BASE_URL}/api/webhooks/mock_processor",
                          json={"event_id": ev, "provider_reference": ref, "status": "settled"}, timeout=TIMEOUT)
        cur = mongo.settlements.find_one({"settlement_id": s["settlement_id"]})
        print("cross-provider webhook result:", r.json(), "-> settlement", cur["internal_status"])
        assert r.status_code == 200, r.text[:200]
        assert r.json()["result"] in ("no_action", "scope_violation"), r.json()
        assert cur["internal_status"] == "SUBMITTED", (
            "SECURITY: mock_processor webhook changed a mock_bank settlement (provider not verified)")

        # ...and the CORRECT provider still works
        ev2 = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"].append(ev2)
        r2 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                           json={"event_id": ev2, "provider_reference": ref, "status": "settled"}, timeout=TIMEOUT)
        assert r2.status_code == 200 and r2.json()["result"] == "applied:SETTLED", r2.text[:200]
        cur2 = mongo.settlements.find_one({"settlement_id": s["settlement_id"]})
        assert cur2["internal_status"] == "SETTLED", cur2["internal_status"]
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"internal_status": "CANCELLED"}})

    def test_bad_tx_and_provider(self, api, maker, payer):
        r = api.post(f"{BASE_URL}/api/admin/settlements",
                     json={"transaction_id": "tx_does_not_exist", "provider": "mock_bank"},
                     headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 404, r.text[:200]
        tx = _make_tx(api, payer["token"], 5.0)
        r = api.post(f"{BASE_URL}/api/admin/settlements",
                     json={"transaction_id": tx, "provider": "nope_bank"},
                     headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 400 and "PROVIDER_UNKNOWN" in r.text
        r = api.post(f"{BASE_URL}/api/admin/settlements",
                     json={"transaction_id": tx, "provider": "mock_bank"},
                     headers=auth(payer["token"]), timeout=TIMEOUT)
        assert r.status_code == 403

    def test_get_unknown_settlement_404(self, api, maker):
        r = api.get(f"{BASE_URL}/api/admin/settlements/stl_nope", headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 404

    def test_concurrent_terminal_transition_single_winner(self, api, maker, payer, tracker, mongo):
        """N concurrent 'settled' webhooks (distinct event ids) -> exactly ONE applied."""
        for round_i in range(3):
            tx = _make_tx(api, payer["token"], 20.0 + round_i)
            s = _new_settlement(api, maker["token"], tx, tracker)
            sub = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                           headers=auth(maker["token"]), timeout=TIMEOUT).json()
            ref = sub["provider_reference"]
            n = 15
            evs = [f"TEST_ev_{uuid.uuid4().hex[:10]}" for _ in range(n)]
            tracker["webhooks"].extend(evs)

            def call(i):
                return requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                                     json={"event_id": evs[i], "provider_reference": ref, "status": "settled"},
                                     timeout=TIMEOUT)

            res = fire(call, n)
            applied = [r for r in res if r.status_code == 200 and r.json().get("result") == "applied:SETTLED"]
            ignored = [r for r in res if r.status_code == 200 and r.json().get("result") == "ignored_terminal"]
            print(f"round {round_i}: applied={len(applied)} ignored_terminal={len(ignored)} "
                  f"others={[r.json() for r in res if r.json().get('result') not in ('applied:SETTLED', 'ignored_terminal')]}")
            assert len(applied) == 1, f"terminal transition applied {len(applied)} times"
            assert len(applied) + len(ignored) == n
            hist = list(mongo.financial_state_history.find(
                {"entity_type": "settlement", "entity_id": s["settlement_id"], "new_state": "SETTLED"}))
            assert len(hist) == 1, f"{len(hist)} SETTLED history rows"
            cur = mongo.settlements.find_one({"settlement_id": s["settlement_id"]})
            assert cur["internal_status"] == "SETTLED"


# ---------------- B4: webhook inbox ----------------
class TestWebhookInbox:
    def test_missing_event_id_400(self, api):
        r = requests.post(f"{BASE_URL}/api/webhooks/mock_bank", json={"status": "settled"}, timeout=TIMEOUT)
        assert r.status_code == 400 and "MISSING_PROVIDER_EVENT_ID" in r.text

    def test_same_event_id_100x_one_effect(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 33.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        sub = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                       headers=auth(maker["token"]), timeout=TIMEOUT).json()
        ref = sub["provider_reference"]
        ev = f"TEST_ev_{uuid.uuid4().hex[:10]}"
        tracker["webhooks"].append(ev)
        body = {"event_id": ev, "provider_reference": ref, "status": "settled"}

        def call(i):
            return requests.post(f"{BASE_URL}/api/webhooks/mock_bank", json=body, timeout=TIMEOUT)

        res = fire(call, 100)
        codes = [r.status_code for r in res]
        assert set(codes) == {200}, set(codes)
        statuses = [r.json()["status"] for r in res]
        processed = statuses.count("processed")
        dupes = statuses.count("duplicate_ignored")
        print(f"100 identical webhooks -> processed={processed} duplicate_ignored={dupes}")
        assert processed == 1, f"{processed} webhooks processed (expected 1)"
        assert dupes == 99
        assert mongo.webhook_inbox.count_documents({"provider_event_id": ev}) == 1
        assert mongo.financial_state_history.count_documents(
            {"entity_type": "settlement", "entity_id": s["settlement_id"], "new_state": "SETTLED"}) == 1
        assert mongo.settlements.find_one({"settlement_id": s["settlement_id"]})["internal_status"] == "SETTLED"

    def test_out_of_order_processing_after_settled_ignored(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 41.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        sub = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                       headers=auth(maker["token"]), timeout=TIMEOUT).json()
        ref = sub["provider_reference"]
        e1, e2 = f"TEST_ev_{uuid.uuid4().hex[:8]}", f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"] += [e1, e2]
        r1 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                           json={"event_id": e1, "provider_reference": ref, "status": "settled"}, timeout=TIMEOUT)
        assert r1.json()["result"] == "applied:SETTLED"
        r2 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                           json={"event_id": e2, "provider_reference": ref, "status": "processing"}, timeout=TIMEOUT)
        assert r2.status_code == 200
        assert r2.json()["result"] == "ignored_terminal", r2.text[:200]
        cur = mongo.settlements.find_one({"settlement_id": s["settlement_id"]})
        assert cur["internal_status"] == "SETTLED", "terminal state was overwritten!"

    def test_settled_on_pending_allowed(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 17.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        # give it a provider_reference without submitting (webhook arrives before submit ack)
        ref = f"mock_bank_ref_{uuid.uuid4().hex[:10]}"
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]}, {"$set": {"provider_reference": ref}})
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"].append(ev)
        r = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                          json={"event_id": ev, "provider_reference": ref, "status": "settled"}, timeout=TIMEOUT)
        assert r.json()["result"] == "applied:SETTLED", r.text[:200]
        assert mongo.settlements.find_one({"settlement_id": s["settlement_id"]})["internal_status"] == "SETTLED"

    def test_unapplicable_on_non_terminal_goes_to_review(self, api, maker, payer, tracker, mongo):
        """PROCESSING -> 'processing' is not an allowed transition and the state is not
        terminal: must move to REQUIRES_REVIEW (auditable), never silently overridden."""
        tx = _make_tx(api, payer["token"], 19.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        sub = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                       headers=auth(maker["token"]), timeout=TIMEOUT).json()
        ref = sub["provider_reference"]
        e1, e2 = f"TEST_ev_{uuid.uuid4().hex[:8]}", f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"] += [e1, e2]
        r1 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                           json={"event_id": e1, "provider_reference": ref, "status": "processing"}, timeout=TIMEOUT)
        assert r1.json()["result"] == "applied:PROCESSING", r1.text[:200]
        r2 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                           json={"event_id": e2, "provider_reference": ref, "status": "processing"}, timeout=TIMEOUT)
        assert r2.json()["result"] == "review", r2.text[:200]
        cur = mongo.settlements.find_one({"settlement_id": s["settlement_id"]})
        assert cur["internal_status"] == "REQUIRES_REVIEW"
        # bring it back to a terminal state so financial-health stays clean
        e3 = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"].append(e3)
        r3 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                           json={"event_id": e3, "provider_reference": ref, "status": "failed"}, timeout=TIMEOUT)
        assert r3.json()["result"] == "applied:FAILED"

    def test_unknown_provider_reference_no_action(self, api, tracker):
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        tracker["webhooks"].append(ev)
        r = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                          json={"event_id": ev, "provider_reference": "nope", "status": "settled"}, timeout=TIMEOUT)
        assert r.status_code == 200 and r.json()["result"] == "no_action"


# ---------------- B4: reconciliation ----------------
class TestReconciliation:
    def test_missing_provider_reference_case(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 27.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        # SUBMITTED without provider_reference (crash between transition and provider ack)
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"internal_status": "SUBMITTED", "provider_reference": None}})
        r = api.post(f"{BASE_URL}/api/admin/reconciliation/run", headers=auth(maker["token"]), timeout=120)
        assert r.status_code == 200, r.text[:200]
        cases = api.get(f"{BASE_URL}/api/admin/reconciliation/cases",
                        headers=auth(maker["token"]), timeout=TIMEOUT).json()
        mine = [c for c in cases if c["settlement_id"] == s["settlement_id"]]
        assert mine, "no reconciliation case opened"
        for c in mine:
            tracker["cases"].append(c["case_id"])
        assert mine[0]["mismatch_type"] == "missing_provider_reference"
        assert mine[0]["status"] == "OPEN"
        # idempotent: re-run must not duplicate the open case
        api.post(f"{BASE_URL}/api/admin/reconciliation/run", headers=auth(maker["token"]), timeout=120)
        assert mongo.reconciliation_cases.count_documents(
            {"settlement_id": s["settlement_id"], "mismatch_type": "missing_provider_reference",
             "status": {"$in": ["OPEN", "INVESTIGATING"]}}) == 1
        # cleanup: put settlement in a terminal state
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"internal_status": "CANCELLED"}})

    def test_amount_mismatch_case(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 31.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"amount": 999.0, "internal_status": "PENDING"}})
        api.post(f"{BASE_URL}/api/admin/reconciliation/run", headers=auth(maker["token"]), timeout=120)
        cases = mongo.reconciliation_cases.find({"settlement_id": s["settlement_id"]})
        types = []
        for c in cases:
            tracker["cases"].append(c["case_id"])
            types.append((c["mismatch_type"], c["severity"]))
        assert ("amount_mismatch", "HIGH") in types, types
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]}, {"$set": {"internal_status": "CANCELLED"}})

    def test_concurrent_resolve_single_winner(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 22.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"internal_status": "PROCESSING", "provider_reference": None}})
        api.post(f"{BASE_URL}/api/admin/reconciliation/run", headers=auth(maker["token"]), timeout=120)
        c = mongo.reconciliation_cases.find_one({"settlement_id": s["settlement_id"], "status": "OPEN"})
        assert c, "case not opened"
        tracker["cases"].append(c["case_id"])
        cid = c["case_id"]
        n = 12

        def call(i):
            return requests.post(f"{BASE_URL}/api/admin/reconciliation/cases/{cid}/resolve",
                                 json={"resolution": "RESOLVED", "note": f"TEST {i}"},
                                 headers=auth(maker["token"]), timeout=TIMEOUT)

        res = fire(call, n)
        ok = [r for r in res if r.status_code == 200]
        conflict = [r for r in res if r.status_code == 409]
        print(f"concurrent resolve: 200={len(ok)} 409={len(conflict)} other={[r.status_code for r in res if r.status_code not in (200, 409)]}")
        assert len(ok) == 1, f"double-resolve: {len(ok)} winners"
        assert len(conflict) == n - 1
        assert mongo.reconciliation_cases.find_one({"case_id": cid})["status"] == "RESOLVED"
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]}, {"$set": {"internal_status": "CANCELLED"}})

    def test_invalid_resolution_400(self, api, maker, tracker, mongo):
        c = mongo.reconciliation_cases.find_one({"status": {"$in": ["OPEN", "INVESTIGATING"]}})
        cid = c["case_id"] if c else "rc_nope"
        r = api.post(f"{BASE_URL}/api/admin/reconciliation/cases/{cid}/resolve",
                     json={"resolution": "WHATEVER"}, headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 400, r.text[:200]

    def test_resolve_unknown_case_409(self, api, maker):
        r = api.post(f"{BASE_URL}/api/admin/reconciliation/cases/rc_nope/resolve",
                     json={"resolution": "RESOLVED"}, headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 409, r.status_code


# ---------------- B4: outbox ----------------
class TestOutbox:
    def test_events_created_and_delivered(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 44.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                 headers=auth(maker["token"]), timeout=TIMEOUT)
        sid = s["settlement_id"]
        deadline = time.time() + 30
        evs = []
        while time.time() < deadline:
            evs = list(mongo.outbox_events.find({"aggregate_id": sid}, {"_id": 0}))
            if evs and all(e["status"] == "DELIVERED" for e in evs):
                break
            time.sleep(2)
        assert evs, "no outbox events emitted for settlement"
        assert all(e["status"] == "DELIVERED" for e in evs), [(e["event_type"], e["status"]) for e in evs]
        types = {e["event_type"] for e in evs}
        assert "Financial.SettlementCreated" in types
        for e in evs:
            assert mongo.outbox_consumed.count_documents({"event_id": e["event_id"]}) == 1

    def test_replay_keeps_single_business_effect(self, api, maker, mongo):
        ev = mongo.outbox_events.find_one({"status": "DELIVERED"})
        assert ev, "no delivered event to replay"
        eid = ev["event_id"]
        for _ in range(5):
            r = api.post(f"{BASE_URL}/api/admin/outbox/{eid}/replay", headers=auth(maker["token"]), timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:200]
        deadline = time.time() + 30
        while time.time() < deadline:
            cur = mongo.outbox_events.find_one({"event_id": eid})
            if cur["status"] == "DELIVERED":
                break
            time.sleep(2)
        assert mongo.outbox_events.find_one({"event_id": eid})["status"] == "DELIVERED"
        assert mongo.outbox_consumed.count_documents({"event_id": eid}) == 1, "replay caused >1 business effect"

    def test_replay_unknown_404(self, api, maker):
        r = api.post(f"{BASE_URL}/api/admin/outbox/evt_nope/replay", headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 404

    def test_outbox_unique_index_and_listing(self, api, maker, mongo):
        idx = mongo.outbox_events.index_information()
        assert any(v.get("unique") and v["key"] == [("event_id", 1)] for v in idx.values()), idx
        idx2 = mongo.outbox_consumed.index_information()
        assert any(v.get("unique") and v["key"] == [("event_id", 1)] for v in idx2.values()), idx2
        idx3 = mongo.webhook_inbox.index_information()
        assert any(v.get("unique") and v["key"] == [("provider", 1), ("provider_event_id", 1)]
                   for v in idx3.values()), idx3
        r = api.get(f"{BASE_URL}/api/admin/outbox?status=DELIVERED", headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 200 and isinstance(r.json(), list)
        assert all(e["status"] == "DELIVERED" for e in r.json())

    def test_dead_letter_after_repeated_failure(self, api, maker, mongo):
        """Simulate an event whose delivery keeps failing (attempts exhausted)."""
        eid = f"TEST_evt_{uuid.uuid4().hex[:10]}"
        mongo.outbox_events.insert_one({
            "event_id": eid, "event_type": "TEST.Fail", "aggregate_type": "test", "aggregate_id": "TEST",
            "payload": {}, "status": "DEAD_LETTER", "attempts": 8,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "available_at": datetime.now(timezone.utc).isoformat(),
            "delivered_at": None, "last_error": "simulated"})
        try:
            scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120).json()
            assert scan["findings"]["dead_letter_outbox"] >= 1
            assert "dead_letter_outbox" in scan["classification"]["CRITICAL"]
            fh = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(maker["token"]), timeout=120).json()
            assert fh["outbox"]["dead_letter"] >= 1
            assert fh["severity"] == "HIGH", fh["severity"]
        finally:
            mongo.outbox_events.delete_one({"event_id": eid})


# ---------------- B5: maker-checker ----------------
class TestMakerChecker:
    def test_non_sensitive_op_400(self, api, maker):
        r = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "send_newsletter", "payload": {}},
                     headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 400 and "OPERATION_NOT_SENSITIVE" in r.text

    def test_requires_admin(self, api, payer):
        r = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "fee_policy_change", "payload": {}},
                     headers=auth(payer["token"]), timeout=TIMEOUT)
        assert r.status_code == 403

    def test_maker_cannot_be_checker_backend(self, api, maker, tracker):
        r = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "fee_policy_change",
                           "payload": {"fee_policy": {"withdrawal": {"pct": 0.01}}}, "reason": "TEST"},
                     headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:200]
        a = r.json()
        tracker["approvals"].append(a["approval_id"])
        assert a["status"] == "PENDING" and a["maker_id"] == maker["user_id"]
        assert a["operation_payload_hash"]
        ra = api.post(f"{BASE_URL}/api/admin/approvals/{a['approval_id']}/approve",
                      headers=auth(maker["token"]), timeout=TIMEOUT)
        assert ra.status_code == 403 and "MAKER_CANNOT_BE_CHECKER" in ra.text
        rr = api.post(f"{BASE_URL}/api/admin/approvals/{a['approval_id']}/reject",
                      headers=auth(maker["token"]), timeout=TIMEOUT)
        assert rr.status_code == 403 and "MAKER_CANNOT_BE_CHECKER" in rr.text

    def test_checker_executes_fee_policy_change(self, api, maker, checker, tracker):
        pol = {"withdrawal": {"pct": 0.03, "flat": 0.5}}
        a = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "fee_policy_change", "payload": {"fee_policy": pol},
                           "reason": "TEST fee"}, headers=auth(maker["token"]), timeout=TIMEOUT).json()
        tracker["approvals"].append(a["approval_id"])
        try:
            r = api.post(f"{BASE_URL}/api/admin/approvals/{a['approval_id']}/approve",
                         headers=auth(checker["token"]), timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:200]
            assert r.json()["execution_status"] == "EXECUTED"
            got = api.get(f"{BASE_URL}/api/admin/fees", headers=auth(maker["token"]), timeout=TIMEOUT).json()
            assert got.get("fee_policy") == pol, got
            lst = api.get(f"{BASE_URL}/api/admin/approvals", headers=auth(maker["token"]), timeout=TIMEOUT).json()
            mine = [x for x in lst if x["approval_id"] == a["approval_id"]][0]
            assert mine["status"] == "APPROVED" and mine["checker_id"] == checker["user_id"]
        finally:
            api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
                    headers=auth(maker["token"]), timeout=TIMEOUT)

    def test_concurrent_approvals_single_execution(self, api, maker, checker, tracker, mongo):
        for round_i in range(2):
            a = api.post(f"{BASE_URL}/api/admin/approvals",
                         json={"operation_type": "kill_switch_critical",
                               "payload": {"name": "agents", "enabled": False}, "reason": "TEST ks"},
                         headers=auth(maker["token"]), timeout=TIMEOUT).json()
            tracker["approvals"].append(a["approval_id"])
            aid = a["approval_id"]
            n = 20

            def call(i):
                return requests.post(f"{BASE_URL}/api/admin/approvals/{aid}/approve",
                                     headers=auth(checker["token"]), timeout=TIMEOUT)

            res = fire(call, n)
            ok = [r for r in res if r.status_code == 200]
            conflict = [r for r in res if r.status_code == 409]
            print(f"round {round_i}: 20 concurrent approvals -> 200={len(ok)} 409={len(conflict)} "
                  f"other={[(r.status_code, r.text[:80]) for r in res if r.status_code not in (200, 409)]}")
            assert len(ok) == 1, f"{len(ok)} approvals executed"
            assert len(conflict) == n - 1
            doc = mongo.approval_requests.find_one({"approval_id": aid})
            assert doc["status"] == "APPROVED" and doc["execution_status"] == "EXECUTED"
            assert mongo.outbox_events.count_documents(
                {"aggregate_id": aid, "event_type": "Financial.MakerCheckerExecuted"}) == 1

    def test_concurrent_reject_single_winner(self, api, maker, checker, tracker, mongo):
        a = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "high_value_refund", "payload": {"tx_id": "TEST"}, "reason": "TEST"},
                     headers=auth(maker["token"]), timeout=TIMEOUT).json()
        tracker["approvals"].append(a["approval_id"])
        aid = a["approval_id"]
        n = 12

        def call(i):
            return requests.post(f"{BASE_URL}/api/admin/approvals/{aid}/reject",
                                 headers=auth(checker["token"]), timeout=TIMEOUT)

        res = fire(call, n)
        ok = [r for r in res if r.status_code == 200]
        assert len(ok) == 1, f"{len(ok)} rejects succeeded"
        assert mongo.approval_requests.find_one({"approval_id": aid})["status"] == "REJECTED"
        r = api.post(f"{BASE_URL}/api/admin/approvals/{aid}/approve",
                     headers=auth(checker["token"]), timeout=TIMEOUT)
        assert r.status_code == 409, r.status_code

    def test_expired_approval_409(self, api, maker, checker, tracker, mongo):
        a = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "settlement_override", "payload": {"x": 1}, "reason": "TEST exp"},
                     headers=auth(maker["token"]), timeout=TIMEOUT).json()
        tracker["approvals"].append(a["approval_id"])
        mongo.approval_requests.update_one(
            {"approval_id": a["approval_id"]},
            {"$set": {"expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}})
        r = api.post(f"{BASE_URL}/api/admin/approvals/{a['approval_id']}/approve",
                     headers=auth(checker["token"]), timeout=TIMEOUT)
        assert r.status_code == 409 and "APPROVAL_EXPIRED" in r.text
        scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120).json()
        assert scan["findings"]["expired_approvals"] >= 1

    def test_payload_tamper_detected(self, api, maker, checker, tracker, mongo):
        a = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "fee_policy_change", "payload": {"fee_policy": {}}, "reason": "TEST t"},
                     headers=auth(maker["token"]), timeout=TIMEOUT).json()
        tracker["approvals"].append(a["approval_id"])
        mongo.approval_requests.update_one({"approval_id": a["approval_id"]},
                                           {"$set": {"payload": {"fee_policy": {"withdrawal": {"pct": 0.9}}}}})
        r = api.post(f"{BASE_URL}/api/admin/approvals/{a['approval_id']}/approve",
                     headers=auth(checker["token"]), timeout=TIMEOUT)
        assert r.status_code == 409 and "PAYLOAD_TAMPERED" in r.text, r.text[:200]
        got = api.get(f"{BASE_URL}/api/admin/fees", headers=auth(maker["token"]), timeout=TIMEOUT).json()
        assert got.get("fee_policy") in ({}, None), got

    def test_approve_unknown_404(self, api, checker):
        r = api.post(f"{BASE_URL}/api/admin/approvals/apr_nope/approve",
                     headers=auth(checker["token"]), timeout=TIMEOUT)
        assert r.status_code == 404

    def test_manual_ledger_adjustment_goes_through_ledger(self, api, maker, checker, tracker, mongo):
        uid, tok = _mk_user(mongo, "b45adj", balance=100.0)
        try:
            fh_before = api.get(f"{BASE_URL}/api/admin/financial-health",
                                headers=auth(maker["token"]), timeout=120).json()
            assert fh_before["ledger_balanced"] is True
            a = api.post(f"{BASE_URL}/api/admin/approvals",
                         json={"operation_type": "manual_ledger_adjustment",
                               "payload": {"account_id": f"acct_cash_{uid}", "amount": 25.0},
                               "reason": "TEST adj"}, headers=auth(maker["token"]), timeout=TIMEOUT).json()
            tracker["approvals"].append(a["approval_id"])
            r = api.post(f"{BASE_URL}/api/admin/approvals/{a['approval_id']}/approve",
                         headers=auth(checker["token"]), timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:300]
            entry = mongo.ledger_entries.find_one({"ref": a["approval_id"]})
            assert entry, "no ledger entry for manual_ledger_adjustment"
            assert len(entry["postings"]) == 2
            assert abs(sum(p["amount"] for p in entry["postings"])) < 1e-9, entry["postings"]
            accts = {p["account_id"]: p["amount"] for p in entry["postings"]}
            assert accts[f"acct_cash_{uid}"] == 25.0
            assert accts["acct_sys_clearing"] == -25.0
            w = api.get(f"{BASE_URL}/api/wallet", headers=auth(tok), timeout=TIMEOUT).json()
            assert w["balance_cc"] == 125.0, w
            fh = api.get(f"{BASE_URL}/api/admin/financial-health",
                         headers=auth(maker["token"]), timeout=120).json()
            assert fh["ledger_balanced"] is True
            assert fh["jcc_supply_reconciled"] is True, fh["per_asset_sum"]
        finally:
            mongo.ledger_entries.delete_many({"ref": a["approval_id"]})
            _cleanup(mongo, uid)


# ---------------- B5: recovery + crash windows ----------------
class TestRecovery:
    def test_scan_shape_and_journal(self, api, maker):
        r = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        for k in ("stale_idempotency", "expired_active_holds", "stuck_settlements", "undelivered_outbox",
                  "dead_letter_outbox", "unprocessed_inbox", "expired_approvals"):
            assert k in body["findings"], k
        for k in ("AUTO_RECOVERABLE", "MANUAL_REVIEW", "CRITICAL"):
            assert k in body["classification"]
        j = api.get(f"{BASE_URL}/api/admin/recovery/journal", headers=auth(maker["token"]), timeout=TIMEOUT)
        assert j.status_code == 200 and any(e["kind"] == "scan" for e in j.json())

    def test_recovery_requires_admin(self, api, payer):
        for path in ("/api/admin/recovery/scan", "/api/admin/recovery/auto-heal"):
            r = api.post(f"{BASE_URL}{path}", headers=auth(payer["token"]), timeout=TIMEOUT)
            assert r.status_code == 403, path

    def test_crash_window_stale_idempotency(self, api, mongo, maker):
        """A PROCESSING idempotency record older than 15min blocks retries; scan flags it,
        auto-heal clears it, and the retry with the SAME key then succeeds."""
        uid, tok = _mk_user(mongo, "b45stale", balance=100.0)
        key = f"TEST_idem_{uuid.uuid4().hex[:10]}"
        try:
            mongo.idempotency_records.insert_one({
                "idem_id": f"withdrawal:{uid}:{key}", "scope": "withdrawal", "user_id": uid,
                "hash": "deadbeef", "state": "PROCESSING", "response": None,
                "created_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()})
            r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 10.0, "iban": "X"},
                         headers=auth(tok, {"Idempotency-Key": key}), timeout=TIMEOUT)
            assert r.status_code == 409, r.text[:200]
            scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120).json()
            assert scan["findings"]["stale_idempotency"] >= 1
            assert "stale_idempotency" in scan["classification"]["AUTO_RECOVERABLE"]
            heal = api.post(f"{BASE_URL}/api/admin/recovery/auto-heal",
                            headers=auth(maker["token"]), timeout=120).json()
            assert heal["healed"]["stale_idempotency_cleared"] >= 1
            assert mongo.idempotency_records.count_documents({"idem_id": f"withdrawal:{uid}:{key}"}) == 0
            r2 = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 10.0, "iban": "X"},
                          headers=auth(tok, {"Idempotency-Key": key}), timeout=TIMEOUT)
            assert r2.status_code == 200, r2.text[:200]
            # replay of the same key now returns the cached response (no double debit)
            r3 = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 10.0, "iban": "X"},
                          headers=auth(tok, {"Idempotency-Key": key}), timeout=TIMEOUT)
            assert r3.status_code == 200
            assert r3.json()["withdrawal"]["wd_id"] == r2.json()["withdrawal"]["wd_id"]
            assert mongo.users.find_one({"user_id": uid})["balance_cc"] == 90.0
        finally:
            mongo.idempotency_records.delete_many({"user_id": uid})
            _cleanup(mongo, uid)

    def test_crash_window_expired_hold_lazy_expiry(self, api, mongo, maker):
        """An ACTIVE hold past expires_at must not consume available balance BEFORE
        auto-heal (lazy expiry) and auto-heal must mark it EXPIRED exactly once."""
        uid, tok = _mk_user(mongo, "b45hold", balance=100.0)
        try:
            r = api.post(f"{BASE_URL}/api/holds", json={"amount": 80.0, "reason": "TEST", "ttl_seconds": 3600},
                         headers=auth(tok), timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:200]
            hid = r.json()["hold_id"]
            w = api.get(f"{BASE_URL}/api/wallet", headers=auth(tok), timeout=TIMEOUT).json()
            assert w["available_balance_cc"] == 20.0, w
            # crash window: hold is overdue but still ACTIVE, held_cc still 80
            mongo.balance_holds.update_one({"hold_id": hid}, {"$set": {
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}})
            w = api.get(f"{BASE_URL}/api/wallet", headers=auth(tok), timeout=TIMEOUT).json()
            assert w["available_balance_cc"] == 100.0, f"expired hold still blocking: {w}"
            scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120).json()
            print("scan expired_active_holds:", scan["findings"]["expired_active_holds"])
            heal = api.post(f"{BASE_URL}/api/admin/recovery/auto-heal",
                            headers=auth(maker["token"]), timeout=120).json()
            h = mongo.balance_holds.find_one({"hold_id": hid})
            assert h["status"] == "EXPIRED", h["status"]
            u = mongo.users.find_one({"user_id": uid})
            assert abs(u.get("held_cc", 0.0)) < 1e-9, f"held_cc not released: {u.get('held_cc')}"
            # idempotent: second auto-heal must not double-decrement
            api.post(f"{BASE_URL}/api/admin/recovery/auto-heal", headers=auth(maker["token"]), timeout=120)
            u = mongo.users.find_one({"user_id": uid})
            assert abs(u.get("held_cc", 0.0)) < 1e-9, f"held_cc drift after 2nd heal: {u.get('held_cc')}"
            assert u["balance_cc"] == 100.0
            hh = api.get(f"{BASE_URL}/api/admin/holds/integrity", headers=auth(maker["token"]), timeout=120).json()
            assert hh["healthy"] is True, hh
            print("auto-heal:", heal["healed"])
        finally:
            mongo.balance_holds.delete_many({"user_id": uid})
            mongo.users.delete_many({"user_id": uid})
            _cleanup(mongo, uid)

    def test_auto_heal_expires_hold_without_lazy_read(self, api, mongo, maker):
        """Same crash window but WITHOUT any read of the owner's wallet first, so the
        auto-heal branch (not lazy expiry) is the one that must expire the hold."""
        uid, tok = _mk_user(mongo, "b45hold2", balance=100.0)
        try:
            r = api.post(f"{BASE_URL}/api/holds", json={"amount": 60.0, "reason": "TEST", "ttl_seconds": 3600},
                         headers=auth(tok), timeout=TIMEOUT)
            hid = r.json()["hold_id"]
            mongo.balance_holds.update_one({"hold_id": hid}, {"$set": {
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}})
            scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120).json()
            print("scan expired_active_holds (no lazy read):", scan["findings"]["expired_active_holds"])
            assert scan["findings"]["expired_active_holds"] >= 1
            assert "expired_active_holds" in scan["classification"]["AUTO_RECOVERABLE"]
            heal = api.post(f"{BASE_URL}/api/admin/recovery/auto-heal",
                            headers=auth(maker["token"]), timeout=120).json()
            print("auto-heal (no lazy read):", heal["healed"])
            assert heal["healed"]["holds_expired"] >= 1
            assert mongo.balance_holds.find_one({"hold_id": hid})["status"] == "EXPIRED"
            u = mongo.users.find_one({"user_id": uid})
            assert abs(u.get("held_cc", 0.0)) < 1e-9, u.get("held_cc")
            assert u["balance_cc"] == 100.0
            api.post(f"{BASE_URL}/api/admin/recovery/auto-heal", headers=auth(maker["token"]), timeout=120)
            u = mongo.users.find_one({"user_id": uid})
            assert abs(u.get("held_cc", 0.0)) < 1e-9, f"double-decrement: {u.get('held_cc')}"
        finally:
            mongo.balance_holds.delete_many({"user_id": uid})
            _cleanup(mongo, uid)

    def test_crash_window_stuck_settlement(self, api, maker, payer, tracker, mongo):
        tx = _make_tx(api, payer["token"], 13.0)
        s = _new_settlement(api, maker["token"], tx, tracker)
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]}, {"$set": {
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()}})
        scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(maker["token"]), timeout=120).json()
        assert scan["findings"]["stuck_settlements"] >= 1
        assert "stuck_settlements" in scan["classification"]["MANUAL_REVIEW"]
        fh = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(maker["token"]), timeout=120).json()
        assert fh["settlements"]["stuck"] >= 1
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"internal_status": "CANCELLED"}})


# ---------------- invariants + honesty ----------------
class TestInvariantsAndHonesty:
    def test_system_status_capabilities_honest(self, api, maker):
        r = api.get(f"{BASE_URL}/api/system/status", headers=auth(maker["token"]), timeout=TIMEOUT)
        assert r.status_code == 200
        caps = r.json()["capabilities"]
        expected = {"asset_registry": "REAL", "maker_checker": "REAL", "recovery_engine": "REAL",
                    "reconciliation": "REAL", "settlement_engine": "PARTIAL", "outbox_events": "PARTIAL",
                    "monetary_precision": "REAL", "provider_adapters": "MOCK",
                    "payments_deposit_stripe": "SANDBOX", "card_issuing": "MOCK",
                    "invest": "PLANNED", "crypto": "PLANNED", "fx": "PLANNED"}
        for k, v in expected.items():
            assert caps.get(k) == v, f"{k}={caps.get(k)} expected {v}"

    def test_final_invariants(self, api, maker, mongo):
        # any settlement left in REQUIRES_REVIEW by earlier tests is intentional -> report it
        fh = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(maker["token"]), timeout=120).json()
        print("financial-health:", {k: fh[k] for k in
                                    ("ledger_balanced", "jcc_supply_reconciled", "per_asset_sum", "settlements",
                                     "outbox", "inbox_unprocessed", "reconciliation_open_cases",
                                     "reconciliation_high_severity", "severity")})
        assert fh["ledger_balanced"] is True, fh["per_asset_sum"]
        assert fh["jcc_supply_reconciled"] is True
        assert fh["holds_health"]["healthy"] is True
        assert fh["outbox"]["dead_letter"] == 0
        assert all(abs(v) < 1e-6 for v in fh["per_asset_sum"].values()), fh["per_asset_sum"]
        assert fh["settlements"]["requires_review"] == 0, "settlement stuck in REQUIRES_REVIEW"
        integ = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=auth(maker["token"]), timeout=120).json()
        assert integ["balanced"] is True, integ
