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

from bot.database.models import User, Channel
from bot.database.repositories.user_repository import UserRepository
from bot.database.repositories.channel_repository import ChannelRepository
from bot.database.repositories.broadcast_repository import BroadcastRepository


logger = logging.getLogger(__name__)


class AdminService:
    """Business logic for admin panel"""

    def __init__(self, session: AsyncSession, bot: Bot):
        self.session = session
        self.bot = bot
        self.user_repo = UserRepository(session)
        self.channel_repo = ChannelRepository(session)
        self.broadcast_repo = BroadcastRepository(session)

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
        total_users = await self.user_repo.get_total_users()
        active_week = await self.user_repo.get_active_users(days=7)

        channels_result = await self.session.execute(select(func.count(Channel.id)).where(Channel.is_active == True))
        active_channels = channels_result.scalar_one() or 0

        return {
            "total_users": total_users,
            "active_week": active_week,
            "active_channels": active_channels,
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
        """Run robust broadcast with retries and delivery journaling."""
        if mode not in {"copy", "forward"}:
            raise ValueError("mode must be 'copy' or 'forward'")

        user_ids = await self.user_repo.get_all_active_user_ids(exclude_user_id=admin_id)
        preview = (source_message.text or source_message.caption or "<media>")[:500]
        broadcast = await self.broadcast_repo.create_broadcast(
            created_by=admin_id,
            mode=mode,
            content_preview=preview,
            total_targets=len(user_ids),
        )

        success = 0
        failed = 0
        cancelled = False
        total = len(user_ids)

        for i, user_id in enumerate(user_ids, start=1):
            status = await self.broadcast_repo.get_status(broadcast.id)
            if status == "cancel_requested":
                cancelled = True
                break

            delivered = False
            error_text: Optional[str] = None

            for attempt in range(3):
                try:
                    if mode == "forward":
                        await self.bot.forward_message(
                            chat_id=user_id,
                            from_chat_id=source_message.chat.id,
                            message_id=source_message.message_id,
                        )
                    else:
                        await self.bot.copy_message(
                            chat_id=user_id,
                            from_chat_id=source_message.chat.id,
                            message_id=source_message.message_id,
                        )
                    delivered = True
                    break
                except TelegramRetryAfter as e:
                    wait_time = max(int(getattr(e, "retry_after", 1)), 1)
                    await asyncio.sleep(wait_time)
                except TelegramForbiddenError:
                    error_text = "forbidden"
                    await self.user_repo.mark_user_blocked(user_id)
                    break
                except TelegramBadRequest as e:
                    error_text = str(e)
                    break
                except TelegramAPIError as e:
                    error_text = str(e)
                    await asyncio.sleep(1 + attempt)

            if delivered:
                success += 1
                await self.broadcast_repo.add_delivery(broadcast.id, user_id, "delivered")
            else:
                failed += 1
                await self.broadcast_repo.add_delivery(broadcast.id, user_id, "failed", error_text)

            # Batch commit every 100 deliveries instead of per-user
            if i % 100 == 0:
                await self.session.commit()

            if progress_callback and (i % 10 == 0 or i == total):
                await progress_callback(i, total, success, failed)

            # Soft rate-limit to avoid flood and keep bot stable on large sends.
            if i % 20 == 0:
                await asyncio.sleep(1)

        processed = success + failed

        # Flush any remaining uncommitted deliveries
        await self.session.commit()

        if cancelled:
            await self.broadcast_repo.mark_cancelled(broadcast.id, success, failed)
            return {
                "broadcast_id": broadcast.id,
                "status": "cancelled",
                "total": total,
                "processed": processed,
                "success": success,
                "failed": failed,
            }

        if failed == total and total > 0:
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
