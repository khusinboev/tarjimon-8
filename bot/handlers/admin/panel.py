from aiogram import Router, F
import logging
import re
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from bot.config.settings import settings
from bot.database.session import AsyncSessionLocal
from bot.keyboards.admin import (
    admin_main_keyboard,
    admin_channels_keyboard,
    admin_broadcast_keyboard,
    back_keyboard,
)
from bot.services.admin_service import AdminService
from bot.states.admin import AdminStates


router = Router()
logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_USER_IDS


@router.message(Command("developer"))
async def developer_info(message: Message):
    await message.answer("Bot dasturchisi: @coder_admin_py")


@router.message(Command("admin"), F.from_user.func(lambda u: u and is_admin(u.id)))
@router.message(Command("panel"), F.from_user.func(lambda u: u and is_admin(u.id)))
async def open_admin_panel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Admin panelga xush kelibsiz.", reply_markup=admin_main_keyboard())


@router.message(F.text == "🔙 Orqaga", F.from_user.func(lambda u: u and is_admin(u.id)))
async def go_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bosh menyu", reply_markup=admin_main_keyboard())


@router.message(F.text == "📊 Statistika", F.from_user.func(lambda u: u and is_admin(u.id)))
async def show_stats(message: Message):
    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        stats = await service.get_stats()

    attempts = stats["attempts_day"]
    error_pct = (stats["errors_day"] / attempts * 100) if attempts else 0.0

    lines = [
        "📊 <b>Statistika</b>",
        "",
        "<b>Foydalanuvchilar</b>",
        f"Jami: {stats['total_users']:,}".replace(",", " "),
        f"Bugun yangi: {stats['new_day']}",
        f"Bugun aktiv: {stats['active_day']}",
        f"7 kun aktiv: {stats['active_week']}",
        f"Botni bloklagan: {stats['blocked']}",
        "",
        "<b>Tarjimalar</b>",
        f"Jami: {stats['translations_total']:,}".replace(",", " "),
        f"Bugun: {stats['translations_day']}",
        f"Xatolik ulushi (bugun): {error_pct:.1f}%",
    ]

    if stats["top_pairs"]:
        lines.append("")
        lines.append("<b>Ommabop yo'nalishlar</b>")
        for src, dst, count in stats["top_pairs"]:
            lines.append(f"{src or '?'} → {dst}: {count:,}".replace(",", " "))

    lines.append("")
    lines.append(f"Aktiv kanallar: {stats['active_channels']}")

    await message.answer("\n".join(lines), reply_markup=admin_main_keyboard())


@router.message(F.text == "🔧 Kanallar", F.from_user.func(lambda u: u and is_admin(u.id)))
async def open_channels(message: Message):
    await message.answer("Kanal boshqaruvi", reply_markup=admin_channels_keyboard())


@router.message(F.text == "➕ Kanal qo'shish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_add_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AdminStates.waiting_channel_add_button_text)
    await message.answer(
        "1/3 Kanal tugmasi nomini yuboring.\nMasalan: \"📢 Rasmiy kanal\"",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_channel_add_button_text, F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_add_button_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Tugma nomi bo'sh bo'lmasin.", reply_markup=back_keyboard())
        return

    await state.update_data(channel_button_text=text)
    await state.set_state(AdminStates.waiting_channel_add_username)
    await message.answer(
        "2/3 Kanal username yuboring.\nMasalan: @mychannel",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_channel_add_username, F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_add_username(message: Message, state: FSMContext):
    username = (message.text or "").strip()
    if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", username):
        await message.answer(
            "Username formati noto'g'ri. Masalan: @mychannel",
            reply_markup=back_keyboard(),
        )
        return

    await state.update_data(channel_username=username)
    await state.set_state(AdminStates.waiting_channel_add_button_url)
    await message.answer(
        "3/3 Tugma uchun havolani yuboring.\nMasalan: https://t.me/mychannel",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_channel_add_button_url, F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_add_finish(message: Message, state: FSMContext, user):
    data = await state.get_data()
    button_text = data.get("channel_button_text", "")
    username = data.get("channel_username", "")
    button_url = (message.text or "").strip()

    try:
        async with AsyncSessionLocal() as session:
            service = AdminService(session, message.bot)
            ok, result = await service.add_channel(
                button_text=button_text,
                channel_username=username,
                button_url=button_url,
                # FK `users.id` ga qaraydi, `telegram_id` ga emas.
                added_by=user.id,
            )
    except Exception as exc:
        logger.exception("Unexpected error in channel_add_finish")
        await message.answer(
            "Kanal qo'shishda kutilmagan xatolik yuz berdi. Qayta urinib ko'ring.",
            reply_markup=back_keyboard(),
        )
        return

    await state.clear()
    await message.answer(result, reply_markup=admin_channels_keyboard() if ok else back_keyboard())


@router.message(F.text == "❌ Kanalni olib tashlash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_remove_start(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_channel_remove)
    await message.answer(
        "O'chiriladigan kanal username (@kanal) yoki ID yuboring.",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_channel_remove, F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_remove_finish(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        ok, result = await service.remove_channel(message.text or "")

    await state.clear()
    await message.answer(result, reply_markup=admin_channels_keyboard() if ok else back_keyboard())


@router.message(F.text == "📋 Kanallar ro'yxati", F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_list(message: Message):
    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        text = await service.list_channels_text()

    await message.answer(text, reply_markup=admin_channels_keyboard())


@router.message(F.text == "📤 Reklama", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_menu(message: Message):
    await message.answer("Reklama bo'limi", reply_markup=admin_broadcast_keyboard())


@router.message(F.text == "📨 Forward xabar yuborish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_forward_start(message: Message, state: FSMContext):
    await state.update_data(broadcast_mode="forward")
    await state.set_state(AdminStates.waiting_broadcast_message)
    await message.answer("Forward qilinadigan xabarni yuboring.", reply_markup=back_keyboard())


@router.message(F.text == "📬 Oddiy xabar yuborish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_copy_start(message: Message, state: FSMContext):
    await state.update_data(broadcast_mode="copy")
    await state.set_state(AdminStates.waiting_broadcast_message)
    await message.answer("Yuboriladigan xabarni yuboring.", reply_markup=back_keyboard())


@router.message(AdminStates.waiting_broadcast_message, F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_send(message: Message, state: FSMContext, user):
    data = await state.get_data()
    mode = data.get("broadcast_mode")

    if mode not in {"copy", "forward"}:
        await state.clear()
        await message.answer("Broadcast rejimi topilmadi. Qayta urinib ko'ring.", reply_markup=admin_broadcast_keyboard())
        return

    progress_message = await message.answer("Yuborish boshlandi, iltimos kuting...")

    async def progress_callback(processed: int, total: int, success: int, failed: int):
        try:
            await progress_message.edit_text(
                "Reklama yuborilmoqda...\n"
                f"Progress: {processed}/{total}\n"
                f"Muvaffaqiyatli: {success}\n"
                f"Xatolik: {failed}"
            )
        except TelegramBadRequest:
            # Message can be unchanged or no longer editable, safe to ignore.
            pass

    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        result = await service.run_broadcast(
            admin_id=user.id,
            source_message=message,
            mode=mode,
            progress_callback=progress_callback,
        )

    await state.clear()
    await message.answer(
        (
            "Reklama yakunlandi.\n"
            f"Broadcast ID: {result['broadcast_id']}\n"
            f"Status: {result['status']}\n"
            f"Jami: {result['total']}\n"
            f"Qayta ishlangan: {result['processed']}\n"
            f"Muvaffaqiyatli: {result['success']}\n"
            f"Xatolik: {result['failed']}"
        ),
        reply_markup=admin_broadcast_keyboard(),
    )


@router.message(F.text == "⛔ Broadcastni to'xtatish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_cancel_start(message: Message, state: FSMContext):
    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        text = await service.get_running_broadcasts_text()

    await state.set_state(AdminStates.waiting_broadcast_cancel_id)
    await message.answer(
        f"{text}\n\nTo'xtatish uchun broadcast ID yuboring.",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_broadcast_cancel_id, F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_cancel_finish(message: Message, state: FSMContext):
    try:
        broadcast_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Iltimos, numeric broadcast ID yuboring.", reply_markup=back_keyboard())
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        cancelled = await service.request_broadcast_cancel(broadcast_id)

    await state.clear()
    if cancelled:
        await message.answer("Bekor qilish so'rovi yuborildi. Yuborish sikli yaqin daqiqalarda to'xtaydi.", reply_markup=admin_broadcast_keyboard())
    else:
        await message.answer("Running holatdagi shu ID topilmadi.", reply_markup=admin_broadcast_keyboard())
