from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

INSERT = text("INSERT INTO preferences (user_id, key, value) VALUES (:u, 'probe', '{}')")
COUNT = text("SELECT count(*) FROM preferences WHERE user_id = :u AND key = 'probe'")


async def test_commit_inside_test_is_only_a_savepoint(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    await session.execute(INSERT, {"u": "fixture-probe"})
    await session.commit()  # code under test may commit; it must not escape the test
    assert await session.scalar(COUNT, {"u": "fixture-probe"}) == 1

    async with engine.connect() as other:  # a second connection sees nothing
        assert await other.scalar(COUNT, {"u": "fixture-probe"}) == 0


async def test_nothing_leaks_between_tests(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert await conn.scalar(COUNT, {"u": "fixture-probe"}) == 0
