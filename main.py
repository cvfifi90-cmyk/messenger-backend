import asyncio
import json
import sqlite3
import uuid
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from passlib.context import CryptContext
from pydantic import BaseModel

SECRET_KEY = os.getenv("SECRET_KEY", "AURA_REAL_PROD_KEY_123!@#")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "super_admin_secret_pass_123")

os.makedirs("uploads", exist_ok=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Aura Telegram Ultimate Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            created_by TEXT,
            pinned_msg_id TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            text TEXT NOT NULL,
            time TEXT NOT NULL,
            media_url TEXT DEFAULT '',
            is_voice INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            media_url TEXT NOT NULL,
            caption TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[str]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except Exception:
        return None

def is_user_banned(user_id: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == 1)

class SecureConnectionManager:
    def __init__(self):
        self.active_sockets: Dict[str, WebSocket] = {}

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
        for m in members:
            await self.send_to_user(m, payload)

manager = SecureConnectionManager()

class RegisterRequest(BaseModel):
    username: str
    password: str
    name: str
    phone: str

class LoginRequest(BaseModel):
    username: str
    password: str

# ----------------- ADMIN DASHBOARD -----------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(key: str = ""):
    if key != ADMIN_SECRET_KEY:
        return HTMLResponse("<h2 style='color:#ef4444;text-align:center;margin-top:50px;font-family:sans-serif;'>403 Доступ запрещен. Укажите правильный ключ: ?key=...</h2>", status_code=403)
    
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Aura Telegram Server Control Center</title>
        <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: 'Inter', sans-serif; background: #0b1120; color: #f8fafc; padding: 24px; }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #1e293b; }
            .title { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
            .badge-live { background: #10b98120; color: #10b981; border: 1px solid #10b981; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
            .stat-card { background: #1e293b; padding: 18px; border-radius: 12px; border: 1px solid #334155; }
            .stat-label { font-size: 13px; color: #94a3b8; margin-bottom: 6px; }
            .stat-value { font-size: 26px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
            table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; border: 1px solid #334155; margin-bottom: 24px; }
            th, td { padding: 12px 16px; text-align: left; font-size: 14px; border-bottom: 1px solid #334155; }
            th { background: #0f172a; color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; }
            tr:hover { background: #243248; }
            .code-pill { font-family: 'JetBrains Mono', monospace; background: #0f172a; padding: 3px 8px; border-radius: 6px; font-size: 12px; color: #38bdf8; }
            .btn-ban { background: #ef4444; color: white; border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 700; }
            .btn-unban { background: #10b981; color: white; border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 700; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">🛡 Aura Telegram Core Panel <span class="badge-live">● LIVE</span></div>
        </div>
        <div class="stats-grid">
            <div class="stat-card"><div class="stat-label">Онлайн сокетов</div><div class="stat-value" id="activeSockets" style="color: #10b981;">0</div></div>
            <div class="stat-card"><div class="stat-label">Пользователей</div><div class="stat-value" id="totalUsers" style="color: #38bdf8;">0</div></div>
            <div class="stat-card"><div class="stat-label">Всего сообщений</div><div class="stat-value" id="totalMessages" style="color: #a855f7;">0</div></div>
        </div>
        <table>
            <thead><tr><th>Статус</th><th>User ID</th><th>Username</th><th>Имя</th><th>Телефон</th><th>Действие</th></tr></thead>
            <tbody id="usersTable"><tr><td colspan="6" style="text-align:center;">Загрузка...</td></tr></tbody>
        </table>
        <script>
            const urlParams = new URLSearchParams(window.location.search);
            const adminKey = urlParams.get('key');
            async function toggleBan(userId, banState) {
                const endpoint = banState ? '/admin/ban' : '/admin/unban';
                await fetch(`${endpoint}?user_id=${userId}&key=${adminKey}`, { method: 'POST' });
                refresh();
            }
            async function refresh() {
                try {
                    const res = await fetch(`/admin/dashboard?key=${adminKey}`);
                    if (!res.ok) return;
                    const data = await res.json();
                    document.getElementById('activeSockets').innerText = data.active_sockets_online;
                    document.getElementById('totalUsers').innerText = data.total_registered_users;
                    document.getElementById('totalMessages').innerText = data.total_messages_sent;
                    document.getElementById('usersTable').innerHTML = data.users.map(u => `
                        <tr>
                            <td>${u.is_banned ? '<span style="color:#ef4444;">BANNED</span>' : (u.is_online ? '<span style="color:#10b981;">ONLINE</span>' : '<span style="color:#64748b;">OFFLINE</span>')}</td>
                            <td><span class="code-pill">${u.id}</span></td>
                            <td style="color:#38bdf8;">${u.username}</td>
                            <td>${u.name}</td>
                            <td>${u.phone}</td>
                            <td>${u.is_banned ? `<button class="btn-unban" onclick="toggleBan('${u.id}', false)">РАЗБЛОКИРОВАТЬ</button>` : `<button class="btn-ban" onclick="toggleBan('${u.id}', true)">ЗАБАНИТЬ</button>`}</td>
                        </tr>
                    `).join('');
                } catch (e) {}
            }
            refresh();
            setInterval(refresh, 3000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/admin/dashboard")
def admin_dashboard(key: str):
    if key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, name, phone, bio, created_at, is_banned FROM users")
    users = c.fetchall()
    c.execute("SELECT COUNT(*) FROM messages")
    total_messages = c.fetchone()[0]
    conn.close()
    return {
        "active_sockets_online": len(manager.active_sockets),
        "total_registered_users": len(users),
        "total_messages_sent": total_messages,
        "users": [
            {
                "id": u[0], "username": f"@{u[1]}", "name": u[2], "phone": u[3], "bio": u[4],
                "registered_at": u[5], "is_banned": bool(u[6] == 1), "is_online": u[0] in manager.active_sockets
            } for u in users
        ]
    }

@app.post("/admin/ban")
async def admin_ban_user(user_id: str, key: str):
    if key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    if user_id in manager.active_sockets:
        ws = manager.active_sockets[user_id]
        try:
            await ws.send_text(json.dumps({"type": "user_banned", "message": "🚨 Аккаунт заблокирован."}))
            await ws.close()
        except Exception:
            pass
        manager.disconnect(user_id)
    return {"status": "ok"}

@app.post("/admin/unban")
def admin_unban_user(user_id: str, key: str):
    if key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# ----------------- UPLOADS & STORIES -----------------
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'bin'
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join("uploads", filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "ok", "url": f"/uploads/{filename}"}

@app.post("/api/user/avatar")
async def update_avatar(token: str = Form(...), file: UploadFile = File(...)):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
    filename = f"avatar_{user_id}_{uuid.uuid4().hex[:6]}.{ext}"
    file_path = os.path.join("uploads", filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    url = f"/uploads/{filename}"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (url, user_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "avatar_url": url}

@app.post("/api/stories/create")
async def create_story(token: str = Form(...), media_url: str = Form(...), caption: str = Form("")):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    story_id = f"st_{uuid.uuid4().hex[:10]}"
    now = datetime.utcnow()
    expires = now + timedelta(hours=24)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO stories (id, user_id, media_url, caption, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
              (story_id, user_id, media_url, caption, now.isoformat(), expires.isoformat()))
    conn.commit()
    conn.close()
    return {"status": "ok", "story_id": story_id}

@app.get("/api/stories/feed")
def get_stories(token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT s.id, s.user_id, u.name, u.avatar_url, s.media_url, s.caption, s.created_at
        FROM stories s
        JOIN users u ON s.user_id = u.id
        WHERE s.expires_at > ?
        ORDER BY s.created_at DESC
    """, (now,))
    stories = c.fetchall()
    conn.close()
    return {
        "stories": [
            {
                "id": s[0], "user_id": s[1], "user_name": s[2], "avatar_url": s[3],
                "media_url": s[4], "caption": s[5], "created_at": s[6]
            } for s in stories
        ]
    }

# ----------------- REST API -----------------
@app.post("/api/register")
def register(req: RegisterRequest):
    username_clean = req.username.strip().replace("@", "").lower()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username_clean,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Юзернейм уже занят")
    user_id = f"usr_{uuid.uuid4().hex[:10]}"
    now = datetime.utcnow().isoformat()
    c.execute("INSERT INTO users (id, username, password_hash, name, phone, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (user_id, username_clean, hash_password(req.password), req.name, req.phone, now))
    conn.commit()
    conn.close()
    token = create_access_token(user_id)
    return {
        "token": token,
        "user": {"id": user_id, "username": username_clean, "name": req.name, "phone": req.phone, "bio": "В сети Aura", "avatar_url": ""}
    }

@app.post("/api/login")
def login(req: LoginRequest):
    username_clean = req.username.strip().replace("@", "").lower()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, password_hash, name, phone, bio, avatar_url, is_banned FROM users WHERE username = ?", (username_clean,))
    row = c.fetchone()
    conn.close()
    if not row or not verify_password(req.password, row[2]):
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    if row[7] == 1:
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован")
    token = create_access_token(row[0])
    return {
        "token": token,
        "user": {"id": row[0], "username": row[1], "name": row[3], "phone": row[4], "bio": row[5], "avatar_url": row[6]}
    }

@app.get("/api/chats")
def get_chats(token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT c.id, c.type, c.name FROM chats c JOIN chat_members cm ON c.id = cm.chat_id WHERE cm.user_id = ?", (user_id,))
    user_chats = c.fetchall()
    result = []
    for chat in user_chats:
        chat_id, chat_type, default_name = chat
        c.execute("SELECT u.id, u.name, u.avatar_url FROM chat_members cm JOIN users u ON cm.user_id = u.id WHERE cm.chat_id = ? AND cm.user_id != ?", (chat_id, user_id))
        partner = c.fetchone()
        c.execute("SELECT text, time FROM messages WHERE chat_id = ? ORDER BY rowid DESC LIMIT 1", (chat_id,))
        last_msg = c.fetchone()
        chat_name = partner[1] if partner else default_name
        partner_id = partner[0] if partner else ""
        avatar_url = partner[2] if partner else ""
        result.append({
            "id": chat_id, "type": chat_type, "name": chat_name, "partner_id": partner_id, "avatar_url": avatar_url,
            "last_message": last_msg[0] if last_msg else "Чат создан", "time": last_msg[1] if last_msg else "",
            "is_online": partner_id in manager.active_sockets, "status_text": "online" if partner_id in manager.active_sockets else "offline",
            "is_archived": False
        })
    conn.close()
    return {"chats": result}

@app.get("/api/messages")
def get_messages(chat_id: str, token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT m.id, m.chat_id, m.sender_id, u.name, m.text, m.time, m.media_url, m.is_voice FROM messages m JOIN users u ON m.sender_id = u.id WHERE m.chat_id = ? ORDER BY m.rowid ASC", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return {
        "messages": [
            {"id": r[0], "chat_id": r[1], "sender_id": r[2], "sender_name": r[3], "text": r[4], "time": r[5], "media_url": r[6], "is_voice": bool(r[7] == 1)} for r in rows
        ]
    }

@app.get("/api/users/search")
def search_users(q: str, token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    clean_q = f"%{q.strip().replace('@', '').lower()}%"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, name, phone, bio, avatar_url FROM users WHERE (username LIKE ? OR name LIKE ?) AND id != ? LIMIT 20", (clean_q, clean_q, user_id))
    users = c.fetchall()
    conn.close()
    return {"users": [{"id": u[0], "username": u[1], "name": u[2], "phone": u[3], "bio": u[4], "avatar_url": u[5]} for u in users]}

@app.post("/api/chats/create")
def create_chat(partner_id: str, token: str, chat_type: str = "direct"):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT cm1.chat_id FROM chat_members cm1 JOIN chat_members cm2 ON cm1.chat_id = cm2.chat_id WHERE cm1.user_id = ? AND cm2.user_id = ?", (user_id, partner_id))
    existing = c.fetchone()
    if existing:
        conn.close()
        return {"chat_id": existing[0]}
    chat_id = f"chat_{uuid.uuid4().hex[:10]}"
    c.execute("INSERT INTO chats (id, type, name, created_by) VALUES (?, ?, 'Chat', ?)", (chat_id, chat_type, user_id))
    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id))
    c.execute("INSERT INTO chat_members (chat_id, user_id) VALUES (?, ?)", (chat_id, partner_id))
    conn.commit()
    conn.close()
    return {"chat_id": chat_id}

@app.get("/api/user/storage_stats")
def get_user_storage_stats(token: str):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE sender_id = ?", (user_id,))
    total_msgs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM messages WHERE sender_id = ? AND media_url != ''", (user_id,))
    total_media = c.fetchone()[0]
    conn.close()
    return {"total_messages_sent": total_msgs, "total_media_sent": total_media}

# ----------------- WEBSOCKET -----------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    user_id = decode_token(token)
    if not user_id or is_user_banned(user_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, user_id)
    try:
        while True:
            text_data = await websocket.receive_text()
            data = json.loads(text_data)
            action = data.get("action")
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif action == "send_message":
                chat_id = data.get("chat_id")
                msg_text = data.get("text", "")
                media_url = data.get("media_url", "")
                is_voice = 1 if data.get("is_voice") else 0
                msg_id = f"msg_{uuid.uuid4().hex[:10]}"
                time_now = datetime.now().strftime("%H:%M")
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO messages (id, chat_id, sender_id, text, time, media_url, is_voice) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (msg_id, chat_id, user_id, msg_text, time_now, media_url, is_voice))
                c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
                sender_name = c.fetchone()[0]
                conn.commit()
                conn.close()
                payload = {
                    "type": "new_message",
                    "message": {
                        "id": msg_id, "chat_id": chat_id, "sender_id": user_id, "sender_name": sender_name,
                        "text": msg_text, "time": time_now, "media_url": media_url, "is_voice": is_voice
                    }
                }
                await manager.broadcast_chat(chat_id, payload)
            elif action == "call_user":
                target_id = data.get("target_id")
                await manager.send_to_user(target_id, {
                    "type": "incoming_call", "caller_id": user_id, "caller_name": data.get("caller_name", "Пользователь")
                })
    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception:
        manager.disconnect(user_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
