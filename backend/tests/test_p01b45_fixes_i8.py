# Iteration 8 — targeted re-test of the iteration_7 fixes (P0.1-B4/B5).
# 1. webhook provider scoping (CRITICAL) incl. concurrency
# 2. webhook payload conflict -> duplicate_conflict + Financial.ProviderWebhookConflict
# 3. submit retry on SUBMITTED-with-null-provider_reference (crash window)
# 4. recovery scan classification (expired_approvals / unprocessed_inbox) + auto-heal expiry
# 5. precision migrate dry_run=false -> 501
# 6. reject compensation (refund failure reverts status + CRITICAL recovery_journal)
# Run SERIALLY (-n 0): mutates the GLOBAL fee_policy and admin integrity endpoints.
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth, wallet_balance

TIMEOUT = 45


@pytest.fixture(scope="module")
def adm(mongo):
    uid, tok = _mk_user(mongo, "i8adm", is_admin=True, balance=100.0)
    yield {"user_id": uid, "token": tok}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def adm2(mongo):
    uid, tok = _mk_user(mongo, "i8adm2", is_admin=True, balance=0.0)
    yield {"user_id": uid, "token": tok}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def usr(mongo):
    uid, tok = _mk_user(mongo, "i8usr", balance=2000.0)
    yield {"user_id": uid, "token": tok}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module", autouse=True)
def zero_fees(api, adm):
    api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}}, headers=auth(adm["token"]), timeout=TIMEOUT)
    yield
    r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}}, headers=auth(adm["token"]), timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:200]


@pytest.fixture(scope="module")
def trk(mongo):
    t = {"settlements": [], "webhooks": [], "approvals": [], "cases": []}
    yield t
    if t["settlements"]:
        mongo.reconciliation_cases.delete_many({"settlement_id": {"$in": t["settlements"]}})
        mongo.settlements.delete_many({"settlement_id": {"$in": t["settlements"]}})
        mongo.financial_state_history.delete_many({"entity_type": "settlement",
                                                   "entity_id": {"$in": t["settlements"]}})
    if t["webhooks"]:
        mongo.webhook_inbox.delete_many({"provider_event_id": {"$in": t["webhooks"]}})
    if t["approvals"]:
        mongo.approval_requests.delete_many({"approval_id": {"$in": t["approvals"]}})
    if t["cases"]:
        mongo.reconciliation_cases.delete_many({"case_id": {"$in": t["cases"]}})


def _make_tx(api, token, amount=25.0):
    r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": amount, "iban": "FR76TEST"},
                 headers=auth(token), timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(token), timeout=TIMEOUT).json()
    out = [t for t in txs if t["category"] == "Retrait" and abs(t["amount"] + amount) < 1e-6]
    assert out
    return out[0]["tx_id"]


def _settlement(api, token, tx, trk, provider="mock_bank"):
    r = api.post(f"{BASE_URL}/api/admin/settlements",
                 json={"transaction_id": tx, "provider": provider, "direction": "payout"},
                 headers=auth(token), timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    s = r.json()
    trk["settlements"].append(s["settlement_id"])
    return s


def _submitted(api, token, tx, trk, provider="mock_bank"):
    s = _settlement(api, token, tx, trk, provider)
    sub = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                   headers=auth(token), timeout=TIMEOUT)
    assert sub.status_code == 200, sub.text[:300]
    return sub.json()


def fire(fn, n):
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(worker, range(n)))


# ---------------- FIX 1: webhook provider scoping ----------------
class TestWebhookProviderScoping:
    def test_wrong_provider_cannot_drive_any_state(self, api, adm, usr, trk, mongo):
        sub = _submitted(api, adm["token"], _make_tx(api, usr["token"], 31.0), trk)
        sid, ref = sub["settlement_id"], sub["provider_reference"]
        for status in ("settled", "failed", "cancelled", "processing"):
            ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
            trk["webhooks"].append(ev)
            r = requests.post(f"{BASE_URL}/api/webhooks/mock_processor",
                              json={"event_id": ev, "provider_reference": ref, "status": status}, timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:200]
            assert r.json()["result"] in ("no_action", "scope_violation"), (status, r.json())
            cur = mongo.settlements.find_one({"settlement_id": sid})
            assert cur["internal_status"] == "SUBMITTED", (status, cur["internal_status"])
            assert cur.get("external_status") == "processing", cur.get("external_status")
        hist = list(mongo.financial_state_history.find({"entity_type": "settlement", "entity_id": sid}))
        assert [h["new_state"] for h in hist] == ["PENDING", "SUBMITTED"], [h["new_state"] for h in hist]
        # correct provider still works
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        trk["webhooks"].append(ev)
        r = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                          json={"event_id": ev, "provider_reference": ref, "status": "settled"}, timeout=TIMEOUT)
        assert r.json()["result"] == "applied:SETTLED", r.json()
        assert mongo.settlements.find_one({"settlement_id": sid})["internal_status"] == "SETTLED"

    def test_scoping_holds_under_concurrency(self, api, adm, usr, trk, mongo):
        """16 simultaneous webhooks: 8 wrong-provider, 8 right-provider.
        Wrong provider must never touch the settlement; right provider -> exactly 1 winner."""
        sub = _submitted(api, adm["token"], _make_tx(api, usr["token"], 17.0), trk)
        sid, ref = sub["settlement_id"], sub["provider_reference"]
        evs = [f"TEST_ev_{uuid.uuid4().hex[:8]}" for _ in range(16)]
        trk["webhooks"].extend(evs)

        def call(i):
            provider = "mock_processor" if i % 2 == 0 else "mock_bank"
            try:
                r = requests.post(f"{BASE_URL}/api/webhooks/{provider}",
                                  json={"event_id": evs[i], "provider_reference": ref, "status": "settled"},
                                  timeout=90)
                return provider, r.status_code, r.json().get("result")
            except Exception as e:  # noqa
                return provider, 0, str(e)[:60]

        res = fire(call, 16)
        wrong = [x for x in res if x[0] == "mock_processor"]
        right = [x for x in res if x[0] == "mock_bank"]
        print("wrong-provider results:", [x[2] for x in wrong])
        print("right-provider results:", [x[2] for x in right])
        assert all(x[2] in ("no_action", "scope_violation") for x in wrong), wrong
        assert sum(1 for x in right if x[2] == "applied:SETTLED") == 1, right
        assert all(x[2] in ("applied:SETTLED", "ignored_terminal") for x in right), right
        settled_rows = list(mongo.financial_state_history.find(
            {"entity_type": "settlement", "entity_id": sid, "new_state": "SETTLED"}))
        assert len(settled_rows) == 1, len(settled_rows)

    def test_unknown_reference_no_action(self, api, trk):
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        trk["webhooks"].append(ev)
        r = requests.post(f"{BASE_URL}/api/webhooks/mock_bank",
                          json={"event_id": ev, "provider_reference": "nope_ref", "status": "settled"}, timeout=TIMEOUT)
        assert r.status_code == 200 and r.json()["result"] == "no_action", r.text[:200]


# ---------------- FIX 2: webhook payload conflict ----------------
class TestWebhookPayloadConflict:
    def test_same_event_id_different_body_is_conflict(self, api, adm, usr, trk, mongo):
        sub = _submitted(api, adm["token"], _make_tx(api, usr["token"], 13.0), trk)
        sid, ref = sub["settlement_id"], sub["provider_reference"]
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        trk["webhooks"].append(ev)
        body = {"event_id": ev, "provider_reference": ref, "status": "processing"}
        r1 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank", json=body, timeout=TIMEOUT)
        assert r1.json() == {"status": "processed", "result": "applied:PROCESSING"}, r1.json()
        # identical duplicate -> ignored, single business effect
        r2 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank", json=body, timeout=TIMEOUT)
        assert r2.json()["status"] == "duplicate_ignored", r2.json()
        # same event_id, DIFFERENT body -> conflict
        tampered = {"event_id": ev, "provider_reference": ref, "status": "settled"}
        r3 = requests.post(f"{BASE_URL}/api/webhooks/mock_bank", json=tampered, timeout=TIMEOUT)
        assert r3.status_code == 200, r3.text[:200]
        assert r3.json()["status"] == "duplicate_conflict", r3.json()
        cur = mongo.settlements.find_one({"settlement_id": sid})
        assert cur["internal_status"] == "PROCESSING", cur["internal_status"]
        assert mongo.webhook_inbox.count_documents({"provider": "mock_bank", "provider_event_id": ev}) == 1
        assert len(list(mongo.financial_state_history.find(
            {"entity_type": "settlement", "entity_id": sid, "new_state": "PROCESSING"}))) == 1
        # conflict event emitted
        assert mongo.outbox_events.count_documents(
            {"event_type": "Financial.ProviderWebhookConflict", "aggregate_id": ev}) == 1
        mongo.settlements.update_one({"settlement_id": sid}, {"$set": {"internal_status": "CANCELLED"}})


# ---------------- FIX 3: submit retry (crash window) ----------------
class TestSubmitRetry:
    def test_retry_when_submitted_with_null_reference(self, api, adm, usr, trk, mongo):
        sub = _submitted(api, adm["token"], _make_tx(api, usr["token"], 21.0), trk)
        sid = sub["settlement_id"]
        # simulate crash between transition and provider_reference write
        mongo.settlements.update_one({"settlement_id": sid},
                                     {"$set": {"provider_reference": None, "external_status": None}})
        r = api.post(f"{BASE_URL}/api/admin/settlements/{sid}/submit",
                     headers=auth(adm["token"]), timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["internal_status"] == "SUBMITTED"
        assert d["provider_reference"] and d["provider_reference"].startswith("mock_bank_ref_")
        assert d["external_status"] == "processing"
        # retry must not add a spurious state row
        hist = [h["new_state"] for h in mongo.financial_state_history.find(
            {"entity_type": "settlement", "entity_id": sid})]
        assert hist == ["PENDING", "SUBMITTED"], hist
        # now a normal double submit is still refused
        r2 = api.post(f"{BASE_URL}/api/admin/settlements/{sid}/submit",
                      headers=auth(adm["token"]), timeout=TIMEOUT)
        assert r2.status_code == 409 and "INVALID_TRANSITION" in r2.text, r2.text[:200]

    def test_no_retry_from_terminal_even_with_null_reference(self, api, adm, usr, trk, mongo):
        s = _settlement(api, adm["token"], _make_tx(api, usr["token"], 7.0), trk)
        mongo.settlements.update_one({"settlement_id": s["settlement_id"]},
                                     {"$set": {"internal_status": "SETTLED", "provider_reference": None}})
        r = api.post(f"{BASE_URL}/api/admin/settlements/{s['settlement_id']}/submit",
                     headers=auth(adm["token"]), timeout=TIMEOUT)
        assert r.status_code == 409, (r.status_code, r.text[:200])
        assert "INVALID_TRANSITION_FROM:SETTLED" in r.text, r.text[:200]


# ---------------- FIX 4: recovery classification ----------------
class TestRecoveryClassification:
    def test_expired_approval_classified_and_healed(self, api, adm, adm2, trk, mongo):
        r = api.post(f"{BASE_URL}/api/admin/approvals",
                     json={"operation_type": "fee_policy_change", "payload": {"fee_policy": {}},
                           "reason": "TEST i8 expiry"}, headers=auth(adm["token"]), timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        aid = r.json()["approval_id"]
        trk["approvals"].append(aid)
        mongo.approval_requests.update_one(
            {"approval_id": aid},
            {"$set": {"expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}})

        scan = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(adm["token"]), timeout=90)
        assert scan.status_code == 200, scan.text[:300]
        d = scan.json()
        assert d["findings"]["expired_approvals"] >= 1, d["findings"]
        assert "expired_approvals" in d["classification"]["AUTO_RECOVERABLE"], d["classification"]

        heal = api.post(f"{BASE_URL}/api/admin/recovery/auto-heal", headers=auth(adm["token"]), timeout=90)
        assert heal.status_code == 200, heal.text[:300]
        assert heal.json()["healed"]["approvals_expired"] >= 1, heal.json()
        assert mongo.approval_requests.find_one({"approval_id": aid})["status"] == "EXPIRED"

        # idempotent second heal
        heal2 = api.post(f"{BASE_URL}/api/admin/recovery/auto-heal", headers=auth(adm["token"]), timeout=90)
        assert heal2.json()["healed"]["approvals_expired"] == 0, heal2.json()
        assert mongo.approval_requests.find_one({"approval_id": aid})["status"] == "EXPIRED"
        # expired approval can no longer be approved
        r2 = api.post(f"{BASE_URL}/api/admin/approvals/{aid}/approve",
                      headers=auth(adm2["token"]), timeout=TIMEOUT)
        assert r2.status_code == 409, (r2.status_code, r2.text[:200])
        scan2 = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(adm["token"]), timeout=90).json()
        assert scan2["findings"]["expired_approvals"] == 0, scan2["findings"]

    def test_unprocessed_inbox_manual_review(self, api, adm, mongo):
        ev = f"TEST_ev_{uuid.uuid4().hex[:8]}"
        mongo.webhook_inbox.insert_one({
            "provider": "mock_bank", "provider_event_id": ev, "payload_hash": "x",
            "received_at": datetime.now(timezone.utc).isoformat(), "processing_status": "RECEIVED",
            "attempts": 0, "last_error": None, "payload": {}})
        try:
            d = api.post(f"{BASE_URL}/api/admin/recovery/scan", headers=auth(adm["token"]), timeout=90).json()
            assert d["findings"]["unprocessed_inbox"] >= 1, d["findings"]
            assert "unprocessed_inbox" in d["classification"]["MANUAL_REVIEW"], d["classification"]
            # auto-heal must NOT silently process it (manual review only)
            api.post(f"{BASE_URL}/api/admin/recovery/auto-heal", headers=auth(adm["token"]), timeout=90)
            assert mongo.webhook_inbox.find_one({"provider_event_id": ev})["processing_status"] == "RECEIVED"
        finally:
            mongo.webhook_inbox.delete_many({"provider_event_id": ev})


# ---------------- FIX 5: precision honesty ----------------
class TestPrecisionHonesty:
    def test_destructive_migration_501(self, api, adm):
        # Real migration is now implemented: dry_run=false performs an idempotent backfill
        # and returns economic_equality=True (no more 501).
        r = api.post(f"{BASE_URL}/api/admin/precision/migrate?dry_run=false",
                     headers=auth(adm["token"]), timeout=120)
        assert r.status_code == 200, (r.status_code, r.text[:300])
        assert r.json()["economic_equality"] is True, r.text[:300]

    def test_dry_run_still_reports(self, api, adm):
        r = api.post(f"{BASE_URL}/api/admin/precision/migrate?dry_run=true",
                     headers=auth(adm["token"]), timeout=120)
        assert r.status_code == 200, r.text[:300]
        rep = r.json()
        assert rep["dry_run"] is True and rep["ledger_postings_checked"] > 0
        assert "representable" in rep

    def test_migration_requires_admin(self, api, usr):
        r = api.post(f"{BASE_URL}/api/admin/precision/migrate?dry_run=false",
                     headers=auth(usr["token"]), timeout=TIMEOUT)
        assert r.status_code == 403, r.status_code


# ---------------- FIX 6: reject compensation ----------------
class TestRejectCompensation:
    def test_normal_reject_refunds_principal_and_fee(self, api, adm, usr, mongo):
        api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {"withdrawal": {"pct": 0.0, "flat": 2.0}}},
                headers=auth(adm["token"]), timeout=TIMEOUT)
        try:
            b0 = wallet_balance(api, usr["token"])
            r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 40.0, "iban": "FR76TEST"},
                         headers=auth(usr["token"]), timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:300]
            wd_id = r.json()["withdrawal"]["wd_id"]
            wd = mongo.withdrawals.find_one({"wd_id": wd_id})
            assert abs(wd["fee_cc"] - 2.0) < 1e-6, wd["fee_cc"]
            assert abs(wallet_balance(api, usr["token"]) - (b0 - 42.0)) < 1e-6
            rj = api.post(f"{BASE_URL}/api/admin/withdrawals/{wd_id}/reject",
                          headers=auth(adm["token"]), timeout=TIMEOUT)
            assert rj.status_code == 200, rj.text[:300]
            assert abs(wallet_balance(api, usr["token"]) - b0) < 1e-6, wallet_balance(api, usr["token"])
            assert mongo.withdrawals.find_one({"wd_id": wd_id})["status"] == "rejected"
        finally:
            api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
                    headers=auth(adm["token"]), timeout=TIMEOUT)

    def test_concurrent_reject_single_refund(self, api, adm, usr, mongo):
        b0 = wallet_balance(api, usr["token"])
        r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 30.0, "iban": "FR76TEST"},
                     headers=auth(usr["token"]), timeout=TIMEOUT)
        wd_id = r.json()["withdrawal"]["wd_id"]
        hdrs = auth(adm["token"], {"Content-Type": "application/json"})

        def call(_):
            try:
                return requests.post(f"{BASE_URL}/api/admin/withdrawals/{wd_id}/reject",
                                     headers=hdrs, timeout=90).status_code
            except Exception:
                return 0

        codes = fire(call, 10)
        print("concurrent reject codes:", codes)
        assert codes.count(200) == 1, codes
        assert all(c in (200, 404) for c in codes), codes
        assert abs(wallet_balance(api, usr["token"]) - b0) < 1e-6
        assert mongo.transactions.count_documents(
            {"user_id": usr["user_id"], "category": "Retrait", "amount": 30.0}) == 1

    def test_refund_failure_reverts_status_and_flags_critical(self, api, adm, mongo):
        """Injected fault: a withdrawal whose amount cannot be posted. A rejected withdrawal
        MUST always refund -> status must revert to pending + CRITICAL recovery_journal entry."""
        uid, tok = _mk_user(mongo, "i8reject", balance=100.0)
        wd_id = f"wd_TEST_{uuid.uuid4().hex[:8]}"
        mongo.withdrawals.insert_one({
            "wd_id": wd_id, "user_id": uid, "frek_id": "FREK-TEST", "amount_cc": "BROKEN",
            "amount_eur": 0.0, "fee_cc": 0, "iban": "FR76TEST", "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(), "processed_at": None})
        try:
            r = api.post(f"{BASE_URL}/api/admin/withdrawals/{wd_id}/reject",
                         headers=auth(adm["token"]), timeout=TIMEOUT)
            print("injected-fault reject status:", r.status_code, r.text[:120])
            assert r.status_code >= 500, (r.status_code, r.text[:200])
            wd = mongo.withdrawals.find_one({"wd_id": wd_id})
            assert wd["status"] == "pending", wd["status"]
            assert wd.get("processed_at") is None, wd.get("processed_at")
            j = mongo.recovery_journal.find_one({"ref": wd_id})
            assert j is not None, "no recovery_journal entry written for failed reject refund"
            assert j["classification"] == "CRITICAL", j
            assert j["kind"] == "withdrawal_reject_refund_failed", j
            # balance untouched, no orphan credit
            assert mongo.users.find_one({"user_id": uid})["balance_cc"] == 100.0
            # after fixing the data, the retry succeeds and refunds -> recoverable
            mongo.withdrawals.update_one({"wd_id": wd_id}, {"$set": {"amount_cc": 10.0}})
            r2 = api.post(f"{BASE_URL}/api/admin/withdrawals/{wd_id}/reject",
                          headers=auth(adm["token"]), timeout=TIMEOUT)
            assert r2.status_code == 200, r2.text[:300]
            assert mongo.withdrawals.find_one({"wd_id": wd_id})["status"] == "rejected"
            assert mongo.users.find_one({"user_id": uid})["balance_cc"] == 110.0
        finally:
            mongo.withdrawals.delete_many({"wd_id": wd_id})
            mongo.recovery_journal.delete_many({"ref": wd_id})
            _cleanup(mongo, uid)


# ---------------- final invariants ----------------
class TestFinalInvariants:
    def test_financial_health(self, api, adm):
        r = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(adm["token"]), timeout=90)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        print("financial-health:", {k: d[k] for k in
                                    ("ledger_balanced", "jcc_supply_reconciled", "severity") if k in d})
        assert d["ledger_balanced"] is True, d.get("per_asset_sum")
        assert abs(d["per_asset_sum"]["JCC"]) < 1e-6, d["per_asset_sum"]
        assert d["jcc_supply_reconciled"] is True, d
        assert d["holds_health"]["healthy"] is True, d["holds_health"]
        assert d["settlements"]["requires_review"] == 0, d["settlements"]
        assert d["outbox"]["dead_letter"] == 0, d["outbox"]
        assert d["severity"] == "INFO", d

    def test_system_status_honest(self, api, adm):
        r = api.get(f"{BASE_URL}/api/system/status", headers=auth(adm["token"]), timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        caps = r.json()["capabilities"]
        expected = {"asset_registry": "REAL", "maker_checker": "REAL", "recovery_engine": "REAL",
                    "reconciliation": "REAL", "settlement_engine": "PARTIAL", "outbox_events": "PARTIAL",
                    "monetary_precision": "REAL", "provider_adapters": "MOCK", "card_issuing": "MOCK",
                    "payments_deposit_stripe": "SANDBOX"}
        for k, v in expected.items():
            got = caps.get(k) if not isinstance(caps.get(k), dict) else caps[k].get("status")
            assert got == v, (k, got, v)
        for k in ("invest", "crypto", "fx"):
            got = caps.get(k) if not isinstance(caps.get(k), dict) else caps[k].get("status")
            assert got == "PLANNED", (k, got)
