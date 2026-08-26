import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

    async def broadcast(self, data: dict):
        for user_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(json.dumps(data))
            except Exception:
                self.disconnect(user_id)

manager = ConnectionManager()

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    try:
        while True:
            raw_data = await websocket.receive_text()
            message_data = json.loads(raw_data)
            
            # Ответ на пинг для поддержания соединения активным
            if message_data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            await manager.broadcast(message_data)
    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception:
        manager.disconnect(user_id)
