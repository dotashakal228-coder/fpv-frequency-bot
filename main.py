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
        await db.commit()


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "Для получения доступа заполните заявку.\n\n"
        "Введите ваш позывной:"
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def form(message: types.Message):

    uid = message.from_user.id

    if uid not in temp_users:
        temp_users[uid] = {
            "callsign": message.text
        }
        await message.answer("Введите подразделение:")
        return

    if "unit" not in temp_users[uid]:
        temp_users[uid]["unit"] = message.text
        await message.answer("Введите должность:")
        return

    temp_users[uid]["position"] = message.text

    data = temp_users[uid]

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO users
            (id, username, callsign, unit, position)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                uid,
                message.from_user.username,
                data["callsign"],
                data["unit"],
                data["position"]
            )
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
        f"Позывной: {data['callsign']}\n"
        f"Подразделение: {data['unit']}\n"
        f"Должность: {data['position']}",
        reply_markup=keyboard
    )

    await message.answer(
        "Заявка отправлена администратору. Ожидайте решения."
    )

    del temp_users[uid]


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

    if band not in FREQUENCIES:
        await call.message.answer("Диапазон не найден.")
        await call.answer()
        return

    text = f"📡 {band}\n\n"

    for group, channels in FREQUENCIES[band].items():
        text += f"🔹 {group}\n"

        for ch, freq in channels.items():
            text += f"{ch}: {freq} MHz\n"

        text += "\n"

    await call.message.answer(text)
    await call.answer()
async def health(request):
    return web.Response(text="Bot is running")


async def main():
    await init_db()

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
