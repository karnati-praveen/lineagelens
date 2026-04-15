from collections import defaultdict

from fastapi import WebSocket


class WebSocketConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, workspace_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[workspace_id].add(websocket)

    def disconnect(self, workspace_id: str, websocket: WebSocket) -> None:
        if workspace_id not in self._connections:
            return

        self._connections[workspace_id].discard(websocket)
        if not self._connections[workspace_id]:
            self._connections.pop(workspace_id, None)

    async def broadcast(self, workspace_id: str, payload: dict) -> None:
        stale: list[WebSocket] = []

        for websocket in self._connections.get(workspace_id, set()):
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)

        for websocket in stale:
            self.disconnect(workspace_id, websocket)
