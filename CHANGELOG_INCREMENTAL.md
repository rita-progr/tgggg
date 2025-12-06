# Changelog: Incremental Export Feature

## Summary

Добавлена функциональность инкрементального экспорта чатов. Бот теперь запоминает, до какого сообщения был выполнен экспорт, и при повторном экспорте скачивает только новые сообщения.

## Изменённые файлы

### Backend (`backend/`)

#### `backend/db.py`
- **Добавлено:**
  - Класс `ChatProgress` (новая таблица для хранения прогресса экспорта)
  - Функция `get_chat_progress(user_id, chat_id, chat_type) -> Optional[int]`
  - Функция `upsert_chat_progress(user_id, chat_id, chat_type, last_message_id)`

### Bot (`bot/`)

#### `bot/db.py`
- **Добавлено:**
  - Класс `ChatProgress` (та же таблица, shared database)
  - Функция `get_chat_progress(user_id, chat_id, chat_type) -> Optional[int]`
  - Функция `upsert_chat_progress(user_id, chat_id, chat_type, last_message_id)`
- **Изменено:**
  - Функция `delete_user_data()` теперь также удаляет записи из `chat_progress`

#### `bot/bot.py`
- **Добавлено:**
  - Функция `get_chat_identity(dialog) -> tuple` - определяет `(chat_id, chat_type)` из Telethon dialog
- **Изменено:**
  - Функция `export_start()`:
    - Теперь сохраняет `chat_id` и `chat_type` для каждого диалога в `context.user_data`
  - Функция `export_do_export()`:
    - Проверяет наличие прогресса через `get_chat_progress()`
    - Использует `min_id` для инкрементального экспорта (только новые сообщения)
    - Сообщает пользователю, если новых сообщений нет
    - Сохраняет новый `last_message_id` через `upsert_chat_progress()`
    - Различные UX-сообщения для полного и инкрементального экспорта

### Документация

#### Новые файлы:
- **`INCREMENTAL_EXPORT.md`** - полное описание фичи, примеры использования, технические детали

#### Обновлённые файлы:
- **`README.md`**:
  - Добавлена фича "Incremental Export" в секцию Features
  - Добавлена таблица `chat_progress` в секцию Database Schema
  - Обновлена Project Structure (упоминание chat_progress в комментариях)

## Новая таблица БД

```sql
CREATE TABLE chat_progress (
    user_id INTEGER,              -- Telegram user ID
    chat_id INTEGER,              -- Dialog entity ID
    chat_type TEXT,               -- 'user', 'chat', or 'channel'
    last_message_id INTEGER,      -- ID последнего экспортированного сообщения
    updated_at INTEGER,           -- Unix timestamp
    PRIMARY KEY (user_id, chat_id, chat_type)
);
```

## Новые функции API

### Database Layer (`backend/db.py` и `bot/db.py`)

```python
def get_chat_progress(user_id: int, chat_id: int, chat_type: str) -> Optional[int]:
    """
    Получить ID последнего экспортированного сообщения для пользователя и чата.
    Возвращает None, если прогресса нет (первый экспорт).
    """

def upsert_chat_progress(user_id: int, chat_id: int, chat_type: str, last_message_id: int) -> None:
    """
    Создать или обновить запись о прогрессе экспорта.
    """
```

### Bot Helpers (`bot/bot.py`)

```python
def get_chat_identity(dialog) -> tuple:
    """
    Извлечь (chat_id, chat_type) из Telethon dialog.

    chat_type может быть:
    - 'user' если это личная переписка
    - 'channel' если это канал
    - 'chat' если это группа/супергруппа
    """
```

## Логика работы

### Первый экспорт (Full Export)
1. Пользователь: `/export` → выбирает чат → указывает лимит (напр., 1000)
2. Бот проверяет `get_chat_progress()` → возвращает `None`
3. Бот скачивает до 1000 сообщений: `client.iter_messages(chat_id, limit=1000)`
4. Бот сохраняет `max(message_ids)` через `upsert_chat_progress()`
5. Пользователь получает: `✅ Full export of ChatName - 1000 messages`

### Повторный экспорт (Incremental Export)
1. Пользователь: `/export` → выбирает тот же чат
2. Бот проверяет `get_chat_progress()` → возвращает `last_message_id` (например, 12345)
3. Бот скачивает только новые сообщения: `client.iter_messages(chat_id, min_id=12345)`
4. Если есть новые сообщения:
   - Бот обновляет прогресс: `upsert_chat_progress(user_id, chat_id, chat_type, new_max_id)`
   - Пользователь получает: `✅ Exported 42 new messages from ChatName (since last export)`
5. Если новых сообщений нет:
   - Пользователь получает: `⚠️ No new messages in ChatName since last export.`
   - Файл не создаётся

## UX изменения

### Сообщения процесса экспорта

**Первый экспорт:**
```
⏳ Exporting up to 1000 messages from ChatName...
This may take a while.
```

**Инкрементальный:**
```
⏳ Exporting new messages from ChatName since last export...
This may take a while.
```

### Captions экспортированных файлов

**Первый экспорт:**
```
✅ Full export of ChatName - 1000 messages
```

**Инкрементальный (есть новые):**
```
✅ Exported 42 new messages from ChatName (since last export)
```

**Инкрементальный (нет новых):**
```
⚠️ No new messages in ChatName since last export.
```

### Содержимое файлов

В header файла добавлено:
```
Chat: ChatName
Exported: 2024-01-15 14:30:25
Export type: Full export  (или: Incremental (new messages only))
Total messages: 1000
================================================================================
```

## Поведение при logout

При выполнении `/logout`:
- Удаляются все данные пользователя, включая:
  - Session string
  - Pending logins
  - **Все записи chat_progress для этого user_id**

При повторной авторизации прогресс экспорта сбрасывается (все экспорты будут "полными").

## Обратная совместимость

- ✅ Новая таблица создаётся автоматически при первом запуске
- ✅ Существующие пользователи не затронуты
- ✅ Первый экспорт после обновления работает как "полный экспорт"
- ✅ Никаких миграций вручную не требуется

## Технические детали

### Использование `min_id` в Telethon

```python
# Обычный экспорт (первый раз):
async for msg in client.iter_messages(chat_id, limit=1000):
    # Получаем до 1000 последних сообщений

# Инкрементальный экспорт:
async for msg in client.iter_messages(chat_id, min_id=12345):
    # Получаем только сообщения с id > 12345
    # limit игнорируется - скачиваются ВСЕ новые
```

### Почему это работает надёжно?

Telegram message IDs:
- **Монотонные**: каждое новое сообщение имеет больший ID
- **Уникальны в пределах чата**: ID не повторяются
- **Последовательны**: ID увеличиваются с каждым новым сообщением

Это делает `min_id` filtering идеальным для инкрементальных экспортов.

## Примеры использования

### Сценарий 1: Первый экспорт
```
User: /export
Bot: [список чатов]
User: 1 (Work Chat)
Bot: How many messages to export? (Default: 1000)
User: 1000
Bot: ⏳ Exporting up to 1000 messages from Work Chat...
Bot: ✅ Full export of Work Chat - 1000 messages
[Файл: export_Work_Chat_20240115_100000.txt]
```

### Сценарий 2: Инкрементальный экспорт (есть новые)
```
User: /export
Bot: [список чатов]
User: 1 (Work Chat)
Bot: ⏳ Exporting new messages from Work Chat since last export...
Bot: ✅ Exported 50 new messages from Work Chat (since last export)
[Файл: export_Work_Chat_20240116_150000.txt]
```

### Сценарий 3: Нет новых сообщений
```
User: /export
Bot: [список чатов]
User: 1 (Work Chat)
Bot: ⚠️ No new messages in Work Chat since last export.
[Файла нет]
```

## Тестирование

Для проверки фичи:

1. Авторизоваться: `/login`
2. Экспортировать чат первый раз: `/export` → выбрать чат → указать лимит
3. Отправить несколько новых сообщений в этот чат
4. Экспортировать тот же чат снова: `/export` → выбрать тот же чат
5. Проверить, что получен только incremental export с новыми сообщениями
6. Попробовать экспорт ещё раз без новых сообщений → должно появиться "No new messages"

## Ограничения текущей реализации

1. **Только текстовые сообщения**: Media (фото, видео и т.д.) не учитываются
2. **Нет UI для прогресса**: Пользователь не может посмотреть текущий прогресс без экспорта
3. **Нет ручного сброса**: Нельзя сбросить прогресс отдельного чата без `/logout`
4. **Нет фильтров**: Нельзя экспортировать сообщения за конкретный период

## Будущие улучшения

Возможные расширения функциональности:

1. **Команда `/export_status`**: показать прогресс по всем чатам
2. **Команда `/reset_progress <chat>`**: сбросить прогресс конкретного чата
3. **Поддержка media**: экспорт вложений (фото, видео, документы)
4. **Фильтрация по датам**: экспорт только сообщений за определённый период
5. **Альтернативные форматы**: JSON, CSV, HTML
6. **Merge утилита**: объединение нескольких инкрементальных экспортов в один файл

## Версия

- **Дата добавления**: 2024
- **Затронутые компоненты**: Backend DB, Bot DB, Bot logic
- **Breaking changes**: Нет
- **Требует ли миграцию**: Нет (автоматическая)

---

**Дополнительная документация**: См. [INCREMENTAL_EXPORT.md](INCREMENTAL_EXPORT.md)
