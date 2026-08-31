"""Post-run cleanup verification for iteration 8 (not a pytest file)."""
import os
from dotenv import dotenv_values
from pymongo import MongoClient

benv = dotenv_values("/app/backend/.env")
cli = MongoClient(os.environ.get("MONGO_URL") or benv["MONGO_URL"])
db = cli[os.environ.get("DB_NAME") or benv["DB_NAME"]]

checks = {
    "TEST users": db.users.count_documents({"user_id": {"$regex": "^TEST_"}}),
    "TEST sessions": db.user_sessions.count_documents({"user_id": {"$regex": "^TEST_"}}),
    "TEST txs": db.transactions.count_documents({"user_id": {"$regex": "^TEST_"}}),
    "TEST withdrawals": db.withdrawals.count_documents({"user_id": {"$regex": "^TEST_"}}),
    "TEST holds": db.balance_holds.count_documents({"user_id": {"$regex": "^TEST_"}}),
    "TEST ledger": db.ledger_entries.count_documents({"postings.account_id": {"$regex": "TEST_"}}),
    "TEST webhook_inbox": db.webhook_inbox.count_documents({"provider_event_id": {"$regex": "^TEST_"}}),
    "settlements": db.settlements.count_documents({}),
    "open recon cases": db.reconciliation_cases.count_documents({"status": {"$in": ["OPEN", "INVESTIGATING"]}}),
    "pending approvals": db.approval_requests.count_documents({"status": "PENDING"}),
    "PROCESSING idem": db.idempotency_records.count_documents({"state": "PROCESSING"}),
    "dead_letter outbox": db.outbox_events.count_documents({"status": "DEAD_LETTER"}),
    "recovery_journal wd_TEST": db.recovery_journal.count_documents({"ref": {"$regex": "^wd_TEST"}}),
}
for k, v in checks.items():
    print(f"{k}: {v}")
print("fee_policy:", (db.settings.find_one({"key": "app"}) or {}).get("fee_policy"))
cli.close()
