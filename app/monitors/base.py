import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime

from app.database import async_session_factory
from app.models import MonitorHeartbeat

logger = logging.getLogger(__name__)

class BaseMonitor(ABC):
    def __init__(self, name: str):
        self.name = name
        self._running = False
        self._task: asyncio.Task = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info(f"{self.name} monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"{self.name} monitor stopped")

    @abstractmethod
    async def _run(self) -> None:
        ...

    async def record_heartbeat(self) -> None:
        async with async_session_factory() as session:
            result = await session.execute(
                MonitorHeartbeat.__table__.select().where(MonitorHeartbeat.monitor == self.name)
            )
            hb = result.fetchone()
            if hb:
                await session.execute(
                    MonitorHeartbeat.__table__.update()
                    .where(MonitorHeartbeat.monitor == self.name)
                    .values(last_beat=datetime.utcnow())
                )
            else:
                session.add(MonitorHeartbeat(monitor=self.name, last_beat=datetime.utcnow()))
            await session.commit()
