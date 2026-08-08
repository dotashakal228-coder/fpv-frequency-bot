import time
import json
import asyncio
import os
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


# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB = "users.db"

with open("frequencies.json", "r", encoding="utf-8") as f:
    FREQUENCIES = json.load(f)


# =========================================================
# КНОПКА НИЖНЕГО МЕНЮ
# =========================================================

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="☰ Меню")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


# =========================================================
# ГЛАВНОЕ INLINE-МЕНЮ
# =========================================================

def inline_main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
                    text="📋 Открыть раздел",
                    callback_data="open_section"
                )
            ]
        ]
    )


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

async def init_db():

    async with aiosqlite.connect(DB) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                band TEXT,
                channel TEXT,
                frequency INTEGER,
                owner INTEGER DEFAULT NULL,
                expires_at INTEGER DEFAULT NULL
            )
        """)

        await db.commit()

        # -----------------------------------------------------
        # Совместимость со старой базой
        # -----------------------------------------------------

        try:
            await db.execute(
                "ALTER TABLE users ADD COLUMN step TEXT DEFAULT 'callsign'"
            )
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute(
                "ALTER TABLE channels ADD COLUMN expires_at INTEGER"
            )
        except aiosqlite.OperationalError:
            pass

        await db.commit()

        # -----------------------------------------------------
        # Определяем этап старых пользователей
        # -----------------------------------------------------

        await db.execute("""
            UPDATE users
            SET step='callsign'
            WHERE callsign IS NULL
        """)

        await db.execute("""
            UPDATE users
            SET step='unit'
            WHERE callsign IS NOT NULL
              AND unit IS NULL
        """)

        await db.execute("""
            UPDATE users
            SET step='position'
            WHERE callsign IS NOT NULL
              AND unit IS NOT NULL
              AND position IS NULL
        """)

        await db.execute("""
            UPDATE users
            SET step='done'
            WHERE callsign IS NOT NULL
              AND unit IS NOT NULL
              AND position IS NOT NULL
        """)

        await db.commit()

        # -----------------------------------------------------
        # Заполняем каналы только если таблица пустая
        # -----------------------------------------------------

        cursor = await db.execute(
            "SELECT COUNT(*) FROM channels"
        )

        count = (await cursor.fetchone())[0]

        if count == 0:

            for band, groups in FREQUENCIES.items():

                for group, channels in groups.items():

                    for channel, freq in channels.items():

                        await db.execute(
                            """
                            INSERT INTO channels
                            (band, channel, frequency, owner, expires_at)
                            VALUES (?, ?, ?, NULL, NULL)
                            """,
                            (
                                band,
                                f"{group}-{channel}",
                                freq
                            )
                        )

            await db.commit()


# =========================================================
# ПОЛУЧИТЬ ПОЛЬЗОВАТЕЛЯ
# =========================================================

async def get_user(uid):

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                id,
                username,
                callsign,
                unit,
                position,
                status,
                step
            FROM users
            WHERE id=?
            """,
            (uid,)
        )

        return await cursor.fetchone()


# =========================================================
# ПРОВЕРКА АДМИНА
# =========================================================

def is_admin(uid):
    return uid == ADMIN_ID


# =========================================================
# ОСВОБОЖДЕНИЕ ПРОСРОЧЕННЫХ КАНАЛОВ
# =========================================================

async def release_expired_channels():

    now = int(time.time())

    async with aiosqlite.connect(DB) as db:

        await db.execute(
            """
            UPDATE channels
            SET owner=NULL,
                expires_at=NULL
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now,)
        )

        await db.commit()


# =========================================================
# ФОРМАТ ОСТАВШЕГОСЯ ВРЕМЕНИ
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

        if seconds > 0:
            return f"{minutes} мин {seconds} сек"

        return f"{minutes} мин"

    return f"{seconds} сек"


# =========================================================
# ТЕКСТ ДЛИТЕЛЬНОСТИ
# =========================================================

def duration_text(minutes):

    if minutes == 15:
        return "15 минут"

    if minutes == 30:
        return "30 минут"

    if minutes == 60:
        return "1 час"

    if minutes == 120:
        return "2 часа"

    if minutes == 240:
        return "4 часа"

    if minutes == 480:
        return "8 часов"

    return f"{minutes} минут"


# =========================================================
# ВЫБОР ВРЕМЕНИ
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
# ПРИВЕТСТВИЕ ДЛЯ НОВОГО ПОЛЬЗОВАТЕЛЯ
# =========================================================

WELCOME_TEXT = (
    "👋 Добро пожаловать!\n\n"
    "Это бот для регистрации и временного занятия каналов.\n\n"
    "Как пользоваться:\n"
    "1. Пройдите регистрацию.\n"
    "2. Дождитесь подтверждения администратора.\n"
    "3. Нажмите «☰ Меню».\n"
    "4. Откройте «📡 Частоты».\n"
    "5. Выберите нужный диапазон.\n"
    "6. Выберите свободный канал.\n"
    "7. Укажите срок занятия канала.\n\n"
    "Важно:\n"
    "• Один пилот может одновременно занимать только один канал.\n"
    "• После окончания выбранного времени канал автоматически освобождается.\n"
    "• Свой канал можно освободить раньше через «📍 Мой канал».\n"
    "• Перед занятием канала обязательно проверяйте его текущий статус.\n\n"
    "Для начала регистрации введите ваш позывной:"
)


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start(message: types.Message):

    uid = message.from_user.id

    user = await get_user(uid)

    # -----------------------------------------------------
    # НОВЫЙ ПОЛЬЗОВАТЕЛЬ
    # -----------------------------------------------------

    if not user:

        async with aiosqlite.connect(DB) as db:

            await db.execute(
                """
                INSERT INTO users
                (id, username, status, step)
                VALUES (?, ?, 'pending', 'callsign')
                """,
                (
                    uid,
                    message.from_user.username
                )
            )

            await db.commit()

        await message.answer(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # ПОЛЬЗОВАТЕЛЬ УЖЕ ЕСТЬ
    # -----------------------------------------------------

    # Обновляем username, но регистрацию не начинаем заново
    async with aiosqlite.connect(DB) as db:

        await db.execute(
            """
            UPDATE users
            SET username=?
            WHERE id=?
            """,
            (
                message.from_user.username,
                uid
            )
        )

        await db.commit()

    status = user[5]
    step = user[6]

    # -----------------------------------------------------
    # Если старая регистрация не завершена
    # -----------------------------------------------------

    if step == "callsign":

        await message.answer(
            "Введите ваш позывной:",
            reply_markup=main_menu_keyboard()
        )

        return

    if step == "unit":

        await message.answer(
            "Введите подразделение:",
            reply_markup=main_menu_keyboard()
        )

        return

    if step == "position":

        await message.answer(
            "Введите должность:",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # Ожидание проверки
    # -----------------------------------------------------

    if status == "pending":

        await message.answer(
            "⏳ Ваша заявка уже отправлена администратору.\n\n"
            "Повторно заполнять регистрацию не нужно.\n"
            "Ожидайте подтверждения.",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # Отклонён
    # -----------------------------------------------------

    if status == "rejected":

        await message.answer(
            "❌ Ваша заявка была отклонена.",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # Одобрен
    # -----------------------------------------------------

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
# КНОПКА ☰ МЕНЮ
# =========================================================

@dp.message(F.text == "☰ Меню")
async def menu_button(message: types.Message):

    uid = message.from_user.id

    user = await get_user(uid)

    if not user:

        await start(message)
        return

    status = user[5]
    step = user[6]

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

    if status != "approved":

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

    status = user[5]
    step = user[6]

    # -----------------------------------------------------
    # Уже одобрен
    # -----------------------------------------------------

    if status == "approved":

        await message.answer(
            "📋 Вы уже зарегистрированы.\n"
            "Используйте кнопку «☰ Меню».",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # Заявка уже отправлена
    # -----------------------------------------------------

    if status == "pending" and step == "done":

        await message.answer(
            "⏳ Ваша заявка уже отправлена администратору.\n"
            "Ожидайте подтверждения."
        )

        return

    # -----------------------------------------------------
    # Отклонён
    # -----------------------------------------------------

    if status == "rejected":

        await message.answer(
            "❌ Ваша заявка была отклонена."
        )

        return

    # -----------------------------------------------------
    # ПОЗЫВНОЙ
    # -----------------------------------------------------

    if step == "callsign":

        async with aiosqlite.connect(DB) as db:

            await db.execute(
                """
                UPDATE users
                SET callsign=?,
                    step='unit'
                WHERE id=?
                """,
                (
                    text,
                    uid
                )
            )

            await db.commit()

        await message.answer(
            "Введите подразделение:"
        )

        return

    # -----------------------------------------------------
    # ПОДРАЗДЕЛЕНИЕ
    # -----------------------------------------------------

    if step == "unit":

        async with aiosqlite.connect(DB) as db:

            await db.execute(
                """
                UPDATE users
                SET unit=?,
                    step='position'
                WHERE id=?
                """,
                (
                    text,
                    uid
                )
            )

            await db.commit()

        await message.answer(
            "Введите должность:"
        )

        return

    # -----------------------------------------------------
    # ДОЛЖНОСТЬ
    # -----------------------------------------------------

    if step == "position":

        async with aiosqlite.connect(DB) as db:

            await db.execute(
                """
                UPDATE users
                SET position=?,
                    step='done',
                    status='pending'
                WHERE id=?
                """,
                (
                    text,
                    uid
                )
            )

            await db.commit()

            cursor = await db.execute(
                """
                SELECT callsign, unit, position
                FROM users
                WHERE id=?
                """,
                (uid,)
            )

            data = await cursor.fetchone()

        callsign, unit, position = data

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
            f"ID: {uid}\n"
            f"Позывной: {callsign}\n"
            f"Подразделение: {unit}\n"
            f"Должность: {position}",
            reply_markup=keyboard
        )

        await message.answer(
            "✅ Регистрация завершена.\n\n"
            "Заявка отправлена администратору.\n"
            "Ожидайте подтверждения."
        )


# =========================================================
# ОДОБРИТЬ
# =========================================================

@dp.callback_query(F.data.startswith("approve_"))
async def approve(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    uid = int(call.data.split("_")[1])

    async with aiosqlite.connect(DB) as db:

        await db.execute(
            """
            UPDATE users
            SET status='approved',
                step='done'
            WHERE id=?
            """,
            (uid,)
        )

        await db.commit()

    await bot.send_message(
        uid,
        "✅ Ваша заявка одобрена!\n\n"
        "Доступ открыт.\n"
        "Для работы с ботом нажмите «☰ Меню».",
        reply_markup=main_menu_keyboard()
    )

    try:
        await call.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await call.answer("Одобрено")


# =========================================================
# ОТКЛОНИТЬ
# =========================================================

@dp.callback_query(F.data.startswith("reject_"))
async def reject(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    uid = int(call.data.split("_")[1])

    async with aiosqlite.connect(DB) as db:

        await db.execute(
            """
            UPDATE users
            SET status='rejected'
            WHERE id=?
            """,
            (uid,)
        )

        await db.commit()

    await bot.send_message(
        uid,
        "❌ Ваша заявка была отклонена.",
        reply_markup=main_menu_keyboard()
    )

    try:
        await call.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await call.answer("Отклонено")


# =========================================================
# /USERS
# =========================================================

@dp.message(Command("users"))
async def users_list(message: types.Message):

    if not is_admin(message.from_user.id):
        return

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                id,
                username,
                callsign,
                unit,
                position,
                status
            FROM users
            ORDER BY callsign
            """
        )

        users = await cursor.fetchall()

    if not users:

        await message.answer(
            "📭 Пользователей нет."
        )

        return

    text = "👥 Пользователи:\n\n"

    for (
        uid,
        username,
        callsign,
        unit,
        position,
        status
    ) in users:

        text += (
            f"ID: {uid}\n"
            f"Username: @{username or 'нет'}\n"
            f"Позывной: {callsign or '—'}\n"
            f"Подразделение: {unit or '—'}\n"
            f"Должность: {position or '—'}\n"
            f"Статус: {status}\n\n"
        )

    await message.answer(text)


# =========================================================
# /APPROVE ID
# =========================================================

@dp.message(Command("approve"))
async def approve_command(message: types.Message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Использование:\n"
            "/approve ID"
        )

        return

    try:
        uid = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            "SELECT id FROM users WHERE id=?",
            (uid,)
        )

        user = await cursor.fetchone()

        if not user:

            await message.answer(
                "❌ Пользователь не найден."
            )

            return

        await db.execute(
            """
            UPDATE users
            SET status='approved',
                step='done'
            WHERE id=?
            """,
            (uid,)
        )

        await db.commit()

    await bot.send_message(
        uid,
        "✅ Ваша заявка одобрена!\n\n"
        "Доступ открыт.",
        reply_markup=main_menu_keyboard()
    )

    await message.answer(
        f"✅ Пользователь {uid} одобрен."
    )


# =========================================================
# /REJECT ID
# =========================================================

@dp.message(Command("reject"))
async def reject_command(message: types.Message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Использование:\n"
            "/reject ID"
        )

        return

    try:
        uid = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            "SELECT id FROM users WHERE id=?",
            (uid,)
        )

        user = await cursor.fetchone()

        if not user:

            await message.answer(
                "❌ Пользователь не найден."
            )

            return

        await db.execute(
            """
            UPDATE users
            SET status='rejected'
            WHERE id=?
            """,
            (uid,)
        )

        await db.commit()

    await bot.send_message(
        uid,
        "❌ Ваша заявка была отклонена.",
        reply_markup=main_menu_keyboard()
    )

    await message.answer(
        f"❌ Пользователь {uid} отклонён."
    )


# =========================================================
# /CHANNELS
# =========================================================

@dp.message(Command("channels"))
async def channels_admin(message: types.Message):

    if not is_admin(message.from_user.id):
        return

    await release_expired_channels()

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                channels.band,
                channels.channel,
                channels.frequency,
                channels.owner,
                channels.expires_at,
                users.callsign
            FROM channels
            LEFT JOIN users
                ON channels.owner = users.id
            ORDER BY channels.band, channels.frequency
            """
        )

        rows = await cursor.fetchall()

    if not rows:

        await message.answer(
            "📭 Каналов нет."
        )

        return

    text = "📡 Состояние каналов:\n\n"

    for (
        band,
        channel,
        frequency,
        owner,
        expires_at,
        callsign
    ) in rows:

        if owner is None:

            text += (
                f"🟢 {band} | {channel} | "
                f"{frequency} MHz | свободен\n"
            )

        else:

            timer = format_time_left(expires_at)

            text += (
                f"🔴 {band} | {channel} | "
                f"{frequency} MHz\n"
                f"   Пилот: {callsign or '—'}\n"
                f"   ID: {owner}\n"
                f"   Осталось: {timer}\n\n"
            )

    # Telegram ограничивает размер одного сообщения
    if len(text) > 4000:

        for i in range(0, len(text), 4000):

            await message.answer(
                text[i:i + 4000]
            )

    else:

        await message.answer(text)


# =========================================================
# /RELEASE ID
# =========================================================

@dp.message(Command("release"))
async def release_command(message: types.Message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Использование:\n"
            "/release ID"
        )

        return

    try:
        uid = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT band, channel, frequency
            FROM channels
            WHERE owner=?
            """,
            (uid,)
        )

        channel = await cursor.fetchone()

        if not channel:

            await message.answer(
                "❌ У этого пользователя нет занятого канала."
            )

            return

        await db.execute(
            """
            UPDATE channels
            SET owner=NULL,
                expires_at=NULL
            WHERE owner=?
            """,
            (uid,)
        )

        await db.commit()

    await message.answer(
        "✅ Канал освобождён.\n\n"
        f"Диапазон: {channel[0]}\n"
        f"Канал: {channel[1]}\n"
        f"Частота: {channel[2]} MHz\n"
        f"Пользователь ID: {uid}"
    )

    try:

        await bot.send_message(
            uid,
            "ℹ️ Администратор освободил ваш канал."
        )

    except Exception:
        pass


# =========================================================
# НАЗАД В МЕНЮ
# =========================================================

@dp.callback_query(F.data == "back_menu")
async def back_menu(call: CallbackQuery):

    await call.message.answer(
        "📋 Главное меню:",
        reply_markup=inline_main_menu()
    )

    await call.answer()


# =========================================================
# ОТКРЫТЬ РАЗДЕЛ
# =========================================================

@dp.callback_query(F.data == "open_section")
async def open_section(call: CallbackQuery):

    await call.message.answer(
        "📋 Раздел доступен."
    )

    await call.answer()


# =========================================================
# ЧАСТОТЫ
# =========================================================

@dp.callback_query(F.data == "frequencies")
async def frequencies(call: CallbackQuery):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1.2 GHz",
                    callback_data="band_1.2GHz"
                )
            ],
            [
                InlineKeyboardButton(
                    text="1.5 GHz",
                    callback_data="band_1.5GHz"
                )
            ],
            [
                InlineKeyboardButton(
                    text="2.4 GHz",
                    callback_data="band_2.4GHz"
                )
            ],
            [
                InlineKeyboardButton(
                    text="3.3 GHz",
                    callback_data="band_3.3GHz"
                )
            ],
            [
                InlineKeyboardButton(
                    text="3.7 GHz",
                    callback_data="band_3.7GHz"
                )
            ],
            [
                InlineKeyboardButton(
                    text="5.8 GHz",
                    callback_data="band_5.8GHz"
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
        "📡 Выберите диапазон:",
        reply_markup=keyboard
    )

    await call.answer()


# =========================================================
# ПОКАЗАТЬ КАНАЛЫ
# =========================================================

@dp.callback_query(F.data.startswith("band_"))
async def show_band(call: CallbackQuery):

    await release_expired_channels()

    band = call.data.replace(
        "band_",
        ""
    )

    keyboard = []

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
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
                ON channels.owner = users.id
            WHERE channels.band=?
            ORDER BY channels.frequency
            """,
            (band,)
        )

        rows = await cursor.fetchall()

    if not rows:

        await call.message.answer(
            "❌ В этом диапазоне каналов нет."
        )

        await call.answer()

        return

    for (
        channel_id,
        channel,
        frequency,
        owner,
        expires_at,
        callsign
    ) in rows:

        # -------------------------------------------------
        # СВОБОДНЫЙ
        # -------------------------------------------------

        if owner is None:

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🟢 {channel} • "
                            f"{frequency} MHz"
                        ),
                        callback_data=f"take_{channel_id}"
                    )
                ]
            )

        # -------------------------------------------------
        # ЗАНЯТЫЙ
        # -------------------------------------------------

        else:

            timer = format_time_left(
                expires_at
            )

            pilot = callsign or "неизвестен"

            # Всё в одну строку.
            # Поэтому позывной не скрывает время.
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🔴 {channel} | "
                            f"{frequency} MHz | "
                            f"{pilot} | {timer}"
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
        "Формат занятого канала: "
        "канал | частота | позывной | время",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

    await call.answer()


# =========================================================
# ЗАНЯТЫЙ КАНАЛ
# =========================================================

@dp.callback_query(F.data == "occupied")
async def occupied(call: CallbackQuery):

    await call.answer(
        "🔴 Этот канал уже занят.",
        show_alert=True
    )


# =========================================================
# НАЖАТИЕ НА СВОБОДНЫЙ КАНАЛ
# =========================================================

@dp.callback_query(F.data.startswith("take_"))
async def take_channel(call: CallbackQuery):

    uid = call.from_user.id

    channel_id = int(
        call.data.replace(
            "take_",
            ""
        )
    )

    await release_expired_channels()

    # -----------------------------------------------------
    # Проверяем регистрацию
    # -----------------------------------------------------

    user = await get_user(uid)

    if not user or user[5] != "approved":

        await call.answer(
            "❌ У вас нет доступа.",
            show_alert=True
        )

        return

    # -----------------------------------------------------
    # Проверяем уже занятый канал
    # -----------------------------------------------------

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                band,
                channel,
                frequency,
                expires_at
            FROM channels
            WHERE owner=?
            """,
            (uid,)
        )

        busy = await cursor.fetchone()

    if busy:

        timer = format_time_left(
            busy[3]
        )

        await call.message.answer(
            "❌ У вас уже занят канал.\n\n"
            f"📡 Диапазон: {busy[0]}\n"
            f"🎯 Канал: {busy[1]}\n"
            f"📶 Частота: {busy[2]} MHz\n"
            f"⏳ Осталось: {timer}\n\n"
            "Сначала освободите текущий канал."
        )

        await call.answer()

        return

    # -----------------------------------------------------
    # Проверяем выбранный канал
    # -----------------------------------------------------

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                band,
                channel,
                frequency,
                owner
            FROM channels
            WHERE id=?
            """,
            (channel_id,)
        )

        channel = await cursor.fetchone()

    if not channel:

        await call.answer(
            "❌ Канал не найден.",
            show_alert=True
        )

        return

    if channel[3] is not None:

        await call.answer(
            "❌ Этот канал уже занят.",
            show_alert=True
        )

        return

    # -----------------------------------------------------
    # Выбор времени
    # -----------------------------------------------------

    await call.message.answer(
        f"📡 Канал: {channel[1]}\n"
        f"📶 Частота: {channel[2]} MHz\n\n"
        "⏳ На сколько времени занять канал?",
        reply_markup=duration_keyboard(channel_id)
    )

    await call.answer()


# =========================================================
# ВЫБОР ДЛИТЕЛЬНОСТИ
# =========================================================

@dp.callback_query(F.data.startswith("duration_"))
async def choose_duration(call: CallbackQuery):

    uid = call.from_user.id

    parts = call.data.split("_")

    if len(parts) != 3:

        await call.answer(
            "❌ Ошибка.",
            show_alert=True
        )

        return

    channel_id = int(parts[1])
    minutes = int(parts[2])

    allowed = [15, 30, 60, 120, 240, 480]

    if minutes not in allowed:

        await call.answer(
            "❌ Недопустимое время.",
            show_alert=True
        )

        return

    await release_expired_channels()

    # -----------------------------------------------------
    # Проверяем пользователя
    # -----------------------------------------------------

    user = await get_user(uid)

    if not user or user[5] != "approved":

        await call.answer(
            "❌ Нет доступа.",
            show_alert=True
        )

        return

    callsign = user[2] or "неизвестен"

    # -----------------------------------------------------
    # Атомарно пытаемся занять канал
    # -----------------------------------------------------

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                band,
                channel,
                frequency,
                owner
            FROM channels
            WHERE id=?
            """,
            (channel_id,)
        )

        channel = await cursor.fetchone()

        if not channel:

            await call.answer(
                "❌ Канал не найден.",
                show_alert=True
            )

            return

        # Проверяем ещё раз наличие канала
        cursor = await db.execute(
            """
            SELECT
                band,
                channel,
                frequency,
                expires_at
            FROM channels
            WHERE owner=?
            """,
            (uid,)
        )

        busy = await cursor.fetchone()

        if busy:

            timer = format_time_left(
                busy[3]
            )

            await call.message.answer(
                "❌ У вас уже занят канал.\n\n"
                f"📡 {busy[0]} • {busy[1]}\n"
                f"📶 {busy[2]} MHz\n"
                f"⏳ Осталось: {timer}"
            )

            await call.answer()

            return

        expire = int(time.time()) + minutes * 60

        cursor = await db.execute(
            """
            UPDATE channels
            SET owner=?,
                expires_at=?
            WHERE id=?
              AND owner IS NULL
            """,
            (
                uid,
                expire,
                channel_id
            )
        )

        if cursor.rowcount == 0:

            await db.rollback()

            await call.answer(
                "❌ Этот канал уже успел занять другой пользователь.",
                show_alert=True
            )

            return

        await db.commit()

        cursor = await db.execute(
            """
            SELECT
                band,
                channel,
                frequency,
                expires_at
            FROM channels
            WHERE id=?
            """,
            (channel_id,)
        )

        ch = await cursor.fetchone()

    # -----------------------------------------------------
    # Подтверждение
    # -----------------------------------------------------

    await call.message.answer(
        "✅ Канал успешно занят!\n\n"
        f"📡 Диапазон: {ch[0]}\n"
        f"🎯 Канал: {ch[1]}\n"
        f"📶 Частота: {ch[2]} MHz\n"
        f"👤 Позывной: {callsign}\n"
        f"⏳ Срок: {duration_text(minutes)}\n"
        f"⏱ Осталось: {format_time_left(ch[3])}"
    )

    await call.answer(
        "Канал занят"
    )


# =========================================================
# МОЙ КАНАЛ
# =========================================================

@dp.callback_query(F.data == "my_channel")
async def my_channel(call: CallbackQuery):

    uid = call.from_user.id

    await release_expired_channels()

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                channels.band,
                channels.channel,
                channels.frequency,
                channels.expires_at,
                users.callsign
            FROM channels
            LEFT JOIN users
                ON channels.owner = users.id
            WHERE channels.owner=?
            """,
            (uid,)
        )

        row = await cursor.fetchone()

    if not row:

        await call.message.answer(
            "📍 У вас сейчас нет занятого канала."
        )

        await call.answer()

        return

    (
        band,
        channel,
        frequency,
        expires_at,
        callsign
    ) = row

    timer = format_time_left(
        expires_at
    )

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
                    callback_data=f"band_{band}"
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
        f"📡 Диапазон: {band}\n"
        f"🎯 Канал: {channel}\n"
        f"📶 Частота: {frequency} MHz\n"
        f"👤 Позывной: {callsign or '—'}\n"
        f"⏳ Осталось: {timer}",
        reply_markup=keyboard
    )

    await call.answer()


# =========================================================
# ОСВОБОДИТЬ КАНАЛ
# =========================================================

@dp.callback_query(F.data == "release_channel")
async def release_channel(call: CallbackQuery):

    uid = call.from_user.id

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            UPDATE channels
            SET owner=NULL,
                expires_at=NULL
            WHERE owner=?
            """,
            (uid,)
        )

        await db.commit()

        released = cursor.rowcount

    if released == 0:

        await call.message.answer(
            "❌ У вас нет занятого канала."
        )

    else:

        await call.message.answer(
            "✅ Канал успешно освобождён."
        )

    await call.answer()


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
# КОМАНДЫ БОТА
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
            command="users",
            description="Пользователи"
        ),
        BotCommand(
            command="channels",
            description="Состояние каналов"
        ),
        BotCommand(
            command="approve",
            description="Одобрить пользователя"
        ),
        BotCommand(
            command="reject",
            description="Отклонить пользователя"
        ),
        BotCommand(
            command="release",
            description="Освободить канал пользователя"
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
# RENDER HEALTH CHECK
# =========================================================

async def health(request):

    return web.Response(
        text="Bot is running"
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    await init_db()

    await setup_bot_commands()

    asyncio.create_task(
        auto_release()
    )

    # -----------------------------------------------------
    # Web-сервер для Render
    # -----------------------------------------------------

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    port = int(
        os.getenv(
            "PORT",
            10000
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

    await dp.start_polling(
        bot
    )


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
