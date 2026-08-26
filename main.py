import asyncio
import json
import uuid
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

app = FastAPI()

# База данных в памяти сервера
users_db = {}        # user_id: {id, username, name, phone, bio, avatar_color, online, last_seen}
chats_db = {}        # chat_id: {id, type, name, members: [user_id], created_by, pinned_msg_id}
messages_db = {}     # chat_id: [ {id, sender_id, sender_name, text, time, reactions, is_pinned, is_voice, media_url} ]
active_sockets = {}  # user_id: WebSocket

class ConnectionManager:
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        active_sockets[user_id] = websocket
        if user_id in users_db:
            users_db[user_id]["online"] = True
            await self.broadcast_user_status(user_id, True)

    def disconnect(self, user_id: str):
        if user_id in active_sockets:
            del active_sockets[user_id]
        if user_id in users_db:
            users_db[user_id]["online"] = False
            users_db[user_id]["last_seen"] = datetime.now().strftime("%H:%M")
            asyncio.create_task(self.broadcast_user_status(user_id, False))

    async def broadcast_user_status(self, user_id: str, is_online: bool):
        payload = {
            "type": "status_update",
            "user_id": user_id,
            "online": is_online,
            "last_seen": users_db[user_id].get("last_seen", "недавно")
        }
        for ws in list(active_sockets.values()):
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                pass

    async def send_personal(self, user_id: str, message: dict):
        if user_id in active_sockets:
            try:
                await active_sockets[user_id].send_text(json.dumps(message))
            except Exception:
                pass

    async def broadcast_to_chat(self, chat_id: str, message: dict):
        chat = chats_db.get(chat_id)
        if not chat:
            return
        for member_id in chat["members"]:
            await self.send_personal(member_id, message)

manager = ConnectionManager()

# ----------------- HTTP REST API -----------------

@app.get("/")
def home():
    return {"status": "Telegram Clone Engine Online", "active_users": len(active_sockets)}

class AuthRequest(BaseModel):
    username: str
    name: str
    phone: str

@app.post("/api/auth")
def auth_user(req: AuthRequest):
    username_clean = req.username.strip().replace("@", "").lower()
    # Ищем существующего пользователя
    for uid, user in users_db.items():
        if user["username"].lower() == username_clean:
            return {"status": "ok", "user": user}
    
    # Регистрация нового аккаунта
    new_id = f"user_{uuid.uuid4().hex[:8]}"
    new_user = {
        "id": new_id,
        "username": username_clean,
        "name": req.name.strip(),
        "phone": req.phone.strip(),
        "bio": "Использую Aura Messenger 🚀",
        "avatar_color": len(req.name) % 8,
        "online": True,
        "last_seen": "в сети",
    }
    users_db[new_id] = new_user

    # Автоматически создаем чат «Избранное» (Saved Messages)
    saved_chat_id = f"saved_{new_id}"
    chats_db[saved_chat_id] = {
        "id": saved_chat_id,
        "type": "saved",
        "name": "Избранное",
        "members": [new_id],
        "created_by": new_id,
        "pinned_msg_id": None
    }
    messages_db[saved_chat_id] = []
    
    return {"status": "ok", "user": new_user}

@app.get("/api/users/search")
def search_users(query: str):
    q = query.strip().replace("@", "").lower()
    results = [
        u for u in users_db.values() 
        if q in u["username"].lower() or q in u["name"].lower() or q in u["phone"]
    ]
    return {"results": results}

@app.get("/api/chats/{user_id}")
def get_user_chats(user_id: str):
    user_chats = []
    for cid, chat in chats_db.items():
        if user_id in chat["members"]:
            msgs = messages_db.get(cid, [])
            last_msg = msgs[-1] if msgs else {"text": "Чат создан", "time": "", "sender_name": ""}
            
            # Для личного диалога подставляем имя и статус собеседника
            chat_title = chat["name"]
            avatar_letter = chat_title[0] if chat_title else "?"
            is_online = False
            status_text = ""
            
            if chat["type"] == "personal":
                other_ids = [m for m in chat["members"] if m != user_id]
                if other_ids and other_ids[0] in users_db:
                    partner = users_db[other_ids[0]]
                    chat_title = partner["name"]
                    avatar_letter = partner["name"][0]
                    is_online = partner.get("online", False)
                    status_text = "в сети" if is_online else f"был(а) в {partner.get('last_seen', 'недавно')}"
            elif chat["type"] == "saved":
                chat_title = "Избранное"
                avatar_letter = "⭐️"
                status_text = "сохраненные сообщения"
            else:
                status_text = f"{len(chat['members'])} участников"

            user_chats.append({
                "id": cid,
                "type": chat["type"],
                "name": chat_title,
                "avatar_letter": avatar_letter,
                "last_message": last_msg.get("text", ""),
                "time": last_msg.get("time", ""),
                "is_online": is_online,
                "status_text": status_text,
                "members": chat["members"],
                "pinned_msg_id": chat.get("pinned_msg_id")
            })
    return {"chats": user_chats}

# ----------------- WEBSOCKET ENGINE -----------------

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            # Отправка обычного или голосового сообщения
            elif msg_type == "send_message":
                chat_id = data["chat_id"]
                msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                time_str = datetime.now().strftime("%H:%M")
                
                new_msg = {
                    "id": msg_id,
                    "chat_id": chat_id,
                    "sender_id": user_id,
                    "sender_name": users_db.get(user_id, {}).get("name", "Пользователь"),
                    "text": data.get("text", ""),
                    "time": time_str,
                    "reactions": {}, # {"❤️": 2, "🔥": 1}
                    "is_pinned": False,
                    "is_voice": data.get("is_voice", False),
                    "voice_duration": data.get("voice_duration", 0),
                    "media_type": data.get("media_type", "none"),
                }
                
                if chat_id not in messages_db:
                    messages_db[chat_id] = []
                messages_db[chat_id].append(new_msg)

                await manager.broadcast_to_chat(chat_id, {
                    "type": "new_message",
                    "message": new_msg
                })

            # Статус "Печатает..." (Typing...)
            elif msg_type == "typing":
                chat_id = data["chat_id"]
                await manager.broadcast_to_chat(chat_id, {
                    "type": "user_typing",
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "user_name": users_db.get(user_id, {}).get("name", "Пользователь")
                })

            # Реакция на сообщение (🔥, ❤️, 👍, 😂, 👎)
            elif msg_type == "add_reaction":
                chat_id = data["chat_id"]
                msg_id = data["msg_id"]
                emoji = data["emoji"]
                if chat_id in messages_db:
                    for m in messages_db[chat_id]:
                        if m["id"] == msg_id:
                            m["reactions"][emoji] = m["reactions"].get(emoji, 0) + 1
                            await manager.broadcast_to_chat(chat_id, {
                                "type": "reaction_updated",
                                "chat_id": chat_id,
                                "msg_id": msg_id,
                                "reactions": m["reactions"]
                            })
                            break

            # Закрепление сообщения (Pin)
            elif msg_type == "pin_message":
                chat_id = data["chat_id"]
                msg_id = data["msg_id"]
                if chat_id in chats_db:
                    chats_db[chat_id]["pinned_msg_id"] = msg_id
                    await manager.broadcast_to_chat(chat_id, {
                        "type": "message_pinned",
                        "chat_id": chat_id,
                        "msg_id": msg_id,
                        "text": data.get("text", "")
                    })

            # Создание прямого диалога с найденным другом
            elif msg_type == "create_chat":
                partner_id = data["partner_id"]
                chat_id = f"chat_{min(user_id, partner_id)}_{max(user_id, partner_id)}"
                if chat_id not in chats_db:
                    chats_db[chat_id] = {
                        "id": chat_id,
                        "type": "personal",
                        "name": "Личный диалог",
                        "members": [user_id, partner_id],
                        "created_by": user_id,
                        "pinned_msg_id": None
                    }
                    messages_db[chat_id] = []
                
                init_event = {"type": "chat_created", "chat_id": chat_id}
                await manager.send_personal(user_id, init_event)
                await manager.send_personal(partner_id, init_event)

            # Создание группы / канала
            elif msg_type == "create_group_channel":
                c_type = data["group_type"] # 'group' или 'channel'
                c_name = data["name"]
                new_c_id = f"{c_type}_{uuid.uuid4().hex[:6]}"
                
                chats_db[new_c_id] = {
                    "id": new_c_id,
                    "type": c_type,
                    "name": c_name,
                    "members": [user_id],
                    "created_by": user_id,
                    "pinned_msg_id": None,
                    "invite_link": f"https://t.me/join_{new_c_id}"
                }
                messages_db[new_c_id] = []
                await manager.send_personal(user_id, {"type": "chat_created", "chat_id": new_c_id})

    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception:
        manager.disconnect(user_id)
