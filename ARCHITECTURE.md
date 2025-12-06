# Architecture Overview

## System Design

This project implements a secure Telegram bot with WebApp authentication for exporting chat history.

### Key Components

```
┌─────────────────┐
│  Telegram User  │
└────────┬────────┘
         │
         ├─────────────────┐
         │                 │
         ▼                 ▼
┌────────────────┐  ┌─────────────────┐
│  Telegram Bot  │  │   WebApp (UI)   │
│ (python-tg-bot)│  │    (HTML/JS)    │
└────────┬───────┘  └────────┬────────┘
         │                   │
         │                   ▼
         │          ┌─────────────────┐
         │          │  FastAPI Backend│
         │          │  (Auth Handler) │
         │          └────────┬────────┘
         │                   │
         │                   │
         ▼                   ▼
    ┌────────────────────────────┐
    │   SQLite Database          │
    │  (Encrypted Sessions)      │
    └────────────────────────────┘
              │
              ▼
         ┌─────────┐
         │ Telethon│
         │ Client  │
         └─────────┘
              │
              ▼
    ┌──────────────────┐
    │ Telegram API     │
    │ (User Account)   │
    └──────────────────┘
```

## Authentication Flow

### Step 1: Login Initiation
```
User → Bot: /login
Bot → User: WebApp button
User → WebApp: Opens in Telegram
```

### Step 2: Phone Verification
```
WebApp → Backend: POST /auth/send_code {phone, initData}
Backend → Telegram API: send_code_request(phone)
Backend → DB: Save pending_login with temp session
Backend → WebApp: {ok: true}
WebApp → User: "Code sent, check Telegram"
```

### Step 3: Code Confirmation
```
WebApp → Backend: POST /auth/confirm_code {code, initData}
Backend → DB: Retrieve pending_login
Backend → Telegram API: sign_in(code)

Case A - No 2FA:
  Backend → DB: Save encrypted session_string to users
  Backend → DB: Delete pending_login
  Backend → WebApp: {ok: true, need_password: false}
  WebApp: Close

Case B - 2FA Required:
  Backend → DB: Update pending_login with new temp session
  Backend → WebApp: {ok: true, need_password: true}
  WebApp: Show password form
```

### Step 4: 2FA (if needed)
```
WebApp → Backend: POST /auth/confirm_password {password, initData}
Backend → DB: Retrieve pending_login
Backend → Telegram API: sign_in(password)
Backend → DB: Save encrypted session_string to users
Backend → DB: Delete pending_login
Backend → WebApp: {ok: true}
WebApp: Close
```

## Security Measures

### 1. WebApp Signature Verification

Every request from WebApp includes `initData` which contains:
- `user`: JSON with user info (id, first_name, etc.)
- `auth_date`: Unix timestamp
- `hash`: HMAC-SHA256 signature

Backend verifies:
```python
secret_key = HMAC-SHA256("WebAppData", bot_token)
calculated_hash = HMAC-SHA256(secret_key, data_check_string)
assert calculated_hash == received_hash
```

This ensures the request truly comes from Telegram and hasn't been tampered with.

### 2. Session Encryption

All Telethon session strings are encrypted before storage:

```python
# Encryption
from cryptography.fernet import Fernet
cipher = Fernet(ENCRYPTION_KEY)
encrypted = cipher.encrypt(session_string.encode()).decode()

# Storage
users.session_string = encrypted

# Decryption (when bot needs to use it)
decrypted = cipher.decrypt(encrypted.encode()).decode()
```

### 3. No Sensitive Data in Bot Chat

The bot **never** asks for:
- Phone number
- SMS code
- 2FA password

All of these are entered in the WebApp (HTTPS encrypted).

### 4. Separated Concerns

- **Backend**: Handles authentication, writes to DB
- **Bot**: Reads from DB, uses sessions (no auth logic)
- **Database**: Single source of truth for encrypted sessions

## Database Schema

### `users` Table
```sql
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,         -- Telegram user ID
    session_string TEXT NOT NULL,        -- Encrypted Telethon session
    is_authenticated BOOLEAN DEFAULT 0,  -- Auth status flag
    last_activity INTEGER                -- Unix timestamp
);
```

Purpose: Long-term storage of authenticated sessions

### `pending_logins` Table
```sql
CREATE TABLE pending_logins (
    user_id INTEGER PRIMARY KEY,         -- Telegram user ID
    phone TEXT NOT NULL,                 -- Phone number
    phone_code_hash TEXT NOT NULL,       -- From send_code_request
    temp_session_string TEXT NOT NULL,   -- Encrypted temp session
    created_at INTEGER                   -- Unix timestamp
);
```

Purpose: Temporary storage during multi-step auth flow

## API Endpoints

### Backend (FastAPI)

#### `GET /webapp`
- Serves the WebApp HTML page
- No authentication required (authenticated by Telegram)

#### `POST /auth/send_code`
Request:
```json
{
  "phone": "+1234567890",
  "initData": "user=%7B%22id%22%3A..."
}
```

Response:
```json
{
  "ok": true
}
```

Errors:
- 400: Invalid phone number
- 403: Invalid initData signature
- 429: Flood wait (rate limit)
- 500: Internal error

#### `POST /auth/confirm_code`
Request:
```json
{
  "code": "12345",
  "initData": "user=%7B%22id%22%3A..."
}
```

Response (no 2FA):
```json
{
  "ok": true,
  "need_password": false
}
```

Response (2FA required):
```json
{
  "ok": true,
  "need_password": true
}
```

Errors:
- 400: Invalid code or no pending login
- 403: Invalid initData signature
- 429: Flood wait
- 500: Internal error

#### `POST /auth/confirm_password`
Request:
```json
{
  "password": "my2fapassword",
  "initData": "user=%7B%22id%22%3A..."
}
```

Response:
```json
{
  "ok": true
}
```

Errors:
- 400: Invalid password or no pending login
- 403: Invalid initData signature
- 429: Flood wait
- 500: Internal error

## Bot Commands

### `/start`
- Shows welcome message
- No authentication required

### `/help`
- Shows all commands and usage instructions
- No authentication required

### `/login`
- Opens WebApp button
- No authentication required

### `/status`
- Shows authentication status
- No authentication required

### `/list`
- **Requires authentication**
- Fetches user's dialogs (chats, groups, channels)
- Uses stored Telethon session

### `/export`
- **Requires authentication**
- Starts conversation to select chat and export messages
- Uses ConversationHandler with states:
  - `SELECTING_CHAT`: User enters chat number
  - `ENTERING_LIMIT`: User enters message limit
- Exports messages to `.txt` file

### `/logout`
- Shows confirmation buttons
- Deletes user data from database

## Error Handling

### Backend
- All Telethon errors are caught and logged
- User-friendly error messages returned to WebApp
- No sensitive data (codes, passwords) in logs

### Bot
- Session expired → prompt to re-login
- Flood wait → inform user of wait time
- Export errors → clear error message

## Deployment Architecture (Railway)

```
┌─────────────────────────────────────────┐
│           Railway Project               │
│                                         │
│  ┌──────────────┐   ┌──────────────┐  │
│  │   Backend    │   │     Bot      │  │
│  │   Service    │   │   Service    │  │
│  │              │   │              │  │
│  │  Port: $PORT │   │   (no port)  │  │
│  │  Public URL  │   │              │  │
│  └──────┬───────┘   └──────┬───────┘  │
│         │                  │           │
│         └──────┬───────────┘           │
│                │                       │
│         ┌──────▼──────┐                │
│         │   SQLite    │                │
│         │  (Volume)   │                │
│         └─────────────┘                │
└─────────────────────────────────────────┘
```

Both services:
- Share same `DATABASE_URL`
- Use same `ENCRYPTION_KEY`
- Auto-deploy on git push

## Code Organization

### Backend Modules

- `main.py`: FastAPI app, endpoints
- `auth_utils.py`: Telegram signature verification
- `db.py`: Database operations (write)
- `telethon_utils.py`: Telethon client creation
- `crypto_utils.py`: Encryption/decryption
- `static/webapp.html`: WebApp UI

### Bot Modules

- `bot.py`: Main bot logic, command handlers
- `db.py`: Database operations (read)
- `crypto_utils.py`: Encryption/decryption (same as backend)

### Shared Code

`crypto_utils.py` is duplicated in both packages to avoid cross-imports and maintain independence of services.

## Future Improvements

1. **PostgreSQL**: Replace SQLite for production
2. **Rate Limiting**: Add request rate limits per user
3. **Session Expiry**: Auto-delete old sessions
4. **Message Formats**: Export to JSON, CSV, HTML
5. **Filters**: Export only messages from specific users/dates
6. **Search**: Search messages before export
7. **Media**: Include media files in export
8. **Encryption**: Encrypt database file itself
9. **Admin Panel**: Web UI to monitor users/sessions
10. **Metrics**: Prometheus metrics for monitoring
