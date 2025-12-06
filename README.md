# Telegram Chat Export Bot

A secure Telegram bot that allows users to export their chat history to text files using WebApp authentication.

## Features

- **Secure Authentication**: All authentication (phone, code, 2FA) happens through Telegram WebApp, never in the bot chat
- **Session Management**: Encrypted session storage using Fernet encryption
- **Chat Export**: Export messages from any chat/channel/group to text files
- **Incremental Export**: Automatically exports only new messages on subsequent exports (see [INCREMENTAL_EXPORT.md](INCREMENTAL_EXPORT.md))
- **Signature Verification**: WebApp initData is cryptographically verified using bot token
- **Railway Ready**: Configured for easy deployment on Railway

## Architecture

The project consists of two main components:

1. **Backend (FastAPI)**: Handles WebApp authentication and stores encrypted sessions
2. **Bot (python-telegram-bot)**: Uses stored sessions to list chats and export messages

Both share the same SQLite database with encrypted session strings.

## Project Structure

```
.
├── backend/
│   ├── main.py              # FastAPI application
│   ├── auth_utils.py        # Telegram WebApp signature verification
│   ├── db.py                # Database layer (users, pending_logins, chat_progress)
│   ├── telethon_utils.py    # Telethon client helpers
│   ├── crypto_utils.py      # Fernet encryption/decryption
│   ├── static/
│   │   └── webapp.html      # WebApp frontend
│   └── requirements.txt
├── bot/
│   ├── bot.py               # Telegram bot implementation
│   ├── db.py                # Database layer (read + write chat_progress)
│   ├── crypto_utils.py      # Fernet encryption/decryption
│   └── requirements.txt
├── data/                    # SQLite database (auto-created)
└── .env                     # Environment variables (create from .env.example)
```

## Setup

### Prerequisites

- Python 3.10+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Telegram API credentials (from [https://my.telegram.org](https://my.telegram.org))

### 1. Get Telegram Credentials

#### Bot Token:
1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` and follow instructions
3. Copy the bot token

#### API ID and Hash:
1. Go to [https://my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Go to "API development tools"
4. Create an application
5. Copy `api_id` and `api_hash`

### 2. Generate Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output - this is your `ENCRYPTION_KEY`.

### 3. Configure Environment Variables

Create a `.env` file in the root directory:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
BOT_TOKEN=your_bot_token_from_botfather
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
ENCRYPTION_KEY=your_base64_fernet_key
DATABASE_URL=sqlite:///./data/database.db
WEBAPP_URL=http://localhost:8000/webapp
```

### 4. Install Dependencies

#### Backend:
```bash
cd backend
pip install -r requirements.txt
```

#### Bot:
```bash
cd bot
pip install -r requirements.txt
```

### 5. Run Locally

#### Terminal 1 - Backend:
```bash
cd backend
export $(cat ../.env | xargs)  # Load env vars (Linux/Mac)
# or: set -a; source ../.env; set +a
uvicorn main:app --reload --port 8000
```

#### Terminal 2 - Bot:
```bash
cd bot
export $(cat ../.env | xargs)  # Load env vars (Linux/Mac)
# or: set -a; source ../.env; set +a
python bot.py
```

### 6. Test the Bot

1. Open your bot in Telegram
2. Send `/start`
3. Send `/login` and click the WebApp button
4. Complete authentication in the web page
5. Use `/list` to see your chats
6. Use `/export` to export a chat

## Deployment on Railway

Railway allows you to deploy multiple services from a single repository.

### Step 1: Prepare Repository

Push your code to GitHub:

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/yourusername/tgBot.git
git push -u origin main
```

### Step 2: Create Railway Project

1. Go to [railway.app](https://railway.app)
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your repository
4. Railway will detect your project

### Step 3: Create Backend Service

1. In Railway project, click "New Service" → "GitHub Repo"
2. Name it `backend`
3. Configure:
   - **Root Directory**: Leave empty (or `/`)
   - **Start Command**: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Environment Variables**: Add all from `.env` (see below)

### Step 4: Create Bot Service

1. Click "New Service" → "GitHub Repo" again
2. Name it `bot`
3. Configure:
   - **Root Directory**: Leave empty (or `/`)
   - **Start Command**: `cd bot && python bot.py`
   - **Environment Variables**: Add all from `.env` (same as backend)

### Step 5: Configure Environment Variables

For **both services**, add these environment variables:

```
BOT_TOKEN=your_bot_token
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
ENCRYPTION_KEY=your_fernet_key
DATABASE_URL=sqlite:///./data/database.db
WEBAPP_URL=https://your-backend.up.railway.app/webapp
```

**Important:**
- `ENCRYPTION_KEY` must be the **same** for both services
- `WEBAPP_URL` should point to your backend's Railway URL (get it from backend service settings → "Networking" → "Public URL")

### Step 6: Enable Public Networking (Backend Only)

1. Go to backend service settings
2. Click "Networking" → "Generate Domain"
3. Copy the generated URL (e.g., `https://backend-production-xxxx.up.railway.app`)
4. Update `WEBAPP_URL` in **both services** to `https://your-domain.up.railway.app/webapp`

### Step 7: Shared Database

Since both services use the same `DATABASE_URL`, they'll share data. Railway's persistent volume is not required for SQLite, but for production, consider PostgreSQL:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

### Step 8: Deploy

1. Both services should auto-deploy after configuration
2. Check logs to ensure no errors
3. Test the bot on Telegram

### Troubleshooting Railway

**Services not starting?**
- Check logs in Railway dashboard
- Ensure all environment variables are set
- Verify start commands are correct

**Database not shared?**
- Ensure both services have identical `DATABASE_URL`
- Use Railway's PostgreSQL addon for production

**WebApp not loading?**
- Ensure backend has public domain enabled
- Update `WEBAPP_URL` in bot service
- Check CORS settings if needed

## Security Considerations

✅ **What's secure:**
- All sessions are encrypted with Fernet (AES-128)
- WebApp initData is cryptographically verified
- Passwords and codes are never logged
- Authentication happens only in WebApp, not in bot chat
- Bot never asks for sensitive data

⚠️ **Important:**
- Keep `ENCRYPTION_KEY` secret and consistent across services
- Use strong bot token (never share it)
- For production, consider using PostgreSQL instead of SQLite
- Add rate limiting for API endpoints
- Consider adding user allowlist/blocklist

## Bot Commands

- `/start` - Start the bot and see welcome message
- `/help` - Show help message with all commands
- `/login` - Open WebApp to authenticate
- `/status` - Check authentication status
- `/list` - List your chats/channels/groups
- `/export` - Export chat messages to text file
- `/logout` - Delete your session data (requires confirmation)
- `/cancel` - Cancel current operation

## How Authentication Works

1. User clicks `/login` in bot
2. Bot shows button that opens WebApp (your backend)
3. WebApp receives `initData` from Telegram (includes user_id, signature)
4. User enters phone → Backend verifies `initData`, sends code via Telethon
5. User enters code → Backend signs in, saves encrypted session to database
6. If 2FA enabled → User enters password → Backend completes sign-in
7. Bot can now use stored session to access user's Telegram account

## Database Schema

### `users` table:
```sql
user_id INTEGER PRIMARY KEY          -- Telegram user ID
session_string TEXT NOT NULL         -- Encrypted Telethon session
is_authenticated BOOLEAN DEFAULT 0   -- Auth status
last_activity INTEGER                -- Unix timestamp
```

### `pending_logins` table:
```sql
user_id INTEGER PRIMARY KEY          -- Telegram user ID
phone TEXT NOT NULL                  -- Phone number
phone_code_hash TEXT NOT NULL        -- From Telegram's send_code_request
temp_session_string TEXT NOT NULL    -- Encrypted temporary session
created_at INTEGER                   -- Unix timestamp
```

### `chat_progress` table:
```sql
user_id INTEGER                      -- Telegram user ID
chat_id INTEGER                      -- Chat/channel/user entity ID
chat_type TEXT                       -- 'user', 'chat', or 'channel'
last_message_id INTEGER              -- ID of last exported message
updated_at INTEGER                   -- Unix timestamp
PRIMARY KEY (user_id, chat_id, chat_type)
```

**Note:** See [INCREMENTAL_EXPORT.md](INCREMENTAL_EXPORT.md) for details on incremental export feature.

## Troubleshooting

**Bot doesn't respond:**
- Check bot is running (`python bot.py`)
- Verify `BOT_TOKEN` is correct
- Check bot logs for errors

**WebApp authentication fails:**
- Ensure `WEBAPP_URL` is correct
- Check backend logs for signature verification errors
- Verify `BOT_TOKEN` matches in both backend and bot

**"Session expired" error:**
- Session may have been invalidated by Telegram
- Use `/logout` then `/login` to re-authenticate

**Export fails:**
- Check if you have permission to read the chat
- Some chats may have message history disabled
- Try exporting fewer messages

## Development

### Running Tests

```bash
# Add your tests here
pytest
```

### Code Style

```bash
# Format code
black backend/ bot/

# Lint
flake8 backend/ bot/
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test locally
5. Submit a pull request

## License

MIT License - feel free to use for personal or commercial projects

## Support

If you encounter issues:
1. Check logs (both backend and bot)
2. Verify environment variables
3. Ensure Telegram API credentials are correct
4. Check Railway dashboard for deployment issues

## Credits

Built with:
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Telethon](https://github.com/LonamiWebs/Telethon)
- [FastAPI](https://fastapi.tiangolo.com/)
- [cryptography](https://cryptography.io/)
