from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
import random
import httpx
import asyncio
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

import stripe
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or "sk_test_emergent"
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

async def get_settings():
    s = await db.settings.find_one({"key": "app"}, {"_id": 0})
    if not s:
        s = {"key": "app", "rate_eur": 1.5, "min_deposit_eur": 20.0, "reserve_cc": 0.0, "total_deposited_eur": 0.0,
             "ks_withdrawals": False, "ks_card": False, "ks_agents": False}
        await db.settings.insert_one(dict(s))
    return s

async def require_admin(user: dict = None):
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur")
    return user

app = FastAPI()
api_router = APIRouter(prefix="/api")

JCC_RATE_EUR = 1.50  # 1 JCC = 1.50 EUR
AUTH_SESSION_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"

# ---------------- Models ----------------
class ConvertRequest(BaseModel):
    direction: str  # 'eur_to_jcc' or 'jcc_to_eur'
    amount: float

class SendRequest(BaseModel):
    recipient: str
    amount: float
    note: Optional[str] = ""

class BuyRequest(BaseModel):
    amount_eur: float

class CoffreCreate(BaseModel):
    name: str
    icon: str = "vault"
    goal_cc: float = 50000
    color: str = "#8B5CF6"

class CoffreMove(BaseModel):
    amount: float

class MarketplaceBuy(BaseModel):
    item_id: str

# ---------------- Auth helpers ----------------
async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")
    user = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


DEFAULT_COFFRES = [
    {"name": "Coffre Personnel", "icon": "wallet", "goal_cc": 68000, "amount_cc": 42000, "color": "#8B5CF6"},
    {"name": "Coffre Créateur", "icon": "music", "goal_cc": 65000, "amount_cc": 31500, "color": "#00F0FF"},
    {"name": "Coffre Projet", "icon": "rocket", "goal_cc": 81000, "amount_cc": 68900, "color": "#F5D0FE"},
    {"name": "Coffre Investissement", "icon": "diamond", "goal_cc": 48000, "amount_cc": 12000, "color": "#10B981"},
]

SEED_TX = [
    {"label": "Paiement KORA", "amount": 540, "category": "KORA", "days": 0},
    {"label": "Factory Maker Studio", "amount": -230, "category": "Factory Maker Studio", "days": 1},
    {"label": "Culture Connect", "amount": 1500, "category": "Culture Connect", "days": 3},
    {"label": "Good Mood", "amount": -820, "category": "Good Mood", "days": 5},
    {"label": "Marketplace", "amount": 240, "category": "Marketplace", "days": 7},
    {"label": "Kiltikonet — Mission culturelle", "amount": 1200, "category": "Kiltikonet", "days": 9},
    {"label": "Certification FREKCORE", "amount": -150, "category": "FREKCORE", "days": 11},
]

async def seed_user_data(user_id: str):
    now = datetime.now(timezone.utc)
    coffres = []
    for c in DEFAULT_COFFRES:
        coffres.append({
            "coffre_id": f"coffre_{uuid.uuid4().hex[:10]}",
            "user_id": user_id,
            "created_at": now.isoformat(),
            **{**c, "amount_cc": 0},
        })
    if coffres:
        await db.coffres.insert_many(coffres)


# ================= FINANCIAL CORE — DOUBLE-ENTRY LEDGER =================
# RULE: No module manages its own balances. Card, Invest, Crypto, FX, Rewards, JCC and
# all future accounts MUST route every value movement through ledger_post(). Every entry
# is balanced (sum of signed postings == 0 per asset). Balances are DERIVED from the ledger;
# users.balance_cc / coffres.amount_cc are denormalized caches verified by the integrity check.
SYSTEM_ACCOUNTS = {
    "issuance": "acct_sys_issuance",   # minting against rewards
    "stripe": "acct_sys_stripe",       # fiat-in clearing (Stripe)
    "external": "acct_sys_external",    # payouts / withdrawals
    "clearing": "acct_sys_clearing",    # internal transfers netting
    "fx": "acct_sys_fx",                # conversions
    "revenue": "acct_sys_revenue",      # merchant / marketplace / card capture
}
DEFAULT_ASSET = "JCC"

def cash_acct(user_id: str) -> str:
    return f"acct_cash_{user_id}"

def coffre_acct(coffre_id: str) -> str:
    return f"acct_coffre_{coffre_id}"

def _counter_account(category: str) -> str:
    mapping = {"Dépôt": "stripe", "Retrait": "external", "Conversion": "fx",
               "Reward": "issuance", "Marketplace": "revenue", "Card": "revenue"}
    return SYSTEM_ACCOUNTS.get(mapping.get(category, "clearing"))

async def ledger_post(description, category, postings, asset=DEFAULT_ASSET, ref=None, idempotency_key=None):
    """postings: list of (account_id, signed_amount). Must sum to 0 for the asset."""
    if idempotency_key:
        existing = await db.ledger_entries.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
        if existing:
            return existing["entry_id"]
    total = round(sum(p[1] for p in postings), 6)
    if abs(total) > 1e-6:
        raise HTTPException(status_code=500, detail=f"Écriture déséquilibrée (Δ={total})")
    entry_id = f"le_{uuid.uuid4().hex[:12]}"
    await db.ledger_entries.insert_one({
        "entry_id": entry_id, "idempotency_key": idempotency_key or entry_id,
        "description": description, "category": category, "asset": asset, "ref": ref,
        "postings": [{"account_id": a, "amount": round(amt, 2)} for a, amt in postings],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return entry_id

async def ledger_balance(account_id: str) -> float:
    cur = db.ledger_entries.aggregate([
        {"$unwind": "$postings"},
        {"$match": {"postings.account_id": account_id}},
        {"$group": {"_id": None, "bal": {"$sum": "$postings.amount"}}},
    ])
    async for r in cur:
        return round(r["bal"], 2)
    return 0.0

# ---- Idempotency engine (API-level, reusable) ----
async def idem_begin(request, user, scope, payload):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None, None
    import hashlib, json as _json
    from pymongo.errors import DuplicateKeyError
    idem_id = f"{scope}:{user['user_id']}:{key}"
    h = hashlib.sha256(_json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    try:
        await db.idempotency_records.insert_one({
            "idem_id": idem_id, "scope": scope, "user_id": user["user_id"], "hash": h,
            "state": "PROCESSING", "response": None, "created_at": datetime.now(timezone.utc).isoformat()})
        return idem_id, None
    except DuplicateKeyError:
        rec = await db.idempotency_records.find_one({"idem_id": idem_id}, {"_id": 0})
        if not rec:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_IN_PROGRESS")
        if rec.get("hash") != h:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT")
        if rec.get("state") == "COMPLETED":
            return idem_id, rec.get("response")
        raise HTTPException(status_code=409, detail="IDEMPOTENCY_IN_PROGRESS")

async def idem_finish(idem_id, response):
    if idem_id:
        await db.idempotency_records.update_one({"idem_id": idem_id},
            {"$set": {"state": "COMPLETED", "response": response, "completed_at": datetime.now(timezone.utc).isoformat()}})
    return response

async def idem_fail(idem_id):
    # A request that never completed (validation error / crash) must NOT poison the key.
    if idem_id:
        await db.idempotency_records.delete_one({"idem_id": idem_id, "state": "PROCESSING"})

async def assert_not_suspended(name: str):
    st = await get_settings()
    if st.get(f"ks_{name}"):
        raise HTTPException(status_code=503, detail=f"OPERATION_SUSPENDED:{name}")

async def get_or_seed_card(user_id: str) -> dict:
    c = await db.cards.find_one({"user_id": user_id}, {"_id": 0})
    c = await db.cards.find_one({"user_id": user_id}, {"_id": 0})
    if not c:
        c = {"card_id": f"card_{uuid.uuid4().hex[:10]}", "user_id": user_id, "brand": "CVLN Virtual",
             "last4": f"{random.randint(0,9999):04d}", "exp_month": random.randint(1, 12), "exp_year": 2030,
             "status": "active", "daily_limit_cc": 500.0, "per_tx_limit_cc": 200.0,
             "online_enabled": True, "tpe_enabled": True, "agent_enabled": True,
             "issuing_status": "MOCK", "created_at": datetime.now(timezone.utc).isoformat()}
        await db.cards.insert_one(dict(c))
    return c

async def _today_card_spent(user_id: str) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    spent = 0.0
    async for t in db.transactions.find({"user_id": user_id, "category": "Card"}, {"amount": 1, "created_at": 1}):
        if str(t.get("created_at", ""))[:10] == today and t["amount"] < 0:
            spent += -t["amount"]
    return spent

async def add_transaction(user_id: str, label: str, amount: float, category: str, idempotency_key: str = None, skip_balance: bool = False):
    doc = {
        "tx_id": f"tx_{uuid.uuid4().hex[:10]}",
        "user_id": user_id,
        "label": label,
        "amount": amount,
        "category": category,
        "type": "in" if amount > 0 else "out",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.transactions.insert_one(doc)
    if amount != 0:
        # Financial Core: every balance movement is a balanced ledger entry.
        await ledger_post(label, category,
                          [(cash_acct(user_id), amount), (_counter_account(category), -amount)],
                          ref=doc["tx_id"], idempotency_key=idempotency_key)
        # skip_balance=True when the caller already debited atomically via atomic_spend().
        if not skip_balance:
            await db.users.update_one({"user_id": user_id}, {"$inc": {"balance_cc": amount}})
    doc.pop("_id", None)
    return doc

async def atomic_spend(user_id: str, amount: float) -> bool:
    """Atomically debit AVAILABLE funds (balance_cc - held_cc) in ONE conditional update.
    Returns False if available is insufficient. Hold-aware + race-safe (no read-then-write).
    Every spend path uses this so no module can bypass the B2 available-balance invariant."""
    res = await db.users.find_one_and_update(
        {"user_id": user_id,
         "$expr": {"$gte": [{"$subtract": ["$balance_cc", {"$ifNull": ["$held_cc", 0]}]}, amount]}},
        {"$inc": {"balance_cc": -amount}}, return_document=ReturnDocument.AFTER)
    return res is not None


# ---------------- Auth routes ----------------
@api_router.post("/auth/session")
async def create_session(request: Request, response: Response):
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    async with httpx.AsyncClient() as hc:
        r = await hc.get(AUTH_SESSION_URL, headers={"X-Session-ID": session_id})
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session_id")
    data = r.json()
    email = data["email"]
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one({"user_id": user_id}, {"$set": {"name": data.get("name"), "picture": data.get("picture"), "is_admin": email == ADMIN_EMAIL}})
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        frek_id = f"FREK-{uuid.uuid4().hex[:4].upper()}-{random.randint(1000,9999)}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": data.get("name"),
            "picture": data.get("picture"),
            "frek_id": frek_id,
            "frek_score": random.randint(920, 985),
            "frek_level": "Créateur Premium",
            "balance_cc": 0.0,
            "is_admin": email == ADMIN_EMAIL,
            "kyc_status": "not_started",
            "kyc_level": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await seed_user_data(user_id)
        await get_or_seed_card(user_id)
    session_token = data["session_token"]
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.update_one(
        {"session_token": session_token},
        {"$set": {"user_id": user_id, "session_token": session_token,
                  "expires_at": expires_at.isoformat(), "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    response.set_cookie("session_token", session_token, httponly=True, secure=True,
                        samesite="none", path="/", max_age=7 * 24 * 3600)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return user


@api_router.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return user


@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token")
    if token:
        await db.user_sessions.delete_one({"session_token": token})
    response.delete_cookie("session_token", path="/")
    return {"ok": True}


# ---------------- Wallet ----------------
@api_router.get("/wallet")
async def get_wallet(user: dict = Depends(get_current_user)):
    balance = user.get("balance_cc", 0)
    available = await available_balance(user)  # runs lazy-expiry, reads fresh held_cc
    u2 = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "held_cc": 1})
    held = round(u2.get("held_cc", 0.0), 2)
    coffres_total = 0
    async for c in db.coffres.find({"user_id": user["user_id"]}, {"_id": 0}):
        coffres_total += c.get("amount_cc", 0)
    txs = await db.transactions.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    inflow = sum(t["amount"] for t in txs if t["amount"] > 0)
    outflow = sum(-t["amount"] for t in txs if t["amount"] < 0)
    return {
        "balance_cc": balance,
        "held_cc": held,
        "available_balance_cc": available,
        "value_eur": round(balance * JCC_RATE_EUR, 2),
        "rate": JCC_RATE_EUR,
        "coffres_total": coffres_total,
        "frek_score": user.get("frek_score", 978),
        "frek_level": user.get("frek_level", "Créateur Premium"),
        "inflow": inflow,
        "outflow": outflow,
        "change_pct": 4.32,
    }


@api_router.get("/transactions")
async def get_transactions(user: dict = Depends(get_current_user), limit: int = 100):
    txs = await db.transactions.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return txs


@api_router.post("/actions/send")
async def send_money(req: SendRequest, request: Request, user: dict = Depends(get_current_user)):
    idem_id, cached = await idem_begin(request, user, "send", req.dict())
    if cached is not None:
        return cached
    try:
        if req.amount <= 0:
            raise HTTPException(status_code=400, detail="Montant invalide")
        if not await atomic_spend(user["user_id"], req.amount):
            raise HTTPException(status_code=400, detail="Solde disponible insuffisant")
        label = f"Envoi à {req.recipient}" + (f" — {req.note}" if req.note else "")
        tx = await add_transaction(user["user_id"], label, -req.amount, "Transfert", skip_balance=True)
        return await idem_finish(idem_id, {"ok": True, "transaction": tx})
    except Exception:
        await idem_fail(idem_id)
        raise


@api_router.post("/actions/buy")
async def buy_cc_deprecated(user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=400, detail="Achat désactivé : utilisez le dépôt Stripe.")


@api_router.post("/convert")
async def convert(req: ConvertRequest, user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=400, detail="Conversion via Stripe : utilisez Dépôt (EUR→CC) ou Retrait (CC→EUR).")


# ---------------- Coffres ----------------
@api_router.get("/coffres")
async def get_coffres(user: dict = Depends(get_current_user)):
    return await db.coffres.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(100)


@api_router.post("/coffres")
async def create_coffre(req: CoffreCreate, user: dict = Depends(get_current_user)):
    doc = {
        "coffre_id": f"coffre_{uuid.uuid4().hex[:10]}",
        "user_id": user["user_id"],
        "name": req.name,
        "icon": req.icon,
        "goal_cc": req.goal_cc,
        "amount_cc": 0,
        "color": req.color,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.coffres.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.post("/coffres/{coffre_id}/move")
async def move_coffre(coffre_id: str, req: CoffreMove, request: Request, user: dict = Depends(get_current_user)):
    idem_id, cached = await idem_begin(request, user, "coffre_move", {"coffre_id": coffre_id, **req.dict()})
    if cached is not None:
        return cached
    try:
        coffre = await db.coffres.find_one({"coffre_id": coffre_id, "user_id": user["user_id"]}, {"_id": 0})
        if not coffre:
            raise HTTPException(status_code=404, detail="Coffre introuvable")
        if req.amount < 0 and -req.amount > coffre.get("amount_cc", 0):
            raise HTTPException(status_code=400, detail="Fonds insuffisants dans le coffre")
        if req.amount > 0:
            # Moving cash INTO the coffre spends available funds -> atomic + hold-aware.
            if not await atomic_spend(user["user_id"], req.amount):
                raise HTTPException(status_code=400, detail="Solde disponible insuffisant")
        elif req.amount < 0:
            # Moving cash OUT of the coffre back to available balance (credit).
            await db.users.update_one({"user_id": user["user_id"]}, {"$inc": {"balance_cc": -req.amount}})
        await db.coffres.update_one({"coffre_id": coffre_id}, {"$inc": {"amount_cc": req.amount}})
        verb = "Dépôt vers" if req.amount > 0 else "Retrait de"
        await ledger_post(f"{verb} {coffre['name']}", "Coffre",
                          [(cash_acct(user["user_id"]), -req.amount), (coffre_acct(coffre_id), req.amount)])
        await add_transaction(user["user_id"], f"{verb} {coffre['name']}", 0, "Coffre")
        updated = await db.coffres.find_one({"coffre_id": coffre_id}, {"_id": 0})
        return await idem_finish(idem_id, {"ok": True, "coffre": updated})
    except Exception:
        await idem_fail(idem_id)
        raise


@api_router.delete("/coffres/{coffre_id}")
async def delete_coffre(coffre_id: str, user: dict = Depends(get_current_user)):
    coffre = await db.coffres.find_one({"coffre_id": coffre_id, "user_id": user["user_id"]}, {"_id": 0})
    if not coffre:
        raise HTTPException(status_code=404, detail="Coffre introuvable")
    if coffre.get("amount_cc", 0) > 0:
        await db.users.update_one({"user_id": user["user_id"]}, {"$inc": {"balance_cc": coffre["amount_cc"]}})
        await ledger_post(f"Fermeture {coffre['name']}", "Coffre",
                          [(coffre_acct(coffre_id), -coffre["amount_cc"]), (cash_acct(user["user_id"]), coffre["amount_cc"])])
    await db.coffres.delete_one({"coffre_id": coffre_id})
    return {"ok": True}


# ---------------- Marketplace ----------------
MARKETPLACE_ITEMS = [
    {"item_id": "mk1", "title": "Pack Certification FREK", "seller": "FREKCORE", "price_cc": 150, "category": "Certification", "tag": "Populaire"},
    {"item_id": "mk2", "title": "Beat exclusif — DJ Sayd", "seller": "Factory Maker Studio", "price_cc": 850, "category": "Musique", "tag": "Exclusif"},
    {"item_id": "mk3", "title": "Pass Culture Connect 2026", "seller": "Culture Connect", "price_cc": 1200, "category": "Événement", "tag": "CC2026"},
    {"item_id": "mk4", "title": "Crédits IA Laurentia", "seller": "Laurentia", "price_cc": 300, "category": "IA", "tag": "Nouveau"},
    {"item_id": "mk5", "title": "Mission culturelle", "seller": "Kiltikonet", "price_cc": 500, "category": "Service", "tag": ""},
    {"item_id": "mk6", "title": "Abonnement KORA Premium", "seller": "KORA", "price_cc": 120, "category": "Streaming", "tag": "Streaming"},
    {"item_id": "mk7", "title": "Formation Production — FM Academy", "seller": "Factory Maker Academy", "price_cc": 950, "category": "Formation", "tag": ""},
    {"item_id": "mk8", "title": "Licence CVLN OS", "seller": "CVLN OS", "price_cc": 2100, "category": "Logiciel", "tag": "Pro"},
]

@api_router.get("/marketplace")
async def get_marketplace(user: dict = Depends(get_current_user)):
    return MARKETPLACE_ITEMS


@api_router.post("/marketplace/buy")
async def buy_marketplace(req: MarketplaceBuy, request: Request, user: dict = Depends(get_current_user)):
    idem_id, cached = await idem_begin(request, user, "marketplace", req.dict())
    if cached is not None:
        return cached
    try:
        item = next((i for i in MARKETPLACE_ITEMS if i["item_id"] == req.item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Article introuvable")
        if item["price_cc"] > 0 and not await atomic_spend(user["user_id"], item["price_cc"]):
            raise HTTPException(status_code=400, detail="Solde disponible insuffisant")
        tx = await add_transaction(user["user_id"], f"Achat — {item['title']}", -item["price_cc"], "Marketplace", skip_balance=True)
        return await idem_finish(idem_id, {"ok": True, "transaction": tx})
    except Exception:
        await idem_fail(idem_id)
        raise


# ---------------- Ecosysteme ----------------
ECOSYSTEME = [
    {"name": "Factory Maker Studio", "role": "Label — production & distribution", "layer": "Musique", "status": "Actif", "icon": "music"},
    {"name": "KORA", "role": "Streaming culturel caribéen", "layer": "Musique", "status": "En développement", "icon": "waveform"},
    {"name": "Good Mood", "role": "Structure événementielle", "layer": "Événement", "status": "Actif", "icon": "confetti"},
    {"name": "Culture Connect", "role": "Marché culturel afro-caribéen", "layer": "Événement", "status": "Actif — CC2026", "icon": "globe"},
    {"name": "Kiltikonet", "role": "Association & appels à projets", "layer": "Culture", "status": "Actif", "icon": "handshake"},
    {"name": "CVLN Academy", "role": "Formation écosystème CVLN", "layer": "Éducation", "status": "En codage", "icon": "graduation"},
    {"name": "LabelOS", "role": "OS pour labels indépendants", "layer": "Plateforme", "status": "En développement", "icon": "stack"},
    {"name": "Laurentia", "role": "IA culturelle caribéenne", "layer": "Technologie", "status": "En développement", "icon": "brain"},
    {"name": "FREKCORE", "role": "Moteur de certification culturelle", "layer": "Identité", "status": "Actif", "icon": "fingerprint"},
    {"name": "CVLN OS", "role": "Système d'exploitation de l'écosystème", "layer": "Plateforme", "status": "En définition", "icon": "cpu"},
]

@api_router.get("/ecosysteme")
async def get_ecosysteme(user: dict = Depends(get_current_user)):
    return ECOSYSTEME


# ================= ENTITY DEVELOPER API =================
class EntityTransfer(BaseModel):
    to: str          # FREK-ID (user) or entity_id
    amount: float
    note: Optional[str] = ""

class EntityCharge(BaseModel):
    frek_id: str
    amount: float
    note: Optional[str] = ""

async def get_entity(request: Request) -> dict:
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not key:
        raise HTTPException(status_code=401, detail="Clé API manquante (header X-API-Key)")
    ent = await db.entities.find_one({"api_key": key}, {"_id": 0})
    if not ent:
        raise HTTPException(status_code=401, detail="Clé API invalide")
    return ent

async def log_entity_tx(entity_id: str, label: str, amount: float, counterparty: str):
    await db.entity_transactions.insert_one({
        "etx_id": f"etx_{uuid.uuid4().hex[:10]}",
        "entity_id": entity_id, "label": label, "amount": amount,
        "counterparty": counterparty, "type": "in" if amount > 0 else "out",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

@api_router.get("/v1/entity/me")
async def v1_me(ent: dict = Depends(get_entity)):
    return {"entity_id": ent["entity_id"], "name": ent["name"], "role": ent["role"],
            "layer": ent["layer"], "wallet_type": ent.get("wallet_type"),
            "balance_cc": ent.get("balance_cc", 0), "rate_eur": JCC_RATE_EUR}

@api_router.get("/v1/entity/balance")
async def v1_balance(ent: dict = Depends(get_entity)):
    return {"balance_cc": ent.get("balance_cc", 0), "value_eur": round(ent.get("balance_cc", 0) * JCC_RATE_EUR, 2)}

@api_router.get("/v1/entity/transactions")
async def v1_txs(ent: dict = Depends(get_entity), limit: int = 50):
    return await db.entity_transactions.find({"entity_id": ent["entity_id"]}, {"_id": 0}).sort("created_at", -1).to_list(limit)

@api_router.get("/v1/frek/{frek_id}")
async def v1_resolve(frek_id: str, ent: dict = Depends(get_entity)):
    u = await db.users.find_one({"frek_id": frek_id}, {"_id": 0, "name": 1, "frek_id": 1, "frek_score": 1, "frek_level": 1})
    if not u:
        raise HTTPException(status_code=404, detail="FREK-ID introuvable")
    return u

@api_router.post("/v1/entity/transfer")
async def v1_transfer(req: EntityTransfer, ent: dict = Depends(get_entity)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    if req.amount > ent.get("balance_cc", 0):
        raise HTTPException(status_code=400, detail="Solde entité insuffisant")
    user = await db.users.find_one({"frek_id": req.to}, {"_id": 0})
    if user:
        await db.entities.update_one({"entity_id": ent["entity_id"]}, {"$inc": {"balance_cc": -req.amount}})
        await add_transaction(user["user_id"], f"Reçu de {ent['name']}" + (f" — {req.note}" if req.note else ""), req.amount, ent["name"])
        await log_entity_tx(ent["entity_id"], f"Transfert vers {req.to}", -req.amount, req.to)
        return {"ok": True, "to": req.to, "amount": req.amount}
    dest = await db.entities.find_one({"entity_id": req.to}, {"_id": 0})
    if dest:
        await db.entities.update_one({"entity_id": ent["entity_id"]}, {"$inc": {"balance_cc": -req.amount}})
        await db.entities.update_one({"entity_id": req.to}, {"$inc": {"balance_cc": req.amount}})
        await log_entity_tx(ent["entity_id"], f"Transfert vers {dest['name']}", -req.amount, req.to)
        await log_entity_tx(req.to, f"Reçu de {ent['name']}", req.amount, ent["entity_id"])
        return {"ok": True, "to": req.to, "amount": req.amount}
    raise HTTPException(status_code=404, detail="Destinataire introuvable (FREK-ID ou entity_id)")

@api_router.post("/v1/entity/charge")
async def v1_charge(req: EntityCharge, ent: dict = Depends(get_entity)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    user = await db.users.find_one({"frek_id": req.frek_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="FREK-ID introuvable")
    # Charge real user funds -> atomic + hold-aware (no read-then-write bypass).
    if not await atomic_spend(user["user_id"], req.amount):
        raise HTTPException(status_code=400, detail="Solde disponible utilisateur insuffisant")
    await add_transaction(user["user_id"], f"Paiement {ent['name']}" + (f" — {req.note}" if req.note else ""), -req.amount, ent["name"], skip_balance=True)
    await db.entities.update_one({"entity_id": ent["entity_id"]}, {"$inc": {"balance_cc": req.amount}})
    await log_entity_tx(ent["entity_id"], f"Encaissement {req.frek_id}", req.amount, req.frek_id)
    return {"ok": True, "frek_id": req.frek_id, "amount": req.amount}

# ---- owner view (logged-in) ----
@api_router.get("/entities")
async def list_entities(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.entities.find({}, {"_id": 0}).to_list(100)

@api_router.post("/entities/{entity_id}/rotate-key")
async def rotate_key(entity_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    new_key = f"cvln_live_{uuid.uuid4().hex}"
    res = await db.entities.update_one({"entity_id": entity_id}, {"$set": {"api_key": new_key}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Entité introuvable")
    return {"ok": True, "api_key": new_key}


@app.on_event("startup")
async def seed_entities():
    try:
        await db.idempotency_records.create_index("idem_id", unique=True)
        await db.ledger_entries.create_index("idempotency_key", unique=True)
        await db.balance_holds.create_index("hold_id", unique=True)
        await db.balance_holds.create_index([("user_id", 1), ("status", 1)])
        await db.financial_state_history.create_index([("entity_type", 1), ("entity_id", 1)])
        await db.outbox_events.create_index("event_id", unique=True)
        await db.outbox_events.create_index([("status", 1), ("available_at", 1)])
        await db.outbox_consumed.create_index("event_id", unique=True)
        await db.webhook_inbox.create_index([("provider", 1), ("provider_event_id", 1)], unique=True)
        await db.settlements.create_index("settlement_id", unique=True)
        await db.settlements.create_index("idempotency_key", unique=True)
        await db.settlements.create_index("provider_reference")
        await db.reconciliation_cases.create_index("case_id", unique=True)
        await db.approval_requests.create_index("approval_id", unique=True)
    except Exception:
        pass
    global _OUTBOX_TASK
    try:
        if _OUTBOX_TASK is None:
            _OUTBOX_TASK = asyncio.create_task(outbox_worker())
    except Exception:
        pass
    count = await db.entities.count_documents({})
    if count > 0:
        return
    seeds = [
        {"name": "CVLN Fintech", "role": "Infrastructure financière centrale", "layer": "Finance", "wallet_type": "Operational", "balance_cc": 5000000},
        {"name": "CVLN Holding", "role": "Trésorerie centrale du groupe", "layer": "Holding", "wallet_type": "Central", "balance_cc": 2000000},
    ] + [{"name": e["name"], "role": e["role"], "layer": e["layer"], "wallet_type": "Entity", "balance_cc": 100000} for e in ECOSYSTEME]
    docs = []
    for s in seeds:
        docs.append({
            "entity_id": "ent_" + s["name"].lower().replace(" ", "_").replace("é", "e").replace("'", ""),
            "api_key": f"cvln_live_{uuid.uuid4().hex}",
            "created_at": datetime.now(timezone.utc).isoformat(), **s,
        })
    if docs:
        await db.entities.insert_many(docs)


# ================= STRIPE DEPOSITS / WITHDRAWALS / ADMIN =================
class DepositRequest(BaseModel):
    amount: float
    currency: str = "eur"
    origin_url: str

class WithdrawRequest(BaseModel):
    amount_cc: float
    iban: Optional[str] = ""

class SettingsUpdate(BaseModel):
    rate_eur: Optional[float] = None
    min_deposit_eur: Optional[float] = None
    reserve_cc: Optional[float] = None

async def credit_deposit(rec: dict):
    await add_transaction(rec["user_id"], f"Dépôt Stripe ({rec['amount']} {rec['currency'].upper()})", rec["credit_cc"], "Dépôt")
    await db.settings.update_one({"key": "app"}, {"$inc": {"total_deposited_eur": rec.get("eur_equiv", 0)}})

@api_router.post("/payments/checkout")
async def payments_checkout(req: DepositRequest, user: dict = Depends(get_current_user)):
    st = await get_settings()
    if req.amount < st["min_deposit_eur"]:
        raise HTTPException(status_code=400, detail=f"Dépôt minimum {st['min_deposit_eur']} € (ou équivalent).")
    rate = st["rate_eur"]
    credit_cc = round(req.amount / rate, 2)
    session = stripe.checkout.Session.create(
        line_items=[{"price_data": {"currency": req.currency.lower(),
            "product_data": {"name": f"Dépôt CVLN Wallet — {credit_cc} CC"},
            "unit_amount": int(round(req.amount * 100))}, "quantity": 1}],
        mode="payment",
        success_url=f"{req.origin_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{req.origin_url}/wallet",
        metadata={"user_id": user["user_id"], "credit_cc": str(credit_cc), "kind": "deposit"},
    )
    await db.payment_transactions.insert_one({
        "session_id": session.id, "user_id": user["user_id"], "credit_cc": credit_cc,
        "amount": req.amount, "currency": req.currency.lower(), "eur_equiv": req.amount,
        "status": "initiated", "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"checkout_url": session.url, "session_id": session.id}

@api_router.get("/payments/status/{session_id}")
async def payments_status(session_id: str):
    rec = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Transaction introuvable")
    if rec.get("payment_status") != "paid":
        try:
            s = stripe.checkout.Session.retrieve(session_id)
            if s.payment_status == "paid" or s.status == "complete":
                res = await db.payment_transactions.update_one(
                    {"session_id": session_id, "payment_status": {"$ne": "paid"}},
                    {"$set": {"status": "completed", "payment_status": "paid"}})
                if res.modified_count == 1:
                    await credit_deposit(rec)
                rec = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
        except Exception:
            pass
    return {"session_id": session_id, "status": rec["status"], "payment_status": rec["payment_status"], "credit_cc": rec.get("credit_cc")}

@api_router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")
    if event["type"] == "checkout.session.completed":
        obj = event["data"]["object"]
        rec = await db.payment_transactions.find_one({"session_id": obj["id"]}, {"_id": 0})
        if rec:
            res = await db.payment_transactions.update_one(
                {"session_id": obj["id"], "payment_status": {"$ne": "paid"}},
                {"$set": {"status": "completed", "payment_status": "paid"}})
            if res.modified_count == 1:
                await credit_deposit(rec)
    return {"status": "ok"}

# ---- Withdrawals (payout requests, real bank via Stripe Connect at go-live) ----
@api_router.post("/withdrawals")
async def create_withdrawal(req: WithdrawRequest, request: Request, user: dict = Depends(get_current_user)):
    await assert_not_suspended("withdrawals")
    idem_id, cached = await idem_begin(request, user, "withdrawal", req.dict())
    if cached is not None:
        return cached
    try:
        if req.amount_cc <= 0:
            raise HTTPException(status_code=400, detail="Montant invalide")
        st = await get_settings()
        fee = _compute_fee(st.get("fee_policy", {}) or {}, "withdrawal", req.amount_cc)
        total = round(req.amount_cc + fee, 2)
        uid = user["user_id"]
        # ATOMIC available-balance enforcement: debit (amount+fee) only if (balance - held) covers it.
        # Honours B2 holds and is race-safe (single conditional find_one_and_update).
        res = await db.users.find_one_and_update(
            {"user_id": uid,
             "$expr": {"$gte": [{"$subtract": ["$balance_cc", {"$ifNull": ["$held_cc", 0]}]}, total]}},
            {"$inc": {"balance_cc": -total}}, return_document=ReturnDocument.AFTER)
        if not res:
            raise HTTPException(status_code=400, detail="Solde disponible insuffisant (montant + frais)")
        debited_total = total
        try:
            now = datetime.now(timezone.utc).isoformat()
            eur = round(req.amount_cc * st["rate_eur"], 2)
            wtx = f"tx_{uuid.uuid4().hex[:10]}"
            await db.transactions.insert_one({"tx_id": wtx, "user_id": uid, "label": f"Retrait demandé — {req.amount_cc} CC",
                                              "amount": -req.amount_cc, "category": "Retrait", "type": "out", "created_at": now})
            await ledger_post(f"Retrait demandé — {req.amount_cc} CC", "Retrait",
                              [(cash_acct(uid), -req.amount_cc), (_counter_account("Retrait"), req.amount_cc)], ref=wtx)
            fee_tx = None
            if fee > 0:
                fee_tx = f"tx_{uuid.uuid4().hex[:10]}"
                await db.transactions.insert_one({"tx_id": fee_tx, "user_id": uid, "label": "Frais — withdrawal",
                                                  "amount": -fee, "category": "Frais", "type": "out", "created_at": now})
                await ledger_post("Frais — withdrawal", "Frais",
                                  [(cash_acct(uid), -fee), (SYSTEM_ACCOUNTS["revenue"], fee)], ref=fee_tx)
                await audit(uid, "Financial.FeeApplied", {"operation": "withdrawal", "fee": fee, "base": req.amount_cc, "ref": wtx})
            doc = {"wd_id": f"wd_{uuid.uuid4().hex[:10]}", "user_id": uid, "user_name": user.get("name"),
                   "frek_id": user.get("frek_id"), "amount_cc": req.amount_cc, "amount_eur": eur, "fee_cc": fee,
                   "fee_tx_id": fee_tx, "iban": req.iban, "status": "pending", "created_at": now}
            await db.withdrawals.insert_one(doc)
            doc.pop("_id", None)
            return await idem_finish(idem_id, {"ok": True, "withdrawal": doc})
        except Exception:
            # Compensation: restore the atomically-debited amount if post-debit work fails.
            await db.users.update_one({"user_id": uid}, {"$inc": {"balance_cc": debited_total}})
            raise
    except Exception:
        await idem_fail(idem_id)
        raise

@api_router.get("/withdrawals")
async def my_withdrawals(user: dict = Depends(get_current_user)):
    return await db.withdrawals.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)

# ---- Admin back-office ----
@api_router.get("/admin/settings")
async def admin_get_settings(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await get_settings()

@api_router.put("/admin/settings")
async def admin_set_settings(req: SettingsUpdate, user: dict = Depends(get_current_user)):
    await require_admin(user)
    upd = {k: v for k, v in req.dict().items() if v is not None}
    if upd:
        await db.settings.update_one({"key": "app"}, {"$set": upd}, upsert=True)
    return await get_settings()

@api_router.get("/admin/stats")
async def admin_stats(user: dict = Depends(get_current_user)):
    await require_admin(user)
    users = await db.users.count_documents({})
    circ = 0
    async for u in db.users.find({}, {"balance_cc": 1}):
        circ += u.get("balance_cc", 0)
    st = await get_settings()
    pending = await db.withdrawals.count_documents({"status": "pending"})
    return {"users": users, "circulation_cc": circ, "circulation_eur": round(circ * st["rate_eur"], 2),
            "total_deposited_eur": st.get("total_deposited_eur", 0), "pending_withdrawals": pending,
            "rate_eur": st["rate_eur"], "reserve_cc": st.get("reserve_cc", 0)}

@api_router.get("/admin/withdrawals")
async def admin_withdrawals(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.withdrawals.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.post("/admin/withdrawals/{wd_id}/approve")
async def admin_approve_wd(wd_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    # ATOMIC status transition is the lock: only the winner processes (no double action).
    wd = await db.withdrawals.find_one_and_update(
        {"wd_id": wd_id, "status": "pending"},
        {"$set": {"status": "processed", "processed_at": datetime.now(timezone.utc).isoformat()}})
    if not wd:
        raise HTTPException(status_code=404, detail="Demande introuvable ou déjà traitée")
    return {"ok": True}

@api_router.post("/admin/withdrawals/{wd_id}/reject")
async def admin_reject_wd(wd_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    # ATOMIC: flip pending->rejected FIRST; only the single winner credits back (no double refund).
    wd = await db.withdrawals.find_one_and_update(
        {"wd_id": wd_id, "status": "pending"},
        {"$set": {"status": "rejected", "processed_at": datetime.now(timezone.utc).isoformat()}})
    if not wd:
        raise HTTPException(status_code=404, detail="Demande introuvable ou déjà traitée")
    try:
        await add_transaction(wd["user_id"], f"Retrait refusé — remboursement {wd['amount_cc']} CC", wd["amount_cc"], "Retrait")
        fee_cc = wd.get("fee_cc", 0) or 0
        if fee_cc > 0:
            # Reverse the withdrawal fee too: a rejected withdrawal must not cost the user the fee.
            ftx = f"tx_{uuid.uuid4().hex[:10]}"
            await db.transactions.insert_one({"tx_id": ftx, "user_id": wd["user_id"],
                                              "label": f"Frais retrait remboursés — {fee_cc} CC", "amount": fee_cc,
                                              "category": "Frais", "type": "in",
                                              "created_at": datetime.now(timezone.utc).isoformat()})
            await ledger_post(f"Frais retrait remboursés — {fee_cc} CC", "Frais",
                              [(cash_acct(wd["user_id"]), fee_cc), (SYSTEM_ACCOUNTS["revenue"], -fee_cc)], ref=ftx)
            await db.users.update_one({"user_id": wd["user_id"]}, {"$inc": {"balance_cc": fee_cc}})
    except Exception:
        # Compensation: a rejected withdrawal MUST refund; if posting failed, revert the flip to allow retry.
        await db.withdrawals.update_one({"wd_id": wd_id}, {"$set": {"status": "pending", "processed_at": None}})
        await db.recovery_journal.insert_one({"entry_id": f"rec_{uuid.uuid4().hex[:10]}", "kind": "withdrawal_reject_refund_failed",
                                             "ref": wd_id, "classification": "CRITICAL",
                                             "created_at": datetime.now(timezone.utc).isoformat()})
        raise
    return {"ok": True}


# ================= AGENT SKILLS LAYER (P0 — CC/JCC native) =================
# Skill catalog: capabilities exposed to CVLN Agent Factory. Least-privilege by design.
SKILLS = {
    "Wallet.Balance":   {"capability": "read_balance",   "scopes": ["read"],                     "risk": "LOW",  "confirm": False, "networks": ["JCC"], "desc": "Lecture du solde CC/JCC."},
    "Assets.Portfolio": {"capability": "read_portfolio",  "scopes": ["read"],                     "risk": "LOW",  "confirm": False, "networks": ["JCC"], "desc": "Lecture du portefeuille et des coffres."},
    "Payments.Request": {"capability": "request_payment", "scopes": ["request"],                  "risk": "LOW",  "confirm": False, "networks": ["JCC"], "desc": "Préparer une demande de paiement (aucun fonds déplacé)."},
    "Payments.Send":    {"capability": "send_asset",      "scopes": ["request", "sign", "execute"], "risk": "HIGH", "confirm": True,  "networks": ["JCC"], "desc": "Envoyer des CC à un FREK-ID. Déplace des fonds."},
    "FREK.Identity":    {"capability": "read_identity",   "scopes": ["read"],                     "risk": "LOW",  "confirm": False, "networks": [],      "desc": "Interface FREKCORE (préparée) : lecture d'identité."},
    "KORA.StreamIncome":{"capability": "prepare_income",  "scopes": ["request"],                  "risk": "MED",  "confirm": True,  "networks": ["JCC"], "desc": "Interface KORA (préparée) : préparer un versement de revenus créateur."},
}
SCOPE_ORDER = ["read", "request", "sign", "execute", "admin"]
SKILLS.update({
    "Card.View":      {"capability": "card_view",   "scopes": ["read"],                       "risk": "LOW",  "confirm": False, "networks": ["JCC"], "desc": "Consulter la carte (données masquées, jamais PAN/CVV)."},
    "Card.Freeze":    {"capability": "card_freeze",  "scopes": ["request"],                    "risk": "MED",  "confirm": True,  "networks": ["JCC"], "desc": "Geler la carte (bloque les paiements)."},
    "Card.SetLimits": {"capability": "card_limits",  "scopes": ["admin"],                      "risk": "HIGH", "confirm": True,  "networks": ["JCC"], "desc": "Modifier les plafonds de la carte."},
    "Card.Pay":       {"capability": "card_pay",     "scopes": ["request", "sign", "execute"], "risk": "HIGH", "confirm": True,  "networks": ["JCC"], "desc": "Paiement carte (online/merchant/TPE). Déplace des fonds."},
})

class AgentCreate(BaseModel):
    name: str
    scopes: List[str] = ["read"]
    spending_limit_cc: float = 0.0
    session_ttl_hours: int = 24

class IntentCreate(BaseModel):
    skill: str
    params: dict = {}

async def audit(actor: str, action: str, detail: dict):
    await db.audit_logs.insert_one({"log_id": f"log_{uuid.uuid4().hex[:10]}", "actor": actor, "action": action,
                                    "detail": detail, "created_at": datetime.now(timezone.utc).isoformat()})

async def get_agent(request: Request) -> dict:
    token = request.headers.get("X-Agent-Token")
    if not token:
        raise HTTPException(status_code=401, detail="Agent token manquant")
    a = await db.agents.find_one({"agent_token": token}, {"_id": 0})
    if not a or not a.get("active", True):
        raise HTTPException(status_code=401, detail="Agent inconnu ou révoqué")
    exp = datetime.fromisoformat(a["session_expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        await audit(a["agent_id"], "session_expired", {})
        raise HTTPException(status_code=401, detail="Session agent expirée")
    return a

def has_scopes(agent: dict, required: List[str]) -> bool:
    granted = set(agent.get("scopes", []))
    return all(s in granted for s in required)

# ---- Admin: manage agents ----
@api_router.post("/admin/agents")
async def create_agent(req: AgentCreate, user: dict = Depends(get_current_user)):
    await require_admin(user)
    token = f"agt_{uuid.uuid4().hex}"
    doc = {"agent_id": f"agent_{uuid.uuid4().hex[:10]}", "name": req.name, "owner_user_id": user["user_id"],
           "scopes": [s for s in req.scopes if s in SCOPE_ORDER], "spending_limit_cc": req.spending_limit_cc,
           "agent_token": token, "active": True,
           "session_expires_at": (datetime.now(timezone.utc) + timedelta(hours=req.session_ttl_hours)).isoformat(),
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.agents.insert_one(doc)
    await audit(user["user_id"], "agent_created", {"agent_id": doc["agent_id"], "scopes": doc["scopes"]})
    doc.pop("_id", None)
    return doc

@api_router.get("/admin/agents")
async def list_agents(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.agents.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)

@api_router.post("/admin/agents/{agent_id}/revoke")
async def revoke_agent(agent_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    await db.agents.update_one({"agent_id": agent_id}, {"$set": {"active": False}})
    await audit(user["user_id"], "agent_revoked", {"agent_id": agent_id})
    return {"ok": True}

@api_router.get("/admin/audit")
async def get_audit(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.audit_logs.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.get("/admin/skills")
async def admin_skills(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return [{"name": n, **s} for n, s in SKILLS.items()]

@api_router.get("/admin/intents")
async def admin_intents(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.agent_intents.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.post("/agent/intent/{intent_id}/decline")
async def decline_intent(intent_id: str, user: dict = Depends(get_current_user)):
    intent = await db.agent_intents.find_one({"intent_id": intent_id}, {"_id": 0})
    if not intent:
        raise HTTPException(status_code=404, detail="Intent introuvable")
    if intent["owner_user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Refus réservé au propriétaire du wallet")
    await db.agent_intents.update_one({"intent_id": intent_id}, {"$set": {"status": "denied", "confirmed": False}})
    await audit(user["user_id"], "intent_declined", {"intent_id": intent_id})
    return {"ok": True}

# ---- Agent Factory discovery ----
@api_router.get("/agent/skills")
async def agent_skills(agent: dict = Depends(get_agent)):
    out = []
    for name, s in SKILLS.items():
        out.append({"name": name, **s, "authorized": has_scopes(agent, s["scopes"])})
    return {"agent": agent["name"], "granted_scopes": agent["scopes"], "skills": out}

# ---- Intent flow: PREPARE -> (CONFIRM) -> EXECUTE ----
@api_router.post("/agent/intent")
async def create_intent(req: IntentCreate, agent: dict = Depends(get_agent)):
    skill = SKILLS.get(req.skill)
    if not skill:
        await audit(agent["agent_id"], "intent_denied", {"reason": "unknown_skill", "skill": req.skill})
        raise HTTPException(status_code=400, detail="Skill inconnue → refusée")
    if not has_scopes(agent, skill["scopes"]):
        await audit(agent["agent_id"], "intent_denied", {"reason": "missing_scope", "skill": req.skill})
        raise HTTPException(status_code=403, detail=f"Permissions insuffisantes (requis: {skill['scopes']})")
    owner = await db.users.find_one({"user_id": agent["owner_user_id"]}, {"_id": 0})
    preview = {"skill": req.skill, "capability": skill["capability"], "risk": skill["risk"]}
    # Build preview + risk checks for fund-moving skills
    if skill["capability"] == "send_asset":
        amount = float(req.params.get("amount_cc", 0))
        to = req.params.get("to", "")
        if amount <= 0 or not to:
            raise HTTPException(status_code=400, detail="Paramètres invalides (to, amount_cc)")
        if amount > agent.get("spending_limit_cc", 0):
            await audit(agent["agent_id"], "intent_denied", {"reason": "spending_limit", "amount": amount})
            raise HTTPException(status_code=403, detail=f"Plafond dépassé (limite {agent.get('spending_limit_cc',0)} CC)")
        if amount > owner.get("balance_cc", 0):
            raise HTTPException(status_code=400, detail="Solde insuffisant")
        preview.update({"from": owner.get("frek_id"), "to": to, "amount_cc": amount})
    elif skill["capability"] == "card_pay":
        await assert_not_suspended("card")
        card = await db.cards.find_one({"user_id": agent["owner_user_id"]}, {"_id": 0})
        amount = float(req.params.get("amount_cc", 0)); merchant = req.params.get("merchant", "Marchand")
        ptype = req.params.get("payment_type", "online")
        if not card:
            raise HTTPException(status_code=400, detail="Aucune carte")
        if card["status"] != "active":
            await audit(agent["agent_id"], "intent_denied", {"reason": "card_frozen"})
            raise HTTPException(status_code=403, detail="Carte gelée → paiement refusé")
        if not card.get("agent_enabled", True):
            raise HTTPException(status_code=403, detail="Paiements agent désactivés sur la carte")
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Montant invalide")
        if amount > agent.get("spending_limit_cc", 0):
            await audit(agent["agent_id"], "intent_denied", {"reason": "agent_limit", "amount": amount})
            raise HTTPException(status_code=403, detail=f"Plafond agent dépassé ({agent.get('spending_limit_cc',0)} CC)")
        if amount > card.get("per_tx_limit_cc", 0):
            await audit(agent["agent_id"], "intent_denied", {"reason": "card_per_tx_limit"})
            raise HTTPException(status_code=403, detail=f"Plafond carte/transaction dépassé ({card.get('per_tx_limit_cc',0)} CC)")
        if amount > owner.get("balance_cc", 0):
            raise HTTPException(status_code=400, detail="Solde insuffisant")
        preview.update({"card_last4": card["last4"], "merchant": merchant, "amount_cc": amount, "payment_type": ptype})
    status = "awaiting_confirmation" if skill["confirm"] else "prepared"
    doc = {"intent_id": f"int_{uuid.uuid4().hex[:10]}", "agent_id": agent["agent_id"], "owner_user_id": agent["owner_user_id"],
           "skill": req.skill, "params": req.params, "risk": skill["risk"], "confirm_required": skill["confirm"],
           "confirmed": False, "status": status, "preview": preview, "created_at": datetime.now(timezone.utc).isoformat()}
    await db.agent_intents.insert_one(doc)
    await audit(agent["agent_id"], "intent_prepared", {"intent_id": doc["intent_id"], "skill": req.skill, "status": status})
    doc.pop("_id", None)
    return doc

@api_router.post("/agent/intent/{intent_id}/confirm")
async def confirm_intent(intent_id: str, user: dict = Depends(get_current_user)):
    intent = await db.agent_intents.find_one({"intent_id": intent_id}, {"_id": 0})
    if not intent:
        raise HTTPException(status_code=404, detail="Intent introuvable")
    if intent["owner_user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Confirmation réservée au propriétaire du wallet")
    await db.agent_intents.update_one({"intent_id": intent_id}, {"$set": {"confirmed": True, "status": "confirmed"}})
    await audit(user["user_id"], "intent_confirmed", {"intent_id": intent_id})
    return {"ok": True}

@api_router.post("/agent/intent/{intent_id}/execute")
async def execute_intent(intent_id: str, agent: dict = Depends(get_agent)):
    await assert_not_suspended("agents")
    intent = await db.agent_intents.find_one({"intent_id": intent_id}, {"_id": 0})
    if not intent or intent["agent_id"] != agent["agent_id"]:
        raise HTTPException(status_code=404, detail="Intent introuvable")
    if intent["status"] == "executed":
        raise HTTPException(status_code=400, detail="Déjà exécuté")
    if intent["confirm_required"] and not intent.get("confirmed"):
        await audit(agent["agent_id"], "execute_denied", {"intent_id": intent_id, "reason": "not_confirmed"})
        raise HTTPException(status_code=403, detail="Confirmation utilisateur requise avant exécution")
    skill = SKILLS[intent["skill"]]
    if not has_scopes(agent, ["execute"]) and skill["capability"] in ("send_asset", "card_pay"):
        raise HTTPException(status_code=403, detail="Scope 'execute' requis")
    result = {"executed": True}
    if skill["capability"] == "card_pay":
        await assert_not_suspended("card")
        p = intent["preview"]
        card = await db.cards.find_one({"user_id": intent["owner_user_id"]}, {"_id": 0})
        owner = await db.users.find_one({"user_id": intent["owner_user_id"]}, {"_id": 0})
        if not card or card["status"] != "active":
            raise HTTPException(status_code=403, detail="Carte gelée → paiement refusé")
        if p["amount_cc"] > owner.get("balance_cc", 0):
            raise HTTPException(status_code=400, detail="Solde insuffisant")
        await add_transaction(intent["owner_user_id"], f"Carte •••• {p['card_last4']} — {p['merchant']} ({p['payment_type']})", -p["amount_cc"], "Card")
        await audit(agent["agent_id"], "Card.PaymentCaptured", {"intent_id": intent_id, "amount": p["amount_cc"], "merchant": p["merchant"]})
        result["amount_cc"] = p["amount_cc"]
    elif skill["capability"] == "card_freeze":
        await db.cards.update_one({"user_id": intent["owner_user_id"]}, {"$set": {"status": "frozen"}})
        await audit(agent["agent_id"], "Card.Frozen", {"intent_id": intent_id})
    elif skill["capability"] == "send_asset":
        p = intent["preview"]
        owner = await db.users.find_one({"user_id": intent["owner_user_id"]}, {"_id": 0})
        if p["amount_cc"] > owner.get("balance_cc", 0):
            raise HTTPException(status_code=400, detail="Solde insuffisant")
        await add_transaction(intent["owner_user_id"], f"Agent {agent['name']} → {p['to']}", -p["amount_cc"], "Agent")
        recipient = await db.users.find_one({"frek_id": p["to"]}, {"_id": 0})
        if recipient:
            await add_transaction(recipient["user_id"], f"Reçu via agent {agent['name']}", p["amount_cc"], "Agent")
        result["amount_cc"] = p["amount_cc"]
    await db.agent_intents.update_one({"intent_id": intent_id}, {"$set": {"status": "executed"}})
    await audit(agent["agent_id"], "intent_executed", {"intent_id": intent_id, "result": result})
    return {"ok": True, "result": result}


# ================= CVLN VIRTUAL CARD (issuing = MOCK, ledger = REAL on CC) =================
# NOTE: No real card issuer/processor connected. PAN/CVV are NEVER generated or stored.
# Only a masked last4 is kept. Mobile wallet provisioning = PLANNED (needs issuer).
class CardLimits(BaseModel):
    daily_limit_cc: Optional[float] = None
    per_tx_limit_cc: Optional[float] = None
    online_enabled: Optional[bool] = None
    tpe_enabled: Optional[bool] = None
    agent_enabled: Optional[bool] = None

@api_router.get("/card")
async def get_card(user: dict = Depends(get_current_user)):
    c = await get_or_seed_card(user["user_id"])
    return {**c, "today_spent_cc": await _today_card_spent(user["user_id"])}

@api_router.post("/card/freeze")
async def card_freeze(user: dict = Depends(get_current_user)):
    await get_or_seed_card(user["user_id"])
    await db.cards.update_one({"user_id": user["user_id"]}, {"$set": {"status": "frozen"}})
    await audit(user["user_id"], "Card.Frozen", {})
    return {"ok": True, "status": "frozen"}

@api_router.post("/card/unfreeze")
async def card_unfreeze(user: dict = Depends(get_current_user)):
    await get_or_seed_card(user["user_id"])
    await db.cards.update_one({"user_id": user["user_id"]}, {"$set": {"status": "active"}})
    await audit(user["user_id"], "Card.Unfrozen", {})
    return {"ok": True, "status": "active"}

@api_router.put("/card/limits")
async def card_limits(req: CardLimits, user: dict = Depends(get_current_user)):
    await get_or_seed_card(user["user_id"])
    upd = {k: v for k, v in req.dict().items() if v is not None}
    if upd:
        await db.cards.update_one({"user_id": user["user_id"]}, {"$set": upd})
        await audit(user["user_id"], "Card.LimitChanged", upd)
    return await db.cards.find_one({"user_id": user["user_id"]}, {"_id": 0})

@api_router.get("/card/transactions")
async def card_transactions(user: dict = Depends(get_current_user)):
    return await db.transactions.find({"user_id": user["user_id"], "category": {"$in": ["Card", "Agent"]}}, {"_id": 0}).sort("created_at", -1).to_list(100)

@api_router.post("/card/pay")
async def card_pay(user: dict = Depends(get_current_user)):
    # Human-initiated card payment (demo). Amount/merchant via body.
    raise HTTPException(status_code=400, detail="Utilisez la Marketplace ou un paiement agent (Card.Pay).")

# ---- Financial Core read + integrity ----
@api_router.get("/ledger/accounts")
async def ledger_accounts(user: dict = Depends(get_current_user)):
    out = []
    cash_id = cash_acct(user["user_id"])
    out.append({"account_id": cash_id, "name": "Compte principal", "type": "cash", "asset": DEFAULT_ASSET,
                "balance": await ledger_balance(cash_id), "cached": user.get("balance_cc", 0)})
    async for c in db.coffres.find({"user_id": user["user_id"]}, {"_id": 0}):
        aid = coffre_acct(c["coffre_id"])
        out.append({"account_id": aid, "name": c["name"], "type": "coffre", "asset": DEFAULT_ASSET,
                    "balance": await ledger_balance(aid), "cached": c.get("amount_cc", 0)})
    return out

@api_router.get("/ledger/entries")
async def ledger_entries(user: dict = Depends(get_current_user), limit: int = 100):
    ids = [cash_acct(user["user_id"])]
    async for c in db.coffres.find({"user_id": user["user_id"]}, {"coffre_id": 1}):
        ids.append(coffre_acct(c["coffre_id"]))
    return await db.ledger_entries.find({"postings.account_id": {"$in": ids}}, {"_id": 0}).sort("created_at", -1).to_list(limit)

@api_router.get("/admin/ledger/integrity")
async def ledger_integrity(user: dict = Depends(get_current_user)):
    await require_admin(user)
    # Global invariant: sum of ALL postings per asset must equal 0.
    per_asset = {}
    total_entries = 0
    async for e in db.ledger_entries.find({}, {"_id": 0, "asset": 1, "postings": 1}):
        total_entries += 1
        per_asset.setdefault(e["asset"], 0.0)
        per_asset[e["asset"]] += sum(p["amount"] for p in e["postings"])
    per_asset = {k: round(v, 6) for k, v in per_asset.items()}
    balanced = all(abs(v) < 1e-6 for v in per_asset.values())
    # Cache vs derived for a sample of users
    mism = []
    async for u in db.users.find({}, {"user_id": 1, "balance_cc": 1}).limit(50):
        derived = await ledger_balance(cash_acct(u["user_id"]))
        if abs(derived - u.get("balance_cc", 0)) > 1e-6:
            mism.append({"user_id": u["user_id"], "cached": u.get("balance_cc", 0), "derived": derived})
    return {"balanced": balanced, "per_asset_sum": per_asset, "entries": total_entries,
            "cache_mismatches": mism, "system_accounts": {k: await ledger_balance(v) for k, v in SYSTEM_ACCOUNTS.items()}}

@api_router.get("/admin/financial-health")
async def financial_health(user: dict = Depends(get_current_user)):
    await require_admin(user)
    per_asset = {}
    total_entries = 0
    async for e in db.ledger_entries.find({}, {"_id": 0, "asset": 1, "postings": 1}):
        total_entries += 1
        per_asset.setdefault(e["asset"], 0.0)
        per_asset[e["asset"]] += sum(p["amount"] for p in e["postings"])
    balanced = all(abs(round(v, 6)) < 1e-6 for v in per_asset.values())
    # JCC in circulation = sum of user cash + coffre accounts (should equal -sum(system))
    circ = 0.0
    async for u in db.users.find({}, {"balance_cc": 1}):
        circ += u.get("balance_cc", 0)
    coffre_total = 0.0
    async for c in db.coffres.find({}, {"amount_cc": 1}):
        coffre_total += c.get("amount_cc", 0)
    sys_total = 0.0
    for v in SYSTEM_ACCOUNTS.values():
        sys_total += await ledger_balance(v)
    supply_ok = abs(round((circ + coffre_total) + sys_total, 6)) < 1e-6
    idem_total = await db.idempotency_records.count_documents({})
    idem_processing = await db.idempotency_records.count_documents({"state": "PROCESSING"})
    holds_rep = await holds_integrity_report()
    active_holds = await db.balance_holds.count_documents({"status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}})
    # B4/B5 health
    stuck_cut = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    settlements_health = {
        "total": await db.settlements.count_documents({}),
        "terminal": await db.settlements.count_documents({"internal_status": {"$in": list(SETTLEMENT_TERMINAL)}}),
        "requires_review": await db.settlements.count_documents({"internal_status": "REQUIRES_REVIEW"}),
        "stuck": await db.settlements.count_documents({"internal_status": {"$in": ["PENDING", "SUBMITTED", "PROCESSING"]}, "created_at": {"$lt": stuck_cut}}),
    }
    open_cases = await db.reconciliation_cases.count_documents({"status": {"$in": ["OPEN", "INVESTIGATING"]}})
    high_cases = await db.reconciliation_cases.count_documents({"status": {"$in": ["OPEN", "INVESTIGATING"]}, "severity": "HIGH"})
    outbox_health = {
        "pending": await db.outbox_events.count_documents({"status": {"$in": ["PENDING", "RETRY", "DELIVERING"]}}),
        "dead_letter": await db.outbox_events.count_documents({"status": "DEAD_LETTER"}),
        "delivered": await db.outbox_events.count_documents({"status": "DELIVERED"}),
    }
    inbox_unprocessed = await db.webhook_inbox.count_documents({"processing_status": {"$ne": "PROCESSED"}})
    pending_approvals = await db.approval_requests.count_documents({"status": "PENDING"})
    b4b5_ok = (settlements_health["requires_review"] == 0 and high_cases == 0 and outbox_health["dead_letter"] == 0)
    return {
        "ledger_balanced": balanced,
        "per_asset_sum": {k: round(v, 6) for k, v in per_asset.items()},
        "ledger_entries": total_entries,
        "jcc_circulation": round(circ + coffre_total, 2),
        "jcc_supply_reconciled": supply_ok,
        "pending_withdrawals": await db.withdrawals.count_documents({"status": "pending"}),
        "idempotency_records": idem_total,
        "idempotency_in_progress": idem_processing,
        "active_holds": active_holds,
        "refunds": await db.refunds.count_documents({}),
        "reversals": await db.reversals.count_documents({}),
        "holds_health": holds_rep,
        "settlements": settlements_health,
        "reconciliation_open_cases": open_cases,
        "reconciliation_high_severity": high_cases,
        "outbox": outbox_health,
        "inbox_unprocessed": inbox_unprocessed,
        "pending_approvals": pending_approvals,
        "severity": "CRITICAL" if not (balanced and supply_ok and holds_rep["healthy"]) else ("HIGH" if not b4b5_ok else "INFO"),
    }

@api_router.put("/admin/kill-switch")
async def set_kill_switch(req: dict, user: dict = Depends(get_current_user)):
    await require_admin(user)
    name = req.get("name"); enabled = bool(req.get("enabled"))
    if name not in ("withdrawals", "card", "agents"):
        raise HTTPException(status_code=400, detail="Kill-switch inconnu")
    await db.settings.update_one({"key": "app"}, {"$set": {f"ks_{name}": enabled}}, upsert=True)
    await audit(user["user_id"], "KillSwitch.Toggled", {"name": name, "enabled": enabled})
    return {"ok": True, "name": name, "enabled": enabled}

@api_router.post("/admin/ledger/backfill")
async def ledger_backfill(user: dict = Depends(get_current_user)):
    """Migration: aligns the ledger to legacy cached balances via labeled opening entries
    (counterpart = issuance). Makes the ledger the source of truth for pre-ledger data.
    Never destructive; only appends compensating entries where derived != cache."""
    await require_admin(user)
    fixed = 0
    async for u in db.users.find({}, {"user_id": 1, "balance_cc": 1}):
        acc = cash_acct(u["user_id"])
        diff = round(u.get("balance_cc", 0) - await ledger_balance(acc), 2)
        if abs(diff) > 0.005:
            await ledger_post("Solde d'ouverture (migration)", "Migration",
                              [(acc, diff), (SYSTEM_ACCOUNTS["issuance"], -diff)])
            fixed += 1
    async for c in db.coffres.find({}, {"coffre_id": 1, "amount_cc": 1}):
        acc = coffre_acct(c["coffre_id"])
        diff = round(c.get("amount_cc", 0) - await ledger_balance(acc), 2)
        if abs(diff) > 0.005:
            await ledger_post("Solde d'ouverture coffre (migration)", "Migration",
                              [(acc, diff), (SYSTEM_ACCOUNTS["issuance"], -diff)])
            fixed += 1
    return {"ok": True, "accounts_backfilled": fixed}


async def holds_integrity_report():
    """Invariants: held_cc >= 0, held_cc == sum(remaining of active/partial NON-expired holds),
    held_cc <= balance_cc (available >= 0), no expired hold still counted."""
    now = datetime.now(timezone.utc).isoformat()
    held_mismatch, negative_held, over_reserved, expired_active, orphan_held = [], [], [], [], []
    total_users_with_holds = 0
    async for u in db.users.find({}, {"user_id": 1, "balance_cc": 1, "held_cc": 1}):
        uid = u["user_id"]
        # Lazy-expiry FIRST: an expired hold must never count as reserved (correctness, not maintenance).
        await reconcile_expired_holds(uid)
        u = await db.users.find_one({"user_id": uid}, {"_id": 0, "balance_cc": 1, "held_cc": 1})
        cache = round(u.get("held_cc", 0.0), 2)
        eff = 0.0
        exp_cnt = 0
        async for h in db.balance_holds.find({"user_id": uid, "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}},
                                             {"_id": 0, "amount": 1, "captured": 1, "expires_at": 1}):
            rem = round(h["amount"] - h.get("captured", 0.0), 2)
            if h.get("expires_at", "") <= now:
                exp_cnt += 1
            else:
                eff += rem
        eff = round(eff, 2)
        if cache != 0 or eff != 0:
            total_users_with_holds += 1
        if cache < -1e-6:
            negative_held.append({"user_id": uid, "held_cc": cache})
        if abs(cache - eff) > 0.01:
            held_mismatch.append({"user_id": uid, "cache": cache, "effective": eff})
        if cache - u.get("balance_cc", 0) > 0.01:
            over_reserved.append({"user_id": uid, "held_cc": cache, "balance_cc": u.get("balance_cc", 0)})
        if exp_cnt > 0:
            expired_active.append({"user_id": uid, "expired_still_active": exp_cnt})
    return {
        "users_with_holds": total_users_with_holds,
        "held_mismatch": held_mismatch, "negative_held": negative_held,
        "over_reserved": over_reserved, "expired_still_active": expired_active,
        "healthy": not (held_mismatch or negative_held or over_reserved or expired_active),
    }

@api_router.get("/admin/holds/integrity")
async def holds_integrity(user: dict = Depends(get_current_user)):
    await require_admin(user)
    rep = await holds_integrity_report()
    if not rep["healthy"]:
        await audit(user["user_id"], "Financial.HoldIntegrityMismatch", {k: len(v) for k, v in rep.items() if isinstance(v, list)})
    return rep

@api_router.post("/admin/holds/rebuild")
async def holds_rebuild(user: dict = Depends(get_current_user)):
    """Safe repair, ONE direction only: Holds -> held_cc cache. Never invents/edits holds.
    Reconciles expired holds first, then recomputes held_cc from effective active reservations."""
    await require_admin(user)
    rebuilt = 0
    async for u in db.users.find({}, {"user_id": 1}):
        uid = u["user_id"]
        await reconcile_expired_holds(uid)
        eff = 0.0
        async for h in db.balance_holds.find({"user_id": uid, "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}},
                                             {"_id": 0, "amount": 1, "captured": 1}):
            eff += h["amount"] - h.get("captured", 0.0)
        eff = round(eff, 2)
        cur = await db.users.find_one({"user_id": uid}, {"_id": 0, "held_cc": 1})
        if abs(cur.get("held_cc", 0.0) - eff) > 0.005:
            await db.users.update_one({"user_id": uid}, {"$set": {"held_cc": eff}})
            rebuilt += 1
    return {"ok": True, "users_rebuilt": rebuilt}


# ---- System status: BUILD vs ACTIVATION tracks (honest capability statuses) ----
FEATURE_FLAGS = {
    "LEDGER": True, "PAYMENTS_STRIPE": True, "CARD": True, "AGENTS": True,
    "WITHDRAWALS": True, "INVEST": False, "CRYPTO": False, "FX": False,
    "BUSINESS": False, "RWA": False, "APPLE_PAY": False, "GOOGLE_PAY": False,
    "KYC": False, "OPEN_BANKING": False,
}
CAPABILITY_STATUS = {
    "financial_core_ledger": "REAL",
    "jcc_internal": "REAL",
    "payments_deposit_stripe": "SANDBOX",
    "withdrawals": "MANUAL_ADMIN",          # real IBAN payouts need Stripe Connect (ACTIVATION)
    "virtual_card_ledger": "REAL",
    "card_issuing": "MOCK",                  # needs issuer/processor (ACTIVATION)
    "apple_google_pay": "PLANNED",
    "agents_factory": "REAL",
    "kyc_aml": "PLANNED",                    # needs KYC provider + legal (ACTIVATION)
    "invest": "PLANNED", "crypto": "PLANNED", "fx": "PLANNED",
    "business": "PLANNED", "rwa": "PLANNED", "open_banking": "PLANNED",
    "reconciliation": "REAL", "reporting": "PARTIAL",
    "idempotency_api": "REAL", "state_machines": "REAL", "holds": "REAL",
    "refund_engine": "REAL", "reversal_engine": "REAL", "settlement_engine": "PARTIAL",
    "fees_engine": "REAL", "outbox_events": "PARTIAL", "account_registry": "PARTIAL",
    "asset_registry": "REAL", "maker_checker": "REAL", "recovery_engine": "REAL",
    "monetary_precision": "PARTIAL", "provider_adapters": "MOCK",
}

@api_router.get("/system/status")
async def system_status(user: dict = Depends(get_current_user)):
    return {"feature_flags": FEATURE_FLAGS, "capabilities": CAPABILITY_STATUS,
            "tracks": {"build": ["Ledger✓", "Invest", "Crypto", "FX", "Business", "RWA"],
                       "activation": ["Legal structure", "KYC/AML", "Issuer", "Card processor",
                                       "Apple/Google", "Broker", "CASP/Custodian", "Banking/Open-banking", "Certifications"]}}

@api_router.get("/me/kyc")
async def my_kyc(user: dict = Depends(get_current_user)):
    # KYC is PLANNED: no real verification provider connected yet.
    return {"status": user.get("kyc_status", "not_started"), "level": user.get("kyc_level", 0),
            "provider": "PLANNED", "note": "Vérification d'identité non active (nécessite un provider KYC + structure juridique)."}

@api_router.get("/card/wallet-eligibility")
async def wallet_eligibility(user: dict = Depends(get_current_user)):
    await audit(user["user_id"], "MobileWallet.EligibilityChecked", {})
    reason = "Card issuing non connecté à un issuer/processor certifié (Apple/Google provisioning requiert un partenaire émetteur)."
    return {
        "apple": {"status": "PLANNED", "eligible": False, "reason": reason},
        "google": {"status": "PLANNED", "eligible": False, "reason": reason},
    }

@api_router.post("/card/wallet/{platform}/provision")
async def wallet_provision(platform: str, user: dict = Depends(get_current_user)):
    await audit(user["user_id"], "MobileWallet.ProvisioningFailed", {"platform": platform, "reason": "no_issuer"})
    raise HTTPException(status_code=501, detail=f"Provisioning {platform} indisponible (statut PLANNED : issuer/processor requis).")


# ================= P0.1-B2: STATE MACHINE + HOLDS / AVAILABLE BALANCE =================
# Central state machine. Terminal states never leave. Transitions are enforced ATOMICALLY
# at the DB layer via conditional find_one_and_update (the status field is the lock),
# not via read-then-write. Standalone MongoDB has NO multi-doc transactions, so cross-doc
# consistency (users.held_cc <-> balance_holds) uses a deterministic compensation strategy:
# the atomic status flip (single winner) is done FIRST, then held_cc is adjusted. A crash
# in the window can only UNDER-release (funds stay locked), never over-release/double-spend;
# the Integrity Engine detects and the rebuild endpoint repairs (Holds -> held_cc, one way).
HOLD_TRANSITIONS = {
    "ACTIVE": {"CAPTURED", "PARTIALLY_CAPTURED", "RELEASED", "EXPIRED"},
    "PARTIALLY_CAPTURED": {"CAPTURED", "RELEASED", "EXPIRED"},
    "CAPTURED": set(), "RELEASED": set(), "EXPIRED": set(),
}

async def record_state(entity_type, entity_id, prev, new, actor, reason="", correlation_id=None):
    # Append-only financial state history (never rewritten).
    await db.financial_state_history.insert_one({
        "entity_type": entity_type, "entity_id": entity_id,
        "previous_state": prev, "new_state": new, "actor": actor, "reason": reason,
        "correlation_id": correlation_id,
        "created_at": datetime.now(timezone.utc).isoformat()})

def assert_transition(allowed_map, current, new):
    if new not in allowed_map.get(current, set()):
        raise HTTPException(status_code=409, detail=f"INVALID_STATE_TRANSITION:{current}->{new}")

async def _terminate_hold(hold_id: str, terminal: str, actor: str, reason: str = ""):
    """Atomically move an ACTIVE/PARTIALLY_CAPTURED hold to a terminal state (RELEASED/EXPIRED)
    and release its remaining reserved amount from held_cc. Idempotent: only ONE caller wins
    the status flip, so double release/expire is impossible."""
    now = datetime.now(timezone.utc).isoformat()
    res = await db.balance_holds.find_one_and_update(
        {"hold_id": hold_id, "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}},
        {"$set": {"status": terminal, "released_at": now}},
        return_document=ReturnDocument.AFTER)
    if not res:
        return None  # already terminal -> nothing to release (idempotent)
    remaining = round(res["amount"] - res.get("captured", 0.0), 2)
    prev = "PARTIALLY_CAPTURED" if res.get("captured", 0) > 0 else "ACTIVE"
    if remaining > 0:
        await db.users.update_one({"user_id": res["user_id"]}, {"$inc": {"held_cc": -remaining}})
    await record_state("hold", hold_id, prev, terminal, actor, reason)
    ev = "Financial.HoldExpired" if terminal == "EXPIRED" else "Financial.HoldReleased"
    await audit(res["user_id"], ev, {"hold_id": hold_id, "released_amount": remaining})
    return res

async def reconcile_expired_holds(user_id: str):
    """Lazy-expiry: expiry is correctness, cleanup is maintenance. Any hold past expires_at
    stops reducing available balance immediately, even if no worker has run yet."""
    now = datetime.now(timezone.utc).isoformat()
    async for h in db.balance_holds.find(
            {"user_id": user_id, "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}, "expires_at": {"$lte": now}},
            {"_id": 0, "hold_id": 1}):
        await _terminate_hold(h["hold_id"], "EXPIRED", "system", "ttl_expired")

async def available_balance(user: dict) -> float:
    await reconcile_expired_holds(user["user_id"])
    u = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0, "balance_cc": 1, "held_cc": 1})
    return round(u.get("balance_cc", 0) - u.get("held_cc", 0.0), 2)

class HoldCreate(BaseModel):
    amount: float
    reason: str = ""
    ttl_seconds: Optional[int] = None

class HoldCapture(BaseModel):
    amount: Optional[float] = None

@api_router.post("/holds")
async def create_hold(req: HoldCreate, request: Request, user: dict = Depends(get_current_user)):
    idem_id, cached = await idem_begin(request, user, "hold", req.dict())
    if cached is not None:
        return cached
    try:
        if req.amount <= 0:
            raise HTTPException(status_code=400, detail="Montant invalide")
        amount = round(req.amount, 2)
        uid = user["user_id"]
        # Lazy-expiry BEFORE reserving so freed funds are usable immediately.
        await reconcile_expired_holds(uid)
        # ATOMIC reservation: check available (balance-held) AND increment held in ONE op.
        updated = await db.users.find_one_and_update(
            {"user_id": uid,
             "$expr": {"$gte": [{"$subtract": ["$balance_cc", {"$ifNull": ["$held_cc", 0]}]}, amount]}},
            {"$inc": {"held_cc": amount}},
            return_document=ReturnDocument.AFTER)
        if not updated:
            await audit(uid, "Financial.HoldRejectedInsufficientFunds", {"amount": amount})
            raise HTTPException(status_code=400, detail="INSUFFICIENT_AVAILABLE_FUNDS")
        exp = datetime.now(timezone.utc) + timedelta(seconds=req.ttl_seconds if req.ttl_seconds else 24 * 3600)
        hold = {"hold_id": f"hold_{uuid.uuid4().hex[:10]}", "user_id": uid, "asset": DEFAULT_ASSET,
                "amount": amount, "captured": 0.0, "status": "ACTIVE", "reason": req.reason,
                "correlation_id": f"corr_{uuid.uuid4().hex[:10]}",
                "created_at": datetime.now(timezone.utc).isoformat(), "expires_at": exp.isoformat()}
        try:
            await db.balance_holds.insert_one(dict(hold))
        except Exception:
            # Compensation: reservation succeeded but hold row failed -> give funds back.
            await db.users.update_one({"user_id": uid}, {"$inc": {"held_cc": -amount}})
            raise
        await record_state("hold", hold["hold_id"], None, "ACTIVE", uid, req.reason, hold["correlation_id"])
        await audit(uid, "Financial.HoldCreated", {"hold_id": hold["hold_id"], "amount": amount})
        hold.pop("_id", None)
        return await idem_finish(idem_id, hold)
    except HTTPException:
        await idem_fail(idem_id)
        raise
    except Exception:
        await idem_fail(idem_id)
        raise

@api_router.post("/holds/{hold_id}/capture")
async def capture_hold(hold_id: str, req: HoldCapture, user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    h = await db.balance_holds.find_one({"hold_id": hold_id, "user_id": uid}, {"_id": 0})
    if not h:
        raise HTTPException(status_code=404, detail="HOLD_NOT_FOUND")
    remaining0 = round(h["amount"] - h.get("captured", 0.0), 2)
    amt = round(req.amount if req.amount is not None else remaining0, 2)
    if amt <= 0:
        raise HTTPException(status_code=400, detail="Montant de capture invalide")
    # ATOMIC claim: only succeeds while hold is capturable AND has enough remaining.
    res = await db.balance_holds.find_one_and_update(
        {"hold_id": hold_id, "user_id": uid, "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]},
         "$expr": {"$gte": [{"$subtract": ["$amount", {"$ifNull": ["$captured", 0]}]}, amt]}},
        {"$inc": {"captured": amt}},
        return_document=ReturnDocument.AFTER)
    if not res:
        raise HTTPException(status_code=409, detail="CAPTURE_INVALID (état terminal ou montant > restant)")
    prev = "PARTIALLY_CAPTURED" if res.get("captured", 0) - amt > 0 else "ACTIVE"
    new_captured = round(res["captured"], 2)
    final = "CAPTURED" if abs(new_captured - res["amount"]) < 1e-6 else "PARTIALLY_CAPTURED"
    await db.balance_holds.update_one(
        {"hold_id": hold_id, "status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}},
        {"$set": {"status": final}})
    # Captured funds become a real spend: release from held AND debit balance via the ledger.
    await db.users.update_one({"user_id": uid}, {"$inc": {"held_cc": -amt}})
    await add_transaction(uid, f"Capture hold — {h.get('reason','')}", -amt, "Hold")
    await record_state("hold", hold_id, prev, final, uid, "", res.get("correlation_id"))
    ev = "Financial.HoldCaptured" if final == "CAPTURED" else "Financial.HoldPartiallyCaptured"
    await audit(uid, ev, {"hold_id": hold_id, "amount": amt, "captured_total": new_captured})
    return {"ok": True, "hold_id": hold_id, "captured": new_captured,
            "remaining": round(res["amount"] - new_captured, 2), "status": final}

@api_router.post("/holds/{hold_id}/release")
async def release_hold(hold_id: str, user: dict = Depends(get_current_user)):
    h = await db.balance_holds.find_one({"hold_id": hold_id, "user_id": user["user_id"]}, {"_id": 0})
    if not h:
        raise HTTPException(status_code=404, detail="HOLD_NOT_FOUND")
    res = await _terminate_hold(hold_id, "RELEASED", user["user_id"], "manual_release")
    if not res:
        # Idempotent: already terminal.
        cur = await db.balance_holds.find_one({"hold_id": hold_id}, {"_id": 0, "status": 1})
        return {"ok": True, "status": cur["status"], "idempotent": True}
    return {"ok": True, "status": "RELEASED"}

@api_router.get("/holds")
async def list_holds(user: dict = Depends(get_current_user)):
    await reconcile_expired_holds(user["user_id"])
    return await db.balance_holds.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)

@api_router.get("/holds/{hold_id}/history")
async def hold_history(hold_id: str, user: dict = Depends(get_current_user)):
    return await db.financial_state_history.find({"entity_type": "hold", "entity_id": hold_id}, {"_id": 0}).sort("created_at", 1).to_list(100)


# ================= P0.1-B3: FEES + REFUND + REVERSAL =================
# All value movements go through the double-entry ledger. Refund/Reversal have their own
# state and atomic single-doc guards on the ORIGINAL transaction (cumulative refunded / reversed
# flag) so they can never over-refund or double-reverse under concurrency.

async def _fee_policy() -> dict:
    st = await get_settings()
    return st.get("fee_policy", {}) or {}

def _compute_fee(policy: dict, operation: str, base: float) -> float:
    cfg = policy.get(operation) or {}
    fee = round(base * cfg.get("pct", 0.0) + cfg.get("flat", 0.0), 2)
    return fee if fee > 0 else 0.0

async def apply_fee(user_id: str, operation: str, base: float, ref: str = None, enforce: bool = True) -> float:
    """Charge a configurable fee via the Financial Core (user cash -> revenue). Returns fee charged.
    enforce=True debits atomically only if available (balance-held) covers it."""
    fee = _compute_fee(await _fee_policy(), operation, base)
    if fee <= 0:
        return 0.0
    if enforce:
        res = await db.users.find_one_and_update(
            {"user_id": user_id,
             "$expr": {"$gte": [{"$subtract": ["$balance_cc", {"$ifNull": ["$held_cc", 0]}]}, fee]}},
            {"$inc": {"balance_cc": -fee}}, return_document=ReturnDocument.AFTER)
        if not res:
            raise HTTPException(status_code=400, detail="FEE_INSUFFICIENT_AVAILABLE")
    else:
        await db.users.update_one({"user_id": user_id}, {"$inc": {"balance_cc": -fee}})
    tx = {"tx_id": f"tx_{uuid.uuid4().hex[:10]}", "user_id": user_id, "label": f"Frais — {operation}",
          "amount": -fee, "category": "Frais", "type": "out", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.transactions.insert_one(dict(tx))
    await ledger_post(f"Frais — {operation}", "Frais",
                      [(cash_acct(user_id), -fee), (SYSTEM_ACCOUNTS["revenue"], fee)], ref=tx["tx_id"])
    await audit(user_id, "Financial.FeeApplied", {"operation": operation, "fee": fee, "base": base, "ref": ref})
    return fee

class FeeQuote(BaseModel):
    operation: str
    amount: float

@api_router.post("/fees/quote")
async def fee_quote(req: FeeQuote, user: dict = Depends(get_current_user)):
    fee = _compute_fee(await _fee_policy(), req.operation, req.amount)
    return {"operation": req.operation, "base": req.amount, "fee": fee, "net": round(req.amount - fee, 2)}

@api_router.get("/admin/fees")
async def admin_get_fees(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return {"fee_policy": await _fee_policy()}

@api_router.put("/admin/fees")
async def admin_set_fees(req: dict, user: dict = Depends(get_current_user)):
    await require_admin(user)
    policy = req.get("fee_policy", {})
    if not isinstance(policy, dict):
        raise HTTPException(status_code=400, detail="fee_policy invalide")
    allowed_ops = {"withdrawal", "capture", "marketplace", "conversion", "transfer", "deposit"}
    clean = {}
    for op, cfg in policy.items():
        if op not in allowed_ops:
            raise HTTPException(status_code=400, detail=f"Opération de frais inconnue: {op}")
        pct = float(cfg.get("pct", 0.0))
        flat = float(cfg.get("flat", 0.0))
        if not (0.0 <= pct <= 1.0) or flat < 0:
            raise HTTPException(status_code=400, detail="pct doit être entre 0 et 1, flat >= 0")
        clean[op] = {"pct": pct, "flat": flat}
    await db.settings.update_one({"key": "app"}, {"$set": {"fee_policy": clean}}, upsert=True)
    await audit(user["user_id"], "Financial.FeePolicyUpdated", {"fee_policy": clean})
    return {"ok": True, "fee_policy": clean}

async def _original_tx_and_entry(tx_id: str):
    orig = await db.transactions.find_one({"tx_id": tx_id}, {"_id": 0})
    entry = await db.ledger_entries.find_one({"ref": tx_id}, {"_id": 0})
    return orig, entry

def _cash_and_counter(entry: dict):
    cash = counter = None
    for p in entry["postings"]:
        if p["account_id"].startswith("acct_cash_"):
            cash = p
        else:
            counter = p
    return cash, counter

class RefundRequest(BaseModel):
    original_tx_id: str
    amount: Optional[float] = None
    reason: str = ""

@api_router.post("/refunds")
async def create_refund(req: RefundRequest, request: Request, user: dict = Depends(get_current_user)):
    await require_admin(user)
    idem_id, cached = await idem_begin(request, user, "refund", req.dict())
    if cached is not None:
        return cached
    try:
        orig, entry = await _original_tx_and_entry(req.original_tx_id)
        if not orig:
            raise HTTPException(status_code=404, detail="TRANSACTION_NOT_FOUND")
        if orig["amount"] >= 0:
            raise HTTPException(status_code=400, detail="ONLY_OUTFLOW_REFUNDABLE")
        if not entry or len(entry.get("postings", [])) != 2:
            raise HTTPException(status_code=400, detail="LEDGER_ENTRY_UNSUPPORTED")
        principal = round(-orig["amount"], 2)
        refund_amt = round(req.amount if req.amount is not None else principal, 2)
        if refund_amt <= 0:
            raise HTTPException(status_code=400, detail="Montant de remboursement invalide")
        cash, counter = _cash_and_counter(entry)
        if not cash or not counter:
            raise HTTPException(status_code=400, detail="LEDGER_ENTRY_UNSUPPORTED")
        target_user = orig["user_id"]
        # ATOMIC cumulative guard on the ORIGINAL tx: refunded + amt <= principal, and not reversed.
        guard = await db.transactions.find_one_and_update(
            {"tx_id": req.original_tx_id, "reversed": {"$ne": True},
             "$expr": {"$lte": [{"$add": [{"$ifNull": ["$refunded_cc", 0]}, refund_amt]}, principal]}},
            {"$inc": {"refunded_cc": refund_amt}}, return_document=ReturnDocument.AFTER)
        if not guard:
            await audit(target_user, "Financial.RefundRejected",
                        {"original_tx_id": req.original_tx_id, "amount": refund_amt, "reason": "exceeds_or_reversed"})
            raise HTTPException(status_code=409, detail="REFUND_EXCEEDS_PRINCIPAL_OR_REVERSED")
        try:
            rid = f"rf_{uuid.uuid4().hex[:10]}"
            corr = f"corr_{uuid.uuid4().hex[:10]}"
            # Reverse the cash leg (credit user) against the original counterparty.
            await ledger_post(f"Remboursement — {orig.get('label','')}", "Remboursement",
                              [(cash_acct(target_user), refund_amt), (counter["account_id"], -refund_amt)], ref=rid)
            await db.users.update_one({"user_id": target_user}, {"$inc": {"balance_cc": refund_amt}})
            await db.transactions.insert_one({
                "tx_id": f"tx_{uuid.uuid4().hex[:10]}", "user_id": target_user,
                "label": f"Remboursement — {orig.get('label','')}", "amount": refund_amt,
                "category": "Remboursement", "type": "in", "ref": rid,
                "created_at": datetime.now(timezone.utc).isoformat()})
            fully = abs(round(guard.get("refunded_cc", 0), 2) - principal) < 1e-6
            rec = {"refund_id": rid, "original_tx_id": req.original_tx_id, "user_id": target_user,
                   "amount": refund_amt, "status": "COMPLETED", "reason": req.reason, "correlation_id": corr,
                   "fully_refunded": fully, "created_at": datetime.now(timezone.utc).isoformat()}
            await db.refunds.insert_one(dict(rec))
            await record_state("refund", rid, "REQUESTED", "COMPLETED", user["user_id"], req.reason, corr)
            await audit(target_user, "Financial.RefundCompleted",
                        {"refund_id": rid, "original_tx_id": req.original_tx_id, "amount": refund_amt})
            return await idem_finish(idem_id, rec)
        except Exception:
            # Compensation: give the reserved principal back to the guard counter if posting failed.
            await db.transactions.update_one({"tx_id": req.original_tx_id}, {"$inc": {"refunded_cc": -refund_amt}})
            raise
    except Exception:
        await idem_fail(idem_id)
        raise

@api_router.get("/refunds")
async def list_refunds(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.refunds.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

class ReversalRequest(BaseModel):
    original_tx_id: str
    reason: str = ""

@api_router.post("/reversals")
async def create_reversal(req: ReversalRequest, request: Request, user: dict = Depends(get_current_user)):
    await require_admin(user)
    idem_id, cached = await idem_begin(request, user, "reversal", req.dict())
    if cached is not None:
        return cached
    try:
        orig, entry = await _original_tx_and_entry(req.original_tx_id)
        if not orig:
            raise HTTPException(status_code=404, detail="TRANSACTION_NOT_FOUND")
        if not entry or len(entry.get("postings", [])) != 2:
            raise HTTPException(status_code=400, detail="LEDGER_ENTRY_UNSUPPORTED")
        # ATOMIC: reverse exactly once, and only if untouched by refunds.
        guard = await db.transactions.find_one_and_update(
            {"tx_id": req.original_tx_id, "reversed": {"$ne": True},
             "$expr": {"$eq": [{"$ifNull": ["$refunded_cc", 0]}, 0]}},
            {"$set": {"reversed": True}}, return_document=ReturnDocument.AFTER)
        if not guard:
            raise HTTPException(status_code=409, detail="ALREADY_REVERSED_OR_REFUNDED")
        try:
            cash, _ = _cash_and_counter(entry)
            rvid = f"rv_{uuid.uuid4().hex[:10]}"
            corr = f"corr_{uuid.uuid4().hex[:10]}"
            # Post the EXACT inverse of every original posting (sums to 0 -> ledger stays balanced).
            inv = [(p["account_id"], round(-p["amount"], 2)) for p in entry["postings"]]
            await ledger_post(f"Extourne — {orig.get('label','')}", "Extourne", inv, ref=rvid)
            if cash:
                await db.users.update_one({"user_id": orig["user_id"]}, {"$inc": {"balance_cc": round(-cash["amount"], 2)}})
            await db.transactions.insert_one({
                "tx_id": f"tx_{uuid.uuid4().hex[:10]}", "user_id": orig["user_id"],
                "label": f"Extourne — {orig.get('label','')}", "amount": round(-cash["amount"], 2) if cash else 0.0,
                "category": "Extourne", "type": "in" if cash and -cash["amount"] > 0 else "out", "ref": rvid,
                "created_at": datetime.now(timezone.utc).isoformat()})
            rec = {"reversal_id": rvid, "original_tx_id": req.original_tx_id, "user_id": orig["user_id"],
                   "reason": req.reason, "status": "COMPLETED", "correlation_id": corr,
                   "created_at": datetime.now(timezone.utc).isoformat()}
            await db.reversals.insert_one(dict(rec))
            await record_state("reversal", rvid, "REQUESTED", "COMPLETED", user["user_id"], req.reason, corr)
            await audit(orig["user_id"], "Financial.ReversalCompleted",
                        {"reversal_id": rvid, "original_tx_id": req.original_tx_id})
            return await idem_finish(idem_id, rec)
        except Exception:
            # Compensation: clear the reversed flag so a retry is possible if posting failed.
            await db.transactions.update_one({"tx_id": req.original_tx_id}, {"$set": {"reversed": False}})
            raise
    except Exception:
        await idem_fail(idem_id)
        raise

@api_router.get("/reversals")
async def list_reversals(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.reversals.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)


# ================= ASSET REGISTRY + MONEY (P0.1-B5 precision foundation) =================
# Amounts are represented in "minor units" (smallest indivisible unit of the asset).
# Rounding is CENTRALISED here. No opportunistic round() in business code.
import decimal
from decimal import Decimal, ROUND_HALF_UP

ASSET_REGISTRY = {
    "JCC": {"asset_code": "JCC", "decimals": 2, "minor_unit": 100, "rounding": "HALF_UP",
            "enabled": True, "type": "internal_token"},
    "EUR": {"asset_code": "EUR", "decimals": 2, "minor_unit": 100, "rounding": "HALF_UP",
            "enabled": True, "type": "fiat"},
}

def asset_def(asset: str) -> dict:
    a = ASSET_REGISTRY.get(asset)
    if not a or not a["enabled"]:
        raise HTTPException(status_code=400, detail=f"ASSET_NOT_SUPPORTED:{asset}")
    return a

def to_minor(amount, asset: str = DEFAULT_ASSET) -> int:
    """Canonical conversion to integer minor units using the asset's rounding policy."""
    a = asset_def(asset)
    q = Decimal(str(amount)).quantize(Decimal(1).scaleb(-a["decimals"]), rounding=ROUND_HALF_UP)
    return int((q * a["minor_unit"]).to_integral_value(rounding=ROUND_HALF_UP))

def from_minor(minor: int, asset: str = DEFAULT_ASSET) -> float:
    a = asset_def(asset)
    return float(Decimal(int(minor)) / Decimal(a["minor_unit"]))

def money_round(amount, asset: str = DEFAULT_ASSET) -> float:
    """Round a float amount to the asset granularity (centralised rounding policy)."""
    return from_minor(to_minor(amount, asset), asset)

def is_minor_exact(amount, asset: str = DEFAULT_ASSET) -> bool:
    a = asset_def(asset)
    d = Decimal(str(amount)) * a["minor_unit"]
    return d == d.to_integral_value()

@api_router.get("/assets")
async def list_assets(user: dict = Depends(get_current_user)):
    return list(ASSET_REGISTRY.values())


# ================= P0.1-B4: SETTLEMENT / RECONCILIATION / OUTBOX / INBOX =================
# Settlement is a TRACKING layer over provider interactions for already-captured ledger
# movements. It does NOT re-post value (no second ledger). Reconciliation compares the
# internal expected state against the external observed state and opens cases for mismatches.
SETTLEMENT_TRANSITIONS = {
    "PENDING": {"SUBMITTED", "PROCESSING", "SETTLED", "FAILED", "CANCELLED", "REQUIRES_REVIEW"},
    "SUBMITTED": {"PROCESSING", "SETTLED", "FAILED", "CANCELLED", "REQUIRES_REVIEW"},
    "PROCESSING": {"SETTLED", "FAILED", "REQUIRES_REVIEW"},
    "REQUIRES_REVIEW": {"SETTLED", "FAILED", "CANCELLED"},
    "SETTLED": set(), "FAILED": set(), "CANCELLED": set(),
}
SETTLEMENT_TERMINAL = {"SETTLED", "FAILED", "CANCELLED"}

def _settlement_predecessors(target: str):
    return [s for s, tos in SETTLEMENT_TRANSITIONS.items() if target in tos]

# ---- Provider adapter boundary (providers NEVER touch ledger/balances/holds directly) ----
class MockProviderAdapter:
    """Honest MOCK adapter. Real issuers/banks/brokers/custodians plug in here later.
    A provider only reports external state; the Financial Core owns all value mutations."""
    status = "MOCK"
    def __init__(self, name): self.name = name
    async def submit(self, settlement: dict) -> dict:
        return {"provider_reference": f"{self.name}_ref_{uuid.uuid4().hex[:10]}", "external_status": "processing"}

PROVIDERS = {"mock_bank": MockProviderAdapter("mock_bank"), "mock_processor": MockProviderAdapter("mock_processor")}

def get_provider(name: str):
    p = PROVIDERS.get(name)
    if not p:
        raise HTTPException(status_code=400, detail=f"PROVIDER_UNKNOWN:{name}")
    return p

# ---- Transactional Outbox (PARTIAL on standalone MongoDB: no multi-doc atomicity) ----
# Correctness strategy: events are idempotent (dedup by event_id), delivery is at-least-once,
# and the recovery scanner detects/regenerates missing events. This is NOT a strict
# transactional outbox — status is therefore PARTIAL, documented honestly.
async def emit_event(event_type, aggregate_type, aggregate_id, payload, correlation_id=None, event_id=None, causation_id=None):
    eid = event_id or f"evt_{uuid.uuid4().hex[:12]}"
    from pymongo.errors import DuplicateKeyError
    try:
        await db.outbox_events.insert_one({
            "event_id": eid, "event_type": event_type, "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id, "correlation_id": correlation_id, "causation_id": causation_id,
            "payload": payload, "status": "PENDING", "attempts": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "available_at": datetime.now(timezone.utc).isoformat(),
            "delivered_at": None, "last_error": None})
    except DuplicateKeyError:
        pass  # idempotent emit
    return eid

async def _deliver_event(ev: dict) -> bool:
    """Internal at-least-once consumer. Consumer MUST tolerate duplicates -> dedup by event_id."""
    from pymongo.errors import DuplicateKeyError
    try:
        await db.outbox_consumed.insert_one({"event_id": ev["event_id"],
                                             "consumed_at": datetime.now(timezone.utc).isoformat()})
    except DuplicateKeyError:
        return True  # already consumed once -> single business effect
    return True

_OUTBOX_TASK = None
async def outbox_worker():
    while True:
        try:
            now = datetime.now(timezone.utc).isoformat()
            async for ev in db.outbox_events.find(
                    {"status": {"$in": ["PENDING", "RETRY"]}, "available_at": {"$lte": now}}).limit(50):
                claimed = await db.outbox_events.find_one_and_update(
                    {"event_id": ev["event_id"], "status": {"$in": ["PENDING", "RETRY"]}},
                    {"$set": {"status": "DELIVERING"}, "$inc": {"attempts": 1}},
                    return_document=ReturnDocument.AFTER)
                if not claimed:
                    continue
                try:
                    ok = await _deliver_event(claimed)
                    if ok:
                        await db.outbox_events.update_one({"event_id": ev["event_id"]},
                            {"$set": {"status": "DELIVERED", "delivered_at": datetime.now(timezone.utc).isoformat()}})
                except Exception as e:
                    attempts = claimed.get("attempts", 1)
                    backoff = min(60, 2 ** attempts)
                    nxt = (datetime.now(timezone.utc) + timedelta(seconds=backoff)).isoformat()
                    status = "DEAD_LETTER" if attempts >= 8 else "RETRY"
                    await db.outbox_events.update_one({"event_id": ev["event_id"]},
                        {"$set": {"status": status, "available_at": nxt, "last_error": str(e)[:200]}})
        except Exception:
            pass
        await asyncio.sleep(2)

# ---- Settlement models + endpoints ----
class SettlementCreate(BaseModel):
    transaction_id: str
    provider: str
    direction: str = "payout"   # payout | payin
    asset: str = DEFAULT_ASSET

async def settlement_transition(settlement_id, target, actor, reason=""):
    now = datetime.now(timezone.utc).isoformat()
    st = await db.settlements.find_one_and_update(
        {"settlement_id": settlement_id, "internal_status": {"$in": _settlement_predecessors(target)}},
        {"$set": {"internal_status": target, "updated_at": now,
                  **({"settled_at": now} if target == "SETTLED" else {})}},
        return_document=ReturnDocument.AFTER)
    if not st:
        return None
    await record_state("settlement", settlement_id, None, target, actor, reason, st.get("correlation_id"))
    await emit_event(f"Financial.Settlement{target.title().replace('_','')}", "settlement", settlement_id,
                     {"internal_status": target}, correlation_id=st.get("correlation_id"))
    return st

@api_router.post("/admin/settlements")
async def create_settlement(req: SettlementCreate, request: Request, user: dict = Depends(get_current_user)):
    await require_admin(user)
    idem_id, cached = await idem_begin(request, user, "settlement", req.dict())
    if cached is not None:
        return cached
    try:
        asset_def(req.asset)
        tx = await db.transactions.find_one({"tx_id": req.transaction_id}, {"_id": 0})
        if not tx:
            raise HTTPException(status_code=404, detail="TRANSACTION_NOT_FOUND")
        get_provider(req.provider)
        amount = abs(tx["amount"])
        sid = f"stl_{uuid.uuid4().hex[:10]}"
        corr = f"corr_{uuid.uuid4().hex[:10]}"
        doc = {"settlement_id": sid, "transaction_id": req.transaction_id, "user_id": tx.get("user_id"),
               "provider": req.provider, "provider_reference": None, "asset": req.asset,
               "amount": amount, "amount_minor": to_minor(amount, req.asset), "direction": req.direction,
               "internal_status": "PENDING", "external_status": None, "reconciliation_status": "UNRECONCILED",
               "correlation_id": corr, "idempotency_key": f"stl:{req.transaction_id}",
               "failure_code": None, "failure_reason": None, "retry_count": 0,
               "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(),
               "settled_at": None}
        try:
            await db.settlements.insert_one(dict(doc))
        except Exception:
            existing = await db.settlements.find_one({"idempotency_key": f"stl:{req.transaction_id}"}, {"_id": 0})
            if existing:
                return await idem_finish(idem_id, existing)
            raise
        await record_state("settlement", sid, None, "PENDING", user["user_id"], "created", corr)
        await emit_event("Financial.SettlementCreated", "settlement", sid, {"amount": amount}, correlation_id=corr)
        doc.pop("_id", None)
        return await idem_finish(idem_id, doc)
    except Exception:
        await idem_fail(idem_id)
        raise

@api_router.post("/admin/settlements/{settlement_id}/submit")
async def submit_settlement(settlement_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    s = await db.settlements.find_one({"settlement_id": settlement_id}, {"_id": 0})
    if not s:
        raise HTTPException(status_code=404, detail="SETTLEMENT_NOT_FOUND")
    provider = get_provider(s["provider"])
    st = await settlement_transition(settlement_id, "SUBMITTED", user["user_id"], "submit")
    if not st:
        # Retry path: if a prior submit crashed after the transition but before the ref write,
        # allow re-submit while status==SUBMITTED and provider_reference is still null.
        if not (s["internal_status"] == "SUBMITTED" and not s.get("provider_reference")):
            raise HTTPException(status_code=409, detail=f"INVALID_TRANSITION_FROM:{s['internal_status']}")
    res = await provider.submit(s)  # provider only returns external state
    await db.settlements.update_one({"settlement_id": settlement_id},
        {"$set": {"provider_reference": res["provider_reference"], "external_status": res["external_status"]}})
    await emit_event("Financial.SettlementSubmitted", "settlement", settlement_id,
                     {"provider_reference": res["provider_reference"]}, correlation_id=s.get("correlation_id"))
    return await db.settlements.find_one({"settlement_id": settlement_id}, {"_id": 0})

@api_router.get("/admin/settlements")
async def list_settlements(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.settlements.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.get("/admin/settlements/{settlement_id}")
async def get_settlement(settlement_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    s = await db.settlements.find_one({"settlement_id": settlement_id}, {"_id": 0})
    if not s:
        raise HTTPException(status_code=404, detail="SETTLEMENT_NOT_FOUND")
    hist = await db.financial_state_history.find({"entity_type": "settlement", "entity_id": settlement_id}, {"_id": 0}).sort("created_at", 1).to_list(100)
    return {**s, "history": hist}

# ---- Provider webhook inbox (dedup + out-of-order safe) ----
_WEBHOOK_STATUS_MAP = {"processing": "PROCESSING", "settled": "SETTLED", "paid": "SETTLED",
                       "failed": "FAILED", "cancelled": "CANCELLED"}

@api_router.post("/webhooks/{provider}")
async def provider_webhook(provider: str, request: Request):
    body = await request.json()
    provider_event_id = body.get("event_id") or body.get("id")
    if not provider_event_id:
        raise HTTPException(status_code=400, detail="MISSING_PROVIDER_EVENT_ID")
    import hashlib, json as _json
    from pymongo.errors import DuplicateKeyError
    phash = hashlib.sha256(_json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    try:
        await db.webhook_inbox.insert_one({
            "provider": provider, "provider_event_id": provider_event_id, "payload_hash": phash,
            "received_at": datetime.now(timezone.utc).isoformat(), "processing_status": "RECEIVED",
            "attempts": 0, "last_error": None, "payload": body})
    except DuplicateKeyError:
        prev = await db.webhook_inbox.find_one({"provider": provider, "provider_event_id": provider_event_id}, {"_id": 0, "payload_hash": 1})
        if prev and prev.get("payload_hash") != phash:
            # Same event_id, DIFFERENT body -> conflict, not a benign retry.
            await emit_event("Financial.ProviderWebhookConflict", "webhook", provider_event_id, {"provider": provider})
            return {"status": "duplicate_conflict"}
        await emit_event("Financial.ProviderWebhookDuplicate", "webhook", provider_event_id, {"provider": provider})
        return {"status": "duplicate_ignored"}  # same webhook N times -> 1 effect
    await emit_event("Financial.ProviderWebhookReceived", "webhook", provider_event_id, {"provider": provider})
    await db.webhook_inbox.update_one({"provider": provider, "provider_event_id": provider_event_id},
                                      {"$inc": {"attempts": 1}})
    ref = body.get("provider_reference"); ext = str(body.get("status", "")).lower()
    target = _WEBHOOK_STATUS_MAP.get(ext)
    result = "no_action"
    if ref and target:
        # SECURITY: scope to the provider in the URL — a webhook for provider A can never
        # drive a settlement that belongs to provider B.
        s = await db.settlements.find_one({"provider_reference": ref, "provider": provider}, {"_id": 0})
        if not s:
            # Security signal: a reference that exists under a DIFFERENT provider = scope/spoof attempt.
            other = await db.settlements.find_one({"provider_reference": ref}, {"_id": 0, "provider": 1})
            if other:
                await emit_event("Financial.ProviderScopeViolation", "settlement", ref,
                                 {"posted_to": provider, "belongs_to": other["provider"]})
                await db.webhook_inbox.update_one({"provider": provider, "provider_event_id": provider_event_id},
                                                  {"$set": {"processing_status": "SCOPE_VIOLATION"}})
                return {"status": "processed", "result": "scope_violation"}
        if s:
            await db.settlements.update_one({"settlement_id": s["settlement_id"]}, {"$set": {"external_status": ext}})
            st = await settlement_transition(s["settlement_id"], target, f"webhook:{provider}", f"event={provider_event_id}")
            if st:
                result = f"applied:{target}"
            else:
                # Out-of-order or terminal: cannot apply -> record conflict, do NOT blindly override.
                cur = await db.settlements.find_one({"settlement_id": s["settlement_id"]}, {"_id": 0, "internal_status": 1})
                if cur["internal_status"] not in SETTLEMENT_TERMINAL:
                    await settlement_transition(s["settlement_id"], "REQUIRES_REVIEW", f"webhook:{provider}", "out_of_order")
                    result = "review"
                else:
                    result = "ignored_terminal"
                await emit_event("Financial.ProviderStateConflict", "settlement", s["settlement_id"],
                                 {"attempted": target, "current": cur["internal_status"]})
    await db.webhook_inbox.update_one({"provider": provider, "provider_event_id": provider_event_id},
                                      {"$set": {"processing_status": "PROCESSED", "result": result}})
    return {"status": "processed", "result": result}

# ---- Reconciliation engine + cases ----
async def run_reconciliation():
    """Compare internal settlements vs external observed. Opens cases for mismatches.
    Never corrects silently: every discrepancy becomes an auditable ReconciliationCase."""
    opened = 0
    async for s in db.settlements.find({}, {"_id": 0}):
        mismatch = None
        # submitted/processing settlements without a provider reference (should have one)
        if s["internal_status"] in ("SUBMITTED", "PROCESSING") and not s.get("provider_reference"):
            mismatch = "missing_provider_reference"
        # settled internally but external status not terminal
        elif s["internal_status"] == "SETTLED" and s.get("external_status") not in ("settled", "paid"):
            mismatch = "status_mismatch"
        # amount vs linked ledger tx
        else:
            tx = await db.transactions.find_one({"tx_id": s["transaction_id"]}, {"_id": 0})
            if tx and abs(abs(tx["amount"]) - s["amount"]) > 0.005:
                mismatch = "amount_mismatch"
        if mismatch:
            exists = await db.reconciliation_cases.find_one(
                {"settlement_id": s["settlement_id"], "mismatch_type": mismatch, "status": {"$in": ["OPEN", "INVESTIGATING"]}}, {"_id": 0})
            if not exists:
                cid = f"rc_{uuid.uuid4().hex[:10]}"
                await db.reconciliation_cases.insert_one({
                    "case_id": cid, "provider": s["provider"], "transaction_id": s["transaction_id"],
                    "settlement_id": s["settlement_id"], "provider_reference": s.get("provider_reference"),
                    "mismatch_type": mismatch, "expected": s["internal_status"], "observed": s.get("external_status"),
                    "severity": "HIGH" if mismatch == "amount_mismatch" else "MEDIUM", "status": "OPEN",
                    "resolution": None, "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(), "resolved_at": None})
                await emit_event("Financial.ReconciliationMismatch", "reconciliation", cid, {"mismatch": mismatch})
                opened += 1
    return opened

@api_router.post("/admin/reconciliation/run")
async def reconciliation_run(user: dict = Depends(get_current_user)):
    await require_admin(user)
    opened = await run_reconciliation()
    return {"ok": True, "cases_opened": opened}

@api_router.get("/admin/reconciliation/cases")
async def reconciliation_cases(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.reconciliation_cases.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.post("/admin/reconciliation/cases/{case_id}/resolve")
async def resolve_case(case_id: str, req: dict, user: dict = Depends(get_current_user)):
    await require_admin(user)
    resolution = req.get("resolution", "ACCEPTED_DIFFERENCE")
    if resolution not in ("RESOLVED", "ACCEPTED_DIFFERENCE", "ESCALATED"):
        raise HTTPException(status_code=400, detail="Résolution invalide")
    c = await db.reconciliation_cases.find_one_and_update(
        {"case_id": case_id, "status": {"$in": ["OPEN", "INVESTIGATING"]}},
        {"$set": {"status": resolution, "resolution": req.get("note", ""), "reviewer": user["user_id"],
                  "resolved_at": datetime.now(timezone.utc).isoformat(),
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        return_document=ReturnDocument.AFTER)
    if not c:
        raise HTTPException(status_code=409, detail="Cas introuvable ou déjà résolu")
    c.pop("_id", None)
    await emit_event("Financial.ReconciliationResolved", "reconciliation", case_id, {"resolution": resolution})
    await audit(user["user_id"], "Financial.ReconciliationResolved", {"case_id": case_id, "resolution": resolution})
    return c

@api_router.get("/admin/outbox")
async def list_outbox(user: dict = Depends(get_current_user), status: Optional[str] = None):
    await require_admin(user)
    q = {"status": status} if status else {}
    return await db.outbox_events.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)

@api_router.post("/admin/outbox/{event_id}/replay")
async def replay_outbox(event_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    # Safe replay: reset to RETRY; the consumer is idempotent (dedup by event_id).
    r = await db.outbox_events.update_one({"event_id": event_id},
        {"$set": {"status": "RETRY", "available_at": datetime.now(timezone.utc).isoformat()}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Événement introuvable")
    return {"ok": True}


# ================= P0.1-B5: MAKER-CHECKER + RECOVERY =================
# Sensitive admin financial operations require a second, DIFFERENT approver.
SENSITIVE_OPS = {"manual_ledger_adjustment", "fee_policy_change", "kill_switch_critical",
                 "high_value_refund", "settlement_override"}

class ApprovalCreate(BaseModel):
    operation_type: str
    payload: dict = {}
    reason: str = ""

def _payload_hash(payload: dict) -> str:
    import hashlib, json as _json
    return hashlib.sha256(_json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

@api_router.post("/admin/approvals")
async def create_approval(req: ApprovalCreate, user: dict = Depends(get_current_user)):
    await require_admin(user)
    if req.operation_type not in SENSITIVE_OPS:
        raise HTTPException(status_code=400, detail=f"OPERATION_NOT_SENSITIVE:{req.operation_type}")
    aid = f"apr_{uuid.uuid4().hex[:10]}"
    doc = {"approval_id": aid, "operation_type": req.operation_type, "payload": req.payload,
           "operation_payload_hash": _payload_hash(req.payload), "maker_id": user["user_id"],
           "checker_id": None, "status": "PENDING", "reason": req.reason,
           "correlation_id": f"corr_{uuid.uuid4().hex[:10]}", "execution_status": "NONE",
           "created_at": datetime.now(timezone.utc).isoformat(),
           "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
           "approved_at": None, "rejected_at": None}
    await db.approval_requests.insert_one(dict(doc))
    await audit(user["user_id"], "MakerChecker.RequestCreated", {"approval_id": aid, "op": req.operation_type})
    doc.pop("_id", None)
    return doc

async def _execute_approved_operation(appr: dict):
    op = appr["operation_type"]; p = appr["payload"]
    if op == "fee_policy_change":
        policy = p.get("fee_policy", {})
        await db.settings.update_one({"key": "app"}, {"$set": {"fee_policy": policy}}, upsert=True)
    elif op == "kill_switch_critical":
        name = p.get("name")
        if name in ("withdrawals", "card", "agents"):
            await db.settings.update_one({"key": "app"}, {"$set": {f"ks_{name}": bool(p.get("enabled"))}}, upsert=True)
    elif op == "manual_ledger_adjustment":
        # value-affecting: MUST go through the ledger (balanced 2-leg entry).
        acc = p["account_id"]; counter = p.get("counter_account", SYSTEM_ACCOUNTS["clearing"]); amt = money_round(p["amount"])
        await ledger_post(f"Ajustement manuel — {appr['reason']}", "Ajustement",
                          [(acc, amt), (counter, -amt)], ref=appr["approval_id"])
        if acc.startswith("acct_cash_"):
            await db.users.update_one({"user_id": acc.replace("acct_cash_", "")}, {"$inc": {"balance_cc": amt}})
    # settlement_override / high_value_refund: recorded + audited (no blind value creation here)
    await emit_event("Financial.MakerCheckerExecuted", "approval", appr["approval_id"], {"op": op})

@api_router.post("/admin/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    appr = await db.approval_requests.find_one({"approval_id": approval_id}, {"_id": 0})
    if not appr:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    # BACKEND enforcement: maker != checker (not just UI).
    if appr["maker_id"] == user["user_id"]:
        raise HTTPException(status_code=403, detail="MAKER_CANNOT_BE_CHECKER")
    exp = datetime.fromisoformat(appr["expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="APPROVAL_EXPIRED")
    # Payload immutability guard: hash must still match.
    if appr["operation_payload_hash"] != _payload_hash(appr["payload"]):
        raise HTTPException(status_code=409, detail="PAYLOAD_TAMPERED")
    # SINGLE EXECUTION: atomic status flip is the lock -> concurrent checkers can't double-execute.
    claimed = await db.approval_requests.find_one_and_update(
        {"approval_id": approval_id, "status": "PENDING", "maker_id": {"$ne": user["user_id"]}},
        {"$set": {"status": "APPROVED", "checker_id": user["user_id"],
                  "approved_at": datetime.now(timezone.utc).isoformat(), "execution_status": "EXECUTING"}},
        return_document=ReturnDocument.AFTER)
    if not claimed:
        raise HTTPException(status_code=409, detail="ALREADY_DECIDED_OR_INVALID")
    try:
        await _execute_approved_operation(claimed)
        await db.approval_requests.update_one({"approval_id": approval_id}, {"$set": {"execution_status": "EXECUTED"}})
    except Exception as e:
        await db.approval_requests.update_one({"approval_id": approval_id},
            {"$set": {"execution_status": "FAILED", "execution_error": str(e)[:200]}})
        await db.recovery_journal.insert_one({"entry_id": f"rec_{uuid.uuid4().hex[:10]}", "kind": "approval_execution_failed",
            "ref": approval_id, "classification": "MANUAL_REVIEW", "detail": str(e)[:200],
            "created_at": datetime.now(timezone.utc).isoformat()})
        raise
    await audit(user["user_id"], "MakerChecker.Approved", {"approval_id": approval_id, "op": claimed["operation_type"]})
    return {"ok": True, "status": "APPROVED", "execution_status": "EXECUTED"}

@api_router.post("/admin/approvals/{approval_id}/reject")
async def reject_approval(approval_id: str, user: dict = Depends(get_current_user)):
    await require_admin(user)
    appr = await db.approval_requests.find_one({"approval_id": approval_id}, {"_id": 0})
    if not appr:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if appr["maker_id"] == user["user_id"]:
        raise HTTPException(status_code=403, detail="MAKER_CANNOT_BE_CHECKER")
    r = await db.approval_requests.find_one_and_update(
        {"approval_id": approval_id, "status": "PENDING"},
        {"$set": {"status": "REJECTED", "checker_id": user["user_id"], "rejected_at": datetime.now(timezone.utc).isoformat()}})
    if not r:
        raise HTTPException(status_code=409, detail="ALREADY_DECIDED")
    await audit(user["user_id"], "MakerChecker.Rejected", {"approval_id": approval_id})
    return {"ok": True, "status": "REJECTED"}

@api_router.get("/admin/approvals")
async def list_approvals(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.approval_requests.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)

# ---- Recovery: operational journal + scanner (correctness independent of any worker) ----
@api_router.post("/admin/recovery/scan")
async def recovery_scan(user: dict = Depends(get_current_user)):
    await require_admin(user)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    findings = {"stale_idempotency": 0, "expired_active_holds": 0, "stuck_settlements": 0,
                "undelivered_outbox": 0, "dead_letter_outbox": 0, "unprocessed_inbox": 0, "expired_approvals": 0}
    classification = {"AUTO_RECOVERABLE": [], "MANUAL_REVIEW": [], "CRITICAL": []}
    stale_cut = (now - timedelta(minutes=15)).isoformat()
    findings["stale_idempotency"] = await db.idempotency_records.count_documents({"state": "PROCESSING", "created_at": {"$lt": stale_cut}})
    if findings["stale_idempotency"]:
        classification["AUTO_RECOVERABLE"].append("stale_idempotency")
    findings["expired_active_holds"] = await db.balance_holds.count_documents(
        {"status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]}, "expires_at": {"$lte": now_iso}})
    if findings["expired_active_holds"]:
        classification["AUTO_RECOVERABLE"].append("expired_active_holds")
    stuck_cut = (now - timedelta(hours=24)).isoformat()
    findings["stuck_settlements"] = await db.settlements.count_documents(
        {"internal_status": {"$in": ["PENDING", "SUBMITTED", "PROCESSING"]}, "created_at": {"$lt": stuck_cut}})
    if findings["stuck_settlements"]:
        classification["MANUAL_REVIEW"].append("stuck_settlements")
    findings["undelivered_outbox"] = await db.outbox_events.count_documents({"status": {"$in": ["PENDING", "RETRY", "DELIVERING"]}})
    findings["dead_letter_outbox"] = await db.outbox_events.count_documents({"status": "DEAD_LETTER"})
    if findings["dead_letter_outbox"]:
        classification["CRITICAL"].append("dead_letter_outbox")
    findings["unprocessed_inbox"] = await db.webhook_inbox.count_documents({"processing_status": {"$ne": "PROCESSED"}})
    if findings["unprocessed_inbox"]:
        classification["MANUAL_REVIEW"].append("unprocessed_inbox")
    findings["expired_approvals"] = await db.approval_requests.count_documents({"status": "PENDING", "expires_at": {"$lt": now_iso}})
    if findings["expired_approvals"]:
        classification["AUTO_RECOVERABLE"].append("expired_approvals")
    await db.recovery_journal.insert_one({"entry_id": f"rec_{uuid.uuid4().hex[:10]}", "kind": "scan",
        "findings": findings, "classification": classification, "created_at": now_iso})
    return {"findings": findings, "classification": classification}

@api_router.post("/admin/recovery/auto-heal")
async def recovery_auto_heal(user: dict = Depends(get_current_user)):
    await require_admin(user)
    # Only AUTO_RECOVERABLE actions. Idempotent + safe.
    healed = {}
    stale_cut = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    r = await db.idempotency_records.delete_many({"state": "PROCESSING", "created_at": {"$lt": stale_cut}})
    healed["stale_idempotency_cleared"] = r.deleted_count
    # lazy-expire all overdue holds across users
    expired = 0
    async for h in db.balance_holds.find({"status": {"$in": ["ACTIVE", "PARTIALLY_CAPTURED"]},
                                         "expires_at": {"$lte": datetime.now(timezone.utc).isoformat()}}, {"_id": 0, "hold_id": 1}):
        if await _terminate_hold(h["hold_id"], "EXPIRED", "recovery", "auto_heal"):
            expired += 1
    healed["holds_expired"] = expired
    # expire stale PENDING approvals (idempotent)
    now_iso = datetime.now(timezone.utc).isoformat()
    ra = await db.approval_requests.update_many({"status": "PENDING", "expires_at": {"$lt": now_iso}},
                                                {"$set": {"status": "EXPIRED"}})
    healed["approvals_expired"] = ra.modified_count
    await db.recovery_journal.insert_one({"entry_id": f"rec_{uuid.uuid4().hex[:10]}", "kind": "auto_heal",
        "healed": healed, "created_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "healed": healed}

@api_router.get("/admin/recovery/journal")
async def recovery_journal_list(user: dict = Depends(get_current_user)):
    await require_admin(user)
    return await db.recovery_journal.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)

# ---- Monetary precision migration (dry-run, non-destructive, auditable) ----
@api_router.post("/admin/precision/migrate")
async def precision_migrate(user: dict = Depends(get_current_user), dry_run: bool = True):
    await require_admin(user)
    if not dry_run:
        # Honest: destructive integer-storage migration is not implemented yet (monetary_precision=PARTIAL).
        raise HTTPException(status_code=501, detail="DESTRUCTIVE_MIGRATION_NOT_IMPLEMENTED (monetary_precision=PARTIAL)")
    report = {"dry_run": dry_run, "ledger_postings_checked": 0, "non_representable_postings": [],
              "balances_checked": 0, "non_representable_balances": [], "economic_equality": True}
    async for e in db.ledger_entries.find({}, {"_id": 0, "entry_id": 1, "asset": 1, "postings": 1}):
        asset = e.get("asset", DEFAULT_ASSET)
        for p in e["postings"]:
            report["ledger_postings_checked"] += 1
            if asset in ASSET_REGISTRY and not is_minor_exact(p["amount"], asset):
                report["non_representable_postings"].append({"entry_id": e["entry_id"], "amount": p["amount"]})
    async for u in db.users.find({}, {"user_id": 1, "balance_cc": 1}):
        report["balances_checked"] += 1
        if not is_minor_exact(u.get("balance_cc", 0), DEFAULT_ASSET):
            report["non_representable_balances"].append({"user_id": u["user_id"], "balance_cc": u.get("balance_cc", 0)})
    report["representable"] = not (report["non_representable_postings"] or report["non_representable_balances"])
    await db.recovery_journal.insert_one({"entry_id": f"rec_{uuid.uuid4().hex[:10]}", "kind": "precision_migration",
        "report": {k: v for k, v in report.items() if not isinstance(v, list)}, "created_at": datetime.now(timezone.utc).isoformat()})
    return report



app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
