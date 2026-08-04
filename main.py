import asyncio
import os
import aiosqlite

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB = "users.db"


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
        "Заявка на доступ.\n\n"
        "Введите ваш позывной:"
    )
    await message.answer(
        "После позывного напишите подразделение и должность."
    )


user_data = {}


@dp.message(F.text)
async def register(message: types.Message):
    user_id = message.from_user.id

    if user_id not in user_data:
        user_data[user_id] = {
            "callsign": message.text
        }
        await message.answer(
            "Теперь укажите подразделение:"
        )
        return

    if "unit" not in user_data[user_id]:
        user_data[user_id]["unit"] = message.text
        await message.answer(
            "Теперь укажите должность:"
        )
        return

    if "position" not in user_data[user_id]:
        user_data[user_id]["position"] = message.text

        data = user_data[user_id]

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO users
                (id, username, callsign, unit, position)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    message.from_user.username,
                    data["callsign"],
                    data["unit"],
                    data["position"]
                )
            )
            await db.commit()

        await message.answer(
            "Заявка отправлена. Ожидайте подтверждения."
        )

        await bot.send_message(
            ADMIN_ID,
            f"Новая заявка:\n\n"
            f"ID: {user_id}\n"
            f"Позывной: {data['callsign']}\n"
            f"Подразделение: {data['unit']}\n"
            f"Должность: {data['position']}"
        )

        del user_data[user_id]


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
