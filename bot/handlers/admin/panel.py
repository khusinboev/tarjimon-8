from aiogram import Router, F
import logging
import re
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from bot.config.settings import settings
from bot.database.repositories.broadcast_repository import BroadcastRepository
from bot.database.session import AsyncSessionLocal
from bot.keyboards.admin import (
    admin_main_keyboard,
    admin_channels_keyboard,
    admin_broadcast_keyboard,
    admin_users_keyboard,
    admin_user_actions_keyboard,
    broadcast_confirm_keyboard,
    back_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.stats import StatsService, render as render_stats
from bot.services.events import utcnow
from bot.states.admin import AdminStates


router = Router()
logger = logging.getLogger(__name__)

# Cho'qqi soatda tasdiq kutayotgan tarqatishlar: admin_id -> (mode, chat_id, message_id)
_pending_broadcasts: dict[int, tuple[str, int, int]] = {}


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_USER_IDS


@router.message(Command("admin"), F.from_user.func(lambda u: u and is_admin(u.id)))
@router.message(Command("panel"), F.from_user.func(lambda u: u and is_admin(u.id)))
async def open_admin_panel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Admin panelga xush kelibsiz.", reply_markup=admin_main_keyboard())


@router.message(F.text == "🔙 Orqaga", F.from_user.func(lambda u: u and is_admin(u.id)))
async def go_back(message: Message, state: FSMContext):
    """Orqaga: foydalanuvchi bo'limida bosqichma-bosqich, qolganida bosh menyu."""
    current = await state.get_state()
    data = await state.get_data()
    target_id = data.get("target_user_id")

    # Limit kiritish → foydalanuvchi kartochkasi
    if (
        current in (AdminStates.waiting_user_limit.state, AdminStates.waiting_user_tts_limit.state)
        and target_id is not None
    ):
        await state.set_state(None)
        ok = await _show_user_card(message, state, int(target_id))
        if not ok:
            await state.clear()
            await message.answer(
                "Foydalanuvchi boshqaruvi",
                reply_markup=admin_users_keyboard(),
            )
        return

    # Qidiruv yoki tanlangan user → foydalanuvchilar menyusi
    if current == AdminStates.waiting_user_query.state or target_id is not None:
        await state.clear()
        await message.answer(
            "Foydalanuvchi boshqaruvi",
            reply_markup=admin_users_keyboard(),
        )
        return

    await state.clear()
    await message.answer("Bosh menyu", reply_markup=admin_main_keyboard())


@router.message(F.text == "📊 Statistika", F.from_user.func(lambda u: u and is_admin(u.id)))
async def show_stats(message: Message):
    """Boy statistika.

    Ko'rinish `bot/services/stats.py` da: tekislangan `<pre>` jadvallar,
    ulush chiziqlari va uzun ro'yxatlar uchun yig'iladigan bloklar.
    """
    async with AsyncSessionLocal() as session:
        data = await StatsService(session).collect()

    await message.answer(render_stats(data), reply_markup=admin_main_keyboard())


@router.message(F.text == "📜 Audit", F.from_user.func(lambda u: u and is_admin(u.id)))
async def show_audit(message: Message):
    """Oxirgi admin amallari — kim, qachon, nima qildi."""
    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        text = await service.format_recent_actions(limit=20)

    await message.answer(text, reply_markup=admin_main_keyboard())


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


@router.message(F.text == "📊 Holat", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_stats(message: Message, session):
    """Hozir ishlab turgan tarqatishlar holati.

    Tugaganlari ko'rsatilmaydi — muhim savol "hozir nima bo'lyapti".
    Skript orqali ishga tushirilgan tarqatishlar ham shu jadvallarga
    yozgani uchun ular ham ko'rinadi.
    """
    repo = BroadcastRepository(session)
    rows = await repo.live_stats()

    if not rows:
        await message.answer(
            "💤 Hozir ishlab turgan tarqatish yo'q.",
            reply_markup=admin_broadcast_keyboard(),
        )
        return

    lines = []
    for row in rows:
        processed = row["delivered"] + row["failed"]
        total = row["total"] or 0
        percent = (processed / total * 100) if total else 0.0
        icon = "⏸" if row["status"] == "cancel_requested" else "🔄"

        lines.append(f"{icon} <b>Tarqatish #{row['id']}</b>")
        if row["status"] == "cancel_requested":
            lines.append("   <i>to'xtatish so'ralgan — joriy batch tugaydi</i>")
        lines.append("")
        lines.append(f"   📊 {processed:,} / {total:,}  ({percent:.1f}%)".replace(",", " "))
        lines.append(f"   ✅ Yetdi: <b>{row['delivered']:,}</b>".replace(",", " "))
        lines.append(f"   ❌ Yetmadi: <b>{row['failed']:,}</b>".replace(",", " "))

        if row["started_at"] and processed:
            elapsed = (utcnow() - row["started_at"]).total_seconds()
            speed = processed / elapsed if elapsed > 0 else 0
            if speed > 0:
                remaining = (total - processed) / speed
                lines.append(
                    f"   ⚡️ {speed:.1f}/sek · qoldi ~{remaining / 60:.0f} daqiqa"
                )

        if row["failed"]:
            reasons = await repo.failure_reasons(row["id"], limit=3)
            lines.append("")
            lines.append("   <i>Xato sabablari:</i>")
            for reason, count in reasons:
                lines.append(f"   • {reason} — {count:,}".replace(",", " "))

        lines.append("")

    await message.answer("\n".join(lines), reply_markup=admin_broadcast_keyboard())


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


# Foydalanuvchi oqimi eng yuqori soatlar (Asia/Tashkent = UTC+5).
# Shu payt tarqatish boshlansa jonli so'rovlarga xalaqit beradi, shuning
# uchun admindan qo'shimcha tasdiq so'raladi.
PEAK_HOURS_TASHKENT = range(18, 23)


def _is_peak_hour() -> bool:
    tashkent_hour = (utcnow().hour + 5) % 24
    return tashkent_hour in PEAK_HOURS_TASHKENT


async def _do_broadcast(message: Message, mode: str, admin_user) -> None:
    """Tarqatishni bajaradi va hisobot beradi."""
    progress_message = await message.answer("📤 Yuborish boshlandi...")

    async def progress_callback(processed: int, total: int, success: int, failed: int):
        percent = (processed / total * 100) if total else 0.0
        try:
            await progress_message.edit_text(
                "📤 <b>Tarqatilmoqda...</b>\n\n"
                f"📊 {processed:,} / {total:,}  ({percent:.1f}%)\n"
                f"✅ Yetdi: {success:,}\n"
                f"❌ Yetmadi: {failed:,}".replace(",", " ")
            )
        except TelegramBadRequest:
            # Xabar o'zgarmagan yoki tahrirlab bo'lmaydi — zararsiz.
            pass

    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        result = await service.run_broadcast(
            admin_id=admin_user.id,
            source_message=message,
            mode=mode,
            progress_callback=progress_callback,
        )

    icon = {"completed": "✅", "cancelled": "⛔", "failed": "🚨"}.get(result["status"], "•")
    await message.answer(
        (
            f"{icon} <b>Tarqatish yakunlandi</b>\n\n"
            f"🆔 #{result['broadcast_id']} · {result['status']}\n"
            f"📊 {result['processed']:,} / {result['total']:,}\n"
            f"✅ Yetdi: <b>{result['success']:,}</b>\n"
            f"❌ Yetmadi: <b>{result['failed']:,}</b>\n"
            f"🚫 Bloklagan: {result.get('blocked', 0):,}\n"
            f"♻️ Qaytgan: {result.get('recovered', 0):,}\n"
            f"⚫️ Yetib bo'lmas: {result.get('unreachable', 0):,}"
        ).replace(",", " "),
        reply_markup=admin_broadcast_keyboard(),
    )

    # Xato bo'lgan foydalanuvchilar ro'yxati fayl bo'lib keladi — keyin
    # tekshirish yoki qayta urinish uchun.
    failures = result.get("failures") or []
    if failures:
        body = "\n".join(f"{tg_id}\t{err}" for tg_id, err in failures)
        await message.answer_document(
            BufferedInputFile(
                body.encode("utf-8"),
                filename=f"xato_{result['broadcast_id']}.txt",
            ),
            caption=f"❌ Yetmagan {len(failures):,} foydalanuvchi".replace(",", " "),
        )


@router.message(AdminStates.waiting_broadcast_message, F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_send(message: Message, state: FSMContext, user):
    data = await state.get_data()
    mode = data.get("broadcast_mode")

    if mode not in {"copy", "forward"}:
        await state.clear()
        await message.answer(
            "Tarqatish rejimi topilmadi. Qayta urinib ko'ring.",
            reply_markup=admin_broadcast_keyboard(),
        )
        return

    await state.clear()

    if _is_peak_hour():
        # Xabarni keyin ham topish uchun id'sini saqlaymiz.
        _pending_broadcasts[message.from_user.id] = (mode, message.chat.id, message.message_id)
        await message.answer(
            "🕗 <b>Hozir eng band vaqt</b> (18:00–23:00).\n\n"
            "Tarqatish jonli so'rovlarga xalaqit berishi mumkin. Davom etamizmi?",
            reply_markup=broadcast_confirm_keyboard(),
        )
        return

    await _do_broadcast(message, mode, user)


@router.callback_query(F.data == "bc:confirm", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_confirm(call: CallbackQuery, user):
    pending = _pending_broadcasts.pop(call.from_user.id, None)
    await call.answer()
    if not pending:
        await call.message.edit_text("Tasdiq muddati o'tgan. Xabarni qaytadan yuboring.")
        return

    mode, chat_id, message_id = pending
    await call.message.edit_text("✅ Tasdiqlandi.")

    # Asl xabarni qayta yuklab olamiz — `copy_message` uchun manba kerak.
    source = await call.bot.forward_message(
        chat_id=chat_id, from_chat_id=chat_id, message_id=message_id
    )
    await _do_broadcast(source, mode, user)


@router.callback_query(F.data == "bc:cancel", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_confirm_cancel(call: CallbackQuery):
    _pending_broadcasts.pop(call.from_user.id, None)
    await call.answer()
    await call.message.edit_text("⛔ Bekor qilindi.")


@router.message(F.text == "⛔ To'xtatish", F.from_user.func(lambda u: u and is_admin(u.id)))
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


# ── Foydalanuvchilar ─────────────────────────────────────────


@router.message(F.text == "👤 Foydalanuvchilar", F.from_user.func(lambda u: u and is_admin(u.id)))
async def users_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Foydalanuvchi boshqaruvi.\n"
        "Qidirish: Telegram ID, @username yoki ichki DB ID.",
        reply_markup=admin_users_keyboard(),
    )


@router.message(F.text == "🔍 Qidirish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_search_start(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_user_query)
    await message.answer(
        "Telegram ID, @username yoki ichki ID yuboring.",
        reply_markup=back_keyboard(),
    )


async def _show_user_card(message: Message, state: FSMContext, user_id: int) -> bool:
    """Kartochkani chiqaradi va FSM da target saqlaydi. Topilmasa False."""
    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        user = await service.user_repo.get_by_id(user_id)
        if not user:
            return False
        card = await service.format_user_card(user)

    await state.update_data(target_user_id=user_id)
    await state.set_state(None)
    await message.answer(card, reply_markup=admin_user_actions_keyboard())
    return True


@router.message(AdminStates.waiting_user_query, F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_search_finish(message: Message, state: FSMContext):
    if (message.text or "").strip() == "🔙 Orqaga":
        await state.clear()
        await message.answer("Foydalanuvchi boshqaruvi", reply_markup=admin_users_keyboard())
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        found = await service.find_users(message.text or "")

    if not found:
        await message.answer(
            "Foydalanuvchi topilmadi. Qayta urinib ko'ring.",
            reply_markup=back_keyboard(),
        )
        return

    if len(found) > 1:
        lines = [
            f"Bir nechta moslik ({len(found)}). Aniqroq ID yuboring:\n"
        ]
        for u in found:
            uname = f"@{u.username}" if u.username else "—"
            lines.append(
                f"• TG <code>{u.telegram_id}</code> · DB {u.id} · {uname} · {u.status}"
            )
        await message.answer("\n".join(lines), reply_markup=back_keyboard())
        return

    await _show_user_card(message, state, found[0].id)


async def _require_target_user(state: FSMContext) -> int | None:
    data = await state.get_data()
    target = data.get("target_user_id")
    return int(target) if target is not None else None


@router.message(F.text == "🔄 Yangilash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_refresh(message: Message, state: FSMContext):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return
    ok = await _show_user_card(message, state, target_id)
    if not ok:
        await state.clear()
        await message.answer("Foydalanuvchi topilmadi.", reply_markup=admin_users_keyboard())


@router.message(F.text == "🚫 Bloklash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_ban(message: Message, state: FSMContext, user):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.ban_user(user.id, target_id)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)


@router.message(F.text == "✅ Blokdan chiqarish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_unban(message: Message, state: FSMContext, user):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.unban_user(user.id, target_id)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)


@router.message(F.text == "🔢 Limit o'rnatish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_limit_start(message: Message, state: FSMContext):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    await state.set_state(AdminStates.waiting_user_limit)
    await message.answer(
        "Kunlik tarjima limitini yuboring.\n"
        "• Oddiy son (masalan <code>100</code>)\n"
        "• <code>0</code> — cheksiz\n\n"
        f"Standart: {settings.DAILY_TRANSLATION_LIMIT}",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_user_limit, F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_limit_finish(message: Message, state: FSMContext, user):
    if (message.text or "").strip() == "🔙 Orqaga":
        target_id = await _require_target_user(state)
        await state.set_state(None)
        if target_id is not None:
            await _show_user_card(message, state, target_id)
        else:
            await message.answer("Foydalanuvchi boshqaruvi", reply_markup=admin_users_keyboard())
        return

    target_id = await _require_target_user(state)
    if target_id is None:
        await state.clear()
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    raw = (message.text or "").strip()
    try:
        limit = int(raw)
    except ValueError:
        await message.answer(
            "Iltimos, butun son yuboring (masalan 100 yoki 0).",
            reply_markup=back_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.set_user_limit(user.id, target_id, limit)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)
    else:
        await state.set_state(AdminStates.waiting_user_limit)


@router.message(F.text == "♻️ Limitni tozalash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_limit_clear(message: Message, state: FSMContext, user):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.clear_user_limit(user.id, target_id)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)


@router.message(F.text == "🔊 Ovoz limiti", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_tts_limit_start(message: Message, state: FSMContext):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    await state.set_state(AdminStates.waiting_user_tts_limit)
    await message.answer(
        "Kunlik ovoz (TTS) limitini yuboring.\n"
        "• Oddiy son (masalan <code>50</code>)\n"
        "• <code>0</code> — cheksiz\n\n"
        f"Standart: {settings.DAILY_TTS_LIMIT}",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_user_tts_limit, F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_tts_limit_finish(message: Message, state: FSMContext, user):
    if (message.text or "").strip() == "🔙 Orqaga":
        target_id = await _require_target_user(state)
        await state.set_state(None)
        if target_id is not None:
            await _show_user_card(message, state, target_id)
        else:
            await message.answer("Foydalanuvchi boshqaruvi", reply_markup=admin_users_keyboard())
        return

    target_id = await _require_target_user(state)
    if target_id is None:
        await state.clear()
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    raw = (message.text or "").strip()
    try:
        limit = int(raw)
    except ValueError:
        await message.answer(
            "Iltimos, butun son yuboring (masalan 50 yoki 0).",
            reply_markup=back_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.set_user_tts_limit(user.id, target_id, limit)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)
    else:
        await state.set_state(AdminStates.waiting_user_tts_limit)


@router.message(F.text == "🔇 Ovoz limitini tozalash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_tts_limit_clear(message: Message, state: FSMContext, user):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.clear_user_tts_limit(user.id, target_id)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)
