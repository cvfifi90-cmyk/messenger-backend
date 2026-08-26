import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel

# ----------------- КОНФИГУРАЦИЯ БЕЗОПАСНОСТИ -----------------
SECRET_KEY = "AURA_SUPER_SECRET_MILITARY_KEY_CHANGE_THIS_IN_PROD_123!@#"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Aura Telegram Secure Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- ПОСТОЯННАЯ БАЗА ДАННЫХ (SQLITE) -----------------
DB_FILE = "messenger_secure.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Таблица пользователей
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            bio TEXT DEFAULT '',
            created_at TEXT
        )
    """)
    # Таблица чатов
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            created_by TEXT,
            pinned_msg_id TEXT
        )
    """)
    # Участники чатов
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    # Сообщения
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            text TEXT NOT NULL,
            time TEXT NOT NULL,
            is_voice INTEGER DEFAULT 0,
            reactions TEXT DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ----------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ КРИПТОГРАФИИ -----------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode = {"sub": user_id, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None

# ----------------- УПРАВЛЕНИЕ WEBSOCKET-СОЕДИНЕНИЯМИ -----------------
class SecureConnectionManager:
    def __init__(self):
        self.active_sockets: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_sockets[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_sockets:
            del self.active_sockets[user_id]

    async def send_to_user(self, user_id: str, payload: dict):
        if user_id in self.active_sockets:
            try:
                await self.active_sockets[user_id].send_text(json.dumps(payload))
            except Exception:
                self.disconnect(user_id)

    async def broadcast_chat(self, chat_id: str, payload: dict):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT user_id FROM chat_members WHERE chat_id = ?", (chat_id,))
        members = [row[0] for row in c.fetchall()]
        conn.close()

        for member_id in members:
            await self.send_to_user(member_id, payload)

manager = SecureConnectionManager()

# ----------------- REST API МОДЕЛИ -----------------
class RegisterRequest(BaseModel):
    username: str
    password: str
    name: str
    phone: str

class LoginRequest(BaseModel):
    username: str
    password: str

# ----------------- ЭНДПОИНТЫ АВТОРИЗАЦИИ -----------------
@app.post("/api/register")
def register(req: RegisterRequest):
    username_clean = req.username.strip().replace("@", "").lower()
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 6 символов")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username_clean,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь с таким юзернеймом уже существует")

    user_id = f"usr_{uuid.uuid4().hex[:10]}"
    hashed_pwd = hash_password(req.password)
    now_str = datetime.now().isoformat()

    c.execute("""
        INSERT INTO users (id, username, password_hash, name, phone, bio, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, username_clean, hashed_pwd, req.name.strip(), req.phone.strip(), "В сети Aura 🛡", now_str))

    # Создаем Избранное
    saved_id = f"saved_{user_id}"
    c.execute("INSERT INTO chats (id, type, name, created_by) VALUES (?, ?, ?, ?)", (saved_id, "saved", "Избранное", user_id))
    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (saved_id, user_id))

    conn.commit()
    conn.close()

    token = create_access_token(user_id)
    return {
        "status": "ok",
        "token": token,
        "user": {
            "id": user_id,
            "username": username_clean,
            "name": req.name.strip(),
            "phone": req.phone.strip(),
            "bio": "В сети Aura 🛡"
        }
    }

@app.post("/api/login")
def login(req: LoginRequest):
    username_clean = req.username.strip().replace("@", "").lower()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, password_hash, name, phone, bio FROM users WHERE username = ?", (username_clean,))
    row = c.fetchone()
    conn.close()

    if not row or not verify_password(req.password, row[2]):
        raise HTTPException(status_code=401, detail="Неверный юзернейм или пароль")

    user_id = row[0]
    token = create_access_token(user_id)
    return {
        "status": "ok",
        "token": token,
        "user": {
            "id": user_id,
            "username": row[1],
            "name": row[3],
            "phone": row[4],
            "bio": row[5]
        }
    }

@app.get("/api/chats")
def get_chats(token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Недействительный токен")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT c.id, c.type, c.name, c.pinned_msg_id
        FROM chats c
        JOIN chat_members cm ON c.id = cm.chat_id
        WHERE cm.user_id = ?
    """, (user_id,))
    chat_rows = c.fetchall()

    result = []
    for cid, ctype, cname, pinned_id in chat_rows:
        # Получаем последнее сообщение
        c.execute("SELECT text, time FROM messages WHERE chat_id = ? ORDER BY rowid DESC LIMIT 1", (cid,))
        last_msg_row = c.fetchone()
        last_text = last_msg_row[0] if last_msg_row else "Чат создан"
        last_time = last_msg_row[1] if last_msg_row else ""

        display_name = cname
        avatar_letter = cname[0] if cname else "?"
        is_online = False
        status_text = ""

        if ctype == "personal":
            c.execute("SELECT u.name, u.username FROM users u JOIN chat_members cm ON u.id = cm.user_id WHERE cm.chat_id = ? AND u.id != ?", (cid, user_id))
            partner = c.fetchone()
            if partner:
                display_name = partner[0]
                avatar_letter = partner[0][0]
                is_online = partner[1] in manager.active_sockets
                status_text = "в сети" if is_online else "был(а) недавно"
        elif ctype == "saved":
            display_name = "Избранное"
            avatar_letter = "⭐️"
            status_text = "облако"
        else:
            c.execute("SELECT COUNT(*) FROM chat_members WHERE chat_id = ?", (cid,))
            count = c.fetchone()[0]
            status_text = f"{count} участников"

        result.append({
            "id": cid,
            "type": ctype,
            "name": display_name,
            "avatar_letter": avatar_letter,
            "last_message": last_text,
            "time": last_time,
            "is_online": is_online,
            "status_text": status_text,
            "pinned_msg_id": pinned_id
        })

    conn.close()
    return {"chats": result}

@app.get("/api/users/search")
def search_users(query: str, token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Недействительный токен")

    q = f"%{query.strip().replace('@', '').lower()}%"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, name, phone, bio FROM users WHERE (username LIKE ? OR name LIKE ? OR phone LIKE ?) AND id != ?", (q, q, q, user_id))
    rows = c.fetchall()
    conn.close()

    return {"results": [{"id": r[0], "username": r[1], "name": r[2], "phone": r[3], "bio": r[4]} for r in rows]}

# ----------------- ЗАЩИЩЕННЫЙ WEBSOCKET -----------------
@app.websocket("/ws")
async def secure_websocket(websocket: WebSocket, token: str):
    user_id = decode_token(token)
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            action = data.get("action")

            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            elif action == "send_message":
                chat_id = data["chat_id"]
                text = data.get("text", "")
                is_voice = 1 if data.get("is_voice") else 0
                time_str = datetime.now().strftime("%H:%M")
                msg_id = f"msg_{uuid.uuid4().hex[:10]}"

                # Серверная проверка: состоит ли отправитель в этом чате
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT 1 FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
                if not c.fetchone():
                    conn.close()
                    continue

                # Имя отправителя
                c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
                sender_name = c.fetchone()[0]

                c.execute("INSERT INTO messages (id, chat_id, sender_id, text, time, is_voice) VALUES (?, ?, ?, ?, ?, ?)",
                          (msg_id, chat_id, user_id, text, time_str, is_voice))
                conn.commit()
                conn.close()

                await manager.broadcast_chat(chat_id, {
                    "type": "new_message",
                    "message": {
                        "id": msg_id,
                        "chat_id": chat_id,
                        "sender_id": user_id,
                        "sender_name": sender_name,
                        "text": text,
                        "time": time_str,
                        "is_voice": bool(is_voice),
                        "reactions": {}
                    }
                })

            elif action == "typing":
                chat_id = data["chat_id"]
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
                user_name = c.fetchone()[0]
                conn.close()

                await manager.broadcast_chat(chat_id, {
                    "type": "user_typing",
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "user_name": user_name
                })

            elif action == "create_personal_chat":
                partner_id = data["partner_id"]
                chat_id = f"chat_{min(user_id, partner_id)}_{max(user_id, partner_id)}"
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT id FROM chats WHERE id = ?", (chat_id,))
                if not c.fetchone():
                    c.execute("INSERT INTO chats (id, type, name, created_by) VALUES (?, ?, ?, ?)", (chat_id, "personal", "Личный диалог", user_id))
                    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id))
                    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, partner_id))
                    conn.commit()
                conn.close()

                init_payload = {"type": "chat_created", "chat_id": chat_id}
                await manager.send_to_user(user_id, init_payload)
                await manager.send_to_user(partner_id, init_payload)

            elif action == "create_group":
                name = data["name"]
                g_type = data.get("group_type", "group")
                chat_id = f"{g_type}_{uuid.uuid4().hex[:8]}"

                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO chats (id, type, name, created_by) VALUES (?, ?, ?, ?)", (chat_id, g_type, name, user_id))
                c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id))
                conn.commit()
                conn.close()

                await manager.send_to_user(user_id, {"type": "chat_created", "chat_id": chat_id})

    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception:
        manager.disconnect(user_id)
