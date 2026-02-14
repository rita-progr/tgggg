"""
Migration: Add api_id and api_hash columns to users table
Purpose: Allow each user to use their own Telegram API credentials
Date: 2026-02-14
"""
import os
import sys

# Add parent directory to path to import db module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/database.db")

def migrate():
    """Add api_id and api_hash columns to users table."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        print("Adding api_id column...")
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN api_id TEXT"))
            conn.commit()
            print("✓ api_id column added")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print("  api_id column already exists, skipping")
            else:
                raise

        print("Adding api_hash column...")
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN api_hash TEXT"))
            conn.commit()
            print("✓ api_hash column added")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print("  api_hash column already exists, skipping")
            else:
                raise

    print("\n✅ Migration completed successfully!")
    print("\nNote: Existing users will use default TG_API_ID/TG_API_HASH from environment.")
    print("New users should provide their own API credentials during login.")

if __name__ == "__main__":
    migrate()
