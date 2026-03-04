import asyncio
from backend.app.jobs.sync_models_stdb import sync_models_stdb
from sqlalchemy.ext.asyncio import async_sessionmaker

async def test():
    # Create a dummy session factory (won't be used)
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await sync_models_stdb(async_session_factory)
    print("Sync completed")

asyncio.run(test())