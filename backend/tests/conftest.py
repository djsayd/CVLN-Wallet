import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

frontend_env = dotenv_values("/app/frontend/.env")
backend_env = dotenv_values("/app/backend/.env")

base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

MONGO_URL = os.environ.get("MONGO_URL") or backend_env.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME") or backend_env.get("DB_NAME")


@pytest.fixture(scope="session")
def mongo():
    cli = MongoClient(MONGO_URL)
    yield cli[DB_NAME]
    cli.close()


def _mk_user(mongo, suffix, is_admin=False, balance=0.0, with_ledger=True):
    """Seed a user + session. Balance (if any) is created through a BALANCED ledger
    entry (issuance -> user cash) so the dataset stays consistent/reconciled."""
    uid = f"TEST_ta_{suffix}_{uuid.uuid4().hex[:6]}"
    token = f"TEST_tok_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    mongo.users.insert_one({
        "user_id": uid, "email": f"{uid}@test.local", "name": f"TEST {suffix}",
        "frek_id": f"FREK-{uid[:12]}", "is_admin": is_admin, "balance_cc": balance,
        "balance_minor": int(round(balance * 100)), "held_cc": 0.0, "held_minor": 0,
        "kyc_status": "not_started", "created_at": now.isoformat(),
    })
    mongo.user_sessions.insert_one({
        "user_id": uid, "session_token": token,
        "expires_at": (now + timedelta(days=7)).isoformat(), "created_at": now.isoformat(),
    })
    if balance and with_ledger:
        mongo.ledger_entries.insert_one({
            "entry_id": f"le_TEST_{uuid.uuid4().hex[:10]}",
            "idempotency_key": f"TEST_seed_{uid}",
            "description": "TEST seed funding", "category": "Reward", "asset": "JCC", "ref": None,
            "postings": [{"account_id": f"acct_cash_{uid}", "amount": balance},
                         {"account_id": "acct_sys_issuance", "amount": -balance}],
            "created_at": now.isoformat(),
        })
    return uid, token


def _cleanup(mongo, uid):
    mongo.users.delete_many({"user_id": uid})
    mongo.user_sessions.delete_many({"user_id": uid})
    mongo.transactions.delete_many({"user_id": uid})
    mongo.withdrawals.delete_many({"user_id": uid})
    mongo.idempotency_records.delete_many({"user_id": uid})
    coffre_ids = [c["coffre_id"] for c in mongo.coffres.find({"user_id": uid}, {"coffre_id": 1})]
    mongo.coffres.delete_many({"user_id": uid})
    accounts = [f"acct_cash_{uid}"] + [f"acct_coffre_{c}" for c in coffre_ids]
    mongo.ledger_entries.delete_many({"postings.account_id": {"$in": accounts}})


@pytest.fixture(scope="module")
def admin_user(mongo):
    uid, token = _mk_user(mongo, "admin", is_admin=True, balance=1000.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)


@pytest.fixture(scope="module")
def plain_user(mongo):
    uid, token = _mk_user(mongo, "user", is_admin=False, balance=500.0)
    yield {"user_id": uid, "token": token}
    _cleanup(mongo, uid)


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def auth(token, extra=None):
    h = {"Authorization": f"Bearer {token}"}
    if extra:
        h.update(extra)
    return h


def wallet_balance(api, token):
    r = api.get(f"{BASE_URL}/api/wallet", headers=auth(token), timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()["balance_cc"]
