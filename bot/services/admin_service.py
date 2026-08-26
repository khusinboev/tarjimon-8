import asyncio
import logging
from datetime import datetime
from typing import Optional
import aiohttp
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramAPIError,
    ClientDecodeError,
)
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from bot.config.settings import settings
from bot.database.models import AdminAction, Broadcast, Channel, User
from bot.database.repositories.user_repository import UserRepository
from bot.database.repositories.channel_repository import ChannelRepository
from bot.database.repositories.broadcast_repository import BroadcastRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.database.repositories.usage_repository import UsageRepository
from bot.services.events import utcnow
from bot.services.system_config import get_effective_limits
from bot.utils.formatters import format_datetime
from bot.utils.text import html_escape


logger = logging.getLogger(__name__)


class AdminService:
    """Business logic for admin panel"""

    def __init__(self, session: AsyncSession, bot: Bot | None = None):
        self.session = session
        self.bot = bot
        self.user_repo = UserRepository(session)
        self.channel_repo = ChannelRepository(session)
        self.broadcast_repo = BroadcastRepository(session)
        self.translation_repo = TranslationRepository(session)
        self.usage_repo = UsageRepository(session)

    async def log_action(
        self,
        admin_id: int,
        action: str,
        *,
        target_type: str | None = None,
        target_id: str | int | None = None,
        payload: dict | None = None,
    ) -> None:
        self.session.add(
            AdminAction(
                admin_id=admin_id,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                payload=payload or {},
            )
        )

    async def _fetch_chat_via_http(self, chat_id: str) -> tuple[bool, dict | str]:
        """Fallback for Telegram API schema changes that aiogram can't decode yet."""
        url = f"https://api.telegram.org/bot{self.bot.token}/getChat"
        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.get(url, params={"chat_id": chat_id}) as response:
                    payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            return False, "Telegram javobi sekin. Iltimos, qayta urinib ko'ring."
        except Exception:
            logger.exception("HTTP fallback failed for getChat(%s)", chat_id)
            return False, "Telegram kanalini tekshirishda tarmoq xatoligi yuz berdi."

        if not payload.get("ok"):
            description = str(payload.get("description") or "Noma'lum xatolik")
            return False, f"Kanalni tekshirib bo'lmadi: {description}"

        result = payload.get("result") or {}
        if not isinstance(result, dict):
            return False, "Telegramdan noto'g'ri javob olindi."
        return True, result

    async def _fetch_chat_member_status_via_http(self, chat_id: int, user_id: int) -> tuple[bool, str]:
        url = f"https://api.telegram.org/bot{self.bot.token}/getChatMember"
        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.get(url, params={"chat_id": chat_id, "user_id": user_id}) as response:
                    payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            return False, "timeout"
        except Exception:
            logger.exception("HTTP fallback failed for getChatMember(chat_id=%s, user_id=%s)", chat_id, user_id)
            return False, "network_error"

        if not payload.get("ok"):
            return False, str(payload.get("description") or "bad_request")

        result = payload.get("result") or {}
        if not isinstance(result, dict):
            return False, "bad_response"

        status = str(result.get("status") or "")
        if not status:
            return False, "bad_response"
        return True, status

    async def add_channel(
        self,
        button_text: str,
        channel_username: str,
        button_url: str,
        added_by: int,
    ) -> tuple[bool, str]:
        raw_button_text = button_text.strip()
        raw_username = channel_username.strip()
        raw_button_url = button_url.strip()

        if not raw_button_text:
            return False, "Tugma nomi bo'sh bo'lmasligi kerak."

        if not raw_username.startswith("@") or len(raw_username) < 2:
            return False, "Kanal username @ bilan boshlanishi kerak."

        if not (raw_button_url.startswith("http://") or raw_button_url.startswith("https://")):
            return False, "Tugma havolasi http:// yoki https:// bilan boshlanishi kerak."

        normalized_username = raw_username[1:].strip().lower()
        if not normalized_username:
            return False, "Kanal username noto'g'ri."

        exists_by_username = await self.channel_repo.get_by_username(normalized_username)
        if exists_by_username:
            return False, "Bu kanal username allaqachon mavjud."

        chat: Optional[object] = None
        chat_data: Optional[dict] = None

        try:
            # Avoid long hangs if Telegram API is slow/unreachable.
            chat = await asyncio.wait_for(self.bot.get_chat(raw_username), timeout=12)
        except asyncio.TimeoutError:
            return False, "Telegram javobi sekin. Iltimos, qayta urinib ko'ring."
        except ClientDecodeError:
            logger.exception("ClientDecodeError while fetching chat %s", raw_username)
            ok_http, result_http = await self._fetch_chat_via_http(raw_username)
            if not ok_http:
                return False, str(result_http)
            chat_data = result_http
        except TelegramBadRequest as exc:
            return False, f"Kanalni tekshirib bo'lmadi: {exc}"
        except TelegramAPIError:
            return False, "Kanal topilmadi yoki bot kanalga kira olmayapti."
        except Exception:
            logger.exception("Unexpected error while fetching chat %s", raw_username)
            return False, "Kanalni tekshirishda kutilmagan xatolik yuz berdi."

        if chat_data is not None:
            chat_id = int(chat_data.get("id") or 0)
            channel_title = str(chat_data.get("title") or normalized_username).strip()
            chat_type = str(chat_data.get("type") or "")
        else:
            chat_id = int(chat.id)
            channel_title = (chat.title or normalized_username).strip()
            raw_chat_type = getattr(chat, "type", "")
            chat_type = getattr(raw_chat_type, "value", str(raw_chat_type))

        if chat_id == 0:
            return False, "Kanal identifikatori topilmadi."

        if chat_type not in {"channel", "supergroup"}:
            return False, "Faqat kanal yoki guruh qo'shish mumkin."

        me = await self.bot.get_me()
        try:
            member = await asyncio.wait_for(self.bot.get_chat_member(chat_id=chat_id, user_id=me.id), timeout=12)
            member_status = getattr(member.status, "value", str(member.status))
        except ClientDecodeError:
            ok_member, member_result = await self._fetch_chat_member_status_via_http(chat_id=chat_id, user_id=me.id)
            if not ok_member:
                return False, "Botning kanal holatini tekshirib bo'lmadi. Bot kanalga qo'shilganini tekshiring."
            member_status = member_result
        except asyncio.TimeoutError:
            return False, "Botning kanal holatini tekshirish sekin kechdi. Qayta urinib ko'ring."
        except TelegramAPIError:
            return False, "Bot kanalga qo'shilmagan yoki unga ruxsat yetarli emas."
        except Exception:
            logger.exception("Unexpected error while checking bot membership for channel %s", chat_id)
            return False, "Botning kanal holatini tekshirishda xatolik yuz berdi."

        if member_status in {"left", "kicked"}:
            return False, "Bot kanalga qo'shilmagan. Avval botni kanalga qo'shing."

        exists = await self.channel_repo.get_by_id(chat_id)
        if exists:
            return False, "Bu kanal allaqachon mavjud."

        try:
            await self.channel_repo.create(
                channel_id=chat_id,
                channel_username=normalized_username,
                channel_title=channel_title,
                button_text=raw_button_text,
                button_url=raw_button_url,
                added_by=added_by,
                is_active=True,
            )
            await self.log_action(
                added_by,
                "channel.add",
                target_type="channel",
                target_id=chat_id,
                payload={"username": normalized_username, "title": channel_title},
            )
        except IntegrityError:
            await self.session.rollback()
            return False, "Bu kanal (yoki username) allaqachon mavjud."
        except Exception as exc:
            await self.session.rollback()
            logger.exception("Unexpected DB error while creating channel %s", chat_id)
            return False, "Kanalni saqlashda kutilmagan xatolik yuz berdi."
        await self.session.commit()
        return True, f"Kanal qo'shildi: {channel_title} | Tugma: {raw_button_text}"

    async def list_channels_text(self) -> str:
        channels = await self.channel_repo.get_active_channels()
        if not channels:
            return "Hozircha aktiv kanallar yo'q."

        lines = ["Aktiv kanallar:"]
        for idx, ch in enumerate(channels, start=1):
            label = ch.channel_username or str(ch.channel_id)
            title = ch.channel_title or "Nomalum"
            lines.append(
                f"{idx}. {title} (@{label})\n"
                f"   Tugma: {ch.button_text}\n"
                f"   Havola: {ch.button_url}"
            )
        return "\n".join(lines)

    async def remove_channel(self, raw_channel: str, *, removed_by: Optional[int] = None) -> tuple[bool, str]:
        value = raw_channel.strip()

        chat_id: Optional[int] = None
        if value.startswith("@"):
            username = value[1:]
            # `ilike()` emas — Telegram usernameda `_` bo'lishi mumkin, u
            # esa LIKE'da "istalgan bitta belgi" degani, boshqa kanalga
            # tegishli bo'lib qolishi mumkin edi (channel_repository.py
            # bilan bir xil tuzatish).
            result = await self.session.execute(
                select(Channel).where(func.lower(Channel.channel_username) == username.lower())
            )
            channel = result.scalar_one_or_none()
        else:
            try:
                chat_id = int(value)
            except ValueError:
                return False, "Kanal username yoki numeric ID kiriting."
            result = await self.session.execute(
                select(Channel).where(Channel.channel_id == chat_id)
            )
            channel = result.scalar_one_or_none()

        if not channel:
            return False, "Bunday kanal topilmadi."

        channel_id_for_log = channel.channel_id
        channel_username_for_log = channel.channel_username
        await self.session.delete(channel)
        if removed_by is not None:
            await self.log_action(
                removed_by,
                "channel.remove",
                target_type="channel",
                target_id=channel_id_for_log,
                payload={"username": channel_username_for_log},
            )
        await self.session.commit()
        return True, "Kanal muvaffaqiyatli o'chirildi."

    async def send_broadcast_test(
        self, chat_id: int, mode: str, src_chat_id: int, src_message_id: int
    ) -> tuple[bool, str]:
        """Yakuniy tasdiqdan OLDIN — xabarni faqat shu (odatda admin)
        chatiga yuborib ko'radi.

        Xabarning o'zi buzuq bo'lsa (bo'sh, buzuq HTML, o'chirilgan media)
        shu yerda ko'rinadi — real foydalanuvchilarga urinishdan oldin,
        hech kimga bekorga xato ketmaydi.
        """
        try:
            if mode == "forward":
                await self.bot.forward_message(
                    chat_id=chat_id, from_chat_id=src_chat_id, message_id=src_message_id
                )
            else:
                await self.bot.copy_message(
                    chat_id=chat_id, from_chat_id=src_chat_id, message_id=src_message_id
                )
            return True, "✅ Sinov yuborildi."
        except Exception as exc:  # noqa: BLE001 — adminga xom xatoni ko'rsatamiz
            return False, f"❌ Sinov muvaffaqiyatsiz: {str(exc)[:300]}"

    async def start_broadcast(
        self, admin_id: int, src_chat_id: int, src_message_id: int,
        mode: str, content_preview: Optional[str],
    ) -> Optional[Broadcast]:
        """Tarqatish yozuvini yaratadi. Haqiqiy yuborish bu yerda EMAS —
        chaqiruvchi `bot/services/broadcast_runner.start()` bilan fon
        vazifasini ishga tushiradi, aks holda adminning o'zi tarqatish
        tugaguncha bandlashib qolardi (aiogram FSM qulfi)."""
        if mode not in {"copy", "forward"}:
            raise ValueError("mode 'copy' yoki 'forward' bo'lishi kerak")

        total = await self.user_repo.count_broadcast_targets(exclude_user_id=admin_id)
        return await self.broadcast_repo.create_broadcast(
            created_by=admin_id,
            mode=mode,
            content_preview=content_preview,
            total_targets=total,
            src_chat_id=src_chat_id,
            src_message_id=src_message_id,
        )

    async def get_active_broadcast(self) -> Optional[Broadcast]:
        return await self.broadcast_repo.get_active()

    async def get_broadcast(self, broadcast_id: int) -> Optional[Broadcast]:
        return await self.broadcast_repo.get(broadcast_id)

    async def broadcast_history(self, limit: int = 10) -> list[Broadcast]:
        return await self.broadcast_repo.recent(limit=limit)

    async def pause_broadcast(self, broadcast_id: int) -> bool:
        return await self.broadcast_repo.request_pause(broadcast_id)

    async def resume_broadcast(self, broadcast_id: int) -> bool:
        return await self.broadcast_repo.resume(broadcast_id)

    async def cancel_broadcast(self, broadcast_id: int) -> bool:
        return await self.broadcast_repo.request_cancel(broadcast_id)

    # ── Foydalanuvchi boshqaruvi ───────────────────────────────

    STATUS_LABELS = {
        "active": "Aktiv",
        "blocked_bot": "Botni bloklagan",
        "banned": "Taqiqlangan",
        "deleted": "O'chirilgan / yetib bo'lmas",
    }

    async def find_users(self, raw_query: str) -> list[User]:
        """Telegram ID, ichki ID yoki @username bo'yicha qidiradi."""
        query = (raw_query or "").strip()
        if not query:
            return []

        digit_part = query.lstrip("@")
        if digit_part.lstrip("-").isdigit():
            number = int(digit_part)
            # Ikkalasini ham tekshiramiz (telegram_id VA ichki id) — agar
            # ikkalasi HAM mos kelsa (turli foydalanuvchilarga tegishli
            # bo'lsa), ikkalasini ham qaytaramiz, admin aniqlashtirsin.
            # Ilgari faqat telegram_id sinalardi, ichki id mosligi esa
            # HECH KIMGA bildirmasdan e'tiborsiz qoldirilardi — amalda
            # Telegram ID'lar (9-10 xonali) ichki id oralig'idan
            # (hozircha ~38 ming) ancha katta bo'lgani uchun to'qnashuv
            # ehtimoli past, lekin nolga teng emas.
            by_tg = await self.user_repo.get_by_telegram_id(number)
            by_id = await self.user_repo.get_by_id(number)
            if by_tg and by_id and by_tg.id != by_id.id:
                return [by_tg, by_id]
            if by_tg:
                return [by_tg]
            return [by_id] if by_id else []

        return await self.user_repo.find_by_username(query)

    @staticmethod
    def _limit_label(override: Optional[int], default: int) -> str:
        if override is None:
            return f"{default} (standart)"
        if override <= 0:
            return "cheksiz"
        return f"{override} (alohida)"

    async def format_user_card(self, user: User) -> str:
        """Admin uchun foydalanuvchi kartochkasi (HTML)."""
        settings_row = user.settings
        limits = await get_effective_limits(self.session)

        premium_until = settings_row.premium_until if settings_row else None
        is_vip = premium_until is not None and premium_until > utcnow()

        limit_text = self._limit_label(
            settings_row.daily_limit_override if settings_row else None,
            limits.translation,
        )
        tts_limit_text = self._limit_label(
            settings_row.tts_limit_override if settings_row else None,
            limits.tts,
        )
        image_limit_text = self._limit_label(
            settings_row.image_limit_override if settings_row else None,
            limits.image_vip if is_vip else limits.image_free,
        )

        usage = await self.usage_repo.get(user.id)
        used_today = usage.translations_count if usage else 0
        tts_today = usage.tts_count if usage else 0
        images_today = usage.images_count if usage else 0
        referrals = await self.user_repo.count_referrals(user.id)

        username = f"@{html_escape(user.username)}" if user.username else "—"
        name_parts = [user.first_name or "", user.last_name or ""]
        name = html_escape(" ".join(p for p in name_parts if p).strip() or "—")
        status = self.STATUS_LABELS.get(user.status, user.status)
        # `is_premium` — Telegram'ning o'zi (Telegram Premium client), bizning
        # VIP tizimimiz emas. Chalkashmasin deb ikkalasi alohida qatorda.
        tg_premium = "ha" if user.is_premium else "yo'q"
        source = html_escape(user.source) if user.source else "—"
        ui_lang = (settings_row.interface_lang if settings_row else None) or "—"
        direction = (
            f"{settings_row.source_lang} → {settings_row.target_lang}"
            if settings_row
            else "—"
        )

        if is_vip:
            vip_text = f"faol, {format_datetime(premium_until)} gacha"
        elif premium_until:
            vip_text = f"tugagan ({format_datetime(premium_until)})"
        else:
            vip_text = "yo'q"

        referred_line = ""
        if user.referred_by is not None:
            referrer = await self.user_repo.get_by_id(user.referred_by)
            ref_label = f"<code>{referrer.telegram_id}</code>" if referrer else str(user.referred_by)
            referred_line = f"\n👥 Taklif qilgan: {ref_label}"

        return (
            "👤 <b>Foydalanuvchi</b>\n\n"
            f"🆔 DB: <code>{user.id}</code>\n"
            f"📱 TG: <code>{user.telegram_id}</code>\n"
            f"👤 {name} · {username}\n"
            f"🏷 Rol: <b>{user.role}</b>\n"
            f"📊 Holat: <b>{status}</b>\n"
            f"⭐ Telegram Premium: {tg_premium}\n"
            f"💎 VIP: <b>{vip_text}</b>\n"
            f"🗣 Interfeys: {ui_lang}\n"
            f"🌐 Yo'nalish: {direction}\n"
            f"📈 Bugun: {used_today} tarjima · {tts_today} ovoz · {images_today} rasm\n"
            f"🔢 Tarjima limiti: <b>{limit_text}</b>\n"
            f"🔊 Ovoz limiti: <b>{tts_limit_text}</b>\n"
            f"🖼 Rasm limiti: <b>{image_limit_text}</b>\n"
            f"🎁 Taklif qilganlari: <b>{referrals}</b>"
            f"{referred_line}\n"
            f"🔗 Manba: {source}\n"
            f"🕐 Ko'rilgan: {format_datetime(user.last_seen_at)}\n"
            f"📅 Ro'yxat: {format_datetime(user.created_at)}"
        )

    def _protected_user(self, user: User) -> bool:
        """Admin/owner yoki env dagi adminlarni tasodifan taqiqlash mumkin emas."""
        if user.role in ("admin", "owner"):
            return True
        return user.telegram_id in settings.ADMIN_USER_IDS

    async def ban_user(self, admin_id: int, target_user_id: int) -> tuple[bool, str]:
        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if self._protected_user(user):
            return False, "Admin yoki owner ni taqiqlab bo'lmaydi."
        if user.status == "banned":
            return False, "Foydalanuvchi allaqachon taqiqlangan."

        await self.user_repo.set_status(user.id, "banned")
        await self.log_action(
            admin_id,
            "user.ban",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "prev_status": user.status},
        )
        await self.session.commit()
        return True, f"🚫 Taqiqlandi: <code>{user.telegram_id}</code>"

    async def unban_user(self, admin_id: int, target_user_id: int) -> tuple[bool, str]:
        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.status != "banned":
            return False, (
                f"Foydalanuvchi taqiqlangan emas (hozirgi holat: "
                f"{self.STATUS_LABELS.get(user.status, user.status)})."
            )

        await self.user_repo.set_status(user.id, "active")
        await self.log_action(
            admin_id,
            "user.unban",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id},
        )
        await self.session.commit()
        return True, f"✅ Taqiq olindi: <code>{user.telegram_id}</code>"

    async def set_user_limit(
        self, admin_id: int, target_user_id: int, limit: int
    ) -> tuple[bool, str]:
        if limit < 0:
            return False, "Limit manfiy bo'lishi mumkin emas. Cheksiz uchun 0 yuboring."
        if limit > 1_000_000:
            return False, "Limit juda katta (maks. 1 000 000)."

        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.settings is None:
            return False, "Foydalanuvchi sozlamalari topilmadi."

        await self.user_repo.set_daily_limit_override(user.id, limit)
        await self.log_action(
            admin_id,
            "user.set_limit",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "limit": limit},
        )
        await self.session.commit()

        label = "cheksiz" if limit <= 0 else str(limit)
        return True, (
            f"🔢 Limit yangilandi: <code>{user.telegram_id}</code> → "
            f"<b>{label}</b>"
        )

    async def clear_user_limit(
        self, admin_id: int, target_user_id: int
    ) -> tuple[bool, str]:
        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.settings is None:
            return False, "Foydalanuvchi sozlamalari topilmadi."

        prev = user.settings.daily_limit_override
        await self.user_repo.set_daily_limit_override(user.id, None)
        await self.log_action(
            admin_id,
            "user.clear_limit",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "prev_limit": prev},
        )
        await self.session.commit()
        return True, (
            f"♻️ Limit tozalandi: <code>{user.telegram_id}</code> → "
            f"standart ({settings.DAILY_TRANSLATION_LIMIT})"
        )

    async def set_user_tts_limit(
        self, admin_id: int, target_user_id: int, limit: int
    ) -> tuple[bool, str]:
        """`set_user_limit` bilan bir xil mantiq, ovoz (TTS) uchun."""
        if limit < 0:
            return False, "Limit manfiy bo'lishi mumkin emas. Cheksiz uchun 0 yuboring."
        if limit > 1_000_000:
            return False, "Limit juda katta (maks. 1 000 000)."

        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.settings is None:
            return False, "Foydalanuvchi sozlamalari topilmadi."

        await self.user_repo.set_tts_limit_override(user.id, limit)
        await self.log_action(
            admin_id,
            "user.set_tts_limit",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "limit": limit},
        )
        await self.session.commit()

        label = "cheksiz" if limit <= 0 else str(limit)
        return True, (
            f"🔊 Ovoz limiti yangilandi: <code>{user.telegram_id}</code> → "
            f"<b>{label}</b>"
        )

    async def clear_user_tts_limit(
        self, admin_id: int, target_user_id: int
    ) -> tuple[bool, str]:
        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.settings is None:
            return False, "Foydalanuvchi sozlamalari topilmadi."

        prev = user.settings.tts_limit_override
        await self.user_repo.set_tts_limit_override(user.id, None)
        await self.log_action(
            admin_id,
            "user.clear_tts_limit",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "prev_limit": prev},
        )
        await self.session.commit()
        return True, (
            f"♻️ Ovoz limiti tozalandi: <code>{user.telegram_id}</code> → "
            f"standart ({settings.DAILY_TTS_LIMIT})"
        )

    async def set_user_image_limit(
        self, admin_id: int, target_user_id: int, limit: int
    ) -> tuple[bool, str]:
        """`set_user_limit` bilan bir xil mantiq, rasm (OCR) uchun."""
        if limit < 0:
            return False, "Limit manfiy bo'lishi mumkin emas. Cheksiz uchun 0 yuboring."
        if limit > 1_000_000:
            return False, "Limit juda katta (maks. 1 000 000)."

        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.settings is None:
            return False, "Foydalanuvchi sozlamalari topilmadi."

        await self.user_repo.set_image_limit_override(user.id, limit)
        await self.log_action(
            admin_id,
            "user.set_image_limit",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "limit": limit},
        )
        await self.session.commit()

        label = "cheksiz" if limit <= 0 else str(limit)
        return True, (
            f"🖼 Rasm limiti yangilandi: <code>{user.telegram_id}</code> → "
            f"<b>{label}</b>"
        )

    async def clear_user_image_limit(
        self, admin_id: int, target_user_id: int
    ) -> tuple[bool, str]:
        user = await self.user_repo.get_by_id(target_user_id)
        if not user:
            return False, "Foydalanuvchi topilmadi."
        if user.settings is None:
            return False, "Foydalanuvchi sozlamalari topilmadi."

        prev = user.settings.image_limit_override
        await self.user_repo.set_image_limit_override(user.id, None)
        await self.log_action(
            admin_id,
            "user.clear_image_limit",
            target_type="user",
            target_id=user.id,
            payload={"telegram_id": user.telegram_id, "prev_limit": prev},
        )
        await self.session.commit()
        limits = await get_effective_limits(self.session)
        return True, (
            f"♻️ Rasm limiti tozalandi: <code>{user.telegram_id}</code> → "
            f"standart (oddiy {limits.image_free} / VIP {limits.image_vip})"
        )

    # ── Audit ────────────────────────────────────────────────

    ACTION_LABELS = {
        "user.ban": "🚫 Bloklash",
        "user.unban": "✅ Blokdan chiqarish",
        "user.set_limit": "🔢 Tarjima limiti",
        "user.clear_limit": "♻️ Tarjima limiti tozalandi",
        "user.set_tts_limit": "🔊 Ovoz limiti",
        "user.clear_tts_limit": "♻️ Ovoz limiti tozalandi",
        "user.set_image_limit": "🖼 Rasm limiti",
        "user.clear_image_limit": "♻️ Rasm limiti tozalandi",
        "system.set_limit": "⚙️ Umumiy limit o'zgardi",
    }

    async def format_recent_actions(self, limit: int = 20) -> str:
        """Oxirgi admin amallari — kim, qachon, nima qildi.

        `AdminAction` yozadigan har bir amal (`log_action`) shu yerda
        ko'rinadi. Admin identifikatorini o'qishga qulay qilish uchun
        `users` bilan LEFT JOIN qilinadi — admin o'chirilgan/topilmasa ham
        (`ondelete="SET NULL"`) qator ko'rinishda qoladi.
        """
        rows = (
            await self.session.execute(
                select(AdminAction, User)
                .outerjoin(User, User.id == AdminAction.admin_id)
                .order_by(AdminAction.id.desc())
                .limit(limit)
            )
        ).all()

        if not rows:
            return "📜 Hozircha audit yozuvlari yo'q."

        lines = [f"📜 <b>Oxirgi amallar</b> (so'nggi {len(rows)} ta)\n"]
        for action, admin in rows:
            label = self.ACTION_LABELS.get(action.action, action.action)
            who = f"@{admin.username}" if admin and admin.username else (
                str(admin.telegram_id) if admin else "—"
            )
            target = f" → {action.target_id}" if action.target_id else ""
            lines.append(
                f"{format_datetime(action.created_at)} · {label}\n"
                f"   👤 {who}{target}"
            )
        return "\n".join(lines)
