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
# НИЖНЕЕ МЕНЮ
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
# ГЛАВНОЕ МЕНЮ
# =========================================================

def inline_main_menu(uid=None):

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
                text="📖 Инструкция",
                callback_data="instructions"
            )
        ]
    ]

    if uid == ADMIN_ID:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Админ-панель",
                    callback_data="admin_panel"
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# ПРИВЕТСТВЕННАЯ ИНСТРУКЦИЯ
# =========================================================

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "Этот бот предназначен для управления доступом к каналам.\n\n"
    "<b>Как пользоваться:</b>\n\n"
    "1️⃣ Пройдите регистрацию:\n"
    "• позывной\n"
    "• подразделение\n"
    "• должность\n\n"
    "2️⃣ Дождитесь проверки администратора.\n\n"
    "3️⃣ После одобрения нажмите <b>☰ Меню</b>.\n\n"
    "4️⃣ Откройте <b>📡 Частоты</b> и выберите нужный диапазон.\n\n"
    "5️⃣ Выберите свободный канал 🟢.\n\n"
    "6️⃣ Укажите, на какой срок хотите его занять.\n\n"
    "7️⃣ Занятые каналы отображаются как 🔴.\n"
    "У них видно позывной пользователя и оставшееся время.\n\n"
    "📍 В разделе <b>Мой канал</b> можно посмотреть свой канал "
    "и освободить его раньше окончания срока.\n\n"
    "⚠️ Один пользователь одновременно может занимать только один канал."
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

        # -------------------------------------------------
        # Совместимость со старой БД
        # -------------------------------------------------

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

        # -------------------------------------------------
        # Определяем этап регистрации существующих
        # пользователей
        # -------------------------------------------------

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

        # -------------------------------------------------
        # Заполнение каналов
        # -------------------------------------------------

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
                            (
                                band,
                                channel,
                                frequency,
                                owner,
                                expires_at
                            )
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
# ОСВОБОЖДЕНИЕ ПРОСРОЧЕННЫХ
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
        return f"{minutes} мин"

    return f"{seconds} сек"


# =========================================================
# ТЕКСТ ДЛИТЕЛЬНОСТИ
# =========================================================

def duration_text(minutes):

    if minutes < 60:
        return f"{minutes} минут"

    hours = minutes // 60

    if hours == 1:
        return "1 час"

    return f"{hours} часа"


# =========================================================
# ВЫБОР СРОКА
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
# START
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
                (
                    id,
                    username,
                    status,
                    step
                )
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

        await message.answer(
            "📝 <b>Начнём регистрацию.</b>\n\n"
            "Введите ваш позывной:",
            reply_markup=main_menu_keyboard()
        )

        return

    status = user[5]
    step = user[6]

    # -----------------------------------------------------
    # НЕЗАКОНЧЕННАЯ РЕГИСТРАЦИЯ
    # -----------------------------------------------------

    if step == "callsign":

        await message.answer(
            "📝 Введите ваш позывной:",
            reply_markup=main_menu_keyboard()
        )

        return

    if step == "unit":

        await message.answer(
            "🏢 Введите подразделение:",
            reply_markup=main_menu_keyboard()
        )

        return

    if step == "position":

        await message.answer(
            "🎖 Введите должность:",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # ОЖИДАНИЕ ПРОВЕРКИ
    # -----------------------------------------------------

    if status == "pending":

        await message.answer(
            "⏳ Ваша заявка уже отправлена администратору.\n\n"
            "Ожидайте подтверждения.",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # ОТКЛОНЁН
    # -----------------------------------------------------

    if status == "rejected":

        await message.answer(
            "❌ Ваша заявка была отклонена.",
            reply_markup=main_menu_keyboard()
        )

        return

    # -----------------------------------------------------
    # ОДОБРЕН
    # -----------------------------------------------------

    if status == "approved":

        await message.answer(
            "📋 <b>Главное меню</b>\n\n"
            "Выберите нужный раздел:",
            reply_markup=main_menu_keyboard()
        )

        await message.answer(
            "Выберите действие:",
            reply_markup=inline_main_menu(uid)
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

    status = user[5]
    step = user[6]

    if step == "callsign":

        await message.answer(
            "📝 Введите ваш позывной."
        )
        return

    if step == "unit":

        await message.answer(
            "🏢 Введите подразделение."
        )
        return

    if step == "position":

        await message.answer(
            "🎖 Введите должность."
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
        "📋 <b>Главное меню</b>",
        reply_markup=inline_main_menu(uid)
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
    # УЖЕ ОДОБРЕН
    # -----------------------------------------------------

    if status == "approved":

        await message.answer(
            "📋 Используйте кнопку «☰ Меню»."
        )

        return

    # -----------------------------------------------------
    # ЗАЯВКА УЖЕ ОТПРАВЛЕНА
    # -----------------------------------------------------

    if status == "pending" and step == "done":

        await message.answer(
            "⏳ Ваша заявка уже отправлена администратору."
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
            "🏢 Введите подразделение:"
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
            "🎖 Введите должность:"
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
            "🆕 <b>Новая заявка</b>\n\n"
            f"🆔 ID: {uid}\n"
            f"📛 Позывной: {callsign}\n"
            f"🏢 Подразделение: {unit}\n"
            f"🎖 Должность: {position}",
            reply_markup=keyboard
        )

        await message.answer(
            "✅ Заявка заполнена и отправлена администратору.\n\n"
            "⏳ Ожидайте подтверждения."
        )


# =========================================================
# ОДОБРЕНИЕ
# =========================================================

@dp.callback_query(F.data.startswith("approve_"))
async def approve(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    uid = int(
        call.data.split("_")[1]
    )

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
        "✅ <b>Ваша заявка одобрена!</b>\n\n"
        "Доступ открыт.\n"
        "Для работы используйте кнопку ☰ Меню.",
        reply_markup=main_menu_keyboard()
    )

    await call.message.edit_reply_markup(
        reply_markup=None
    )

    await call.answer("Одобрено")


# =========================================================
# ОТКЛОНЕНИЕ
# =========================================================

@dp.callback_query(F.data.startswith("reject_"))
async def reject(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    uid = int(
        call.data.split("_")[1]
    )

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

    await call.message.edit_reply_markup(
        reply_markup=None
    )

    await call.answer("Отклонено")


# =========================================================
# ИНСТРУКЦИЯ
# =========================================================

@dp.callback_query(F.data == "instructions")
async def instructions(call: CallbackQuery):

    await call.message.answer(
        WELCOME_TEXT
    )

    await call.answer()


# =========================================================
# ГЛАВНОЕ МЕНЮ INLINE
# =========================================================

@dp.callback_query(F.data == "back_menu")
async def back_menu(call: CallbackQuery):

    await call.message.answer(
        "📋 <b>Главное меню</b>",
        reply_markup=inline_main_menu(call.from_user.id)
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
        "📡 <b>Выберите диапазон:</b>",
        reply_markup=keyboard
    )

    await call.answer()


# =========================================================
# КАНАЛЫ ДИАПАЗОНА
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

    # -----------------------------------------------------
    # КНОПКИ КАНАЛОВ
    # -----------------------------------------------------

    for (
        channel_id,
        channel,
        frequency,
        owner,
        expires_at,
        callsign
    ) in rows:

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

        else:

            pilot = callsign or "неизвестен"
            timer = format_time_left(expires_at)

            # Короткая первая строка,
            # чтобы Telegram не обрезал время
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🔴 {channel} • "
                            f"{frequency} MHz"
                        ),
                        callback_data="occupied"
                    )
                ]
            )

            # Отдельная строка с позывным и временем
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"👤 {pilot}  •  "
                            f"⏳ {timer}"
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
        f"📡 <b>Каналы диапазона {band}</b>\n\n"
        "🟢 — свободен\n"
        "🔴 — занят\n"
        "👤 — позывной\n"
        "⏳ — оставшееся время",
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
# ВЫБОР КАНАЛА
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
    # Проверяем существующий канал
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

        await call.message.answer(
            "❌ <b>У вас уже занят канал.</b>\n\n"
            f"📡 {busy[0]}\n"
            f"🎯 {busy[1]}\n"
            f"📶 {busy[2]} MHz\n"
            f"⏳ Осталось: {format_time_left(busy[3])}\n\n"
            "Сначала освободите текущий канал."
        )

        await call.answer()

        return

    # -----------------------------------------------------
    # Получаем выбранный канал
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

    await call.message.answer(
        f"📡 <b>Канал:</b> {channel[1]}\n"
        f"📶 <b>Частота:</b> {channel[2]} MHz\n\n"
        "⏳ <b>На сколько времени занять канал?</b>",
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

    channel_id = int(parts[1])
    minutes = int(parts[2])

    await release_expired_channels()

    # -----------------------------------------------------
    # Проверяем существующий канал пользователя
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

            await call.message.answer(
                "❌ <b>У вас уже занят канал.</b>\n\n"
                f"📡 {busy[0]} • {busy[1]}\n"
                f"📶 {busy[2]} MHz\n"
                f"⏳ Осталось: {format_time_left(busy[3])}"
            )

            await call.answer()

            return

        # -------------------------------------------------
        # Атомарно занимаем канал
        # -------------------------------------------------

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

        cursor = await db.execute(
            """
            SELECT callsign
            FROM users
            WHERE id=?
            """,
            (uid,)
        )

        user = await cursor.fetchone()

    callsign = user[0] if user else "неизвестен"

    await call.message.answer(
        "✅ <b>Канал успешно занят!</b>\n\n"
        f"📡 Диапазон: {ch[0]}\n"
        f"🎯 Канал: {ch[1]}\n"
        f"📶 Частота: {ch[2]} MHz\n"
        f"👤 Позывной: {callsign}\n"
        f"⏳ Срок: {duration_text(minutes)}\n\n"
        "Освободить канал раньше можно через "
        "📍 <b>Мой канал</b>."
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
                    text="📡 К этому диапазону",
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
        "📍 <b>Ваш канал</b>\n\n"
        f"📡 Диапазон: {band}\n"
        f"🎯 Канал: {channel}\n"
        f"📶 Частота: {frequency} MHz\n"
        f"👤 Позывной: {callsign or '—'}\n"
        f"⏳ Осталось: {format_time_left(expires_at)}",
        reply_markup=keyboard
    )

    await call.answer()


# =========================================================
# ОСВОБОДИТЬ СВОЙ КАНАЛ
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
# =========================================================
# АДМИН-ПАНЕЛЬ
# =========================================================
# =========================================================

def admin_keyboard():

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
                    text="🔄 Обновить",
                    callback_data="admin_panel"
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


# =========================================================
# ОТКРЫТЬ АДМИН-ПАНЕЛЬ
# =========================================================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await release_expired_channels()

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users"
        )

        total_users = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE status='approved'
            """
        )

        approved_users = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM channels
            WHERE owner IS NOT NULL
            """
        )

        occupied = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM channels
            WHERE owner IS NULL
            """
        )

        free = (await cursor.fetchone())[0]

    await call.message.answer(
        "⚙️ <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Одобрено: {approved_users}\n"
        f"🔴 Занято каналов: {occupied}\n"
        f"🟢 Свободно каналов: {free}\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard()
    )

    await call.answer()


# =========================================================
# ПОЛЬЗОВАТЕЛИ АДМИНА
# =========================================================

@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

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

        await call.message.answer(
            "📭 Пользователей нет.",
            reply_markup=admin_keyboard()
        )

        await call.answer()

        return

    text = "👥 <b>ПОЛЬЗОВАТЕЛИ</b>\n\n"

    for (
        uid,
        username,
        callsign,
        unit,
        position,
        status
    ) in users:

        if status == "approved":
            status_text = "✅ одобрен"
        elif status == "pending":
            status_text = "⏳ ожидает"
        else:
            status_text = "❌ отклонён"

        text += (
            f"🆔 {uid}\n"
            f"👤 @{username or 'нет'}\n"
            f"📛 {callsign or '—'}\n"
            f"🏢 {unit or '—'}\n"
            f"🎖 {position or '—'}\n"
            f"📌 {status_text}\n\n"
        )

    # Telegram имеет ограничение длины сообщения
    if len(text) > 3900:
        text = text[:3900] + "\n\n…"

    await call.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Админ-панель",
                        callback_data="admin_panel"
                    )
                ]
            ]
        )
    )

    await call.answer()


# =========================================================
# ЗАНЯТЫЕ КАНАЛЫ ДЛЯ АДМИНА
# =========================================================

@dp.callback_query(F.data == "admin_channels")
async def admin_channels(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    await release_expired_channels()

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
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
                ON channels.owner = users.id
            WHERE channels.owner IS NOT NULL
            ORDER BY channels.band,
                     channels.frequency
            """
        )

        rows = await cursor.fetchall()

    if not rows:

        await call.message.answer(
            "📡 Сейчас занятых каналов нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Админ-панель",
                            callback_data="admin_panel"
                        )
                    ]
                ]
            )
        )

        await call.answer()

        return

    keyboard = []

    for (
        channel_id,
        band,
        channel,
        frequency,
        expires_at,
        callsign
    ) in rows:

        pilot = callsign or "неизвестен"
        timer = format_time_left(expires_at)

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🔴 {channel} • "
                        f"{frequency} MHz"
                    ),
                    callback_data=f"admin_ch_{channel_id}"
                )
            ]
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"👤 {pilot} • "
                        f"⏳ {timer}"
                    ),
                    callback_data=f"admin_ch_{channel_id}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Админ-панель",
                callback_data="admin_panel"
            )
        ]
    )

    await call.message.answer(
        "📡 <b>ЗАНЯТЫЕ КАНАЛЫ</b>\n\n"
        "Нажмите на нужный канал для управления:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

    await call.answer()


# =========================================================
# КАРТОЧКА КАНАЛА АДМИНА
# =========================================================

@dp.callback_query(F.data.startswith("admin_ch_"))
async def admin_channel_card(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    channel_id = int(
        call.data.replace(
            "admin_ch_",
            ""
        )
    )

    await release_expired_channels()

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                channels.band,
                channels.channel,
                channels.frequency,
                channels.expires_at,
                channels.owner,
                users.callsign,
                users.unit,
                users.position
            FROM channels
            LEFT JOIN users
                ON channels.owner = users.id
            WHERE channels.id=?
            """,
            (channel_id,)
        )

        row = await cursor.fetchone()

    if not row:

        await call.answer(
            "Канал не найден.",
            show_alert=True
        )

        return

    (
        band,
        channel,
        frequency,
        expires_at,
        owner,
        callsign,
        unit,
        position
    ) = row

    if owner is None:

        await call.message.answer(
            "🟢 Этот канал уже свободен.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Занятые каналы",
                            callback_data="admin_channels"
                        )
                    ]
                ]
            )
        )

        await call.answer()

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Принудительно освободить",
                    callback_data=f"admin_release_{channel_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Занятые каналы",
                    callback_data="admin_channels"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Админ-панель",
                    callback_data="admin_panel"
                )
            ]
        ]
    )

    await call.message.answer(
        "📡 <b>КАНАЛ</b>\n\n"
        f"📡 Диапазон: {band}\n"
        f"🎯 Канал: {channel}\n"
        f"📶 Частота: {frequency} MHz\n\n"
        f"👤 Позывной: {callsign or '—'}\n"
        f"🏢 Подразделение: {unit or '—'}\n"
        f"🎖 Должность: {position or '—'}\n"
        f"🆔 ID пользователя: {owner}\n\n"
        f"⏳ Осталось: {format_time_left(expires_at)}",
        reply_markup=keyboard
    )

    await call.answer()


# =========================================================
# ПРИНУДИТЕЛЬНОЕ ОСВОБОЖДЕНИЕ АДМИНОМ
# =========================================================

@dp.callback_query(F.data.startswith("admin_release_"))
async def admin_release_channel(call: CallbackQuery):

    if not is_admin(call.from_user.id):

        await call.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    channel_id = int(
        call.data.replace(
            "admin_release_",
            ""
        )
    )

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT
                owner,
                channel,
                frequency
            FROM channels
            WHERE id=?
            """,
            (channel_id,)
        )

        channel = await cursor.fetchone()

        if not channel:

            await call.answer(
                "Канал не найден.",
                show_alert=True
            )

            return

        owner = channel[0]

        await db.execute(
            """
            UPDATE channels
            SET owner=NULL,
                expires_at=NULL
            WHERE id=?
            """,
            (channel_id,)
        )

        await db.commit()

    # Сообщаем владельцу
    if owner:

        try:

            await bot.send_message(
                owner,
                "⚠️ <b>Ваш канал был освобождён администратором.</b>\n\n"
                f"🎯 Канал: {channel[1]}\n"
                f"📶 Частота: {channel[2]} MHz"
            )

        except Exception as e:

            print(
                f"Не удалось уведомить пользователя {owner}: {e}"
            )

    await call.message.answer(
        "✅ Канал принудительно освобождён."
    )

    await call.answer(
        "Канал освобождён"
    )


# =========================================================
# /USERS — СТАРАЯ КОМАНДА
# =========================================================

@dp.message(Command("users"))
async def users_command(message: types.Message):

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

    text = "👥 <b>Пользователи:</b>\n\n"

    for (
        uid,
        username,
        callsign,
        unit,
        position,
        status
    ) in users:

        text += (
            f"🆔 {uid}\n"
            f"👤 @{username or 'нет'}\n"
            f"📛 {callsign or '—'}\n"
            f"🏢 {unit or '—'}\n"
            f"🎖 {position or '—'}\n"
            f"📌 {status}\n\n"
        )

    await message.answer(text)


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
            description="Пользователи — администратор"
        )
    ]

    await bot.set_my_commands(
        commands
    )


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
