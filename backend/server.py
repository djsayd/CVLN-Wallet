from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
import random
import httpx
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

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
            **c,
        })
    if coffres:
        await db.coffres.insert_many(coffres)
    txs = []
    for t in SEED_TX:
        ts = now - timedelta(days=t["days"], hours=random.randint(0, 10))
        txs.append({
            "tx_id": f"tx_{uuid.uuid4().hex[:10]}",
            "user_id": user_id,
            "label": t["label"],
            "amount": t["amount"],
            "category": t["category"],
            "type": "in" if t["amount"] > 0 else "out",
            "created_at": ts.isoformat(),
        })
    if txs:
        await db.transactions.insert_many(txs)


async def add_transaction(user_id: str, label: str, amount: float, category: str):
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
        await db.users.update_one({"user_id": user_id}, {"$inc": {"balance_cc": amount}})
    doc.pop("_id", None)
    return doc


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
        await db.users.update_one({"user_id": user_id}, {"$set": {"name": data.get("name"), "picture": data.get("picture")}})
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
            "balance_cc": 154280.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await seed_user_data(user_id)
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
    coffres_total = 0
    async for c in db.coffres.find({"user_id": user["user_id"]}, {"_id": 0}):
        coffres_total += c.get("amount_cc", 0)
    txs = await db.transactions.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    inflow = sum(t["amount"] for t in txs if t["amount"] > 0)
    outflow = sum(-t["amount"] for t in txs if t["amount"] < 0)
    return {
        "balance_cc": balance,
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
async def send_money(req: SendRequest, user: dict = Depends(get_current_user)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    if req.amount > user.get("balance_cc", 0):
        raise HTTPException(status_code=400, detail="Solde insuffisant")
    label = f"Envoi à {req.recipient}" + (f" — {req.note}" if req.note else "")
    tx = await add_transaction(user["user_id"], label, -req.amount, "Transfert")
    return {"ok": True, "transaction": tx}


@api_router.post("/actions/buy")
async def buy_cc(req: BuyRequest, user: dict = Depends(get_current_user)):
    if req.amount_eur <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    cc = round(req.amount_eur / JCC_RATE_EUR, 2)
    tx = await add_transaction(user["user_id"], f"Achat de CC ({req.amount_eur} €)", cc, "Dépôt")
    return {"ok": True, "cc": cc, "transaction": tx}


@api_router.post("/convert")
async def convert(req: ConvertRequest, user: dict = Depends(get_current_user)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    if req.direction == "eur_to_jcc":
        cc = round(req.amount / JCC_RATE_EUR, 2)
        tx = await add_transaction(user["user_id"], f"Conversion {req.amount} € → CC", cc, "Conversion")
        return {"ok": True, "received_cc": cc, "transaction": tx}
    elif req.direction == "jcc_to_eur":
        if req.amount > user.get("balance_cc", 0):
            raise HTTPException(status_code=400, detail="Solde CC insuffisant")
        eur = round(req.amount * JCC_RATE_EUR, 2)
        tx = await add_transaction(user["user_id"], f"Conversion {req.amount} CC → EUR", -req.amount, "Conversion")
        return {"ok": True, "received_eur": eur, "transaction": tx}
    raise HTTPException(status_code=400, detail="Direction invalide")


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
async def move_coffre(coffre_id: str, req: CoffreMove, user: dict = Depends(get_current_user)):
    coffre = await db.coffres.find_one({"coffre_id": coffre_id, "user_id": user["user_id"]}, {"_id": 0})
    if not coffre:
        raise HTTPException(status_code=404, detail="Coffre introuvable")
    if req.amount > 0 and req.amount > user.get("balance_cc", 0):
        raise HTTPException(status_code=400, detail="Solde insuffisant")
    if req.amount < 0 and -req.amount > coffre.get("amount_cc", 0):
        raise HTTPException(status_code=400, detail="Fonds insuffisants dans le coffre")
    await db.coffres.update_one({"coffre_id": coffre_id}, {"$inc": {"amount_cc": req.amount}})
    await db.users.update_one({"user_id": user["user_id"]}, {"$inc": {"balance_cc": -req.amount}})
    verb = "Dépôt vers" if req.amount > 0 else "Retrait de"
    await add_transaction(user["user_id"], f"{verb} {coffre['name']}", 0, "Coffre")
    updated = await db.coffres.find_one({"coffre_id": coffre_id}, {"_id": 0})
    return {"ok": True, "coffre": updated}


@api_router.delete("/coffres/{coffre_id}")
async def delete_coffre(coffre_id: str, user: dict = Depends(get_current_user)):
    coffre = await db.coffres.find_one({"coffre_id": coffre_id, "user_id": user["user_id"]}, {"_id": 0})
    if not coffre:
        raise HTTPException(status_code=404, detail="Coffre introuvable")
    if coffre.get("amount_cc", 0) > 0:
        await db.users.update_one({"user_id": user["user_id"]}, {"$inc": {"balance_cc": coffre["amount_cc"]}})
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
async def buy_marketplace(req: MarketplaceBuy, user: dict = Depends(get_current_user)):
    item = next((i for i in MARKETPLACE_ITEMS if i["item_id"] == req.item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Article introuvable")
    if item["price_cc"] > user.get("balance_cc", 0):
        raise HTTPException(status_code=400, detail="Solde insuffisant")
    tx = await add_transaction(user["user_id"], f"Achat — {item['title']}", -item["price_cc"], "Marketplace")
    return {"ok": True, "transaction": tx}


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
    if req.amount > user.get("balance_cc", 0):
        raise HTTPException(status_code=400, detail="Solde utilisateur insuffisant")
    await add_transaction(user["user_id"], f"Paiement {ent['name']}" + (f" — {req.note}" if req.note else ""), -req.amount, ent["name"])
    await db.entities.update_one({"entity_id": ent["entity_id"]}, {"$inc": {"balance_cc": req.amount}})
    await log_entity_tx(ent["entity_id"], f"Encaissement {req.frek_id}", req.amount, req.frek_id)
    return {"ok": True, "frek_id": req.frek_id, "amount": req.amount}

# ---- owner view (logged-in) ----
@api_router.get("/entities")
async def list_entities(user: dict = Depends(get_current_user)):
    return await db.entities.find({}, {"_id": 0}).to_list(100)

@api_router.post("/entities/{entity_id}/rotate-key")
async def rotate_key(entity_id: str, user: dict = Depends(get_current_user)):
    new_key = f"cvln_live_{uuid.uuid4().hex}"
    res = await db.entities.update_one({"entity_id": entity_id}, {"$set": {"api_key": new_key}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Entité introuvable")
    return {"ok": True, "api_key": new_key}


@app.on_event("startup")
async def seed_entities():
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
