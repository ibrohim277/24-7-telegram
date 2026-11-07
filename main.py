"""
Ko'p foydalanuvchili "24/7 Online" Telegram boti.

Foydalanuvchi botga kirib, o'z akkauntini ulaydi (telefon -> kod -> kerak bo'lsa parol),
bot esa uning profilini doim "online" qilib turadi.

Ishga tushirish:  python bot.py
Sozlamalar: .env.example faylga qarang.
"""

import asyncio
import logging
import os
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest

load_dotenv()

# ----------------------------------------------------------------------------
# SOZLAMALAR
# ----------------------------------------------------------------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]  # Fernet kalit (README ga qarang)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "users.db")
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "30"))

# ----------------------------------------------------------------------------
# TUGMA DIZAYNI  (shu yerni o'zingiz tahrirlaysiz)
#
# STYLES:    "primary" (ko'k) | "success" (yashil) | "danger" (qizil) | None (oddiy)
# EMOJI_IDS: premium emoji ID (matn ko'rinishida). Bo'sh qoldirsangiz emoji qo'yilmaydi.
#            Eslatma: icon_custom_emoji_id faqat bot egasida Telegram Premium bo'lsa
#            (yoki bot Fragment'da username sotib olgan bo'lsa) ishlaydi.
# ----------------------------------------------------------------------------
STYLES = {
    "connect": "success",
    "agree": "success",
    "cancel": "danger",
    "status": "primary",
    "start": "success",
    "stop": "danger",
    "logout": "danger",
    "logout_yes": "danger",
    "logout_no": "primary",
    "help": None,
}

EMOJI_IDS = {
    "connect": "",
    "agree": "",
    "cancel": "",
    "status": "",
    "start": "",
    "stop": "",
    "logout": "",
    "logout_yes": "",
    "logout_no": "",
    "help": "",
}

# ----------------------------------------------------------------------------
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("online-bot")

fernet = Fernet(ENCRYPTION_KEY.encode())
router = Router()
TZ = timezone(timedelta(hours=5))  # Toshkent

workers: dict[int, asyncio.Task] = {}
last_ping: dict[int, datetime] = {}
pending: dict[int, dict] = {}  # login jarayonidagi vaqtinchalik klientlar


class Login(StatesGroup):
    phone = State()
    code = State()
    password = State()


# ----------------------------------------------------------------------------
# Tugmalar
# ----------------------------------------------------------------------------
def kb(*rows) -> InlineKeyboardMarkup:
    """Har bir qator: [(kalit, matn, callback_data), ...]"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=data,
                    style=STYLES.get(key),
                    icon_custom_emoji_id=EMOJI_IDS.get(key) or None,
                )
                for key, text, data in row
            ]
            for row in rows
        ]
    )


async def menu(uid: int) -> InlineKeyboardMarkup:
    user = await db_get(uid)
    if not user:
        return kb([("connect", "Akkauntni ulash", "connect")], [("help", "Yordam", "help")])
    toggle = (
        ("stop", "To'xtatish", "stop") if user["active"] else ("start", "Yoqish", "start")
    )
    return kb(
        [("status", "Holat", "status"), toggle],
        [("logout", "Akkauntni uzish", "logout")],
        [("help", "Yordam", "help")],
    )


# ----------------------------------------------------------------------------
# Baza
# ----------------------------------------------------------------------------
async def db_init() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                session TEXT NOT NULL,
                phone TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )"""
        )
        await db.commit()


async def db_get(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
        return await cur.fetchone()


async def db_save(uid: int, session: str, phone: str) -> None:
    enc = fernet.encrypt(session.encode()).decode()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, session, phone, active, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (uid, enc, phone, datetime.now(TZ).isoformat()),
        )
        await db.commit()


async def db_set_active(uid: int, active: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET active = ? WHERE user_id = ?", (int(active), uid))
        await db.commit()


async def db_delete(uid: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (uid,))
        await db.commit()


async def db_active_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE active = 1")
        return await cur.fetchall()


def decrypt(row) -> str:
    return fernet.decrypt(row["session"].encode()).decode()


# ----------------------------------------------------------------------------
# Online ushlab turuvchi ishchilar
# ----------------------------------------------------------------------------
async def run_worker(bot: Bot, uid: int, session: str) -> None:
    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise AuthKeyUnregisteredError(request=None)
        while True:
            try:
                if not client.is_connected():
                    await client.connect()
                await client(UpdateStatusRequest(offline=False))
                last_ping[uid] = datetime.now(TZ)
                await asyncio.sleep(PING_INTERVAL)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError):
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("[%s] xato: %s", uid, e)
                await asyncio.sleep(15)
    except asyncio.CancelledError:
        raise
    except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError):
        log.info("[%s] sessiya yaroqsiz bo'ldi", uid)
        await db_delete(uid)
        with suppress(Exception):
            await bot.send_message(
                uid,
                "⚠️ Akkauntingiz sessiyasi tugatildi (Telegram'dan chiqarilgan bo'lishi mumkin). "
                "Qaytadan ulash uchun /start bosing.",
            )
    finally:
        last_ping.pop(uid, None)
        with suppress(Exception):
            await client.disconnect()


def start_worker(bot: Bot, uid: int, session: str) -> None:
    task = workers.get(uid)
    if task and not task.done():
        task.cancel()
    workers[uid] = asyncio.create_task(run_worker(bot, uid, session))


async def stop_worker(uid: int) -> None:
    task = workers.pop(uid, None)
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


# ----------------------------------------------------------------------------
# Matnlar
# ----------------------------------------------------------------------------
WELCOME = (
    "👋 <b>Salom!</b>\n\n"
    "Men sizning Telegram profilingizni <b>24/7 online</b> qilib turaman.\n"
    "Boshlash uchun akkauntingizni ulang."
)

CONSENT = (
    "🔐 <b>Muhim ma'lumot</b>\n\n"
    "Online holatini o'zgartirish uchun bot sizning akkauntingiz nomidan ulanishi kerak. "
    "Buning uchun telefon raqamingiz, Telegram yuborgan kod (va agar bo'lsa, 2 bosqichli parol) so'raladi.\n\n"
    "• Sessiya <b>shifrlangan</b> holda saqlanadi.\n"
    "• Bot xabarlaringizni o'qimaydi va hech narsa yubormaydi, faqat online holatini yangilaydi.\n"
    "• Istalgan payt <b>«Akkauntni uzish»</b> orqali sessiyani o'chirib tashlashingiz mumkin.\n"
    "• Telegram → Sozlamalar → Qurilmalar bo'limidan ham sessiyani tugatishingiz mumkin.\n\n"
    "Rozimisiz?"
)

HELP = (
    "ℹ️ <b>Yordam</b>\n\n"
    "1. «Akkauntni ulash» tugmasini bosing.\n"
    "2. Telefon raqamingizni <code>+998901234567</code> ko'rinishida yuboring.\n"
    "3. Telegram yuborgan kodni <b>raqamlar orasiga bo'sh joy qo'yib</b> yozing: "
    "<code>1 2 3 4 5</code> (oddiy yozilsa Telegram kodni bloklaydi).\n"
    "4. 2 bosqichli parol bo'lsa, uni yuboring (xabar darrov o'chiriladi).\n\n"
    "Buyruqlar: /start, /cancel"
)


# ----------------------------------------------------------------------------
# Handlerlar
# ----------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await drop_pending(message.from_user.id)
    await state.clear()
    await message.answer(WELCOME, reply_markup=await menu(message.from_user.id))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await drop_pending(message.from_user.id)
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=await menu(message.from_user.id))


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur.fetchone())[0]
    running = sum(1 for t in workers.values() if not t.done())
    await message.answer(f"👥 Jami: <b>{total}</b>\n🟢 Hozir ishlayapti: <b>{running}</b>")


@router.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(HELP, reply_markup=await menu(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data == "connect")
async def cb_connect(cb: CallbackQuery):
    await cb.message.edit_text(
        CONSENT,
        reply_markup=kb([("agree", "Roziman", "agree"), ("cancel", "Bekor qilish", "cancel")]),
    )
    await cb.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await drop_pending(cb.from_user.id)
    await state.clear()
    await cb.message.edit_text(WELCOME, reply_markup=await menu(cb.from_user.id))
    await cb.answer("Bekor qilindi")


@router.callback_query(F.data == "agree")
async def cb_agree(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Login.phone)
    await cb.message.edit_text(
        "📱 Telefon raqamingizni xalqaro formatda yuboring:\n<code>+998901234567</code>",
        reply_markup=kb([("cancel", "Bekor qilish", "cancel")]),
    )
    await cb.answer()


@router.message(Login.phone)
async def got_phone(message: Message, state: FSMContext):
    uid = message.from_user.id
    phone = re.sub(r"[\s\-()]", "", message.text or "")
    if not re.fullmatch(r"\+?\d{9,15}", phone):
        await message.answer("❌ Raqam noto'g'ri. Masalan: <code>+998901234567</code>")
        return
    if not phone.startswith("+"):
        phone = "+" + phone

    await drop_pending(uid)
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        await client.disconnect()
        await message.answer("❌ Bunday telefon raqam yo'q. Qayta yuboring.")
        return
    except FloodWaitError as e:
        await client.disconnect()
        await message.answer(f"⏳ Juda ko'p urinish. {e.seconds} soniyadan keyin urinib ko'ring.")
        return
    except ApiIdInvalidError:
        await client.disconnect()
        log.error("TG_API_ID / TG_API_HASH noto'g'ri!")
        await message.answer("⚠️ Botda texnik nosozlik. Keyinroq urinib ko'ring.")
        await state.clear()
        return
    except Exception as e:
        await client.disconnect()
        log.exception("send_code xatosi")
        await message.answer(f"⚠️ Xatolik: {e}")
        return

    pending[uid] = {"client": client, "phone": phone, "hash": sent.phone_code_hash}
    await state.set_state(Login.code)
    await message.answer(
        "✉️ Telegram'dan kod keldi.\n\n"
        "Kodni <b>raqamlar orasiga bo'sh joy qo'yib</b> yuboring:\n<code>1 2 3 4 5</code>\n\n"
        "<i>(Oddiy yozilsa Telegram xavfsizlik uchun kodni bekor qiladi.)</i>",
        reply_markup=kb([("cancel", "Bekor qilish", "cancel")]),
    )


@router.message(Login.code)
async def got_code(message: Message, state: FSMContext):
    uid = message.from_user.id
    data = pending.get(uid)
    with suppress(Exception):
        await message.delete()
    if not data:
        await state.clear()
        await message.answer("Sessiya tugagan. /start bosing.")
        return

    code = re.sub(r"\D", "", message.text or "")
    if not code:
        await message.answer("❌ Kodni raqamlar bilan yuboring: <code>1 2 3 4 5</code>")
        return

    client: TelegramClient = data["client"]
    try:
        await client.sign_in(phone=data["phone"], code=code, phone_code_hash=data["hash"])
    except SessionPasswordNeededError:
        await state.set_state(Login.password)
        await message.answer(
            "🔑 Akkauntingizda 2 bosqichli parol bor. Parolni yuboring "
            "(xabar darrov o'chiriladi).",
            reply_markup=kb([("cancel", "Bekor qilish", "cancel")]),
        )
        return
    except PhoneCodeInvalidError:
        await message.answer("❌ Kod noto'g'ri. Qayta yuboring.")
        return
    except PhoneCodeExpiredError:
        await drop_pending(uid)
        await state.clear()
        await message.answer("⌛ Kod eskirdi. /start bosib qaytadan urining.")
        return
    except FloodWaitError as e:
        await message.answer(f"⏳ {e.seconds} soniyadan keyin urinib ko'ring.")
        return
    except Exception as e:
        log.exception("sign_in xatosi")
        await message.answer(f"⚠️ Xatolik: {e}")
        return

    await finish_login(message, state, uid)


@router.message(Login.password)
async def got_password(message: Message, state: FSMContext):
    uid = message.from_user.id
    data = pending.get(uid)
    password = message.text or ""
    with suppress(Exception):
        await message.delete()
    if not data:
        await state.clear()
        await message.answer("Sessiya tugagan. /start bosing.")
        return
    try:
        await data["client"].sign_in(password=password)
    except PasswordHashInvalidError:
        await message.answer("❌ Parol noto'g'ri. Qayta yuboring.")
        return
    except FloodWaitError as e:
        await message.answer(f"⏳ {e.seconds} soniyadan keyin urinib ko'ring.")
        return
    except Exception as e:
        log.exception("parol xatosi")
        await message.answer(f"⚠️ Xatolik: {e}")
        return
    await finish_login(message, state, uid)


async def finish_login(message: Message, state: FSMContext, uid: int) -> None:
    data = pending.pop(uid)
    client: TelegramClient = data["client"]
    session = client.session.save()
    await client.disconnect()
    await db_save(uid, session, data["phone"])
    start_worker(message.bot, uid, session)
    await state.clear()
    await message.answer(
        "✅ <b>Ulandi!</b> Endi profilingiz doim online turadi.",
        reply_markup=await menu(uid),
    )


@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery):
    uid = cb.from_user.id
    task = workers.get(uid)
    if task and not task.done():
        ping = last_ping.get(uid)
        when = ping.strftime("%H:%M:%S") if ping else "hozirgina"
        text = f"🟢 <b>Ishlayapti</b>\nOxirgi signal: <code>{when}</code> (Toshkent vaqti)"
    else:
        text = "🔴 <b>To'xtatilgan</b>\n«Yoqish» tugmasini bosing."
    await cb.message.edit_text(text, reply_markup=await menu(uid))
    await cb.answer()


@router.callback_query(F.data == "stop")
async def cb_stop(cb: CallbackQuery):
    uid = cb.from_user.id
    await stop_worker(uid)
    await db_set_active(uid, False)
    await cb.message.edit_text("⏹ To'xtatildi.", reply_markup=await menu(uid))
    await cb.answer()


@router.callback_query(F.data == "start")
async def cb_start_worker(cb: CallbackQuery):
    uid = cb.from_user.id
    user = await db_get(uid)
    if not user:
        await cb.answer("Avval akkauntni ulang", show_alert=True)
        return
    await db_set_active(uid, True)
    start_worker(cb.bot, uid, decrypt(user))
    await cb.message.edit_text("▶️ Yoqildi.", reply_markup=await menu(uid))
    await cb.answer()


@router.callback_query(F.data == "logout")
async def cb_logout(cb: CallbackQuery):
    await cb.message.edit_text(
        "❗ Akkauntingiz botdan butunlay uziladi va saqlangan sessiya o'chiriladi. Davom etasizmi?",
        reply_markup=kb(
            [("logout_yes", "Ha, uzish", "logout_yes"), ("logout_no", "Yo'q", "logout_no")]
        ),
    )
    await cb.answer()


@router.callback_query(F.data == "logout_no")
async def cb_logout_no(cb: CallbackQuery):
    await cb.message.edit_text(WELCOME, reply_markup=await menu(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data == "logout_yes")
async def cb_logout_yes(cb: CallbackQuery):
    uid = cb.from_user.id
    user = await db_get(uid)
    await stop_worker(uid)
    if user:
        client = TelegramClient(StringSession(decrypt(user)), API_ID, API_HASH)
        with suppress(Exception):
            await client.connect()
            await client.log_out()  # Telegram tomonda sessiyani tugatadi
        with suppress(Exception):
            await client.disconnect()
    await db_delete(uid)
    await cb.message.edit_text(
        "✅ Akkauntingiz uzildi, sessiya o'chirildi.", reply_markup=await menu(uid)
    )
    await cb.answer()


async def drop_pending(uid: int) -> None:
    data = pending.pop(uid, None)
    if data:
        with suppress(Exception):
            await data["client"].disconnect()


# ----------------------------------------------------------------------------
async def start_health_server():
    """Render kabi hostinglar Web Service uchun port talab qiladi.
    PORT o'zgaruvchisi bo'lsa, kichik HTTP server ochamiz (UptimeRobot ham shuni ping qiladi)."""
    port = int(os.environ.get("PORT", "0"))
    if not port:
        return None

    async def ok(_request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health server %s-portda ishga tushdi", port)
    return runner


async def main() -> None:
    health = await start_health_server()
    await db_init()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Qayta ishga tushganda avval ulangan foydalanuvchilarni tiklaymiz
    users = await db_active_users()
    for row in users:
        start_worker(bot, row["user_id"], decrypt(row))
    log.info("Tiklangan foydalanuvchilar: %d", len(users))

    try:
        await dp.start_polling(bot)
    finally:
        for uid in list(workers):
            await stop_worker(uid)
        await bot.session.close()
        if health:
            await health.cleanup()


if __name__ == "__main__":
    asyncio.run(main())