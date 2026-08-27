from aiogram import Bot, Router, F
import logging
import re
from typing import Awaitable, Callable
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select

from bot.config.settings import settings
from bot.database.models import SystemSettings
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal
from bot.keyboards.admin import (
    admin_main_keyboard,
    admin_channels_keyboard,
    admin_broadcast_keyboard,
    broadcast_active_keyboard,
    broadcast_test_confirm_keyboard,
    broadcast_peak_confirm_keyboard,
    admin_users_keyboard,
    admin_user_actions_keyboard,
    admin_global_limits_keyboard,
    back_keyboard,
)
from bot.services import broadcast_runner
from bot.services.admin_service import AdminService
from bot.services.broadcast_progress import render_card, render_history_line
from bot.services.stats import StatsService, render as render_stats
from bot.services.events import EventService, utcnow
from bot.services.system_config import get_effective_limits, set_limit as set_system_limit
from bot.states.admin import AdminStates
# `deliver_admin_message`/`SupportRepository` — admin panelidan yuborilgan
# xabar ham ODDIY murojaat javobi bilan BIR XIL yozishma ipiga (va
# "↩️ Javob yozish"/pin mexanizmiga) qo'shilishi uchun ATAYLAB
# `support.py`dan (handler qatlami) olinadi, alohida takrorlanmasin deb.
from bot.database.repositories.support_repository import SupportRepository
from bot.handlers.user.support import deliver_admin_message


router = Router()
logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Panel kirishi FAQAT `.env`dagi `ADMIN_USER_IDS`ga qaraladi.

    DIQQAT: bu DB'dagi `User.role in ("admin", "owner")`dan MUSTAQIL —
    o'sha rol faqat ban-immunitet/cheksiz kvota beradi (`quota.py`,
    `subscription.py`), panelga kirish bermaydi. Ataylab shunday: panel
    kirishi — ishonch chegarasi, u DB qatoridan (kimdir buzib kirsa
    o'zgartirishi mumkin) emas, faqat serverga jismoniy/deploy kirish
    huquqi bo'lgan kishi tahrirlaydigan `.env`dan kelishi kerak. Buni
    async DB so'roviga aylantirish har bir admin-filtrlangan handlerda
    HAR BIR xabar uchun so'rov qo'shardi — hozircha faqat bitta admin
    bor va DB orqali `role='admin'` beradigan hech qanday in-app funksiya
    yo'q, shuning uchun amaliy nomuvofiqlik yo'q. Ikkinchi admin DB
    orqali (qo'lda SQL bilan) qo'shilsa, uni `ADMIN_USER_IDS`ga ham
    qo'shishni unutmang — aks holda ular ban-immun/cheksiz bo'ladi-yu,
    panelga kira olmaydi.
    """
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

    # Tarqatish sehrgari → reklama menyusi (faol tarqatish bo'lsa — kartasi)
    broadcast_wizard_states = (
        AdminStates.waiting_broadcast_message.state,
        AdminStates.waiting_broadcast_test_confirm.state,
        AdminStates.waiting_broadcast_peak_confirm.state,
    )
    if current in broadcast_wizard_states:
        await state.clear()
        await _show_broadcast_menu(message)
        return

    # Umumiy limit kiritish → umumiy limitlar menyusi
    global_limit_states = (
        AdminStates.waiting_global_translation_limit.state,
        AdminStates.waiting_global_tts_limit.state,
        AdminStates.waiting_global_image_free_limit.state,
        AdminStates.waiting_global_image_vip_limit.state,
    )
    if current in global_limit_states:
        await state.set_state(None)
        await global_limits_menu(message, state)
        return

    # Limit kiritish → foydalanuvchi kartochkasi
    if (
        current
        in (
            AdminStates.waiting_user_limit.state,
            AdminStates.waiting_user_tts_limit.state,
            AdminStates.waiting_user_image_limit.state,
        )
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


# Tugma matni → (system_settings ustuni, FSM holati, so'rov matni).
_GLOBAL_LIMIT_FIELDS = {
    "✏️ Matn limiti": (
        "daily_translation_limit",
        AdminStates.waiting_global_translation_limit,
        "Kunlik matn tarjima limiti (hammaga standart)",
    ),
    "✏️ Ovoz limiti": (
        "daily_tts_limit",
        AdminStates.waiting_global_tts_limit,
        "Kunlik ovoz limiti (hammaga standart)",
    ),
    "✏️ Rasm (bepul)": (
        "daily_image_limit_free",
        AdminStates.waiting_global_image_free_limit,
        "Kunlik rasm limiti — oddiy foydalanuvchi",
    ),
    "✏️ Rasm (VIP)": (
        "daily_image_limit_vip",
        AdminStates.waiting_global_image_vip_limit,
        "Kunlik rasm limiti — VIP foydalanuvchi",
    ),
}


@router.message(F.text == "⚙️ Umumiy limitlar", F.from_user.func(lambda u: u and is_admin(u.id)))
async def global_limits_menu(message: Message, state: FSMContext):
    """Admin panelidan sozlanadigan umumiy (hamma uchun) standart limitlar.

    Har birida qavs ichida "(o'zgartirilgan)" — bazada aniq qiymat qo'yilgan,
    yo'q bo'lsa `.env` dagi standart ishlatilmoqda degani.
    """
    await state.set_state(None)
    async with AsyncSessionLocal() as session:
        limits = await get_effective_limits(session)
        row = await session.execute(select(SystemSettings).where(SystemSettings.id == 1))
        row = row.scalar_one_or_none()

    def line(label: str, value: int, db_value) -> str:
        note = " <i>(o'zgartirilgan)</i>" if db_value is not None else ""
        return f"{label}: <b>{value}</b>{note}"

    text = (
        "⚙️ <b>Umumiy limitlar</b>\n"
        "Bular hamma uchun STANDART qiymat — foydalanuvchi kartochkasidagi\n"
        "alohida limit bo'lsa, u bundan ustun turadi.\n\n"
        + line("📝 Matn/kun", limits.translation, row.daily_translation_limit if row else None) + "\n"
        + line("🔊 Ovoz/kun", limits.tts, row.daily_tts_limit if row else None) + "\n"
        + line("🖼 Rasm/kun (oddiy)", limits.image_free, row.daily_image_limit_free if row else None) + "\n"
        + line("💎 Rasm/kun (VIP)", limits.image_vip, row.daily_image_limit_vip if row else None)
    )
    await message.answer(text, reply_markup=admin_global_limits_keyboard())


@router.message(F.text.in_(_GLOBAL_LIMIT_FIELDS), F.from_user.func(lambda u: u and is_admin(u.id)))
async def global_limit_edit_start(message: Message, state: FSMContext):
    field, fsm_state, label = _GLOBAL_LIMIT_FIELDS[message.text]
    await state.update_data(global_limit_field=field)
    await state.set_state(fsm_state)
    await message.answer(
        f"{label}ni yuboring.\n"
        "• Oddiy son (masalan <code>100</code>)\n"
        "• <code>0</code> — cheksiz\n"
        "• <code>standart</code> — .env qiymatiga qaytarish",
        reply_markup=back_keyboard(),
    )


@router.message(
    StateFilter(
        AdminStates.waiting_global_translation_limit,
        AdminStates.waiting_global_tts_limit,
        AdminStates.waiting_global_image_free_limit,
        AdminStates.waiting_global_image_vip_limit,
    ),
    F.from_user.func(lambda u: u and is_admin(u.id)),
)
async def global_limit_edit_finish(message: Message, state: FSMContext, user):
    if (message.text or "").strip() == "🔙 Orqaga":
        await state.set_state(None)
        await global_limits_menu(message, state)
        return

    data = await state.get_data()
    field = data.get("global_limit_field")
    raw = (message.text or "").strip().lower()

    if raw in ("standart", "avto", "default"):
        value = None
    else:
        try:
            value = int(raw)
        except ValueError:
            await message.answer(
                "Iltimos, butun son, <code>0</code> yoki <code>standart</code> yuboring.",
                reply_markup=back_keyboard(),
            )
            return
        if value < 0:
            await message.answer(
                "Limit manfiy bo'lishi mumkin emas. Cheksiz uchun 0 yuboring.",
                reply_markup=back_keyboard(),
            )
            return
        # Foydalanuvchi-darajasidagi limit bilan bir xil chegara
        # (`AdminService.set_user_limit`) — aks holda qo'shimcha nol
        # (yozuv xatosi) qabul qilinib, Postgres Integer chegarasidan
        # oshib ketishi (unhandled xato) yoki amalda cheksiz bo'lib
        # qolishi mumkin edi.
        if value > 1_000_000:
            await message.answer(
                "Limit juda katta (maks. 1 000 000).",
                reply_markup=back_keyboard(),
            )
            return

    async with AsyncSessionLocal() as session:
        await set_system_limit(session, field, value)
        service = AdminService(session)
        await service.log_action(
            user.id,
            "system.set_limit",
            target_type="system",
            target_id=field,
            payload={"field": field, "value": value},
        )
        await session.commit()

    await state.set_state(None)
    await message.answer("✅ Yangilandi.")
    await global_limits_menu(message, state)


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
async def channel_remove_finish(message: Message, state: FSMContext, user):
    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        ok, result = await service.remove_channel(message.text or "", removed_by=user.id)

    await state.clear()
    await message.answer(result, reply_markup=admin_channels_keyboard() if ok else back_keyboard())


@router.message(F.text == "📋 Kanallar ro'yxati", F.from_user.func(lambda u: u and is_admin(u.id)))
async def channel_list(message: Message):
    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        text = await service.list_channels_text()

    await message.answer(text, reply_markup=admin_channels_keyboard())


async def _show_broadcast_menu(message: Message) -> None:
    """Faol (yoki pauzadagi) tarqatish bo'lsa — kartasi; bo'lmasa yangisini
    boshlash menyusi. Faqat bitta faol tarqatishga ruxsat berilgani uchun
    bu yerda tanlash kerak emas."""
    async with AsyncSessionLocal() as session:
        active = await AdminService(session).get_active_broadcast()

    if active is None:
        await message.answer("📤 Reklama bo'limi", reply_markup=admin_broadcast_keyboard())
        return

    await message.answer(render_card(active), reply_markup=broadcast_active_keyboard(active.status))


@router.message(F.text == "📤 Reklama", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_menu(message: Message, state: FSMContext):
    await state.clear()
    await _show_broadcast_menu(message)


@router.message(F.text == "🔄 Yangilash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_refresh(message: Message):
    await _show_broadcast_menu(message)


@router.message(F.text == "🗂 Tarix", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_history_view(message: Message):
    async with AsyncSessionLocal() as session:
        items = await AdminService(session).broadcast_history(limit=10)

    if not items:
        await message.answer("🗂 Tarix bo'sh.", reply_markup=admin_broadcast_keyboard())
        return

    text = "🗂 <b>Oxirgi tarqatishlar</b>\n\n" + "\n\n".join(
        render_history_line(bc) for bc in items
    )
    await message.answer(text, reply_markup=admin_broadcast_keyboard())


async def _start_wizard(message: Message, state: FSMContext, *, mode: str) -> None:
    async with AsyncSessionLocal() as session:
        active = await AdminService(session).get_active_broadcast()
    if active is not None:
        await message.answer(
            "⚠️ Hozir allaqachon bitta tarqatish faol — avval shuni yakunlang "
            "(bir vaqtda faqat bitta tarqatish bo'lishi mumkin).",
            reply_markup=broadcast_active_keyboard(active.status),
        )
        return

    await state.clear()
    await state.update_data(broadcast_mode=mode)
    await state.set_state(AdminStates.waiting_broadcast_message)
    prompt = (
        "Forward qilinadigan xabarni yuboring."
        if mode == "forward"
        else "Yuboriladigan xabarni yuboring."
    )
    await message.answer(prompt, reply_markup=back_keyboard())


@router.message(F.text == "📨 Forward xabar yuborish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_forward_start(message: Message, state: FSMContext):
    await _start_wizard(message, state, mode="forward")


@router.message(F.text == "📬 Oddiy xabar yuborish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_copy_start(message: Message, state: FSMContext):
    await _start_wizard(message, state, mode="copy")


# Foydalanuvchi oqimi eng yuqori soatlar (Asia/Tashkent = UTC+5).
# Shu payt tarqatish boshlansa jonli so'rovlarga xalaqit beradi, shuning
# uchun admindan qo'shimcha tasdiq so'raladi.
PEAK_HOURS_TASHKENT = range(18, 23)


def _is_peak_hour() -> bool:
    tashkent_hour = (utcnow().hour + 5) % 24
    return tashkent_hour in PEAK_HOURS_TASHKENT


@router.message(AdminStates.waiting_broadcast_message, F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_receive_message(message: Message, state: FSMContext, user):
    """Xabar qabul qilinishi bilan — DARHOL faqat adminga sinov yuboriladi.

    Xabarning o'zi buzuq bo'lsa (bo'sh, o'chirilgan media, noto'g'ri HTML)
    shu yerda ko'rinadi — 37 ming real foydalanuvchiga urinishdan oldin,
    hech kimga bekorga xato ketmaydi.
    """
    data = await state.get_data()
    mode = data.get("broadcast_mode")
    if mode not in {"copy", "forward"}:
        await state.clear()
        await message.answer(
            "Tarqatish rejimi topilmadi. Qayta urinib ko'ring.",
            reply_markup=admin_broadcast_keyboard(),
        )
        return

    src_chat_id = message.chat.id
    src_message_id = message.message_id
    preview = (message.text or message.caption or "<media>")[:500]

    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        ok, result_text = await service.send_broadcast_test(
            src_chat_id, mode, src_chat_id, src_message_id
        )
        if not ok:
            await message.answer(
                f"{result_text}\n\nBoshqa xabar yuboring yoki orqaga qayting.",
                reply_markup=back_keyboard(),
            )
            return
        total = await service.user_repo.count_broadcast_targets(exclude_user_id=user.id)

    await state.update_data(
        src_chat_id=src_chat_id, src_message_id=src_message_id, preview=preview,
    )
    await state.set_state(AdminStates.waiting_broadcast_test_confirm)
    await message.answer(
        f"{result_text}\n\n"
        "👆 Yuqorida — hammaga aynan shu ko'rinishda ketadi.\n"
        f"📦 Taxminan <b>{total:,}</b> foydalanuvchiga yuboriladi.".replace(",", " ")
        + "\n\nDavom etamizmi?",
        reply_markup=broadcast_test_confirm_keyboard(),
    )


@router.message(
    AdminStates.waiting_broadcast_test_confirm, F.text == "🔁 Boshqa xabar",
    F.from_user.func(lambda u: u and is_admin(u.id)),
)
async def broadcast_retry_message(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("broadcast_mode")
    await state.set_state(AdminStates.waiting_broadcast_message)
    prompt = (
        "Forward qilinadigan xabarni yuboring."
        if mode == "forward"
        else "Yuboriladigan xabarni yuboring."
    )
    await message.answer(prompt, reply_markup=back_keyboard())


@router.message(
    AdminStates.waiting_broadcast_test_confirm, F.text == "🚀 Ha, hammaga yuborilsin",
    F.from_user.func(lambda u: u and is_admin(u.id)),
)
async def broadcast_confirm_send(message: Message, state: FSMContext, user):
    if _is_peak_hour():
        await state.set_state(AdminStates.waiting_broadcast_peak_confirm)
        await message.answer(
            "🕗 <b>Hozir eng band vaqt</b> (18:00–23:00).\n\n"
            "Tarqatish jonli so'rovlarga xalaqit berishi mumkin. Baribir yuboramizmi?",
            reply_markup=broadcast_peak_confirm_keyboard(),
        )
        return
    await _launch_broadcast(message, state, user)


@router.message(
    AdminStates.waiting_broadcast_peak_confirm, F.text == "✅ Ha, baribir yubor",
    F.from_user.func(lambda u: u and is_admin(u.id)),
)
async def broadcast_peak_confirmed(message: Message, state: FSMContext, user):
    await _launch_broadcast(message, state, user)


@router.message(
    AdminStates.waiting_broadcast_peak_confirm, F.text == "⛔ Bekor qilish",
    F.from_user.func(lambda u: u and is_admin(u.id)),
)
async def broadcast_peak_cancelled(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=admin_broadcast_keyboard())


def _make_finish_notifier(bot: Bot, admin_chat_id: int) -> Callable[[int, bool], Awaitable[None]]:
    """Tarqatish TUGAGANDA (pauza/bekor/yakun/xato) adminga bitta xabar
    yuboradi — u boshqa ish bilan band bo'lsa ham natijadan xabardor bo'ladi.
    Oraliq checkpoint'larda chaqirilmaydi (`finished=False` — e'tiborsiz).

    `admin_chat_id` — HOZIR harakat qilayotgan admin (boshlagan yoki
    davom ettirgan). Bundan tashqari, agar tarqatishni ORIGINAL
    boshlagan admin (`bc.created_by`) BOSHQA odam bo'lsa — ularga ham
    xabar boradi, aks holda: admin A boshlab qo'yib ketsa, keyin bot
    qayta ishga tushib admin B davom ettirsa — admin A natijadan
    HECH QACHON xabar topmasdi.
    """

    async def notify(broadcast_id: int, finished: bool) -> None:
        if not finished:
            return
        async with AsyncSessionLocal() as session:
            bc = await AdminService(session).get_broadcast(broadcast_id)
            creator_chat_id = None
            if bc is not None and bc.created_by is not None:
                creator = await UserRepository(session).get_by_id(bc.created_by)
                if creator is not None:
                    creator_chat_id = creator.telegram_id
        if bc is None:
            return

        recipients = {admin_chat_id}
        if creator_chat_id is not None:
            recipients.add(creator_chat_id)

        for chat_id in recipients:
            try:
                await bot.send_message(
                    chat_id,
                    "🔔 " + render_card(bc),
                    reply_markup=broadcast_active_keyboard(bc.status),
                )
            except Exception:
                logger.warning(
                    "Tarqatish #%s haqida adminga (%s) xabar berib bo'lmadi", broadcast_id, chat_id
                )

    return notify


async def _launch_broadcast(message: Message, state: FSMContext, user) -> None:
    data = await state.get_data()
    mode = data.get("broadcast_mode")
    src_chat_id = data.get("src_chat_id")
    src_message_id = data.get("src_message_id")
    preview = data.get("preview")
    await state.clear()

    if mode not in {"copy", "forward"} or src_chat_id is None or src_message_id is None:
        await message.answer(
            "Ma'lumot yo'qolgan, qaytadan boshlang.", reply_markup=admin_broadcast_keyboard()
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session, message.bot)
        active = await service.get_active_broadcast()
        if active is not None:
            await message.answer(
                "⚠️ Boshqa tarqatish allaqachon boshlangan.",
                reply_markup=broadcast_active_keyboard(active.status),
            )
            return
        broadcast = await service.start_broadcast(
            admin_id=user.id,
            src_chat_id=src_chat_id,
            src_message_id=src_message_id,
            mode=mode,
            content_preview=preview,
        )
        if broadcast is None:
            # Yuqoridagi tekshiruv bilan shu yer orasida (deyarli bir
            # vaqtda, masalan ikki admin) boshqa tarqatish ulgurib
            # boshlangan — DB darajasidagi cheklov (migratsiya 015) buni
            # ushladi, ikkalasi ham ishga tushib ketishining oldini oldi.
            active = await service.get_active_broadcast()
            await message.answer(
                "⚠️ Boshqa tarqatish deyarli bir vaqtda boshlanib ulgurdi.",
                reply_markup=broadcast_active_keyboard(active.status if active else "running"),
            )
            return

    notify = _make_finish_notifier(message.bot, message.chat.id)
    broadcast_runner.start(message.bot, broadcast.id, progress_notify=notify)

    await message.answer(
        f"🚀 <b>Tarqatish #{broadcast.id} boshlandi.</b>\n\n"
        "Boshqa ishlaringizni davom ettirishingiz mumkin — bu yerga "
        "qaytmasdan ham tarqatish davom etadi. Tugagach xabar beraman, "
        "yoki istalgan payt \"📤 Reklama\"dan holatni ko'rishingiz mumkin.",
        reply_markup=broadcast_active_keyboard("running"),
    )


@router.message(F.text == "⏸ Pauza", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_pause(message: Message):
    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        active = await service.get_active_broadcast()
        if active is None:
            await message.answer(
                "Hozir ishlab turgan tarqatish topilmadi.", reply_markup=admin_broadcast_keyboard()
            )
            return
        if active.status == "pause_requested":
            # Ikki marta bosilgan — "topilmadi" chalg'ituvchi bo'lardi,
            # aslida tarqatish bor, faqat allaqachon pauzaga o'tmoqda.
            await message.answer(
                "⏸ Pauza allaqachon so'ralgan — bir necha soniya kuting.",
                reply_markup=broadcast_active_keyboard("pause_requested"),
            )
            return
        if active.status != "running":
            await message.answer(
                "Hozir ishlab turgan tarqatish topilmadi.", reply_markup=admin_broadcast_keyboard()
            )
            return
        await service.pause_broadcast(active.id)

    await message.answer(
        "⏸ Pauza so'raldi — joriy yuborishlar tugagach bir necha soniyada to'xtaydi.",
        reply_markup=broadcast_active_keyboard("pause_requested"),
    )


@router.message(F.text == "▶️ Davom ettirish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_resume(message: Message):
    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        active = await service.get_active_broadcast()
        if active is None or active.status != "paused":
            await message.answer(
                "Pauzadagi tarqatish topilmadi.", reply_markup=admin_broadcast_keyboard()
            )
            return
        ok = await service.resume_broadcast(active.id)
        broadcast_id = active.id

    if not ok:
        await message.answer("Holat o'zgardi, qayta urining.", reply_markup=admin_broadcast_keyboard())
        return

    notify = _make_finish_notifier(message.bot, message.chat.id)
    broadcast_runner.start(message.bot, broadcast_id, progress_notify=notify)
    await message.answer("▶️ Davom etmoqda.", reply_markup=broadcast_active_keyboard("running"))


@router.message(F.text == "⛔ Bekor qilish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def broadcast_cancel(message: Message):
    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        active = await service.get_active_broadcast()
        if active is None:
            await message.answer(
                "Faol tarqatish topilmadi.", reply_markup=admin_broadcast_keyboard()
            )
            return
        if active.status == "cancel_requested":
            # Ikki marta bosilgan — "holat o'zgardi" chalg'ituvchi
            # bo'lardi, aslida hammasi rejadagidek, faqat kutish kerak.
            await message.answer(
                "⛔ Bekor qilish allaqachon so'ralgan — bir necha soniya kuting.",
                reply_markup=broadcast_active_keyboard("cancel_requested"),
            )
            return
        was_paused = active.status == "paused"
        ok = await service.cancel_broadcast(active.id)

    if not ok:
        await message.answer("Holat o'zgardi, qayta urining.", reply_markup=admin_broadcast_keyboard())
        return

    if was_paused:
        # Pauzada ishlab turgan vazifa yo'q — bekor qilish darhol yakunlandi.
        await message.answer("⛔ Bekor qilindi.", reply_markup=admin_broadcast_keyboard())
    else:
        await message.answer(
            "⛔ Bekor qilish so'raldi — bir necha soniyada to'xtaydi.",
            reply_markup=broadcast_active_keyboard("cancel_requested"),
        )


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


@router.message(F.text == "✉️ Xabar yuborish", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_message_start(message: Message, state: FSMContext):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    await state.set_state(AdminStates.waiting_user_message)
    await message.answer(
        "Foydalanuvchiga yuboriladigan xabarni yozing (matn, rasm, ovoz — "
        "istalgan turi).",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_user_message, F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_message_finish(message: Message, state: FSMContext, session_id):
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

    async with AsyncSessionLocal() as session:
        target = await SupportRepository(session).find_user_by_id(target_id)
        if target is None:
            await state.set_state(None)
            await message.answer(
                "Foydalanuvchi topilmadi (o'chirilgan bo'lishi mumkin).",
                reply_markup=admin_users_keyboard(),
            )
            return

        # `events` ataylab shu (ICHKI) sessiyaga bog'lanadi — ContextMiddleware
        # tashqi sessiyasidan olinganda, pastdagi `session.commit()` uni
        # kiritmay qolardi (ikki xil tranzaksiya, atomik bo'lmagan yozuv).
        events = EventService(session)
        ok, reply_text = await deliver_admin_message(message, session, events, session_id, target)
        if ok:
            # Adminning tugmasiz keyingi oddiy xabari ham shu foydalanuvchiga
            # ketishi uchun ("davom etadigan" suhbat, xuddi murojaatga
            # javob berilgandagi kabi) — bir martalik.
            await SupportRepository(session).set_pinned_target(message.chat.id, target_id)
        await session.commit()

    await state.set_state(None)
    await message.answer(reply_text, reply_markup=admin_user_actions_keyboard())
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

    async with AsyncSessionLocal() as session:
        limits = await get_effective_limits(session)

    await state.set_state(AdminStates.waiting_user_limit)
    await message.answer(
        "Kunlik tarjima limitini yuboring.\n"
        "• Oddiy son (masalan <code>100</code>)\n"
        "• <code>0</code> — cheksiz\n\n"
        f"Standart: {limits.translation}",
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

    async with AsyncSessionLocal() as session:
        limits = await get_effective_limits(session)

    await state.set_state(AdminStates.waiting_user_tts_limit)
    await message.answer(
        "Kunlik ovoz (TTS) limitini yuboring.\n"
        "• Oddiy son (masalan <code>50</code>)\n"
        "• <code>0</code> — cheksiz\n\n"
        f"Standart: {limits.tts}",
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


@router.message(F.text == "🖼 Rasm limiti", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_image_limit_start(message: Message, state: FSMContext):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        limits = await get_effective_limits(session)

    await state.set_state(AdminStates.waiting_user_image_limit)
    await message.answer(
        "Kunlik rasm (OCR) limitini yuboring.\n"
        "• Oddiy son (masalan <code>10</code>)\n"
        "• <code>0</code> — cheksiz\n\n"
        f"Standart: oddiy {limits.image_free} / VIP {limits.image_vip}",
        reply_markup=back_keyboard(),
    )


@router.message(AdminStates.waiting_user_image_limit, F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_image_limit_finish(message: Message, state: FSMContext, user):
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
            "Iltimos, butun son yuboring (masalan 10 yoki 0).",
            reply_markup=back_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.set_user_image_limit(user.id, target_id, limit)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)
    else:
        await state.set_state(AdminStates.waiting_user_image_limit)


@router.message(F.text == "🚫 Rasm limitini tozalash", F.from_user.func(lambda u: u and is_admin(u.id)))
async def user_image_limit_clear(message: Message, state: FSMContext, user):
    target_id = await _require_target_user(state)
    if target_id is None:
        await message.answer(
            "Avval foydalanuvchini qidiring.",
            reply_markup=admin_users_keyboard(),
        )
        return

    async with AsyncSessionLocal() as session:
        service = AdminService(session)
        ok, text = await service.clear_user_image_limit(user.id, target_id)

    await message.answer(text, reply_markup=admin_user_actions_keyboard())
    if ok:
        await _show_user_card(message, state, target_id)
