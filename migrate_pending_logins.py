#!/usr/bin/env python3
"""
Migration script to fix pending_logins table column type.
Changes user_id from INTEGER to BIGINT.
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect

# Determine database URL
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/database.db")

def migrate():
    engine = create_engine(DATABASE_URL, echo=True)

    # Check if table exists
    inspector = inspect(engine)
    if 'pending_logins' not in inspector.get_table_names():
        print("✅ Table 'pending_logins' doesn't exist yet - will be created with correct schema")
        return

    print("🔍 Checking pending_logins table...")

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
            print("ALTER TABLE pending_logins ALTER COLUMN user_id TYPE BIGINT;")

def migrate_sqlite(conn):
    """
    SQLite doesn't support ALTER COLUMN TYPE.
    Need to recreate the table.
    """
    print("\n🔄 SQLite detected - recreating table...")

    # Check if table has data
    result = conn.execute(text("SELECT COUNT(*) FROM pending_logins")).scalar()
    has_data = result > 0

    if has_data:
        print(f"⚠️  Table has {result} rows - backing up data...")

        # Create backup table
        conn.execute(text("""
            CREATE TABLE pending_logins_backup AS
            SELECT * FROM pending_logins
        """))
        conn.commit()
        print("✅ Backup created: pending_logins_backup")

    # Drop old table
    conn.execute(text("DROP TABLE pending_logins"))
    conn.commit()
    print("✅ Dropped old table")

    # Create new table with correct types
    conn.execute(text("""
        CREATE TABLE pending_logins (
            user_id BIGINT NOT NULL PRIMARY KEY,
            phone TEXT NOT NULL,
            phone_code_hash TEXT NOT NULL,
            temp_session_string TEXT NOT NULL,
            created_at INTEGER
        )
    """))
    conn.execute(text("CREATE INDEX ix_pending_logins_user_id ON pending_logins (user_id)"))
    conn.commit()
    print("✅ Created new table with BIGINT type")

    if has_data:
        # Restore data
        conn.execute(text("""
            INSERT INTO pending_logins
            SELECT * FROM pending_logins_backup
        """))
        conn.commit()
        print("✅ Restored data from backup")

        # Clean up backup
        conn.execute(text("DROP TABLE pending_logins_backup"))
        conn.commit()
        print("✅ Cleaned up backup table")

    print("\n✅ SQLite migration completed!")

def migrate_postgresql(conn):
    """
    PostgreSQL supports ALTER COLUMN TYPE.
    """
    print("\n🔄 PostgreSQL detected - altering column type...")

    try:
        print("Changing user_id to BIGINT...")
        conn.execute(text("""
            ALTER TABLE pending_logins
            ALTER COLUMN user_id TYPE BIGINT
        """))
        conn.commit()
        print("✅ user_id changed to BIGINT")

        print("\n✅ PostgreSQL migration completed!")
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        print("\nYou may need to run manually:")
        print("ALTER TABLE pending_logins ALTER COLUMN user_id TYPE BIGINT;")
        raise

if __name__ == "__main__":
    print("=" * 60)
    print("🔧 Pending Logins Table Migration")
    print("=" * 60)
    print(f"\nDatabase: {DATABASE_URL}")
    print("\nThis will change user_id from INTEGER to BIGINT")

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
