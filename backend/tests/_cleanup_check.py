import json
from pymongo import MongoClient
from dotenv import dotenv_values

env = dotenv_values("/app/backend/.env")
db = MongoClient(env["MONGO_URL"])[env["DB_NAME"]]

st = db.settings.find_one({"key": "app"}, {"_id": 0, "fee_policy": 1})
print("fee_policy:", st.get("fee_policy") if st else None)
print("TEST users:", db.users.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST sessions:", db.user_sessions.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST txs:", db.transactions.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST withdrawals:", db.withdrawals.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST holds:", db.balance_holds.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST refunds:", db.refunds.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST reversals:", db.reversals.count_documents({"user_id": {"$regex": "^TEST_"}}))
print("TEST ledger:", db.ledger_entries.count_documents({"postings.account_id": {"$regex": "TEST_"}}))
