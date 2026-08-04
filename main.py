import asyncio
import os
import aiosqlite

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


@dp.message(F.text)
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
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
