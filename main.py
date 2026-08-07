import time
import json
import asyncio
import os
import aiosqlite

from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB = "users.db"
with open("frequencies.json", "r", encoding="utf-8") as f:
    FREQUENCIES = json.load(f)

temp_users = {}


async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            callsign TEXT,
            unit TEXT,
            position TEXT,
            status TEXT DEFAULT 'pending'
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            band TEXT,
            channel TEXT,
            frequency INTEGER,
            owner INTEGER DEFAULT NULL
        )
        """)
 
        await db.commit()
        
        try:
            await db.execute(
                "ALTER TABLE channels ADD COLUMN expires_at INTEGER"
            )
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute(
        "ALTER TABLE users ADD COLUMN step TEXT DEFAULT 'done'"
            )
        except aiosqlite.OperationalError:
            pass

        await db.commit()    
        cursor = await db.execute("SELECT COUNT(*) FROM channels")
        count = (await cursor.fetchone())[0]

        if count == 0:
            for band, groups in FREQUENCIES.items():
                for group, channels in groups.items():
                    for channel, freq in channels.items():
                        await db.execute(
                            """
                            INSERT INTO channels (band, channel, frequency)
                            VALUES (?, ?, ?)
                            """,
                            (
                                band,
                                f"{group}-{channel}",
                                freq
                            )
                        )

            await db.commit()

@dp.message(CommandStart())
async def start(message: types.Message):
    uid = message.from_user.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT status FROM users WHERE id=?",
            (uid,)
        )
        user = await cursor.fetchone()

    if user:
        if user[0] == "approved":
            await message.answer(
                "✅ Вы уже зарегистрированы.\nИспользуйте /menu."
            )
        elif user[0] == "pending":
            await message.answer(
                "⏳ Ваша заявка уже отправлена и ожидает проверки."
            )
        elif user[0] == "rejected":
            await message.answer(
                "❌ Ваша заявка была отклонена."
            )
        return
    temp_users[uid] = {}
    
    await message.answer(
        "Для получения доступа заполните заявку.\n\n"
        "Введите ваш позывной:"
    )

@dp.message(F.text & ~F.text.startswith("/"))
async def form(message: types.Message):
    uid = message.from_user.id
    text = message.text.strip()

    async with aiosqlite.connect(DB) as db:

        cursor = await db.execute(
            """
            SELECT callsign, unit, position, status
            FROM users
            WHERE id=?
            """,
            (uid,)
        )

        user = await cursor.fetchone()

        if user:
            callsign, unit, position, status = user

            if status == "approved":
                return

            if callsign is None:
                await db.execute(
                    "UPDATE users SET callsign=? WHERE id=?",
                    (text, uid)
                )
                await db.commit()

                await message.answer("Введите подразделение:")
                return

            if unit is None:
                await db.execute(
                    "UPDATE users SET unit=? WHERE id=?",
                    (text, uid)
                )
                await db.commit()

                await message.answer("Введите должность:")
                return

            if position is None:
                await db.execute(
                    """
                    UPDATE users
                    SET position=?
                    WHERE id=?
                    """,
                    (text, uid)
                )
                await db.commit()

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
                    f"Новая заявка:\n\n"
                    f"ID: {uid}\n"
                    f"Позывной: {callsign}\n"
                    f"Подразделение: {unit}\n"
                    f"Должность: {text}",
                    reply_markup=keyboard
                )

                await message.answer(
                    "✅ Заявка отправлена администратору."
                )

                return

        else:
            await db.execute(
                """
                INSERT INTO users
                (id, username, callsign, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (
                    uid,
                    message.from_user.username,
                    text
                )
            )

            await db.commit()

            await message.answer(
                "Введите подразделение:"
            )
@dp.callback_query(F.data.startswith("approve_"))
async def approve(call: CallbackQuery):
    uid = int(call.data.split("_")[1])

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE users SET status='approved' WHERE id=?",
            (uid,)
        )
        await db.commit()

    await bot.send_message(
        uid,
        "✅ Ваша заявка одобрена. Доступ открыт."
    )

    await call.answer("Одобрено")


@dp.callback_query(F.data.startswith("reject_"))
async def reject(call: CallbackQuery):
    uid = int(call.data.split("_")[1])

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE users SET status='rejected' WHERE id=?",
            (uid,)
        )
        await db.commit()

    await bot.send_message(
        uid,
        "❌ Ваша заявка отклонена."
    )

    await call.answer("Отклонено")
@dp.message(Command("users"))
async def users_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("""
            SELECT id, callsign, unit, position, status
            FROM users
            ORDER BY callsign
        """)
        users = await cursor.fetchall()

    if not users:
        await message.answer("📭 Пользователей нет.")
        return

    text = "👥 Список пользователей:\n\n"

    for uid, callsign, unit, position, status in users:
        text += (
            f"🆔 {uid}\n"
            f"📛 {callsign}\n"
            f"🏢 {unit}\n"
            f"🎖 {position}\n"
            f"📌 {status}\n\n"
        )

    await message.answer(text)
@dp.message(Command("menu"))
async def menu(message: types.Message):
    uid = message.from_user.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT status FROM users WHERE id=?",
            (uid,)
        )
        user = await cursor.fetchone()

    if not user:
        await message.answer(
            "Вы ещё не подавали заявку."
        )
        return

    if user[0] != "approved":
        await message.answer(
            "Ваша заявка ещё не одобрена."
        )
        return

    keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📋 Открыть раздел",
                callback_data="open_section"
            )
        ],
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
        ]
    ]
    )

    await message.answer(
        "Доступ открыт.\nВыберите раздел:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "open_section")
async def open_section(call: CallbackQuery):
    uid = call.from_user.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT status FROM users WHERE id=?",
            (uid,)
        )
        user = await cursor.fetchone()

    if not user or user[0] != "approved":
        await call.message.answer(
            "Нет доступа."
        )
        return

    await call.message.answer(
        "Раздел доступен."
    )

    await call.answer()
    
@dp.callback_query(F.data == "frequencies")
async def frequencies(call: CallbackQuery):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1.2 GHz", callback_data="band_1.2GHz")],
            [InlineKeyboardButton(text="1.5 GHz", callback_data="band_1.5GHz")],
            [InlineKeyboardButton(text="2.4 GHz", callback_data="band_2.4GHz")],
            [InlineKeyboardButton(text="3.3 GHz", callback_data="band_3.3GHz")],
            [InlineKeyboardButton(text="3.7 GHz", callback_data="band_3.7GHz")],
            [InlineKeyboardButton(text="5.8 GHz", callback_data="band_5.8GHz")]
        ]
    )

    await call.message.answer(
        "Выберите диапазон:",
        reply_markup=keyboard
    )

    await call.answer()
@dp.callback_query(F.data.startswith("band_"))
async def show_band(call: CallbackQuery):
    band = call.data.replace("band_", "")

    keyboard = []

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT id, channel, frequency, owner, expires_at
            FROM channels
            WHERE band=?
            ORDER BY frequency
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

    for channel_id, channel, frequency, owner, expires_at in rows:

        # Свободный канал
        if owner is None:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🟢 {channel} • {frequency} MHz",
                    callback_data=f"take_{channel_id}"
                )
            ])

        # Занятый канал
        else:
            if expires_at:
                left = expires_at - int(time.time())

                if left < 0:
                    left = 0

                minutes = left // 60
                seconds = left % 60

                timer = f"{minutes} мин {seconds} сек"
            else:
                timer = "∞"

            keyboard.append([
                InlineKeyboardButton(
                    text=f"🔴 {channel} • {frequency} MHz • {timer}",
                    callback_data="occupied"
                )
            ])

    await call.message.answer(
        f"📡 Каналы диапазона {band}:\n\n"
        "🟢 — свободен\n"
        "🔴 — занят",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )
    await call.answer(
        
 @dp.callback_query(F.data == "occupied")
async def occupied(call: CallbackQuery):
    await call.answer(
        "🔴 Этот канал уже занят.",
        show_alert=True
    )   
@dp.callback_query(F.data.startswith("take_"))
async def take_channel(call: CallbackQuery):
    uid = call.from_user.id
    channel_id = int(call.data.replace("take_", ""))

    async with aiosqlite.connect(DB) as db:

        # Проверяем, есть ли уже занятый канал
        cursor = await db.execute(
            """
            SELECT band, channel, frequency
            FROM channels
            WHERE owner=?
            """,
            (uid,)
        )
        busy = await cursor.fetchone()

        if busy:
            await call.message.answer(
                f"❌ У вас уже занят канал {busy[0]} {busy[1]} ({busy[2]} MHz).\n"
                "Сначала освободите его."
            )
            await call.answer()
            return

        # Пытаемся занять канал
        expire = int(time.time()) + 3600

        cursor = await db.execute(
            """
            UPDATE channels
            SET owner=?, expires_at=?
            WHERE id=? AND owner IS NULL
            """,
            (uid, expire, channel_id)
       )

        # Если канал уже успел занять другой человек
        if cursor.rowcount == 0:
            await call.message.answer("❌ Этот канал уже занят.")
            await call.answer()
            return

        await db.commit()

        cursor = await db.execute(
            """
            SELECT band, channel, frequency
            FROM channels
            WHERE id=?
            """,
            (channel_id,)
        )

        ch = await cursor.fetchone()

    await call.message.answer(
        f"✅ Вы заняли канал:\n{ch[0]} • {ch[1]} • {ch[2]} MHz"
    )

    await call.answer()
@dp.callback_query(F.data == "my_channel")
async def my_channel(call: CallbackQuery):
    uid = call.from_user.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            SELECT band, channel, frequency, expires_at
            FROM channels
            WHERE owner=?
            """,
            (uid,)
        )

        row = await cursor.fetchone()

    if not row:
        await call.message.answer("❌ У вас нет занятого канала.")
        await call.answer()
        return

    band, channel, frequency, expires_at = row

    if expires_at:
        left = expires_at - int(time.time())

        if left < 0:
            left = 0

        minutes = left // 60
        seconds = left % 60

        timer = f"{minutes} мин {seconds} сек"
    else:
        timer = "∞"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Освободить канал",
                    callback_data="release_channel"
                )
            ]
        ]
    )

    await call.message.answer(
        f"📍 Ваш канал:\n\n"
        f"📡 Диапазон: {band}\n"
        f"🎯 Канал: {channel}\n"
        f"📶 Частота: {frequency} MHz\n"
        f"⏳ Осталось: {timer}",
        reply_markup=keyboard
    )

    await call.answer()
@dp.callback_query(F.data == "release_channel")
async def release_channel(call: CallbackQuery):
    uid = call.from_user.id

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """
            UPDATE channels
            SET owner = NULL,
                expires_at = NULL
            WHERE owner = ?
            """,
            (uid,)
        )

        await db.commit()

        if cursor.rowcount == 0:
            await call.message.answer(
                "❌ У вас нет занятого канала."
            )
        else:
            await call.message.answer(
                "✅ Канал успешно освобождён."
            )

    await call.answer()
async def auto_release():
    while True:
        async with aiosqlite.connect(DB) as db:
            now = int(time.time())

            await db.execute(
                """
                UPDATE channels
                SET owner = NULL,
                    expires_at = NULL
                WHERE expires_at IS 
        NOT NULL
                AND expires_at <= ?
                """,
                (now,)
            )

            await db.commit()

        await asyncio.sleep(60)
async def health(request):
    return web.Response(text="Bot is running")


async def main():
    await init_db()

    asyncio.create_task(auto_release())

    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
