"""P0.1-B3: Fees engine + Refund engine + Reversal engine (ledger-backed, atomic guards).

Run SERIALLY (-n 0): admin integrity endpoints are GLOBAL.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE_URL, _mk_user, _cleanup, auth, wallet_balance


# ---------- module fixtures ----------
@pytest.fixture(scope="module")
def admin(mongo):
    uid, token = _mk_user(mongo, "b3admin", is_admin=True, balance=100.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)
    mongo.refunds.delete_many({"user_id": uid})
    mongo.reversals.delete_many({"user_id": uid})


@pytest.fixture(scope="module")
def payer(mongo):
    uid, token = _mk_user(mongo, "b3user", is_admin=False, balance=5000.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)
    mongo.refunds.delete_many({"user_id": uid})
    mongo.reversals.delete_many({"user_id": uid})


@pytest.fixture(scope="module", autouse=True)
def fee_policy_2pct(api, admin):
    """Global fee policy: withdrawal 2%. Reset at module teardown."""
    r = api.put(f"{BASE_URL}/api/admin/fees",
                json={"fee_policy": {"withdrawal": {"pct": 0.02, "flat": 0.0}}},
                headers=auth(admin["token"]), timeout=30)
    assert r.status_code == 200, r.text[:300]
    yield r.json()["fee_policy"]
    r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
                headers=auth(admin["token"]), timeout=30)
    assert r.status_code == 200


# ---------- helpers ----------
def _withdraw(api, token, amount, iban="FR7630006000011234567890189"):
    return api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": amount, "iban": iban},
                    headers=auth(token), timeout=60)


def _outflow_tx(api, user, amount):
    """Create a refundable OUTFLOW 'Retrait' tx of -amount and return its tx_id."""
    r = _withdraw(api, user["token"], amount)
    assert r.status_code == 200, r.text[:400]
    txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(user["token"]), timeout=30).json()
    cand = [t for t in txs if t["category"] == "Retrait" and abs(t["amount"] + amount) < 1e-6]
    assert cand, f"no Retrait tx of -{amount} found"
    return cand[0]["tx_id"]


def _health(api, token):
    r = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(token), timeout=60)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _assert_balanced(api, token):
    h = _health(api, token)
    assert h["ledger_balanced"] is True, h["per_asset_sum"]
    return h


def _post_threaded(url, payloads, headers, n_workers=None):
    def call(p):
        try:
            resp = requests.post(url, json=p, headers=headers, timeout=90)
            return resp.status_code, resp.text
        except Exception as e:  # noqa
            return 0, str(e)
    with ThreadPoolExecutor(max_workers=n_workers or len(payloads)) as ex:
        return list(ex.map(call, payloads))


# ================= Fee policy config =================
class TestFeePolicy:
    def test_get_policy_admin(self, api, admin):
        r = api.get(f"{BASE_URL}/api/admin/fees", headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["fee_policy"]["withdrawal"]["pct"] == 0.02

    def test_non_admin_forbidden(self, api, payer):
        r = api.get(f"{BASE_URL}/api/admin/fees", headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 403, r.status_code
        r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {}},
                    headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 403, r.status_code

    def test_invalid_policy_rejected(self, api, admin):
        r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": "nope"},
                    headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, r.status_code

    # ---- FIX #5: operation whitelist + pct/flat bounds ----
    def test_unknown_operation_rejected(self, api, admin):
        r = api.put(f"{BASE_URL}/api/admin/fees",
                    json={"fee_policy": {"nope_xyz": {"pct": 0.01}}},
                    headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        # policy untouched
        cur = api.get(f"{BASE_URL}/api/admin/fees", headers=auth(admin["token"]), timeout=30).json()
        assert "nope_xyz" not in cur["fee_policy"]
        assert cur["fee_policy"]["withdrawal"]["pct"] == 0.02

    @pytest.mark.parametrize("cfg", [{"pct": 1.5}, {"pct": -0.1}, {"pct": 0.1, "flat": -1}])
    def test_out_of_range_values_rejected(self, api, admin, cfg):
        r = api.put(f"{BASE_URL}/api/admin/fees", json={"fee_policy": {"withdrawal": cfg}},
                    headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        cur = api.get(f"{BASE_URL}/api/admin/fees", headers=auth(admin["token"]), timeout=30).json()
        assert cur["fee_policy"]["withdrawal"]["pct"] == 0.02, cur

    @pytest.mark.parametrize("op", ["withdrawal", "capture", "marketplace", "conversion", "transfer", "deposit"])
    def test_all_valid_operations_accepted(self, api, admin, op):
        r = api.put(f"{BASE_URL}/api/admin/fees",
                    json={"fee_policy": {"withdrawal": {"pct": 0.02, "flat": 0.0},
                                         op: {"pct": 0.01, "flat": 0.5}}},
                    headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 200, (r.status_code, r.text[:200])
        assert op in r.json()["fee_policy"]
        # restore module policy
        api.put(f"{BASE_URL}/api/admin/fees",
                json={"fee_policy": {"withdrawal": {"pct": 0.02, "flat": 0.0}}},
                headers=auth(admin["token"]), timeout=30)


# ================= Fee quote =================
class TestFeeQuote:
    def test_quote_pct(self, api, payer):
        r = api.post(f"{BASE_URL}/api/fees/quote", json={"operation": "withdrawal", "amount": 100},
                     headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["fee"] == 2.0 and d["net"] == 98.0 and d["base"] == 100

    def test_quote_unknown_op_is_zero(self, api, payer):
        r = api.post(f"{BASE_URL}/api/fees/quote", json={"operation": "nope_xyz", "amount": 100},
                     headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 200
        assert r.json()["fee"] == 0.0 and r.json()["net"] == 100.0

    def test_quote_never_negative(self, api, admin, payer, fee_policy_2pct):
        """Negative pct is now rejected by PUT (FIX #5); the quote must stay non-negative."""
        r = api.put(f"{BASE_URL}/api/admin/fees",
                    json={"fee_policy": {"withdrawal": {"pct": 0.02, "flat": 0.0},
                                         "transfer": {"pct": -0.5, "flat": 0.0}}},
                    headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        r = api.post(f"{BASE_URL}/api/fees/quote", json={"operation": "transfer", "amount": 100},
                     headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 200
        assert r.json()["fee"] == 0.0

    def test_quote_does_not_charge(self, api, payer):
        before = wallet_balance(api, payer["token"])
        api.post(f"{BASE_URL}/api/fees/quote", json={"operation": "withdrawal", "amount": 50},
                 headers=auth(payer["token"]), timeout=30)
        assert wallet_balance(api, payer["token"]) == before


# ================= Fees on withdrawal =================
class TestWithdrawalFee:
    def test_withdrawal_charges_fee_via_ledger(self, api, payer, admin):
        before = wallet_balance(api, payer["token"])
        r = _withdraw(api, payer["token"], 100)
        assert r.status_code == 200, r.text[:400]
        wd = r.json()["withdrawal"]
        assert wd["fee_cc"] == 2.0, wd
        after = wallet_balance(api, payer["token"])
        assert abs(after - (before - 102.0)) < 1e-6, (before, after)
        txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(payer["token"]), timeout=30).json()
        fees = [t for t in txs if t["category"] == "Frais" and t["amount"] == -2.0]
        assert fees, "no 'Frais' transaction recorded"
        _assert_balanced(api, admin["token"])

    def test_withdrawal_insufficient_with_fee(self, api, mongo, admin):
        uid, token = _mk_user(mongo, "b3poor", balance=100.0)
        try:
            r = _withdraw(api, token, 99)  # 99 + 1.98 > 100
            assert r.status_code == 400, (r.status_code, r.text[:200])
            assert wallet_balance(api, token) == 100.0
        finally:
            _cleanup(mongo, uid)

    def test_rejected_withdrawal_refunds_principal_and_fee(self, api, admin, mongo):
        """FIX #2: admin reject must credit back BOTH principal and the withdrawal fee."""
        uid, token = _mk_user(mongo, "b3rej", balance=300.0)
        try:
            r = _withdraw(api, token, 100)
            assert r.status_code == 200, r.text[:300]
            wd_id = r.json()["withdrawal"]["wd_id"]
            assert wallet_balance(api, token) == 198.0
            rr = api.post(f"{BASE_URL}/api/admin/withdrawals/{wd_id}/reject",
                          headers=auth(admin["token"]), timeout=30)
            assert rr.status_code == 200, rr.text[:300]
            bal = wallet_balance(api, token)
            assert abs(bal - 300.0) < 1e-6, f"fee not refunded on reject: balance={bal} (expected 300)"
            txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(token), timeout=30).json()
            fee_back = [t for t in txs if "Frais retrait remboursés" in t["label"] and t["amount"] == 2.0]
            assert fee_back, f"no 'Frais retrait remboursés' inflow tx: {[t['label'] for t in txs]}"
            assert fee_back[0]["type"] == "in"
            _assert_balanced(api, admin["token"])
        finally:
            _cleanup(mongo, uid)

    def test_rejected_small_withdrawal_fee_1_restores_100(self, api, admin, mongo):
        """FIX #2 exact scenario: balance 100, withdraw 50 (fee 1) -> 49, reject -> 100."""
        uid, token = _mk_user(mongo, "b3rej2", balance=100.0)
        try:
            r = _withdraw(api, token, 50)
            assert r.status_code == 200, r.text[:300]
            wd = r.json()["withdrawal"]
            assert wd["fee_cc"] == 1.0, wd
            assert abs(wallet_balance(api, token) - 49.0) < 1e-6
            rr = api.post(f"{BASE_URL}/api/admin/withdrawals/{wd['wd_id']}/reject",
                          headers=auth(admin["token"]), timeout=30)
            assert rr.status_code == 200, rr.text[:300]
            assert abs(wallet_balance(api, token) - 100.0) < 1e-6
        finally:
            _cleanup(mongo, uid)


# ================= Refund engine =================
class TestRefund:
    def test_partial_then_over_then_full(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 100)
        b0 = wallet_balance(api, payer["token"])
        r = api.post(f"{BASE_URL}/api/refunds",
                     json={"original_tx_id": tx_id, "amount": 30, "reason": "TEST partial"},
                     headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert d["status"] == "COMPLETED" and d["amount"] == 30 and d["fully_refunded"] is False
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 30)) < 1e-6
        txs = api.get(f"{BASE_URL}/api/transactions", headers=auth(payer["token"]), timeout=30).json()
        assert any(t["category"] == "Remboursement" and t["amount"] == 30 for t in txs)

        # over-refund
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id, "amount": 80},
                     headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 409, (r.status_code, r.text[:200])
        assert "REFUND_EXCEEDS_PRINCIPAL_OR_REVERSED" in r.text
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 30)) < 1e-6

        # remaining
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id, "amount": 70},
                     headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:400]
        assert r.json()["fully_refunded"] is True
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 100)) < 1e-6
        _assert_balanced(api, admin["token"])

    def test_refund_not_found(self, api, admin):
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": "tx_does_not_exist"},
                     headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 404, (r.status_code, r.text[:200])

    def test_refund_inflow_rejected(self, api, admin, payer, mongo):
        """An INFLOW tx (created via add_transaction, so it has a ledger entry ref) -> 400."""
        r = _withdraw(api, payer["token"], 15)
        assert r.status_code == 200, r.text[:300]
        wd_id = r.json()["withdrawal"]["wd_id"]
        rr = api.post(f"{BASE_URL}/api/admin/withdrawals/{wd_id}/reject",
                      headers=auth(admin["token"]), timeout=30)
        assert rr.status_code == 200, rr.text[:300]
        inflow = mongo.transactions.find_one(
            {"user_id": payer["user_id"], "category": "Retrait", "amount": 15})
        assert inflow, "expected inflow 'Retrait refusé' tx"
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": inflow["tx_id"]},
                     headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        assert "ONLY_OUTFLOW_REFUNDABLE" in r.text

    def test_refund_of_a_refund_tx_returns_400(self, api, admin, payer, mongo):
        """FIX #3: an EXISTING but non-refundable inflow (Remboursement) -> 400
        ONLY_OUTFLOW_REFUNDABLE, never 404. 404 only for truly missing txs."""
        inflow = mongo.transactions.find_one({"user_id": payer["user_id"], "category": "Remboursement"})
        assert inflow, "need a Remboursement tx"
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": inflow["tx_id"]},
                     headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        assert "ONLY_OUTFLOW_REFUNDABLE" in r.text

    def test_refund_of_reversal_tx_returns_400(self, api, admin, payer, mongo):
        """FIX #3: an Extourne inflow tx must also give 400, not 404."""
        tx_id = _outflow_tx(api, payer, 12)
        rv = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id, "reason": "TEST rev400"},
                      headers=auth(admin["token"]), timeout=60)
        assert rv.status_code == 200, rv.text[:300]
        rvid = rv.json()["reversal_id"]
        inflow = mongo.transactions.find_one({"ref": rvid, "category": "Extourne"})
        assert inflow and inflow["amount"] > 0, inflow
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": inflow["tx_id"]},
                     headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        assert "ONLY_OUTFLOW_REFUNDABLE" in r.text

    def test_refund_requires_admin(self, api, payer):
        tx_id = _outflow_tx(api, payer, 10)
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id},
                     headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 403, r.status_code

    def test_refund_default_amount_is_full_principal(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 40)
        b0 = wallet_balance(api, payer["token"])
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id},
                     headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["amount"] == 40 and r.json()["fully_refunded"] is True
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 40)) < 1e-6

    def test_refund_zero_or_negative_rejected(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 20)
        for amt in (0, -5):
            r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id, "amount": amt},
                         headers=auth(admin["token"]), timeout=30)
            assert r.status_code == 400, (amt, r.status_code, r.text[:200])


# ================= Reversal engine =================
class TestReversal:
    def test_reversal_restores_balance_and_double_reverse_409(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 60)
        b0 = wallet_balance(api, payer["token"])
        r = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id, "reason": "TEST rev"},
                     headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:400]
        assert r.json()["status"] == "COMPLETED"
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 60)) < 1e-6

        r2 = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id, "reason": "TEST rev2"},
                      headers=auth(admin["token"]), timeout=60)
        assert r2.status_code == 409, (r2.status_code, r2.text[:200])
        assert "ALREADY_REVERSED_OR_REFUNDED" in r2.text
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 60)) < 1e-6

        # refund of a reversed tx -> 409
        r3 = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id, "amount": 10},
                      headers=auth(admin["token"]), timeout=60)
        assert r3.status_code == 409, (r3.status_code, r3.text[:200])
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 60)) < 1e-6
        _assert_balanced(api, admin["token"])

    def test_reverse_after_refund_409(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 50)
        r = api.post(f"{BASE_URL}/api/refunds", json={"original_tx_id": tx_id, "amount": 10},
                     headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        b = wallet_balance(api, payer["token"])
        r2 = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id},
                      headers=auth(admin["token"]), timeout=60)
        assert r2.status_code == 409, (r2.status_code, r2.text[:200])
        assert wallet_balance(api, payer["token"]) == b
        _assert_balanced(api, admin["token"])

    def test_reversal_not_found_and_requires_admin(self, api, admin, payer):
        r = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": "tx_nope"},
                     headers=auth(admin["token"]), timeout=30)
        assert r.status_code == 404, r.status_code
        tx_id = _outflow_tx(api, payer, 10)
        r = api.post(f"{BASE_URL}/api/reversals", json={"original_tx_id": tx_id},
                     headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 403, r.status_code


# ================= Idempotency =================
class TestIdempotency:
    def test_refund_idem_replay_no_double_credit(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 30)
        b0 = wallet_balance(api, payer["token"])
        key = f"TEST-rf-{uuid.uuid4().hex[:8]}"
        payload = {"original_tx_id": tx_id, "amount": 10, "reason": "TEST idem"}
        r1 = api.post(f"{BASE_URL}/api/refunds", json=payload,
                      headers=auth(admin["token"], {"Idempotency-Key": key}), timeout=60)
        assert r1.status_code == 200, r1.text[:300]
        r2 = api.post(f"{BASE_URL}/api/refunds", json=payload,
                      headers=auth(admin["token"], {"Idempotency-Key": key}), timeout=60)
        assert r2.status_code == 200, r2.text[:300]
        assert r1.json()["refund_id"] == r2.json()["refund_id"]
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 10)) < 1e-6
        # same key, different payload -> 409
        r3 = api.post(f"{BASE_URL}/api/refunds",
                      json={"original_tx_id": tx_id, "amount": 5, "reason": "TEST idem"},
                      headers=auth(admin["token"], {"Idempotency-Key": key}), timeout=60)
        assert r3.status_code == 409, (r3.status_code, r3.text[:200])

    def test_reversal_idem_replay_no_double_credit(self, api, admin, payer):
        tx_id = _outflow_tx(api, payer, 25)
        b0 = wallet_balance(api, payer["token"])
        key = f"TEST-rv-{uuid.uuid4().hex[:8]}"
        payload = {"original_tx_id": tx_id, "reason": "TEST idem rev"}
        r1 = api.post(f"{BASE_URL}/api/reversals", json=payload,
                      headers=auth(admin["token"], {"Idempotency-Key": key}), timeout=60)
        assert r1.status_code == 200, r1.text[:300]
        r2 = api.post(f"{BASE_URL}/api/reversals", json=payload,
                      headers=auth(admin["token"], {"Idempotency-Key": key}), timeout=60)
        assert r2.status_code == 200, r2.text[:300]
        assert r1.json()["reversal_id"] == r2.json()["reversal_id"]
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 25)) < 1e-6
        _assert_balanced(api, admin["token"])


# ================= TRUE CONCURRENCY =================
class TestConcurrency:
    def test_concurrent_partial_refunds_never_over_refund(self, api, admin, payer, mongo):
        principal = 100.0
        tx_id = _outflow_tx(api, payer, principal)
        b0 = wallet_balance(api, payer["token"])
        payloads = [{"original_tx_id": tx_id, "amount": 30, "reason": f"TEST conc {i}"} for i in range(10)]
        results = _post_threaded(f"{BASE_URL}/api/refunds", payloads, auth(admin["token"]))
        codes = [c for c, _ in results]
        ok = codes.count(200)
        print(f"concurrent refunds codes={sorted(codes)}")
        assert ok == 3, f"expected exactly 3 accepted refunds of 30 within principal 100, got {ok}: {codes}"
        assert all(c in (200, 409) for c in codes), codes
        # DB truth
        orig = mongo.transactions.find_one({"tx_id": tx_id})
        assert round(orig.get("refunded_cc", 0), 2) <= principal, orig.get("refunded_cc")
        assert round(orig.get("refunded_cc", 0), 2) == round(ok * 30, 2)
        total_refunds = sum(r["amount"] for r in mongo.refunds.find({"original_tx_id": tx_id}))
        assert round(total_refunds, 2) == round(ok * 30, 2)
        assert abs(wallet_balance(api, payer["token"]) - (b0 + ok * 30)) < 1e-6
        _assert_balanced(api, admin["token"])

    def test_concurrent_reversals_exactly_one_winner(self, api, admin, payer, mongo):
        tx_id = _outflow_tx(api, payer, 45)
        b0 = wallet_balance(api, payer["token"])
        payloads = [{"original_tx_id": tx_id, "reason": f"TEST conc rev {i}"} for i in range(8)]
        results = _post_threaded(f"{BASE_URL}/api/reversals", payloads, auth(admin["token"]))
        codes = [c for c, _ in results]
        print(f"concurrent reversals codes={sorted(codes)}")
        assert codes.count(200) == 1, f"expected exactly 1 winner, got {codes}"
        assert all(c in (200, 409) for c in codes), codes
        assert mongo.reversals.count_documents({"original_tx_id": tx_id}) == 1
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 45)) < 1e-6
        _assert_balanced(api, admin["token"])

    def test_concurrent_refund_and_reversal_mutually_exclusive(self, api, admin, payer, mongo):
        tx_id = _outflow_tx(api, payer, 50)
        b0 = wallet_balance(api, payer["token"])

        def refund():
            return requests.post(f"{BASE_URL}/api/refunds",
                                 json={"original_tx_id": tx_id, "amount": 50, "reason": "TEST mix"},
                                 headers=auth(admin["token"]), timeout=90)

        def reversal():
            return requests.post(f"{BASE_URL}/api/reversals",
                                 json={"original_tx_id": tx_id, "reason": "TEST mix"},
                                 headers=auth(admin["token"]), timeout=90)

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1, f2 = ex.submit(refund), ex.submit(reversal)
            r1, r2 = f1.result(), f2.result()
        oks = [r for r in (r1, r2) if r.status_code == 200]
        print(f"mixed refund/reversal codes={[r1.status_code, r2.status_code]}")
        assert len(oks) >= 1, (r1.text[:200], r2.text[:200])
        assert len(oks) == 1, "refund AND reversal both applied to the same tx (double credit)"
        assert abs(wallet_balance(api, payer["token"]) - (b0 + 50)) < 1e-6
        _assert_balanced(api, admin["token"])


# ================= Integrity + honesty =================
class TestIntegrityAndStatus:
    def test_financial_health(self, api, admin):
        h = _health(api, admin["token"])
        assert h["ledger_balanced"] is True, h["per_asset_sum"]
        assert h["jcc_supply_reconciled"] is True, h
        assert h["holds_health"]["healthy"] is True, h["holds_health"]
        assert isinstance(h["refunds"], int) and isinstance(h["reversals"], int)
        assert h["severity"] == "INFO", h

    def test_ledger_integrity_no_cache_mismatch(self, api, admin):
        r = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=auth(admin["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["balanced"] is True, d["per_asset_sum"]
        assert d["cache_mismatches"] == [], d["cache_mismatches"]

    def test_capabilities_honest(self, api, payer):
        r = api.get(f"{BASE_URL}/api/system/status", headers=auth(payer["token"]), timeout=30)
        assert r.status_code == 200, r.text[:300]
        caps = r.json()["capabilities"]
        assert caps["refund_engine"] == "REAL"
        assert caps["reversal_engine"] == "REAL"
        assert caps["fees_engine"] == "REAL"
        assert caps["settlement_engine"] == "PARTIAL"
        assert caps["outbox_events"] == "PLANNED"
        assert caps["payments_deposit_stripe"] == "SANDBOX"
        assert caps["card_issuing"] == "MOCK"

    def test_refunds_reversals_lists_admin_only(self, api, admin, payer):
        for path in ("/api/refunds", "/api/reversals"):
            r = api.get(f"{BASE_URL}{path}", headers=auth(admin["token"]), timeout=30)
            assert r.status_code == 200, r.text[:200]
            assert isinstance(r.json(), list)
            assert all("_id" not in x for x in r.json())
            r = api.get(f"{BASE_URL}{path}", headers=auth(payer["token"]), timeout=30)
            assert r.status_code == 403, (path, r.status_code)
