"""CVLN Financial Core hardening tests (Mission P0.1)
Modules covered: idempotency engine (idem_begin/idem_finish), /api/actions/send,
/api/withdrawals, /api/admin/ledger/integrity, /api/admin/financial-health, require_admin.
"""
import uuid

import pytest

from conftest import BASE_URL, auth, wallet_balance


# ---------- Sanity / auth ----------
class TestSanity:
    def test_wallet_reachable(self, api, plain_user):
        r = api.get(f"{BASE_URL}/api/wallet", headers=auth(plain_user["token"]), timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["balance_cc"] == 500.0

    def test_unauthenticated_rejected(self, api):
        r = api.get(f"{BASE_URL}/api/wallet", timeout=30)
        assert r.status_code == 401


# ---------- Idempotency: /api/actions/send ----------
class TestSendIdempotency:
    def test_replay_same_key_same_body_debits_once(self, api, plain_user, mongo):
        token = plain_user["token"]
        before = wallet_balance(api, token)
        key = f"TEST-send-{uuid.uuid4().hex[:10]}"
        body = {"recipient": "FREK-TARGET-0001", "amount": 25, "note": "TEST idem"}

        r1 = api.post(f"{BASE_URL}/api/actions/send", json=body,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 200, r1.text[:400]
        d1 = r1.json()
        assert d1["ok"] is True
        assert d1["transaction"]["amount"] == -25
        after_first = wallet_balance(api, token)
        assert after_first == pytest.approx(before - 25)

        r2 = api.post(f"{BASE_URL}/api/actions/send", json=body,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 200, r2.text[:400]
        d2 = r2.json()
        assert d2["transaction"]["tx_id"] == d1["transaction"]["tx_id"], "replay returned a NEW transaction"
        assert wallet_balance(api, token) == pytest.approx(after_first), "replay debited a second time"

        # only one persisted transaction + one ledger entry for that tx
        assert mongo.transactions.count_documents({"tx_id": d1["transaction"]["tx_id"]}) == 1
        assert mongo.transactions.count_documents(
            {"user_id": plain_user["user_id"], "label": d1["transaction"]["label"]}) == 1
        assert mongo.ledger_entries.count_documents({"ref": d1["transaction"]["tx_id"]}) == 1

    def test_same_key_different_body_conflict(self, api, plain_user):
        token = plain_user["token"]
        key = f"TEST-conf-{uuid.uuid4().hex[:10]}"
        body = {"recipient": "FREK-TARGET-0002", "amount": 5, "note": "a"}
        r1 = api.post(f"{BASE_URL}/api/actions/send", json=body,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        before = wallet_balance(api, token)
        r2 = api.post(f"{BASE_URL}/api/actions/send",
                      json={"recipient": "FREK-TARGET-0002", "amount": 7, "note": "a"},
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 409, f"expected 409, got {r2.status_code}: {r2.text[:300]}"
        assert r2.json().get("detail") == "IDEMPOTENCY_CONFLICT"
        assert wallet_balance(api, token) == pytest.approx(before)

    def test_key_scoped_per_user(self, api, plain_user, admin_user):
        """Same key used by a different user must NOT collide."""
        key = f"TEST-scope-{uuid.uuid4().hex[:10]}"
        body = {"recipient": "FREK-SCOPE", "amount": 3, "note": "scope"}
        r1 = api.post(f"{BASE_URL}/api/actions/send", json=body,
                      headers=auth(plain_user["token"], {"Idempotency-Key": key}), timeout=30)
        r2 = api.post(f"{BASE_URL}/api/actions/send", json=body,
                      headers=auth(admin_user["token"], {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        assert r2.status_code == 200, r2.text[:300]
        assert r1.json()["transaction"]["tx_id"] != r2.json()["transaction"]["tx_id"]

    def test_failed_request_key_is_not_poisoned(self, api, plain_user):
        """A key used on a request that fails validation should be retryable
        (record must not stay PROCESSING forever)."""
        token = plain_user["token"]
        key = f"TEST-fail-{uuid.uuid4().hex[:10]}"
        bad = {"recipient": "FREK-X", "amount": 999999, "note": "insufficient"}
        r1 = api.post(f"{BASE_URL}/api/actions/send", json=bad,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 400, r1.text[:300]
        assert r1.json().get("detail") in ("Solde insuffisant", "Solde disponible insuffisant"), r1.text[:300]
        r2 = api.post(f"{BASE_URL}/api/actions/send", json=bad,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 400, (
            f"retry after failed request returned {r2.status_code} "
            f"({r2.json().get('detail') if r2.headers.get('content-type','').startswith('application/json') else r2.text[:200]}) "
            "- idempotency key is permanently poisoned")

    # FIX #1: after a 400 the SAME key must be reusable with a corrected amount
    def test_failed_key_retry_with_corrected_amount_succeeds(self, api, plain_user, mongo):
        token = plain_user["token"]
        key = f"TEST-KX-{uuid.uuid4().hex[:10]}"
        before = wallet_balance(api, token)
        bad = {"recipient": "FREK-KX", "amount": before + 100000, "note": "too much"}
        r1 = api.post(f"{BASE_URL}/api/actions/send", json=bad,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 400, r1.text[:300]
        assert r1.json().get("detail") in ("Solde insuffisant", "Solde disponible insuffisant"), r1.text[:300]
        # PROCESSING record must be gone
        assert mongo.idempotency_records.count_documents(
            {"idem_id": f"send:{plain_user['user_id']}:{key}"}) == 0, \
            "failed request left an idempotency record behind"

        good = {"recipient": "FREK-KX", "amount": 12, "note": "too much"}
        r2 = api.post(f"{BASE_URL}/api/actions/send", json=good,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 200, (
            f"retry of key {key} with corrected amount -> {r2.status_code} {r2.text[:200]}")
        assert r2.json()["ok"] is True
        after = wallet_balance(api, token)
        assert after == pytest.approx(before - 12), "corrected retry did not debit exactly once"

        # and now the completed key replays (no second debit)
        r3 = api.post(f"{BASE_URL}/api/actions/send", json=good,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r3.status_code == 200, r3.text[:300]
        assert r3.json()["transaction"]["tx_id"] == r2.json()["transaction"]["tx_id"]
        assert wallet_balance(api, token) == pytest.approx(after)


# ---------- Idempotency: /api/withdrawals ----------
class TestWithdrawalIdempotency:
    def test_replay_does_not_create_second_withdrawal(self, api, plain_user, mongo):
        token = plain_user["token"]
        before = wallet_balance(api, token)
        key = f"TEST-wd-{uuid.uuid4().hex[:10]}"
        body = {"amount_cc": 20, "iban": "FR7630006000011234567890189"}
        r1 = api.post(f"{BASE_URL}/api/withdrawals", json=body,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 200, r1.text[:400]
        wd1 = r1.json()["withdrawal"]
        assert wd1["amount_cc"] == 20
        assert wd1["status"] == "pending"
        after_first = wallet_balance(api, token)
        assert after_first == pytest.approx(before - 20)

        r2 = api.post(f"{BASE_URL}/api/withdrawals", json=body,
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 200, r2.text[:400]
        assert r2.json()["withdrawal"]["wd_id"] == wd1["wd_id"], "replay created a new withdrawal"
        assert wallet_balance(api, token) == pytest.approx(after_first), "replay debited twice"
        assert mongo.withdrawals.count_documents(
            {"user_id": plain_user["user_id"], "amount_cc": 20}) == 1

        lst = api.get(f"{BASE_URL}/api/withdrawals", headers=auth(token), timeout=30)
        assert lst.status_code == 200
        assert len([w for w in lst.json() if w["wd_id"] == wd1["wd_id"]]) == 1

    def test_withdrawal_conflict_different_body(self, api, plain_user):
        token = plain_user["token"]
        key = f"TEST-wdc-{uuid.uuid4().hex[:10]}"
        r1 = api.post(f"{BASE_URL}/api/withdrawals",
                      json={"amount_cc": 5, "iban": "FR7630006000011234567890189"},
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        r2 = api.post(f"{BASE_URL}/api/withdrawals",
                      json={"amount_cc": 6, "iban": "FR7630006000011234567890189"},
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 409, r2.text[:300]
        assert r2.json().get("detail") == "IDEMPOTENCY_CONFLICT"

    # FIX #1 on withdrawals: 400 then retry same key with valid amount must succeed
    def test_withdrawal_failed_key_retry_succeeds(self, api, plain_user, mongo):
        token = plain_user["token"]
        key = f"TEST-wdKX-{uuid.uuid4().hex[:10]}"
        before = wallet_balance(api, token)
        r1 = api.post(f"{BASE_URL}/api/withdrawals",
                      json={"amount_cc": before + 50000, "iban": "FR7630006000011234567890189"},
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r1.status_code == 400, r1.text[:300]
        # B3 FIX #1: message now mentions available balance + fees
        assert "insuffisant" in str(r1.json().get("detail", "")).lower(), r1.text[:300]
        assert mongo.idempotency_records.count_documents(
            {"idem_id": f"withdrawal:{plain_user['user_id']}:{key}"}) == 0

        r2 = api.post(f"{BASE_URL}/api/withdrawals",
                      json={"amount_cc": 7, "iban": "FR7630006000011234567890189"},
                      headers=auth(token, {"Idempotency-Key": key}), timeout=30)
        assert r2.status_code == 200, (
            f"withdrawal retry with same key -> {r2.status_code} {r2.text[:200]}")
        wd = r2.json()["withdrawal"]
        after = wallet_balance(api, token)
        assert after == pytest.approx(before - 7)
        assert mongo.withdrawals.count_documents(
            {"user_id": plain_user["user_id"], "wd_id": wd["wd_id"]}) == 1


# ---------- No regression: send without Idempotency-Key ----------
class TestSendWithoutKey:
    def test_send_without_key_debits_and_caches_match(self, api, plain_user, admin_user, mongo):
        token = plain_user["token"]
        before = wallet_balance(api, token)
        r = api.post(f"{BASE_URL}/api/actions/send",
                     json={"recipient": "FREK-NOKEY", "amount": 15, "note": "TEST nokey"},
                     headers=auth(token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        tx = r.json()["transaction"]
        assert tx["amount"] == -15 and tx["type"] == "out"
        after = wallet_balance(api, token)
        assert after == pytest.approx(before - 15)

        # transaction recorded
        txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(token), timeout=30)
        assert txs.status_code == 200
        assert any(t["tx_id"] == tx["tx_id"] for t in txs.json())

        # cache == derived ledger balance
        acc = api.get(f"{BASE_URL}/api/ledger/accounts", headers=auth(token), timeout=30)
        assert acc.status_code == 200, acc.text[:300]
        cash = [a for a in acc.json() if a["account_id"] == f"acct_cash_{plain_user['user_id']}"]
        assert cash, acc.json()
        assert cash[0]["balance"] == pytest.approx(cash[0]["cached"]) == pytest.approx(after)

    def test_coffre_move_routes_through_ledger(self, api, plain_user, mongo):
        token = plain_user["token"]
        c = api.post(f"{BASE_URL}/api/coffres", json={"name": "TEST Coffre", "goal_cc": 1000},
                     headers=auth(token), timeout=30)
        assert c.status_code == 200, c.text[:300]
        cid = c.json()["coffre_id"]
        before = wallet_balance(api, token)
        m = api.post(f"{BASE_URL}/api/coffres/{cid}/move", json={"amount": 30},
                     headers=auth(token), timeout=30)
        assert m.status_code == 200, m.text[:300]
        assert m.json()["coffre"]["amount_cc"] == 30
        assert wallet_balance(api, token) == pytest.approx(before - 30)
        entry = mongo.ledger_entries.find_one({"postings.account_id": f"acct_coffre_{cid}"})
        assert entry is not None, "coffre move did not post to the ledger"
        assert abs(sum(p["amount"] for p in entry["postings"])) < 1e-6


# ---------- Admin integrity / health ----------
class TestAdminIntegrity:
    # FIX #2: migration backfill must align cache vs derived ledger balances
    def test_backfill_then_no_cache_mismatches(self, api, admin_user, mongo):
        h = auth(admin_user["token"])
        b = api.post(f"{BASE_URL}/api/admin/ledger/backfill", headers=h, timeout=120)
        assert b.status_code == 200, b.text[:400]
        db_ = b.json()
        assert db_["ok"] is True
        assert isinstance(db_["accounts_backfilled"], int)

        i = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=h, timeout=60)
        assert i.status_code == 200, i.text[:300]
        d = i.json()
        assert d["balanced"] is True, d["per_asset_sum"]
        assert d["cache_mismatches"] == [], d["cache_mismatches"]

        hh = api.get(f"{BASE_URL}/api/admin/financial-health", headers=h, timeout=60).json()
        assert hh["ledger_balanced"] is True, hh
        assert hh["jcc_supply_reconciled"] is True, hh
        assert hh["severity"] == "INFO", hh

    def test_backfill_is_idempotent(self, api, admin_user):
        h = auth(admin_user["token"])
        r = api.post(f"{BASE_URL}/api/admin/ledger/backfill", headers=h, timeout=120)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["accounts_backfilled"] == 0, \
            f"second backfill still reports drift: {r.json()}"

    def test_integrity_balanced(self, api, admin_user, plain_user):
        r = api.get(f"{BASE_URL}/api/admin/ledger/integrity",
                    headers=auth(admin_user["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["balanced"] is True, d
        assert abs(d["per_asset_sum"].get("JCC", 0)) < 1e-6, d["per_asset_sum"]
        assert isinstance(d["entries"], int) and d["entries"] > 0
        mism_ids = [m["user_id"] for m in d["cache_mismatches"]]
        for uid in (admin_user["user_id"], plain_user["user_id"]):
            assert uid not in mism_ids, f"cache mismatch for ledger-created user {uid}: {d['cache_mismatches']}"
        assert d["cache_mismatches"] == [], f"global cache mismatches: {d['cache_mismatches']}"

    def test_financial_health(self, api, admin_user):
        r = api.get(f"{BASE_URL}/api/admin/financial-health",
                    headers=auth(admin_user["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["ledger_balanced"] is True, d
        assert d["jcc_supply_reconciled"] is True, (
            f"supply not reconciled: circulation={d.get('jcc_circulation')} health={d}")
        assert d["severity"] == "INFO"
        assert isinstance(d["idempotency_records"], int)

    def test_supply_discrepancy_unchanged_by_ledger_ops(self, api, admin_user):
        """Isolates whether any supply mismatch comes from legacy/orphan data rather
        than from operations that go through ledger_post: the delta must be 0."""
        h = auth(admin_user["token"])

        def snapshot():
            d = api.get(f"{BASE_URL}/api/admin/financial-health", headers=h, timeout=60).json()
            i = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=h, timeout=60).json()
            sys_total = sum(i["system_accounts"].values())
            return round(d["jcc_circulation"] + sys_total, 2)

        before = snapshot()
        r = api.post(f"{BASE_URL}/api/actions/send",
                     json={"recipient": "FREK-DELTA", "amount": 11, "note": "TEST delta"},
                     headers=auth(admin_user["token"]), timeout=30)
        assert r.status_code == 200, r.text[:300]
        after = snapshot()
        assert after == pytest.approx(before), (
            f"ledger operation changed the supply discrepancy: {before} -> {after}")
        assert before == pytest.approx(0.0), (
            f"pre-existing (legacy) supply discrepancy of {before} JCC unrelated to test ops")

    def test_non_admin_forbidden(self, api, plain_user):
        for path in ("/api/admin/ledger/integrity", "/api/admin/financial-health"):
            r = api.get(f"{BASE_URL}{path}", headers=auth(plain_user["token"]), timeout=30)
            assert r.status_code == 403, f"{path} -> {r.status_code}"
        r = api.post(f"{BASE_URL}/api/admin/ledger/backfill",
                     headers=auth(plain_user["token"]), timeout=30)
        assert r.status_code == 403, f"backfill non-admin -> {r.status_code} {r.text[:200]}"

    def test_unauthenticated_admin_endpoints(self, api):
        for path in ("/api/admin/ledger/integrity", "/api/admin/financial-health"):
            r = api.get(f"{BASE_URL}{path}", timeout=30)
            assert r.status_code == 401, f"{path} -> {r.status_code}"
        r = api.post(f"{BASE_URL}/api/admin/ledger/backfill", timeout=30)
        assert r.status_code == 401, f"backfill unauth -> {r.status_code}"
