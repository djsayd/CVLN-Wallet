# Auth-Gated App Testing Playbook (Emergent Google Auth)

## Step 1: Create Test User & Session (mongosh)
```
mongosh --eval "
use('test_database');
var userId = 'user_test' + Date.now();
var sessionToken = 'test_session_' + Date.now();
db.users.insertOne({
  user_id: userId,
  email: 'test.user.' + Date.now() + '@example.com',
  name: 'Test User',
  picture: 'https://via.placeholder.com/150',
  frek_id: 'FREK-TST-' + Date.now(),
  frek_score: 900,
  balance_cc: 100000,
  created_at: new Date()
});
db.user_sessions.insertOne({
  user_id: userId,
  session_token: sessionToken,
  expires_at: new Date(Date.now() + 7*24*60*60*1000),
  created_at: new Date()
});
print('Session token: ' + sessionToken);
print('User ID: ' + userId);
"
```

## Step 2: Test Backend API
```
curl -X GET "$URL/api/auth/me" -H "Authorization: Bearer YOUR_SESSION_TOKEN"
curl -X GET "$URL/api/wallet" -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## Step 3: Browser Testing
Set cookie `session_token` (domain of app, path=/, httpOnly, secure, sameSite None), navigate to app.

## Notes
- Users authenticate via Google (auth.emergentagent.com). No app-managed passwords.
- Session stored in `user_sessions` collection; cookie `session_token`, 7 day expiry.
