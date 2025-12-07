"""
FastAPI backend for Telegram WebApp authentication.
Handles phone code sending, code confirmation, and 2FA password.
"""
import os
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telethon.errors import (
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
    FloodWaitError,
)

from backend.auth_utils import check_telegram_auth
from backend.telethon_utils import create_client_from_string
from backend import db
from backend.crypto_utils import decrypt


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Telegram Auth Backend")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (if exists)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

BOT_TOKEN = os.environ["BOT_TOKEN"]


# Request/Response models
class SendCodeRequest(BaseModel):
    phone: str
    initData: str


class ConfirmCodeRequest(BaseModel):
    code: str
    initData: str


class ConfirmPasswordRequest(BaseModel):
    password: str
    initData: str


@app.get("/webapp", response_class=HTMLResponse)
async def serve_webapp():
    """
    Serve the WebApp HTML page.
    """
    webapp_path = Path(__file__).parent / "static" / "webapp.html"

    if not webapp_path.exists():
        raise HTTPException(status_code=404, detail="WebApp not found")

    return HTMLResponse(content=webapp_path.read_text(), status_code=200)


@app.post("/auth/send_code")
async def send_code(request: SendCodeRequest):
    """
    Step 1: Send authentication code to user's phone.

    1. Verify initData signature
    2. Create new Telethon client
    3. Send code request
    4. Save pending login info to database
    """
    try:
        # Verify Telegram WebApp signature
        user_id = check_telegram_auth(request.initData, BOT_TOKEN)
        logger.info(f"Send code request from user_id={user_id}")

        # Create Telethon client with empty session
        client = create_client_from_string(None)
        await client.connect()

        # Send code request
        sent = await client.send_code_request(request.phone)

        # Save session and code hash for next step
        session_string = client.session.save()
        db.create_or_update_pending_login(
            user_id=user_id,
            phone=request.phone,
            phone_code_hash=sent.phone_code_hash,
            temp_session_string=session_string
        )

        await client.disconnect()

        logger.info(f"Code sent successfully to user_id={user_id}")
        return {"ok": True}

    except PhoneNumberInvalidError:
        logger.warning(f"Invalid phone number: {request.phone}")
        raise HTTPException(status_code=400, detail="Invalid phone number")

    except FloodWaitError as e:
        logger.warning(f"Flood wait error: {e.seconds} seconds")
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {e.seconds} seconds"
        )

    except ValueError as e:
        logger.error(f"Auth validation error: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        logger.error(f"Error sending code: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to send code: {str(e)}")


@app.post("/auth/confirm_code")
async def confirm_code(request: ConfirmCodeRequest):
    """
    Step 2: Confirm authentication code.

    1. Verify initData signature
    2. Retrieve pending login from database
    3. Sign in with code
    4. If successful -> save session, mark authenticated
    5. If 2FA required -> update pending login, ask for password
    """
    try:
        # Verify Telegram WebApp signature
        user_id = check_telegram_auth(request.initData, BOT_TOKEN)
        logger.info(f"Confirm code request from user_id={user_id}")

        # Get pending login
        pending = db.get_pending_login(user_id)
        if not pending:
            raise HTTPException(status_code=400, detail="No pending login found. Please start from /auth/send_code")

        # Decrypt temp session
        temp_session = decrypt(pending.temp_session_string)

        # Create client from temp session
        client = create_client_from_string(temp_session)
        await client.connect()

        try:
            # Try to sign in with code
            await client.sign_in(
                phone=pending.phone,
                code=request.code.replace(" ", "").replace("-", ""),
                phone_code_hash=pending.phone_code_hash
            )

            # Success! Save session and mark as authenticated
            session_string = client.session.save()
            db.save_session_string(user_id, session_string)
            db.set_authenticated(user_id, True)
            db.delete_pending_login(user_id)

            await client.disconnect()

            logger.info(f"User {user_id} authenticated successfully (no 2FA)")
            return {"ok": True, "need_password": False}

        except SessionPasswordNeededError:
            # 2FA is enabled, need password
            logger.info(f"User {user_id} requires 2FA password")

            # Update temp session (it's now in "password needed" state)
            session_string = client.session.save()
            db.create_or_update_pending_login(
                user_id=user_id,
                phone=pending.phone,
                phone_code_hash=pending.phone_code_hash,
                temp_session_string=session_string
            )

            await client.disconnect()

            return {"ok": True, "need_password": True}

    except PhoneCodeInvalidError:
        logger.warning(f"Invalid code from user_id={user_id}")
        raise HTTPException(status_code=400, detail="Invalid code")

    except FloodWaitError as e:
        logger.warning(f"Flood wait error: {e.seconds} seconds")
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {e.seconds} seconds"
        )

    except ValueError as e:
        logger.error(f"Auth validation error: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        logger.error(f"Error confirming code: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to confirm code: {str(e)}")


@app.post("/auth/confirm_password")
async def confirm_password(request: ConfirmPasswordRequest):
    """
    Step 3: Confirm 2FA password (if required).

    1. Verify initData signature
    2. Retrieve pending login from database
    3. Sign in with password
    4. Save session and mark authenticated
    """
    try:
        # Verify Telegram WebApp signature
        user_id = check_telegram_auth(request.initData, BOT_TOKEN)
        logger.info(f"Confirm password request from user_id={user_id}")

        # Get pending login
        pending = db.get_pending_login(user_id)
        if not pending:
            raise HTTPException(status_code=400, detail="No pending login found")

        # Decrypt temp session
        temp_session = decrypt(pending.temp_session_string)

        # Create client from temp session
        client = create_client_from_string(temp_session)
        await client.connect()

        # Sign in with password
        await client.sign_in(password=request.password)

        # Success! Save session and mark as authenticated
        session_string = client.session.save()
        db.save_session_string(user_id, session_string)
        db.set_authenticated(user_id, True)
        db.delete_pending_login(user_id)

        await client.disconnect()

        logger.info(f"User {user_id} authenticated successfully (with 2FA)")
        return {"ok": True}

    except PasswordHashInvalidError:
        logger.warning(f"Invalid password from user_id={user_id}")
        raise HTTPException(status_code=400, detail="Invalid password")

    except FloodWaitError as e:
        logger.warning(f"Flood wait error: {e.seconds} seconds")
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {e.seconds} seconds"
        )

    except ValueError as e:
        logger.error(f"Auth validation error: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))

    except Exception as e:
        logger.error(f"Error confirming password: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to confirm password: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
