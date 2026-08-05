import asyncio
import logging
from datetime import datetime
from typing import Optional, Awaitable, Callable
import aiohttp
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramRetryAfter,
    TelegramAPIError,
    ClientDecodeError,
)
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from bot.config.settings import settings
from bot.database.models import AdminAction, Channel, User
from bot.database.repositories.user_repository import UserRepository
from bot.database.repositories.channel_repository import ChannelRepository
from bot.database.repositories.broadcast_repository import BroadcastRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.database.repositories.usage_repository import UsageRepository
from bot.services.broadcast import (
    DEFAULT_CONCURRENCY,
    DEFAULT_RATE_PER_SEC,
    RateLimiter,
    is_permanently_unreachable,
)
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

    async def get_stats(self) -> dict:
        total_users = await self.user_repo.count_total()
        active_week = await self.user_repo.count_active_since(days=7)
        active_day = await self.user_repo.count_active_since(days=1)
        new_day = await self.user_repo.count_new_since(days=1)
        by_status = await self.user_repo.count_by_status()

        channels_result = await self.session.execute(
            select(func.count(Channel.id)).where(Channel.is_active.is_(True))
        )
        active_channels = channels_result.scalar_one() or 0

        translations_total = await self.translation_repo.count_total()
        translations_day = await self.translation_repo.count_since(days=1)
        errors_day, attempts_day = await self.translation_repo.error_rate_since(days=1)
        top_pairs = await self.translation_repo.top_language_pairs(limit=5)

        return {
            "total_users": total_users,
            "active_week": active_week,
            "active_day": active_day,
            "new_day": new_day,
            "blocked": by_status.get("blocked_bot", 0),
            "active_channels": active_channels,
            "translations_total": translations_total,
            "translations_day": translations_day,
            "errors_day": errors_day,
            "attempts_day": attempts_day,
            "top_pairs": top_pairs,
        }

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
        except IntegrityError:
            await self.session.rollback()
            return False, "Bu kanal (yoki username) allaqachon mavjud."
        except Exception as exc:
            await self.session.rollback()
            logger.exception("Unexpected DB error while creating channel %s", chat_id)
            return False, "Kanalni saqlashda kutilmagan xatolik yuz berdi."
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

    async def remove_channel(self, raw_channel: str) -> tuple[bool, str]:
        value = raw_channel.strip()

        chat_id: Optional[int] = None
        if value.startswith("@"):
            username = value[1:]
            result = await self.session.execute(
                select(Channel).where(Channel.channel_username.ilike(username))
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

        await self.session.delete(channel)
        await self.session.commit()
        return True, "Kanal muvaffaqiyatli o'chirildi."

    async def run_broadcast(
        self,
        admin_id: int,
        source_message: Message,
        mode: str,
        progress_callback: Optional[Callable[[int, int, int, int], Awaitable[None]]] = None,
    ) -> dict:
        """Xabarni barcha aktiv foydalanuvchilarga tarqatadi.

        Tezlik ikki mexanizm bilan boshqariladi (batafsil: services/broadcast.py):
        semafor tarmoq kutishini yashiradi, `RateLimiter` esa Telegram'ga
        ketadigan chaqiruvlar oqimini tekis ushlab turadi. Ketma-ket yuborishda
        tezlik ~5/sek bilan cheklangan edi; bu yerda ~15/sek.
        """
        if mode not in {"copy", "forward"}:
            raise ValueError("mode 'copy' yoki 'forward' bo'lishi kerak")

        # `(user_id, telegram_id)` juftliklari: birinchisi FK uchun, ikkinchisi
        # Telegram API uchun. Sxemada FK'lar `users.id` ga qaraydi.
        targets = await self.user_repo.iter_broadcast_targets(exclude_user_id=admin_id)
        preview = (source_message.text or source_message.caption or "<media>")[:500]
        broadcast = await self.broadcast_repo.create_broadcast(
            created_by=admin_id,
            mode=mode,
            content_preview=preview,
            total_targets=len(targets),
        )

        total = len(targets)
        limiter = RateLimiter(DEFAULT_RATE_PER_SEC)
        semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

        success = 0
        failed = 0
        blocked = 0
        unreachable = 0
        recovered = 0
        failures: list[tuple[int, str]] = []
        cancelled = False

        async def deliver(telegram_id: int) -> tuple[bool, Optional[str]]:
            """Telegram'ga yuboradi. `(yetdimi, xato_matni)` qaytaradi.

            Bazaga ataylab tegmaydi. `AsyncSession` parallel ishlatishga
            xavfsiz emas: bir nechta korutina bitta sessiyada `execute()`
            chaqirsa sessiya holati buziladi. Shuning uchun yozishni
            chaqiruvchi batch tugagach ketma-ket bajaradi.
            """
            async with semaphore:
                error_text: Optional[str] = None

                for attempt in range(3):
                    await limiter.wait()
                    try:
                        if mode == "forward":
                            await self.bot.forward_message(
                                chat_id=telegram_id,
                                from_chat_id=source_message.chat.id,
                                message_id=source_message.message_id,
                            )
                        else:
                            await self.bot.copy_message(
                                chat_id=telegram_id,
                                from_chat_id=source_message.chat.id,
                                message_id=source_message.message_id,
                            )
                        return True, None
                    except TelegramRetryAfter as exc:
                        wait = max(int(getattr(exc, "retry_after", 1)), 1)
                        # Limitga bitta so'rov tegsa qolgani ham tegadi —
                        # barcha yuboruvchilarni birga kechiktiramiz, aks holda
                        # flood-wait cho'zilib ketardi.
                        limiter.pause(wait + 0.5)
                        await asyncio.sleep(wait)
                    except TelegramForbiddenError:
                        # Botni bloklagan yoki akkauntni o'chirgan.
                        return False, "forbidden"
                    except TelegramBadRequest as exc:
                        # Chat topilmadi va shunga o'xshash qaytarib bo'lmaydigan
                        # xatolar — qayta urinish foydasiz.
                        return False, str(exc)[:200]
                    except TelegramAPIError as exc:
                        error_text = str(exc)[:200]
                        await asyncio.sleep(1 + attempt)

                return False, error_text or "noma'lum"

        # Batch'lar: Telegram chaqiruvlari parallel, bazaga yozish va bekor
        # qilish tekshiruvi batch oxirida ketma-ket.
        batch_size = 50
        for offset in range(0, total, batch_size):
            status = await self.broadcast_repo.get_status(broadcast.id)
            if status == "cancel_requested":
                cancelled = True
                break

            batch = targets[offset : offset + batch_size]
            results = await asyncio.gather(
                *(deliver(tg_id) for _, tg_id in batch),
                return_exceptions=True,
            )

            reached: list[int] = []

            for (user_id, telegram_id), result in zip(batch, results):
                if isinstance(result, BaseException):
                    delivered, error_text = False, str(result)[:200]
                else:
                    delivered, error_text = result

                if delivered:
                    success += 1
                    reached.append(user_id)
                    await self.broadcast_repo.add_delivery(
                        broadcast.id, user_id, "delivered"
                    )
                    continue

                failed += 1
                failures.append((telegram_id, error_text or "noma'lum"))
                await self.broadcast_repo.add_delivery(
                    broadcast.id, user_id, "failed", error_text
                )

                if error_text == "forbidden":
                    # Bloklagan — qaytishi mumkin, `/start` da tiklanadi.
                    await self.user_repo.mark_blocked(user_id)
                    blocked += 1
                elif is_permanently_unreachable(error_text):
                    # Chat umuman yo'q — keyingi tarqatishlarga tushmasin.
                    await self.user_repo.mark_unreachable(user_id)
                    unreachable += 1

            # Bloklagan deb belgilangan odamga xabar yetib borgan bo'lsa —
            # u blokdan chiqargan. Telegram bu haqda xabar bermaydi, bilishning
            # yagona yo'li shu. Bitta so'rov: allaqachon `active` bo'lganlarga
            # ta'sir qilmaydi.
            recovered += await self.user_repo.mark_many_unblocked(reached)

            await self.session.commit()

            if progress_callback:
                await progress_callback(success + failed, total, success, failed)

        processed = success + failed
        await self.session.commit()

        if cancelled:
            await self.broadcast_repo.mark_cancelled(broadcast.id, success, failed)
            status_label = "cancelled"
        elif failed == total and total > 0:
            await self.broadcast_repo.fail_broadcast(broadcast.id, failed)
            status_label = "failed"
        else:
            await self.broadcast_repo.finish_broadcast(broadcast.id, success, failed)
            status_label = "completed"

        return {
            "broadcast_id": broadcast.id,
            "status": status_label,
            "total": total,
            "processed": processed,
            "success": success,
            "failed": failed,
            "blocked": blocked,
            "unreachable": unreachable,
            "recovered": recovered,
            "failures": failures,
        }

    async def get_running_broadcasts_text(self) -> str:
        broadcasts = await self.broadcast_repo.get_running_broadcasts()
        if not broadcasts:
            return "Hozir aktiv broadcast yo'q."

        lines = ["Aktiv broadcastlar:"]
        for b in broadcasts:
            lines.append(
                f"ID: {b.id} | status: {b.status} | total: {b.total_targets} | success: {b.success_count} | failed: {b.failed_count}"
            )
        return "\n".join(lines)

    async def request_broadcast_cancel(self, broadcast_id: int) -> bool:
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
            # Avval telegram_id (admin odatda shuni yuboradi), keyin ichki id.
            by_tg = await self.user_repo.get_by_telegram_id(number)
            if by_tg:
                return [by_tg]
            by_id = await self.user_repo.get_by_id(number)
            return [by_id] if by_id else []

        return await self.user_repo.find_by_username(query)

    async def format_user_card(self, user: User) -> str:
        """Admin uchun foydalanuvchi kartochkasi (HTML)."""
        settings_row = user.settings
        override = settings_row.daily_limit_override if settings_row else None
        if override is None:
            limit_text = f"{settings.DAILY_TRANSLATION_LIMIT} (standart)"
        elif override <= 0:
            limit_text = "cheksiz"
        else:
            limit_text = f"{override} (alohida)"

        usage = await self.usage_repo.get(user.id)
        used_today = usage.translations_count if usage else 0
        tts_today = usage.tts_count if usage else 0

        username = f"@{html_escape(user.username)}" if user.username else "—"
        name_parts = [user.first_name or "", user.last_name or ""]
        name = html_escape(" ".join(p for p in name_parts if p).strip() or "—")
        status = self.STATUS_LABELS.get(user.status, user.status)
        premium = "ha" if user.is_premium else "yo'q"
        source = html_escape(user.source) if user.source else "—"
        ui_lang = (settings_row.interface_lang if settings_row else None) or "—"
        direction = (
            f"{settings_row.source_lang} → {settings_row.target_lang}"
            if settings_row
            else "—"
        )

        return (
            "👤 <b>Foydalanuvchi</b>\n\n"
            f"🆔 DB: <code>{user.id}</code>\n"
            f"📱 TG: <code>{user.telegram_id}</code>\n"
            f"👤 {name} · {username}\n"
            f"🏷 Rol: <b>{user.role}</b>\n"
            f"📊 Holat: <b>{status}</b>\n"
            f"⭐ Premium: {premium}\n"
            f"🗣 Interfeys: {ui_lang}\n"
            f"🌐 Yo'nalish: {direction}\n"
            f"📈 Bugun: {used_today} tarjima · {tts_today} ovoz\n"
            f"🔢 Kunlik limit: <b>{limit_text}</b>\n"
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
