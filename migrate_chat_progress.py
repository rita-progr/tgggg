#!/usr/bin/env python3
"""
Migration script to fix chat_progress table column types.
Changes user_id, chat_id, and last_message_id from INTEGER to BIGINT.
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect, BigInteger
from sqlalchemy.orm import sessionmaker

# Determine database URL
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/database.db")

def migrate():
    engine = create_engine(DATABASE_URL, echo=True)

    # Check if table exists
    inspector = inspect(engine)
    if 'chat_progress' not in inspector.get_table_names():
        print("✅ Table 'chat_progress' doesn't exist yet - will be created with correct schema")
        return

    print("🔍 Checking chat_progress table...")

    with engine.connect() as conn:
        # Detect database type
        db_type = engine.dialect.name
        print(f"📊 Database type: {db_type}")

        if db_type == 'sqlite':
            migrate_sqlite(conn)
        elif db_type == 'postgresql':
            migrate_postgresql(conn)
        else:
            print(f"⚠️  Unsupported database type: {db_type}")
            print("Please manually alter the table:")
            print("ALTER TABLE chat_progress ALTER COLUMN last_message_id TYPE BIGINT;")

def migrate_sqlite(conn):
    """
    SQLite doesn't support ALTER COLUMN TYPE.
    Need to recreate the table.
    """
    print("\n🔄 SQLite detected - recreating table...")

    # Check if table has data
    result = conn.execute(text("SELECT COUNT(*) FROM chat_progress")).scalar()
    has_data = result > 0

    if has_data:
        print(f"⚠️  Table has {result} rows - backing up data...")

        # Create backup table
        conn.execute(text("""
            CREATE TABLE chat_progress_backup AS
            SELECT * FROM chat_progress
        """))
        conn.commit()
        print("✅ Backup created: chat_progress_backup")

    # Drop old table
    conn.execute(text("DROP TABLE chat_progress"))
    conn.commit()
    print("✅ Dropped old table")

    # Create new table with correct types
    conn.execute(text("""
        CREATE TABLE chat_progress (
            user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            chat_type TEXT NOT NULL,
            last_message_id BIGINT NOT NULL,
            updated_at INTEGER,
            PRIMARY KEY (user_id, chat_id, chat_type)
        )
    """))
    conn.execute(text("CREATE INDEX ix_chat_progress_user_id ON chat_progress (user_id)"))
    conn.execute(text("CREATE INDEX ix_chat_progress_chat_id ON chat_progress (chat_id)"))
    conn.commit()
    print("✅ Created new table with BIGINT types")

    if has_data:
        # Restore data
        conn.execute(text("""
            INSERT INTO chat_progress
            SELECT * FROM chat_progress_backup
        """))
        conn.commit()
        print("✅ Restored data from backup")

        # Clean up backup
        conn.execute(text("DROP TABLE chat_progress_backup"))
        conn.commit()
        print("✅ Cleaned up backup table")

    print("\n✅ SQLite migration completed!")

def migrate_postgresql(conn):
    """
    PostgreSQL supports ALTER COLUMN TYPE.
    """
    print("\n🔄 PostgreSQL detected - altering column types...")

    try:
        # PostgreSQL requires separate ALTER COLUMN statements
        print("Changing user_id to BIGINT...")
        conn.execute(text("""
            ALTER TABLE chat_progress
            ALTER COLUMN user_id TYPE BIGINT
        """))
        conn.commit()
        print("✅ user_id changed to BIGINT")

        print("Changing chat_id to BIGINT...")
        conn.execute(text("""
            ALTER TABLE chat_progress
            ALTER COLUMN chat_id TYPE BIGINT
        """))
        conn.commit()
        print("✅ chat_id changed to BIGINT")

        print("Changing last_message_id to BIGINT...")
        conn.execute(text("""
            ALTER TABLE chat_progress
            ALTER COLUMN last_message_id TYPE BIGINT
        """))
        conn.commit()
        print("✅ last_message_id changed to BIGINT")

        print("\n✅ PostgreSQL migration completed!")
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        print("\nYou may need to run manually:")
        print("ALTER TABLE chat_progress ALTER COLUMN user_id TYPE BIGINT;")
        print("ALTER TABLE chat_progress ALTER COLUMN chat_id TYPE BIGINT;")
        print("ALTER TABLE chat_progress ALTER COLUMN last_message_id TYPE BIGINT;")
        raise

if __name__ == "__main__":
    print("=" * 60)
    print("🔧 Chat Progress Table Migration")
    print("=" * 60)
    print(f"\nDatabase: {DATABASE_URL}")
    print("\nThis will change the following columns from INTEGER to BIGINT:")
    print("  - user_id")
    print("  - chat_id")
    print("  - last_message_id")

    response = input("\nContinue? (yes/no): ")
    if response.lower() != 'yes':
        print("❌ Migration cancelled")
        sys.exit(0)

    try:
        migrate()
        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
        print("=" * 60)
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ Migration failed: {e}")
        print("=" * 60)
        sys.exit(1)
