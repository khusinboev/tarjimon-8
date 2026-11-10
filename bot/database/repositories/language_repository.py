from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Language


class LanguageRepository:
    """Tillar ma'lumotnomasi. Deyarli o'zgarmaydi — jarayon xotirasida keshlanadi."""

    _cache: Optional[List[Language]] = None

    def __init__(self, session: AsyncSession):
        self.session = session

    async def all_active(self) -> List[Language]:
        if LanguageRepository._cache is None:
            result = await self.session.execute(
                select(Language)
                .where(Language.is_active.is_(True))
                .order_by(Language.sort_order, Language.code)
            )
            LanguageRepository._cache = list(result.scalars().all())
            # Session'dan uzamiz — kesh jarayon davomida yashaydi, session esa yo'q.
            for lang in LanguageRepository._cache:
                self.session.expunge(lang)
        return LanguageRepository._cache

    async def selectable(self, *, include_auto: bool) -> List[Language]:
        """Menyuda ko'rsatiladigan tillar.

        `auto` faqat manba tili sifatida mantiqiy — hech kim "avto tilga tarjima
        qil" deb so'ramaydi.
        """
        langs = await self.all_active()
        if include_auto:
            return langs
        return [lang for lang in langs if lang.code != "auto"]

    async def by_code(self, code: str) -> Optional[Language]:
        langs = await self.all_active()
        return next((lang for lang in langs if lang.code == code), None)

    async def as_dict(self) -> Dict[str, Language]:
        return {lang.code: lang for lang in await self.all_active()}

    async def tts_voice(self, code: str) -> Optional[str]:
        lang = await self.by_code(code)
        return lang.tts_voice if lang and lang.supports_tts else None

    @classmethod
    def invalidate_cache(cls) -> None:
        """Admin tillar ro'yxatini o'zgartirganda chaqiriladi."""
        cls._cache = None
