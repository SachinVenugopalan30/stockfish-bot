import pytest
import json
from app.api.websocket import ConnectionManager


async def test_connection_manager_broadcast():
    manager = ConnectionManager()

    messages_received = []

    class MockWebSocket:
        async def accept(self):
            pass

        async def send_text(self, data):
            messages_received.append(json.loads(data))

        async def receive_text(self):
            pass

    ws = MockWebSocket()
    await manager.connect(ws)
    assert manager.connection_count == 1

    await manager.broadcast({"type": "trade", "ticker": "AAPL", "action": "buy"})
    assert len(messages_received) == 1
    assert messages_received[0]["ticker"] == "AAPL"


async def test_connection_manager_disconnect():
    manager = ConnectionManager()

    class MockWebSocket:
        async def accept(self):
            pass

        async def send_text(self, data):
            pass

    ws = MockWebSocket()
    await manager.connect(ws)
    assert manager.connection_count == 1
    manager.disconnect(ws)
    assert manager.connection_count == 0


async def test_broadcast_no_connections():
    manager = ConnectionManager()
    # Should not raise
    await manager.broadcast({"type": "test"})


async def test_broadcast_removes_dead_connections():
    manager = ConnectionManager()

    class BrokenWebSocket:
        async def accept(self):
            pass

        async def send_text(self, data):
            raise RuntimeError("Connection closed")

    ws = BrokenWebSocket()
    await manager.connect(ws)
    assert manager.connection_count == 1

    await manager.broadcast({"type": "test"})
    # Dead connection should be cleaned up
    assert manager.connection_count == 0
