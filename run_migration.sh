#!/bin/bash
# Script to run migration on Railway

echo "🚀 Running database migration on Railway..."
echo ""

# Set the DATABASE_URL
export DATABASE_URL="postgresql://postgres:MZBdeCaLJSrqmYzyBKCWwkQszvYJQASR@postgres.railway.internal:5432/railway"

# Run the migration script
python3 migrate_chat_progress.py
