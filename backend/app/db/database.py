from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

engine = None
async_session_factory = None


async def init_db(database_url: str):
    global engine, async_session_factory
    engine = create_async_engine(database_url, echo=False, pool_size=20, max_overflow=10)
    async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Dispose the pool and let aiomysql's finalizers run WHILE the loop is still
    alive.

    D-102 (2026-09-11): without this the strategy worker exited 1 on every clean
    shutdown with `RuntimeError: Event loop is closed` raised from
    aiomysql.Connection.__del__ — the GC ran the connection finalizer after
    asyncio.run() had already closed the loop, so systemd recorded a FAILURE and
    a restart on what was actually a clean stop (seen 2026-09-06 13:38 and
    2026-09-08 11:40). Dropping the references, forcing a collection and yielding
    to the loop makes the finalizers run in-loop and the exit code 0."""
    global engine, async_session_factory
    if engine:
        await engine.dispose()
    engine = None
    async_session_factory = None
    import asyncio as _asyncio
    import gc as _gc
    _gc.collect()
    await _asyncio.sleep(0)      # let any queued finalizer callbacks run in-loop


def get_session_factory():
    return async_session_factory
