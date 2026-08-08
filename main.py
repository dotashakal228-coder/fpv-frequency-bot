import time
import json
import asyncio
import os

import asyncpg
import aiosqlite

from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
    BotCommand
)
from aiogram.exceptions import TelegramBadRequest


# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("Не найден DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_POOL = None


# =========================================================
# ЗАГРУЗКА ЧАСТОТ
# =========================================================

with open("frequencies.json", "r", encoding="utf-8") as f:
    FREQUENCIES = json.load(f)


# =========================================================
# БЕЗОПАСНЫЙ CALLBACK ANSWER
# =========================================================

async def safe_answer(
    call: CallbackQuery,
    text=None,
    show_alert=False
):
    """
    Безопасно отвечает на callback Telegram.

    Если callback уже устарел, бот не падает.
    """

    try:

        await call.answer(
            text=text,
            show_alert=show_alert
        )

    except TelegramBadRequest as e:

        error_text = str(e)

        if (
            "query is too old" in error_text
            or "query ID is invalid" in error_text
        ):

            print(
                f"Старый callback пропущен: {error_text}"
            )

        else:

            print(
                f"TelegramBadRequest в callback: {error_text}"
            )

    except Exception as e:

        print(
            f"Ошибка callback: {e}"
        )


# =========================================================
# КЛАВИАТУРА НИЖНЕГО МЕНЮ
# =========================================================

def main_menu_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="☰ Меню"
                )
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


# =========================================================
# ГЛАВНОЕ INLINE-МЕНЮ
# =========================================================

def inline_main_menu():

    buttons = [
        [
            InlineKeyboardButton(
                text="📡 Частоты",
                callback_data="frequencies"
            )
        ],
        [
            InlineKeyboardButton(
                text="📍 Мой канал",
                callback_data="my_channel"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Инструкция",
                callback_data="instructions"
            )
        ]
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# МЕНЮ АДМИНИСТРАТОРА
# =========================================================

def admin_menu_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📡 Занятые каналы",
                    callback_data="admin_channels"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Главное меню",
                    callback_data="back_menu"
                )
            ]
        ]
    )


# =========================================================
# ПОДКЛЮЧЕНИЕ К POSTGRESQL
# =========================================================

async def connect_db():

    global DB_POOL

    DB_POOL = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30
    )

    print("PostgreSQL подключён")


# =========================================================
# СОЗДАНИЕ ТАБЛИЦ
# =========================================================

async def init_db():

    async with DB_POOL.acquire() as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                username TEXT,
                callsign TEXT,
                unit TEXT,
                position TEXT,
                status TEXT DEFAULT 'pending',
                step TEXT DEFAULT 'callsign'
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY,
                band TEXT NOT NULL,
                channel TEXT NOT NULL,
                frequency INTEGER NOT NULL,
                owner BIGINT DEFAULT NULL,
                expires_at BIGINT DEFAULT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_channels_owner
            ON channels(owner)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_channels_band
            ON channels(band)
        """)

        count = await db.fetchval(
            "SELECT COUNT(*) FROM channels"
        )

        if count == 0:

            channel_id = 1

            for band, groups in FREQUENCIES.items():

                for group, channels in groups.items():

                    for channel, frequency in channels.items():

                        await db.execute(
                            """
                            INSERT INTO channels
                            (id, band, channel, frequency)
                            VALUES ($1, $2, $3, $4)
                            """,
                            channel_id,
                            band,
                            f"{group}-{channel}",
                            int(frequency)
                        )

                        channel_id += 1

            print(
                f"Создано каналов: {channel_id - 1}"
            )


# =========================================================
# МИГРАЦИЯ СТАРОГО SQLITE
# =========================================================

async def migrate_old_sqlite():

    if not os.path.exists("users.db"):

        print(
            "Старого users.db нет — миграция не требуется"
        )

        return

    print(
        "Найден старый users.db. Проверяем миграцию..."
    )

    try:

        async with aiosqlite.connect("users.db") as old_db:

            try:

                cursor = await old_db.execute(
                    """
                    SELECT id, username, callsign,
                           unit, position, status
                    FROM users
                    """
                )

                old_users = await cursor.fetchall()

            except Exception:

                old_users = []

            try:

                cursor = await old_db.execute(
                    """
                    SELECT id, band, channel,
                           frequency, owner, expires_at
                    FROM channels
                    """
                )

                old_channels = await cursor.fetchall()

            except Exception:

                old_channels = []

    except Exception as e:

        print(
            f"Ошибка чтения старого users.db: {e}"
        )

        return

    async with DB_POOL.acquire() as db:

        for row in old_users:

            uid, username, callsign, unit, position, status = row

            if callsign and unit and position:

                step = "done"

            elif callsign and unit:

                step = "position"

            elif callsign:

                step = "unit"

            else:

                step = "callsign"

            await db.execute(
                """
                INSERT INTO users
                (id, username, callsign, unit,
                 position, status, step)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (id) DO NOTHING
                """,
                uid,
                username,
                callsign,
                unit,
                position,
                status,
                step
            )

        pg_count = await db.fetchval(
            "SELECT COUNT(*) FROM channels"
        )

        if pg_count == 0 and old_channels:

            for row in old_channels:

                (
                    channel_id,
                    band,
                    channel,
                    frequency,
                    owner,
                    expires_at
                ) = row

                await db.execute(
                    """
                    INSERT INTO channels
                    (id, band, channel, frequency,
                     owner, expires_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    channel_id,
                    band,
                    channel,
                    frequency,
                    owner,
                    expires_at
                )

    print(
        f"Миграция завершена. "
        f"Пользователей: {len(old_users)}"
    )


# =========================================================
# ПОЛУЧИТЬ ПОЛЬЗОВАТЕЛЯ
# =========================================================

async def get_user(uid):

    async with DB_POOL.acquire() as db:

        return await db.fetchrow(
            """
            SELECT id, username, callsign,
                   unit, position, status, step
            FROM users
            WHERE id=$1
            """,
            uid
        )


# =========================================================
# ПРОВЕРКА ДОСТУПА
# =========================================================

async def is_approved(uid):

    user = await get_user(uid)

    return (
        user is not None
        and user["status"] == "approved"
    )


# =========================================================
# ОСВОБОЖДЕНИЕ ПРОСРОЧЕННЫХ
# =========================================================

async def release_expired_channels():

    now = int(time.time())

    async with DB_POOL.acquire() as db:

        result = await db.execute(
            """
            UPDATE channels
            SET owner=NULL,
                expires_at=NULL
            WHERE expires_at IS NOT NULL
              AND expires_at <= $1
            """,
            now
        )

    return result


# =========================================================
# ФОРМАТ ВРЕМЕНИ
# =========================================================

def format_time_left(expires_at):

    if not expires_at:
        return "∞"

    left = expires_at - int(time.time())

    if left <= 0:
        return "истёк"

    hours = left // 3600
    minutes = (left % 3600) // 60
    seconds = left % 60

    if hours > 0:

        if minutes > 0:
            return f"{hours} ч {minutes} мин"

        return f"{hours} ч"

    if minutes > 0:
        return f"{minutes} мин {seconds} сек"

    return f"{seconds} сек"


# =========================================================
# ПРИВЕТСТВЕННАЯ ИНСТРУКЦИЯ
# =========================================================

WELCOME_TEXT = """
👋 Добро пожаловать!

Это бот для работы с каналами и частотами.

📌 Как пользоваться:

1️⃣ Пройдите регистрацию:
• позывной
• подразделение
• должность

2️⃣ После одобрения администратора нажмите «☰ Меню».

3️⃣ В разделе «📡 Частоты» выберите нужный диапазон.

4️⃣ Выберите свободный канал.

5️⃣ Укажите, на сколько времени хотите его занять.

6️⃣ После занятия канала другие пользователи увидят:
• канал
• частоту
• позывной владельца
• оставшееся время

📍 В разделе «Мой канал» можно посмотреть свой канал и досрочно его освободить.

⚠️ Один пользователь может одновременно занимать только один канал.

⏳ После окончания выбранного времени канал автоматически освобождается.
"""


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start(message: types.Message):

    uid = message.from_user.id

    user = await get_user(uid)

    if not user:

        async with DB_POOL.acquire() as db:

            await db.execute(
                """
                INSERT INTO users
                (id, username, status, step)
                VALUES ($1, $2, 'pending', 'callsign')
                """,
                uid,
                message.from_user.username
            )

        await message.answer(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard()
        )

        await message.answer(
            "Для начала регистрации введите ваш позывной:"
        )

        return

    async with DB_POOL.acquire() as db:

        await db.execute(
            """
            UPDATE users
            SET username=$1
            WHERE id=$2
            """,
            message.from_user.username,
            uid
        )

    status = user["status"]
    step = user["step"]

    if step == "callsign":

        await message.answer(
            "Введите ваш позывной:",
            reply_markup=main_menu_keyboard()
        )

        return

    if step == "unit":

        await message.answer(
            "Введите подразделение:"
        )

        return

    if step == "position":

        await message.answer(
            "Введите должность:"
        )

        return

    if status == "pending":

        await message.answer(
            "⏳ Ваша заявка уже отправлена администратору.\n\n"
            "Ожидайте подтверждения.",
            reply_markup=main_menu_keyboard()
        )

        return

    if status == "rejected":

        await message.answer(
            "❌ Ваша заявка была отклонена.",
            reply_markup=main_menu_keyboard()
        )

        return

    if status == "approved":

        await message.answer(
            "📋 Главное меню:",
            reply_markup=main_menu_keyboard()
        )

        await message.answer(
            "Выберите нужный раздел:",
            reply_markup=inline_main_menu()
        )


# =========================================================
# КНОПКА МЕНЮ
# =========================================================

@dp.message(F.text == "☰ Меню")
async def menu_button(message: types.Message):

    uid = message.from_user.id

    user = await get_user(uid)

    if not user:

        await start(message)

        return

    status = user["status"]
    step = user["step"]

    if step == "callsign":

        await message.answer(
            "Введите ваш позывной:"
        )

        return

    if step == "unit":

        await message.answer(
            "Введите подразделение:"
        )

        return

    if step == "position":

        await message.answer(
            "Введите должность:"
        )

        return

    if status == "pending":

        await message.answer(
            "⏳ Ваша заявка ещё находится на проверке."
        )

        return

    if status == "rejected":

        await message.answer(
            "❌ Ваша заявка была отклонена."
        )

        return

    await message.answer(
        "📋 Главное меню:",
        reply_markup=inline_main_menu()
    )


# =========================================================
# РЕГИСТРАЦИЯ
# =========================================================

@dp.message(F.text & ~F.text.startswith("/"))
async def registration(message: types.Message):

    if message.text == "☰ Меню":
        return

    uid = message.from_user.id
    text = message.text.strip()

    if not text:
        return

    user = await get_user(uid)

    if not user:
        return

    status = user["status"]
    step = user["step"]

    if status == "approved":

        await message.answer(
            "📋 Используйте кнопку «☰ Меню»."
        )

        return

    if status == "pending" and step == "done":

        await message.answer(
            "⏳ Ваша заявка уже отправлена администратору."
        )

        return

    if step == "callsign":

        async with DB_POOL.acquire() as db:

            await db.execute(
                """
                UPDATE users
                SET callsign=$1,
                    step='unit'
                WHERE id=$2
                """,
                text,
                uid
            )

        await message.answer(
            "Введите подразделение:"
        )

        return

    if step == "unit":

        async with DB_POOL.acquire() as db:

            await db.execute(
                """
                UPDATE users
                SET unit=$1,
                    step='position'
                WHERE id=$2
                """,
                text,
                uid
            )

        await message.answer(
            "Введите должность:"
        )

        return

    if step == "position":

        async with DB_POOL.acquire() as db:

            await db.execute(
                """
                UPDATE users
                SET position=$1,
                    step='done',
                    status='pending'
                WHERE id=$2
                """,
                text,
                uid
            )

            user_data = await db.fetchrow(
                """
                SELECT callsign, unit, position
                FROM users
                WHERE id=$1
                """,
                uid
            )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Одобрить",
                        callback_data=f"approve_{uid}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"reject_{uid}"
                    )
                ]
            ]
        )

        await bot.send_message(
            ADMIN_ID,
            "🆕 Новая заявка:\n\n"
            f"🆔 ID: {uid}\n"
            f"📛 Позывной: {user_data['callsign']}\n"
            f"🏢 Подразделение: {user_data['unit']}\n"
            f"🎖 Должность: {user_data['position']}",
            reply_markup=keyboard
        )

        await message.answer(
            "✅ Заявка заполнена и отправлена администратору.\n\n"
            "Ожидайте подтверждения."
        )


# =========================================================
# ОДОБРЕНИЕ
# =========================================================

@dp.callback_query(F.data.startswith("approve_"))
async def approve(call: CallbackQuery):

    await safe_answer(call)

    if call.from_user.id != ADMIN_ID:

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    uid = int(
        call.data.split("_")[1]
    )

    async with DB_POOL.acquire() as db:

        result = await db.execute(
            """
            UPDATE users
            SET status='approved',
                step='done'
            WHERE id=$1
            """,
            uid
        )

    if result == "UPDATE 0":

        await safe_answer(
            call,
            "Пользователь не найден.",
            show_alert=True
        )

        return

    await bot.send_message(
        uid,
        "✅ Ваша заявка одобрена!\n\n"
        "Доступ открыт.",
        reply_markup=main_menu_keyboard()
    )

    try:

        await call.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception as e:

        print(
            f"Не удалось убрать клавиатуру заявки: {e}"
        )


# =========================================================
# ОТКЛОНЕНИЕ
# =========================================================

@dp.callback_query(F.data.startswith("reject_"))
async def reject(call: CallbackQuery):

    await safe_answer(call)

    if call.from_user.id != ADMIN_ID:

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    uid = int(
        call.data.split("_")[1]
    )

    async with DB_POOL.acquire() as db:

        await db.execute(
            """
            UPDATE users
            SET status='rejected'
            WHERE id=$1
            """,
            uid
        )

    await bot.send_message(
        uid,
        "❌ Ваша заявка была отклонена.",
        reply_markup=main_menu_keyboard()
    )

    try:

        await call.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception as e:

        print(
            f"Не удалось убрать клавиатуру заявки: {e}"
        )


# =========================================================
# ADMIN
# =========================================================

@dp.message(Command("admin"))
async def admin_command(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "🔐 Панель администратора:",
        reply_markup=admin_menu_keyboard()
    )


# =========================================================
# USERS
# =========================================================

@dp.message(Command("users"))
async def users_list(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return

    async with DB_POOL.acquire() as db:

        users = await db.fetch(
            """
            SELECT id, username, callsign,
                   unit, position, status
            FROM users
            ORDER BY callsign
            """
        )

    if not users:

        await message.answer(
            "📭 Пользователей нет."
        )

        return

    text = "👥 Пользователи:\n\n"

    for user in users:

        text += (
            f"🆔 {user['id']}\n"
            f"👤 @{user['username'] or 'нет'}\n"
            f"📛 {user['callsign'] or '—'}\n"
            f"🏢 {user['unit'] or '—'}\n"
            f"🎖 {user['position'] or '—'}\n"
            f"📌 {user['status']}\n\n"
        )

    await message.answer(text)


# =========================================================
# ADMIN USERS
# =========================================================

@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):

    await safe_answer(call)

    if call.from_user.id != ADMIN_ID:

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    async with DB_POOL.acquire() as db:

        users = await db.fetch(
            """
            SELECT id, callsign, unit,
                   position, status
            FROM users
            ORDER BY callsign
            """
        )

    if not users:

        await call.message.answer(
            "📭 Пользователей нет."
        )

        return

    text = "👥 Пользователи:\n\n"

    for user in users:

        text += (
            f"🆔 {user['id']}\n"
            f"📛 {user['callsign'] or '—'}\n"
            f"🏢 {user['unit'] or '—'}\n"
            f"🎖 {user['position'] or '—'}\n"
            f"📌 {user['status']}\n\n"
        )

    await call.message.answer(text)


# =========================================================
# ADMIN CHANNELS
# =========================================================

@dp.callback_query(F.data == "admin_channels")
async def admin_channels(call: CallbackQuery):

    await safe_answer(call)

    if call.from_user.id != ADMIN_ID:

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    await release_expired_channels()

    async with DB_POOL.acquire() as db:

        rows = await db.fetch(
            """
            SELECT
                channels.id,
                channels.band,
                channels.channel,
                channels.frequency,
                channels.expires_at,
                users.callsign
            FROM channels
            LEFT JOIN users
                ON channels.owner=users.id
            WHERE channels.owner IS NOT NULL
            ORDER BY channels.band,
                     channels.frequency
            """
        )

    if not rows:

        await call.message.answer(
            "📡 Сейчас занятых каналов нет."
        )

        return

    text = "📡 Занятые каналы:\n\n"

    for row in rows:

        text += (
            f"🆔 Канал ID: {row['id']}\n"
            f"📡 {row['band']}\n"
            f"🎯 {row['channel']}\n"
            f"📶 {row['frequency']} MHz\n"
            f"👤 {row['callsign'] or '—'}\n"
            f"⏳ {format_time_left(row['expires_at'])}\n\n"
        )

    await call.message.answer(text)


# =========================================================
# BACK MENU
# =========================================================

@dp.callback_query(F.data == "back_menu")
async def back_menu(call: CallbackQuery):

    await safe_answer(call)

    await call.message.answer(
        "📋 Главное меню:",
        reply_markup=inline_main_menu()
    )


# =========================================================
# INSTRUCTIONS
# =========================================================

@dp.callback_query(F.data == "instructions")
async def instructions(call: CallbackQuery):

    await safe_answer(call)

    await call.message.answer(
        WELCOME_TEXT,
        reply_markup=inline_main_menu()
    )


# =========================================================
# OPEN SECTION
# =========================================================

@dp.callback_query(F.data == "open_section")
async def open_section(call: CallbackQuery):

    await safe_answer(call)

    await call.message.answer(
        "📋 Раздел доступен."
    )


# =========================================================
# FREQUENCIES
# =========================================================

@dp.callback_query(F.data == "frequencies")
async def frequencies(call: CallbackQuery):

    # Подтверждаем кнопку СРАЗУ
    await safe_answer(call)

    keyboard = []

    for band in FREQUENCIES.keys():

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=band.replace(
                        "GHz",
                        " GHz"
                    ),
                    callback_data=f"band_{band}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="back_menu"
            )
        ]
    )

    await call.message.answer(
        "📡 Выберите диапазон:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )


# =========================================================
# ПОКАЗАТЬ ДИАПАЗОН
# =========================================================

@dp.callback_query(F.data.startswith("band_"))
async def show_band(call: CallbackQuery):

    await safe_answer(call)

    if not await is_approved(
        call.from_user.id
    ):

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    await release_expired_channels()

    band = call.data.replace(
        "band_",
        "",
        1
    )

    async with DB_POOL.acquire() as db:

        rows = await db.fetch(
            """
            SELECT
                channels.id,
                channels.channel,
                channels.frequency,
                channels.owner,
                channels.expires_at,
                users.callsign
            FROM channels
            LEFT JOIN users
                ON channels.owner=users.id
            WHERE channels.band=$1
            ORDER BY channels.frequency
            """,
            band
        )

    if not rows:

        await call.message.answer(
            "❌ В этом диапазоне каналов нет."
        )

        return

    keyboard = []

    for row in rows:

        if row["owner"] is None:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🟢 {row['channel']} • "
                            f"{row['frequency']} MHz"
                        ),
                        callback_data=f"take_{row['id']}"
                    )
                ]
            )

        else:

            timer = format_time_left(
                row["expires_at"]
            )

            pilot = (
                row["callsign"]
                or "неизвестен"
            )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🔴 {row['channel']} • "
                            f"{row['frequency']} MHz"
                        ),
                        callback_data="occupied"
                    )
                ]
            )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"👤 {pilot} • "
                            f"⏳ осталось {timer}"
                        ),
                        callback_data="occupied"
                    )
                ]
            )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Диапазоны",
                callback_data="frequencies"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="📍 Мой канал",
                callback_data="my_channel"
            )
        ]
    )

    await call.message.answer(
        f"📡 Каналы диапазона {band}:\n\n"
        "🟢 — свободен\n"
        "🔴 — занят\n"
        "👤 — позывной владельца\n"
        "⏳ — оставшееся время",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )


# =========================================================
# ЗАНЯТЫЙ
# =========================================================

@dp.callback_query(F.data == "occupied")
async def occupied(call: CallbackQuery):

    await safe_answer(
        call,
        "🔴 Этот канал уже занят.",
        show_alert=True
    )


# =========================================================
# ВЫБОР КАНАЛА
# =========================================================

def duration_keyboard(channel_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="15 минут",
                    callback_data=f"duration_{channel_id}_15"
                )
            ],
            [
                InlineKeyboardButton(
                    text="30 минут",
                    callback_data=f"duration_{channel_id}_30"
                )
            ],
            [
                InlineKeyboardButton(
                    text="1 час",
                    callback_data=f"duration_{channel_id}_60"
                )
            ],
            [
                InlineKeyboardButton(
                    text="2 часа",
                    callback_data=f"duration_{channel_id}_120"
                )
            ],
            [
                InlineKeyboardButton(
                    text="4 часа",
                    callback_data=f"duration_{channel_id}_240"
                )
            ],
            [
                InlineKeyboardButton(
                    text="8 часов",
                    callback_data=f"duration_{channel_id}_480"
                )
            ]
        ]
    )


# =========================================================
# НАЖАТИЕ НА СВОБОДНЫЙ КАНАЛ
# =========================================================

@dp.callback_query(F.data.startswith("take_"))
async def take_channel(call: CallbackQuery):

    await safe_answer(call)

    uid = call.from_user.id

    if not await is_approved(uid):

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    channel_id = int(
        call.data.replace(
            "take_",
            "",
            1
        )
    )

    await release_expired_channels()

    async with DB_POOL.acquire() as db:

        busy = await db.fetchrow(
            """
            SELECT band, channel,
                   frequency, expires_at
            FROM channels
            WHERE owner=$1
            LIMIT 1
            """,
            uid
        )

        if busy:

            await call.message.answer(
                "❌ У вас уже занят канал.\n\n"
                f"📡 {busy['band']}\n"
                f"🎯 {busy['channel']}\n"
                f"📶 {busy['frequency']} MHz\n"
                f"⏳ Осталось: "
                f"{format_time_left(busy['expires_at'])}\n\n"
                "Сначала освободите текущий канал."
            )

            return

        channel = await db.fetchrow(
            """
            SELECT band, channel,
                   frequency, owner
            FROM channels
            WHERE id=$1
            """,
            channel_id
        )

    if not channel:

        await safe_answer(
            call,
            "❌ Канал не найден.",
            show_alert=True
        )

        return

    if channel["owner"] is not None:

        await safe_answer(
            call,
            "❌ Этот канал уже занят.",
            show_alert=True
        )

        return

    await call.message.answer(
        f"📡 Канал: {channel['channel']}\n"
        f"📶 Частота: {channel['frequency']} MHz\n\n"
        "⏳ На сколько времени занять канал?",
        reply_markup=duration_keyboard(channel_id)
    )


# =========================================================
# ВЫБОР ДЛИТЕЛЬНОСТИ
# =========================================================

@dp.callback_query(F.data.startswith("duration_"))
async def choose_duration(call: CallbackQuery):

    await safe_answer(call)

    uid = call.from_user.id

    if not await is_approved(uid):

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    parts = call.data.split("_")

    if len(parts) != 3:

        return

    channel_id = int(parts[1])
    minutes = int(parts[2])

    await release_expired_channels()

    async with DB_POOL.acquire() as db:

        busy = await db.fetchrow(
            """
            SELECT band, channel,
                   frequency, expires_at
            FROM channels
            WHERE owner=$1
            LIMIT 1
            """,
            uid
        )

        if busy:

            await call.message.answer(
                "❌ У вас уже занят канал.\n\n"
                f"📡 {busy['band']} • {busy['channel']}\n"
                f"📶 {busy['frequency']} MHz\n"
                f"⏳ Осталось: "
                f"{format_time_left(busy['expires_at'])}"
            )

            return

        expire = (
            int(time.time())
            + minutes * 60
        )

        result = await db.execute(
            """
            UPDATE channels
            SET owner=$1,
                expires_at=$2
            WHERE id=$3
              AND owner IS NULL
            """,
            uid,
            expire,
            channel_id
        )

        if result == "UPDATE 0":

            await safe_answer(
                call,
                "❌ Этот канал уже успел занять другой пользователь.",
                show_alert=True
            )

            return

        ch = await db.fetchrow(
            """
            SELECT band, channel,
                   frequency, expires_at
            FROM channels
            WHERE id=$1
            """,
            channel_id
        )

        user = await db.fetchrow(
            """
            SELECT callsign
            FROM users
            WHERE id=$1
            """,
            uid
        )

    callsign = (
        user["callsign"]
        if user
        else "неизвестен"
    )

    if minutes < 60:

        duration_text = (
            f"{minutes} минут"
        )

    else:

        hours = minutes // 60

        if hours == 1:
            duration_text = "1 час"

        else:
            duration_text = (
                f"{hours} часа"
            )

    await call.message.answer(
        "✅ Канал успешно занят!\n\n"
        f"📡 Диапазон: {ch['band']}\n"
        f"🎯 Канал: {ch['channel']}\n"
        f"📶 Частота: {ch['frequency']} MHz\n"
        f"👤 Позывной: {callsign}\n"
        f"⏳ Срок: {duration_text}\n"
        f"⏱ Осталось: "
        f"{format_time_left(ch['expires_at'])}"
    )


# =========================================================
# МОЙ КАНАЛ
# =========================================================

@dp.callback_query(F.data == "my_channel")
async def my_channel(call: CallbackQuery):

    await safe_answer(call)

    uid = call.from_user.id

    if not await is_approved(uid):

        await safe_answer(
            call,
            "Нет доступа.",
            show_alert=True
        )

        return

    await release_expired_channels()

    async with DB_POOL.acquire() as db:

        row = await db.fetchrow(
            """
            SELECT
                channels.band,
                channels.channel,
                channels.frequency,
                channels.expires_at,
                users.callsign
            FROM channels
            LEFT JOIN users
                ON channels.owner=users.id
            WHERE channels.owner=$1
            """,
            uid
        )

    if not row:

        await call.message.answer(
            "📍 У вас сейчас нет занятого канала."
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Освободить канал",
                    callback_data="release_channel"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📡 Частоты",
                    callback_data=f"band_{row['band']}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]
        ]
    )

    await call.message.answer(
        "📍 Ваш канал:\n\n"
        f"📡 Диапазон: {row['band']}\n"
        f"🎯 Канал: {row['channel']}\n"
        f"📶 Частота: {row['frequency']} MHz\n"
        f"👤 Позывной: {row['callsign'] or '—'}\n"
        f"⏳ Осталось: "
        f"{format_time_left(row['expires_at'])}",
        reply_markup=keyboard
    )


# =========================================================
# ОСВОБОДИТЬ КАНАЛ
# =========================================================

@dp.callback_query(F.data == "release_channel")
async def release_channel(call: CallbackQuery):

    await safe_answer(call)

    uid = call.from_user.id

    async with DB_POOL.acquire() as db:

        result = await db.execute(
            """
            UPDATE channels
            SET owner=NULL,
                expires_at=NULL
            WHERE owner=$1
            """,
            uid
        )

    if result == "UPDATE 0":

        await call.message.answer(
            "❌ У вас нет занятого канала."
        )

    else:

        await call.message.answer(
            "✅ Канал успешно освобождён."
        )


# =========================================================
# АВТООСВОБОЖДЕНИЕ
# =========================================================

async def auto_release():

    while True:

        try:

            await release_expired_channels()

        except Exception as e:

            print(
                f"Ошибка auto_release: {e}"
            )

        await asyncio.sleep(30)


# =========================================================
# КОМАНДЫ
# =========================================================

async def setup_bot_commands():

    commands = [
        BotCommand(
            command="start",
            description="Запустить бота"
        ),
        BotCommand(
            command="menu",
            description="Открыть меню"
        ),
        BotCommand(
            command="admin",
            description="Панель администратора"
        )
    ]

    await bot.set_my_commands(commands)


# =========================================================
# /MENU
# =========================================================

@dp.message(Command("menu"))
async def menu_command(message: types.Message):

    await menu_button(message)


# =========================================================
# HEALTH CHECK RENDER
# =========================================================

async def health(request):

    return web.Response(
        text="Bot is running",
        status=200
    )


# =========================================================
# ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК
# =========================================================

@dp.errors()
async def global_error_handler(event):

    exception = event.exception

    print(
        f"Ошибка обработчика: {type(exception).__name__}: {exception}"
    )

    return True


# =========================================================
# MAIN
# =========================================================

async def main():

    await connect_db()

    await init_db()

    await migrate_old_sqlite()

    await setup_bot_commands()

    auto_release_task = asyncio.create_task(
        auto_release()
    )

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"Web server started on port {port}"
    )

    print(
        "Bot started"
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        auto_release_task.cancel()

        try:

            await auto_release_task

        except asyncio.CancelledError:

            pass

        await bot.session.close()

        if DB_POOL:

            await DB_POOL.close()

        await runner.cleanup()


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print(
            "Бот остановлен"
        )

    except Exception as e:

        print(
            f"Критическая ошибка: {type(e).__name__}: {e}"
        )
