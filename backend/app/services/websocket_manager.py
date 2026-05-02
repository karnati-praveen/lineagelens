from collections import defaultdict
import asyncio

from fastapi import WebSocket


class WebSocketConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, workspace_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[workspace_id].add(websocket)

    async def disconnect(self, workspace_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if workspace_id not in self._connections:
                return

            self._connections[workspace_id].discard(websocket)
            if not self._connections[workspace_id]:
                self._connections.pop(workspace_id, None)

    async def broadcast(self, workspace_id: str, payload: dict) -> None:
        stale: list[WebSocket] = []
        async with self._lock:
            sockets = list(self._connections.get(workspace_id, set()))

        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)

        for websocket in stale:
            await self.disconnect(workspace_id, websocket)
