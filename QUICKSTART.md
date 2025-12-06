# Quick Start Guide

Get your Telegram Chat Export Bot running in 5 minutes!

## Prerequisites Checklist

- [ ] Python 3.10+ installed
- [ ] Telegram account
- [ ] Bot token from [@BotFather](https://t.me/botfather)
- [ ] Telegram API credentials from [my.telegram.org](https://my.telegram.org)

## Step-by-Step Setup

### 1. Get Telegram Credentials (5 minutes)

#### A. Bot Token
```
1. Open Telegram and search for @BotFather
2. Send: /newbot
3. Follow instructions (choose name and username)
4. Copy the bot token (looks like: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz)
```

#### B. API ID and Hash
```
1. Visit https://my.telegram.org
2. Log in with your phone number
3. Click "API development tools"
4. Fill in the form:
   - App title: My Chat Export Bot
   - Short name: chatbot
   - Platform: Other
5. Copy api_id (number) and api_hash (string)
```

### 2. Generate Encryption Key (30 seconds)

```bash
cd /path/to/tgBot
python3 generate_key.py
```

Copy the output.

### 3. Configure Environment (1 minute)

```bash
cp .env.example .env
nano .env  # or use any text editor
```

Fill in your values:
```env
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
ENCRYPTION_KEY=your_generated_key_here
DATABASE_URL=sqlite:///./data/database.db
WEBAPP_URL=http://localhost:8000/webapp
```

Save and exit.

### 4. Install Dependencies (2 minutes)

#### Backend:
```bash
cd backend
pip3 install -r requirements.txt
cd ..
```

#### Bot:
```bash
cd bot
pip3 install -r requirements.txt
cd ..
```

### 5. Run the Project (30 seconds)

Open **TWO** terminal windows:

#### Terminal 1 - Backend:
```bash
cd backend
export $(cat ../.env | xargs)
uvicorn main:app --reload --port 8000
```

You should see: `Uvicorn running on http://0.0.0.0:8000`

#### Terminal 2 - Bot:
```bash
cd bot
export $(cat ../.env | xargs)
python3 bot.py
```

You should see: `Bot started successfully`

### 6. Test the Bot (1 minute)

1. Open Telegram
2. Search for your bot (the username you chose with BotFather)
3. Send: `/start`
4. You should get a welcome message!

### 7. Authenticate (1 minute)

1. In bot, send: `/login`
2. Click the "🔐 Login via WebApp" button
3. Enter your phone number (with country code, e.g., +1234567890)
4. Enter the code from Telegram
5. If you have 2FA, enter your password
6. Done! WebApp will close automatically

### 8. Export Your First Chat (1 minute)

1. Send: `/list` to see your chats
2. Send: `/export`
3. Reply with the chat number (e.g., `1`)
4. Reply with message limit (e.g., `100`) or just press Enter for default
5. Wait a few seconds
6. Download the exported file!

## Troubleshooting

### "Bot doesn't respond"
- Check Terminal 2 (bot) is running
- Check BOT_TOKEN is correct
- Make sure you started the bot in Telegram with /start

### "WebApp button doesn't work"
- Check Terminal 1 (backend) is running
- Check WEBAPP_URL is `http://localhost:8000/webapp`
- Try clicking the button again

### "Session expired" after /list
- Send /logout
- Send /login again
- Complete authentication

### "Module not found" error
- Make sure you're in the correct directory (backend/ or bot/)
- Run `pip3 install -r requirements.txt` again
- Check you activated the virtual environment if using one

### "Permission denied" on generate_key.py
```bash
chmod +x generate_key.py
python3 generate_key.py
```

## Windows Users

Replace these commands:

**Export environment variables:**
```cmd
# Instead of: export $(cat ../.env | xargs)
# Use (in CMD):
for /F "tokens=*" %i in (.env) do set %i

# Or (in PowerShell):
Get-Content .env | ForEach-Object {
    $name, $value = $_.split('=')
    Set-Item -Path "env:$name" -Value $value
}
```

**Or use python-dotenv:**

Add to top of `backend/main.py` and `bot/bot.py`:
```python
from dotenv import load_dotenv
load_dotenv()
```

Then install:
```bash
pip install python-dotenv
```

## Next Steps

- Read [README.md](README.md) for detailed documentation
- Read [ARCHITECTURE.md](ARCHITECTURE.md) to understand the system
- Deploy to Railway (see README.md section "Deployment on Railway")

## Common Commands Reference

```bash
# Start backend
cd backend && uvicorn main:app --reload --port 8000

# Start bot
cd bot && python3 bot.py

# Generate new encryption key
python3 generate_key.py

# Check bot logs
# (visible in Terminal 2)

# Check backend logs
# (visible in Terminal 1)

# Reset database (delete all data)
rm -rf data/

# Update dependencies
cd backend && pip3 install -r requirements.txt --upgrade
cd bot && pip3 install -r requirements.txt --upgrade
```

## Production Checklist

Before deploying to production:

- [ ] Use PostgreSQL instead of SQLite
- [ ] Set strong ENCRYPTION_KEY (never reuse development key)
- [ ] Enable HTTPS for backend
- [ ] Add rate limiting
- [ ] Set up monitoring/logging
- [ ] Configure firewall rules
- [ ] Enable automatic backups
- [ ] Review security settings

See README.md for Railway deployment guide.

## Support

Issues? Questions?

1. Check logs in both terminals
2. Read [README.md](README.md) troubleshooting section
3. Check [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
4. Verify all environment variables are set correctly

## Success!

If you completed all steps:
- ✅ Bot responds to commands
- ✅ WebApp authentication works
- ✅ You can list your chats
- ✅ You can export chat history

Congratulations! Your Telegram Chat Export Bot is running!
