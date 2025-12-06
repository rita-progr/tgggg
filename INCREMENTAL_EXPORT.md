# Incremental Chat Export Feature

## Overview

The bot now supports **incremental chat exports** - it remembers where you left off and exports only new messages on subsequent exports of the same chat.

## How It Works

### First Export (Full Export)
1. User runs `/export` and selects a chat
2. Bot exports up to the specified limit (default: 1000 messages)
3. Bot saves the ID of the most recent message exported
4. User receives: `✅ Full export of ChatName - 1000 messages`

### Subsequent Exports (Incremental)
1. User runs `/export` and selects the same chat again
2. Bot checks if this chat was exported before
3. Bot exports **only messages newer** than the last export
4. Bot updates the saved message ID
5. User receives: `✅ Exported 42 new messages from ChatName (since last export)`

### No New Messages
If there are no new messages since the last export:
- User receives: `⚠️ No new messages in ChatName since last export.`
- No file is created

## Database Schema

### New Table: `chat_progress`

```sql
CREATE TABLE chat_progress (
    user_id INTEGER,              -- Telegram user ID
    chat_id INTEGER,              -- Dialog entity ID
    chat_type TEXT,               -- 'user', 'chat', or 'channel'
    last_message_id INTEGER,      -- ID of last exported message
    updated_at INTEGER,           -- Unix timestamp
    PRIMARY KEY (user_id, chat_id, chat_type)
);
```

Each `(user_id, chat_id, chat_type)` combination represents a unique export track record.

## Implementation Details

### 1. Chat Identity (`get_chat_identity` helper)

```python
def get_chat_identity(dialog) -> tuple:
    """
    Extract chat_id and chat_type from Telethon dialog.

    Returns:
        (chat_id, chat_type) where:
        - chat_id: dialog.entity.id
        - chat_type: 'user' | 'chat' | 'channel'
    """
```

Determines:
- `chat_id`: Unique identifier for the chat
- `chat_type`:
  - `'user'` if it's a direct message
  - `'channel'` if it's a channel
  - `'chat'` if it's a group/supergroup

### 2. Progress Tracking Functions

**`get_chat_progress(user_id, chat_id, chat_type) -> Optional[int]`**
- Returns the `last_message_id` if this chat was exported before
- Returns `None` if this is a first-time export

**`upsert_chat_progress(user_id, chat_id, chat_type, last_message_id)`**
- Creates new record if it doesn't exist
- Updates existing record with new `last_message_id`

### 3. Export Logic

#### First Export (No Progress)
```python
# No last_message_id found
async for message in client.iter_messages(chat_id, limit=1000):
    # Process message...

# After export:
new_last_message_id = max(message_ids)
db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
```

#### Incremental Export (Has Progress)
```python
# last_message_id exists
async for message in client.iter_messages(chat_id, min_id=last_message_id):
    # Process only messages with id > last_message_id

# After export:
new_last_message_id = max(message_ids)
db.upsert_chat_progress(user_id, chat_id, chat_type, new_last_message_id)
```

**Key difference:** `min_id` parameter tells Telethon to return only messages with ID greater than `last_message_id`.

## User Experience

### File Metadata

Export files now include type information:

**Full Export:**
```
Chat: John Doe
Exported: 2024-01-15 14:30:25
Export type: Full export
Total messages: 1000
================================================================================

[2024-01-10 10:00:00] John: Hello!
...
```

**Incremental Export:**
```
Chat: John Doe
Exported: 2024-01-16 09:15:10
Export type: Incremental (new messages only)
Total messages: 42
================================================================================

[2024-01-15 18:00:00] John: Hey, what's up?
...
```

### Captions

- **First export:** `✅ Full export of ChatName - 1000 messages`
- **Incremental:** `✅ Exported 42 new messages from ChatName (since last export)`
- **No new messages:** `⚠️ No new messages in ChatName since last export.`

## Data Management

### Logout Behavior

When user runs `/logout`:
- Deletes all user data including:
  - Session string
  - Pending logins
  - **Chat export progress** (all `chat_progress` records for that user)

This ensures clean slate on re-authentication.

### Progress Reset

To reset progress for a specific chat without logging out:
- Currently not implemented (manual database operation required)
- Future feature: `/reset_progress` command

## Advantages

1. **Bandwidth Efficiency**: Only downloads new messages
2. **Storage Efficiency**: User doesn't re-download the same messages
3. **Time Savings**: Faster exports for active chats
4. **Organization**: Each export file contains only new messages since last time

## Workflow Example

```
Day 1:
User: /export → Select "Work Chat" → Enter 1000
Bot: ✅ Full export of Work Chat - 1000 messages
[File: export_Work_Chat_20240115_100000.txt with 1000 messages]

Day 2 (after 50 new messages):
User: /export → Select "Work Chat" → Enter any number (ignored)
Bot: ✅ Exported 50 new messages from Work Chat (since last export)
[File: export_Work_Chat_20240116_150000.txt with 50 messages]

Day 3 (no new messages):
User: /export → Select "Work Chat"
Bot: ⚠️ No new messages in Work Chat since last export.
[No file created]

Day 4 (after 100 new messages):
User: /export → Select "Work Chat"
Bot: ✅ Exported 100 new messages from Work Chat (since last export)
[File: export_Work_Chat_20240118_120000.txt with 100 messages]
```

User now has:
- File 1: Messages 1-1000 (full history)
- File 2: Messages 1001-1050 (incremental)
- File 3: Messages 1051-1150 (incremental)

Total: 1150 messages across 3 files.

## Technical Considerations

### Message ID Monotonicity

Telegram message IDs are:
- **Sequential**: Each new message has a higher ID
- **Per-chat**: IDs are unique within a chat
- **Monotonic**: Always increasing

This makes `min_id` filtering reliable for incremental exports.

### Edge Cases Handled

1. **No text messages in range**: Returns appropriate message, no file created
2. **Chat with only media**: Only text messages are tracked, media-only messages don't affect progress
3. **Deleted messages**: Gaps in message IDs are handled correctly
4. **User re-authenticates**: Progress is preserved (tied to user_id, not session)

### Limitations

1. **Text-only tracking**: Currently only tracks text messages (images, videos, etc. are not included)
2. **No progress visibility**: User can't see current progress without exporting
3. **No manual reset**: Can't reset progress for a specific chat without logout or database access

## Future Enhancements

Potential improvements:

1. **Progress Status Command**: `/export_status` to show last export date per chat
2. **Manual Reset**: `/reset_chat ChatName` to start fresh
3. **Media Support**: Track and export media files incrementally
4. **Date-based Filtering**: Export messages from specific date ranges
5. **Format Options**: Export to JSON, CSV, or HTML formats
6. **Merge Tool**: Combine multiple incremental exports into one file

## API Reference

### Database Functions (bot/db.py and backend/db.py)

```python
def get_chat_progress(user_id: int, chat_id: int, chat_type: str) -> Optional[int]:
    """
    Get the last exported message ID for a user-chat pair.

    Args:
        user_id: Telegram user ID
        chat_id: Chat/channel/user entity ID
        chat_type: 'user', 'chat', or 'channel'

    Returns:
        Last message ID or None if no progress exists
    """

def upsert_chat_progress(user_id: int, chat_id: int, chat_type: str, last_message_id: int) -> None:
    """
    Create or update export progress.

    Args:
        user_id: Telegram user ID
        chat_id: Chat/channel/user entity ID
        chat_type: 'user', 'chat', or 'channel'
        last_message_id: ID of the last exported message
    """
```

### Bot Helper Function (bot/bot.py)

```python
def get_chat_identity(dialog) -> tuple:
    """
    Extract chat identity from Telethon dialog.

    Args:
        dialog: Telethon Dialog object

    Returns:
        (chat_id, chat_type) tuple
    """
```

## Migration Notes

### Existing Users

For users who already have the bot installed:

1. **Database**: New `chat_progress` table is created automatically on first run
2. **Existing exports**: No retroactive tracking - first export after update will be treated as "full export"
3. **No data loss**: All existing session data remains intact

### Deployment

When deploying this update:

1. Both backend and bot services should be updated simultaneously
2. Database migrations run automatically (SQLAlchemy `create_all`)
3. No manual database changes required
4. No downtime needed (backward compatible)

## Testing

To test incremental export:

1. Authenticate with `/login`
2. Export a chat: `/export` → Select chat → Enter limit
3. Send new messages to that chat (or wait for new messages)
4. Export same chat again: `/export` → Select same chat
5. Verify you receive only the new messages
6. Export again without new messages: verify "no new messages" response

## Summary

Incremental export feature provides:
- ✅ Automatic progress tracking per chat
- ✅ Efficient exports (only new messages)
- ✅ Clear UX feedback (full vs incremental)
- ✅ Persistent across sessions
- ✅ Clean logout (deletes all progress)
- ✅ Backward compatible
- ✅ Zero configuration needed

Users can now maintain up-to-date chat archives with minimal bandwidth and time investment!
