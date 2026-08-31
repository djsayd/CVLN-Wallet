"""P0.1-B: idempotency extension (marketplace buy, coffre move) + production kill-switches.

NOTE: kill-switches are GLOBAL app settings. pytest.ini forces `-n 2 --dist loadscope`, so
running the whole `tests/` dir can make TestKillSwitch (which suspends withdrawals/agents for
a few ms) collide with backend_test.py withdrawal tests on the other worker (spurious 503).
Run this file on its own (`pytest tests/test_p01b_idem_killswitch.py`) for a clean signal.
"""
import uuid

import pytest

from conftest import BASE_URL, auth, wallet_balance, _mk_user, _cleanup


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def killswitch_reset(mongo):
    """Guarantee kill-switches are OFF before and after this module."""
    mongo.settings.update_one({"key": "app"},
                              {"$set": {"ks_withdrawals": False, "ks_card": False, "ks_agents": False}},
                              upsert=True)
    yield
    mongo.settings.update_one({"key": "app"},
                              {"$set": {"ks_withdrawals": False, "ks_card": False, "ks_agents": False}},
                              upsert=True)


def set_ks(api, token, name, enabled):
    return api.put(f"{BASE_URL}/api/admin/kill-switch", json={"name": name, "enabled": enabled},
                   headers=auth(token), timeout=30)


# ---------- Idempotency: POST /api/marketplace/buy ----------
class TestMarketplaceIdempotency:
    def test_same_key_debits_once(self, api, admin_user):
        t = admin_user["token"]
        before = wallet_balance(api, t)
        key = f"TESTMX-{uuid.uuid4().hex[:10]}"
        h = auth(t, {"Idempotency-Key": key})

        r1 = api.post(f"{BASE_URL}/api/marketplace/buy", json={"item_id": "mk1"}, headers=h, timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        assert r1.json()["ok"] is True
        assert r1.json()["transaction"]["amount"] == -150

        mid = wallet_balance(api, t)
        assert mid == before - 150, f"first buy should debit 150 (before={before}, after={mid})"

        r2 = api.post(f"{BASE_URL}/api/marketplace/buy", json={"item_id": "mk1"}, headers=h, timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        # replayed cached response = same transaction id
        assert r2.json()["transaction"]["tx_id"] == r1.json()["transaction"]["tx_id"]

        after = wallet_balance(api, t)
        assert after == mid, f"replay must NOT debit again (mid={mid}, after={after})"

    def test_same_key_different_body_conflict(self, api, admin_user):
        t = admin_user["token"]
        key = f"TESTMX-{uuid.uuid4().hex[:10]}"
        h = auth(t, {"Idempotency-Key": key})
        r1 = api.post(f"{BASE_URL}/api/marketplace/buy", json={"item_id": "mk1"}, headers=h, timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        before = wallet_balance(api, t)

        r2 = api.post(f"{BASE_URL}/api/marketplace/buy", json={"item_id": "mk6"}, headers=h, timeout=30)
        assert r2.status_code == 409, f"expected 409, got {r2.status_code}: {r2.text[:200]}"
        assert "IDEMPOTENCY_CONFLICT" in r2.text
        assert wallet_balance(api, t) == before

    def test_no_key_allows_two_debits(self, api, admin_user):
        """Without the header, two identical buys must both apply (no accidental dedupe)."""
        t = admin_user["token"]
        before = wallet_balance(api, t)
        for _ in range(2):
            r = api.post(f"{BASE_URL}/api/marketplace/buy", json={"item_id": "mk6"},
                         headers=auth(t), timeout=30)
            assert r.status_code == 200, r.text[:300]
        assert wallet_balance(api, t) == before - 240


# ---------- Idempotency: POST /api/coffres/{id}/move ----------
class TestCoffreMoveIdempotency:
    def test_move_applied_once(self, api, admin_user):
        t = admin_user["token"]
        rc = api.post(f"{BASE_URL}/api/coffres", json={"name": "TEST_coffre_idem"},
                      headers=auth(t), timeout=30)
        assert rc.status_code == 200, rc.text[:300]
        cid = rc.json()["coffre_id"]
        assert rc.json()["amount_cc"] == 0

        before = wallet_balance(api, t)
        key = f"TESTCF-{uuid.uuid4().hex[:10]}"
        h = auth(t, {"Idempotency-Key": key})

        r1 = api.post(f"{BASE_URL}/api/coffres/{cid}/move", json={"amount": 100}, headers=h, timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        assert r1.json()["coffre"]["amount_cc"] == 100
        assert wallet_balance(api, t) == before - 100

        r2 = api.post(f"{BASE_URL}/api/coffres/{cid}/move", json={"amount": 100}, headers=h, timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json()["coffre"]["amount_cc"] == 100, "replay must not move funds twice"
        assert wallet_balance(api, t) == before - 100

        # persistence check via GET
        rg = api.get(f"{BASE_URL}/api/coffres", headers=auth(t), timeout=30)
        assert rg.status_code == 200
        got = next(c for c in rg.json() if c["coffre_id"] == cid)
        assert got["amount_cc"] == 100

        # cleanup
        rd = api.delete(f"{BASE_URL}/api/coffres/{cid}", headers=auth(t), timeout=30)
        assert rd.status_code == 200

    def test_move_conflict_on_different_amount(self, api, admin_user):
        t = admin_user["token"]
        rc = api.post(f"{BASE_URL}/api/coffres", json={"name": "TEST_coffre_conflict"},
                      headers=auth(t), timeout=30)
        cid = rc.json()["coffre_id"]
        key = f"TESTCF-{uuid.uuid4().hex[:10]}"
        h = auth(t, {"Idempotency-Key": key})
        r1 = api.post(f"{BASE_URL}/api/coffres/{cid}/move", json={"amount": 50}, headers=h, timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        r2 = api.post(f"{BASE_URL}/api/coffres/{cid}/move", json={"amount": 60}, headers=h, timeout=30)
        assert r2.status_code == 409 and "IDEMPOTENCY_CONFLICT" in r2.text, r2.text[:200]
        api.delete(f"{BASE_URL}/api/coffres/{cid}", headers=auth(t), timeout=30)


# ---------- Kill-switches (single class: global shared state must not run in parallel) ----------
class TestKillSwitch:
    def test_invalid_name_400(self, api, admin_user, killswitch_reset):
        r = set_ks(api, admin_user["token"], "bogus", True)
        assert r.status_code == 400, r.text[:200]

    def test_non_admin_forbidden(self, api, plain_user, killswitch_reset):
        r = set_ks(api, plain_user["token"], "withdrawals", True)
        assert r.status_code == 403, r.text[:200]
        # ensure it did not take effect
        rw = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 1, "iban": "TEST"},
                      headers=auth(plain_user["token"]), timeout=30)
        assert rw.status_code == 200, rw.text[:200]

    def test_unauthenticated_rejected(self, api, killswitch_reset):
        r = api.put(f"{BASE_URL}/api/admin/kill-switch", json={"name": "withdrawals", "enabled": True}, timeout=30)
        assert r.status_code in (401, 403), r.status_code


    # --- withdrawals ---
    def test_suspend_then_resume(self, api, admin_user, killswitch_reset):
        t = admin_user["token"]
        assert set_ks(api, t, "withdrawals", True).status_code == 200
        before = wallet_balance(api, t)

        r = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 10, "iban": "TEST_IBAN"},
                     headers=auth(t), timeout=30)
        assert r.status_code == 503, f"expected 503, got {r.status_code}: {r.text[:200]}"
        detail = r.json().get("detail", "")
        assert detail.startswith("OPERATION_SUSPENDED"), detail
        assert wallet_balance(api, t) == before, "suspended withdrawal must not debit"

        assert set_ks(api, t, "withdrawals", False).status_code == 200
        r2 = api.post(f"{BASE_URL}/api/withdrawals", json={"amount_cc": 10, "iban": "TEST_IBAN"},
                      headers=auth(t), timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json()["withdrawal"]["amount_cc"] == 10
        assert wallet_balance(api, t) == before - 10


    # --- agents (+ card branch) ---
    @pytest.fixture(scope="class")
    def agent_ctx(self, api, mongo, killswitch_reset):
        uid, token = _mk_user(mongo, "agentowner", is_admin=True, balance=1000.0)
        # seed the card for the owner
        rcard = api.get(f"{BASE_URL}/api/card", headers=auth(token), timeout=30)
        assert rcard.status_code == 200, rcard.text[:200]
        ra = api.post(f"{BASE_URL}/api/admin/agents",
                      json={"name": "TEST_agent_ks", "scopes": ["read", "request", "sign", "execute"],
                            "spending_limit_cc": 500},
                      headers=auth(token), timeout=30)
        assert ra.status_code == 200, ra.text[:300]
        agent = ra.json()
        yield {"uid": uid, "token": token, "agent_id": agent["agent_id"], "agent_token": agent["agent_token"]}
        mongo.agents.delete_many({"agent_id": agent["agent_id"]})
        mongo.agent_intents.delete_many({"agent_id": agent["agent_id"]})
        mongo.audit_logs.delete_many({"actor": {"$in": [agent["agent_id"], uid]}})
        mongo.cards.delete_many({"user_id": uid})
        _cleanup(mongo, uid)

    def _mk_confirmed_intent(self, api, ctx, amount=50):
        ri = api.post(f"{BASE_URL}/api/agent/intent",
                      json={"skill": "Card.Pay", "params": {"amount_cc": amount, "merchant": "TEST_M",
                                                            "payment_type": "online"}},
                      headers={"X-Agent-Token": ctx["agent_token"]}, timeout=30)
        assert ri.status_code == 200, ri.text[:300]
        iid = ri.json()["intent_id"]
        rcf = api.post(f"{BASE_URL}/api/agent/intent/{iid}/confirm", headers=auth(ctx["token"]), timeout=30)
        assert rcf.status_code == 200, rcf.text[:300]
        return iid

    def test_execute_suspended_then_resumed(self, api, admin_user, agent_ctx):
        adm = admin_user["token"]
        iid = self._mk_confirmed_intent(api, agent_ctx)
        before = wallet_balance(api, agent_ctx["token"])

        assert set_ks(api, adm, "agents", True).status_code == 200
        r = api.post(f"{BASE_URL}/api/agent/intent/{iid}/execute",
                     headers={"X-Agent-Token": agent_ctx["agent_token"]}, timeout=30)
        assert r.status_code == 503, f"expected 503, got {r.status_code}: {r.text[:200]}"
        assert r.json().get("detail", "").startswith("OPERATION_SUSPENDED")
        assert wallet_balance(api, agent_ctx["token"]) == before

        assert set_ks(api, adm, "agents", False).status_code == 200
        r2 = api.post(f"{BASE_URL}/api/agent/intent/{iid}/execute",
                      headers={"X-Agent-Token": agent_ctx["agent_token"]}, timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json()["result"]["amount_cc"] == 50
        assert wallet_balance(api, agent_ctx["token"]) == before - 50

    def test_card_killswitch_blocks_intent_creation(self, api, admin_user, agent_ctx):
        adm = admin_user["token"]
        assert set_ks(api, adm, "card", True).status_code == 200
        r = api.post(f"{BASE_URL}/api/agent/intent",
                     json={"skill": "Card.Pay", "params": {"amount_cc": 20, "merchant": "TEST_M"}},
                     headers={"X-Agent-Token": agent_ctx["agent_token"]}, timeout=30)
        assert r.status_code == 503, f"expected 503, got {r.status_code}: {r.text[:200]}"
        assert r.json().get("detail", "").startswith("OPERATION_SUSPENDED")
        assert set_ks(api, adm, "card", False).status_code == 200
        r2 = api.post(f"{BASE_URL}/api/agent/intent",
                      json={"skill": "Card.Pay", "params": {"amount_cc": 20, "merchant": "TEST_M"}},
                      headers={"X-Agent-Token": agent_ctx["agent_token"]}, timeout=30)
        assert r2.status_code == 200, r2.text[:300]

    def test_card_killswitch_should_also_block_execute(self, api, admin_user, agent_ctx):
        """Gap probe: an intent prepared BEFORE the card kill-switch is flipped can still be
        executed while card ops are suspended (execute only checks ks_agents)."""
        adm = admin_user["token"]
        iid = self._mk_confirmed_intent(api, agent_ctx, amount=25)
        before = wallet_balance(api, agent_ctx["token"])
        assert set_ks(api, adm, "card", True).status_code == 200
        try:
            r = api.post(f"{BASE_URL}/api/agent/intent/{iid}/execute",
                         headers={"X-Agent-Token": agent_ctx["agent_token"]}, timeout=30)
            after = wallet_balance(api, agent_ctx["token"])
            assert r.status_code == 503, (
                f"card kill-switch NOT enforced on execute: status={r.status_code}, "
                f"balance {before} -> {after}")
        finally:
            assert set_ks(api, adm, "card", False).status_code == 200


    # --- observability ---
    def test_settings_exposes_kill_switch_state(self, api, admin_user, killswitch_reset):
        t = admin_user["token"]
        assert set_ks(api, t, "withdrawals", True).status_code == 200
        try:
            r = api.get(f"{BASE_URL}/api/admin/settings", headers=auth(t), timeout=30)
            assert r.status_code == 200, r.text[:300]
            d = r.json()
            assert "_id" not in d, "settings response leaks Mongo _id"
            assert d.get("ks_withdrawals") is True, f"kill-switch state not readable: {d}"
        finally:
            assert set_ks(api, t, "withdrawals", False).status_code == 200


# ---------- No regression: ledger integrity ----------
class TestLedgerRegression:
    def test_financial_health(self, api, admin_user):
        r = api.get(f"{BASE_URL}/api/admin/financial-health", headers=auth(admin_user["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["ledger_balanced"] is True, d
        assert d["idempotency_in_progress"] == 0, f"stuck PROCESSING idempotency records: {d}"

    def test_ledger_integrity(self, api, admin_user):
        r = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=auth(admin_user["token"]), timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["balanced"] is True, d

    def test_backfill_still_works(self, api, admin_user):
        r = api.post(f"{BASE_URL}/api/admin/ledger/backfill", headers=auth(admin_user["token"]), timeout=90)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["ok"] is True
        r2 = api.get(f"{BASE_URL}/api/admin/ledger/integrity", headers=auth(admin_user["token"]), timeout=60)
        assert r2.json()["balanced"] is True
