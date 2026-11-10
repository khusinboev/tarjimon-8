from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from bot.config.settings import settings
from bot.database.models import Base

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=3600,
)

# Create session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class StatementScopedSession:
    """`AsyncSession`ning tor o'rami: har bir `execute()` o'z tranzaksiyasini
    DARHOL yakunlaydi (commit) — ulanish pool'ga qaytadi.

    Nega kerak: `TranslationService.translate(session, ...)` va
    `ocr.extract_text(session, ...)` tashqi HTTP chaqiruvdan OLDIN bazadan
    kalit hisobini (`pick_key`, OCR hisobi) o'qiydi. Oddiy sessiyada o'sha
    SELECT yangi tranzaksiya boshlab qo'yadi va pool ulanishi 15–60 s
    davom etadigan provayder chaqiruvi tugaguncha band qolardi — ~30 ta
    sekin tarjima pool'ni (10+20) to'ldirib, HAMMA yangi update yiqilardi.
    Bu servislar sessiyadan faqat `execute()`/`scalar()` ishlatadi, shuning
    uchun bitta o'ram yetarli — servis kodiga tegilmaydi.

    `AsyncSession.execute()` natijani oldindan buferlaydi (`prebuffer_rows`),
    shuning uchun commit'dan keyin ham `.all()`/`.scalar_one()` ishlaydi.
    Xato bo'lsa rollback qilinadi — asosiy sessiya "buzilgan tranzaksiya"
    holatida qolib, keyingi yozuvlarni (xato qatori, kvota qaytarish)
    yiqitmasin.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(self, *args, **kwargs):
        try:
            result = await self.session.execute(*args, **kwargs)
        except BaseException:
            await self.session.rollback()
            raise
        await self.session.commit()
        return result

    async def scalar(self, *args, **kwargs):
        return (await self.execute(*args, **kwargs)).scalar()

    async def scalars(self, *args, **kwargs):
        return (await self.execute(*args, **kwargs)).scalars()

    def in_transaction(self) -> bool:
        return self.session.in_transaction()


async def get_session() -> AsyncSession:
    """Get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initialize database (create tables)"""
    async with engine.begin() as conn:
        # Explicitly controlled for local/dev bootstrap only.
        if settings.AUTO_CREATE_SCHEMA:
            await conn.run_sync(Base.metadata.create_all)
