import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from passlib.context import CryptContext
from pydantic import BaseModel

# ----------------- КОНФИГУРАЦИЯ БЕЗОПАСНОСТИ -----------------
SECRET_KEY = "AURA_SUPER_SECRET_MILITARY_KEY_CHANGE_THIS_IN_PROD_123!@#"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
ADMIN_SECRET_KEY = "super_admin_secret_pass_123"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Aura Telegram Full Production Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "messenger_secure.db"

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
            is_voice INTEGER DEFAULT 0,
            media_type TEXT DEFAULT 'none',
            media_url TEXT DEFAULT '',
            reactions TEXT DEFAULT '{}'
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
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
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

class RegisterRequest(BaseModel):
    username: str
    password: str
    name: str
    phone: str

class LoginRequest(BaseModel):
    username: str
    password: str

@app.get("/")
def home():
    return {
        "status": "online",
        "service": "Aura Telegram Full Production Core",
        "active_sockets": len(manager.active_sockets),
        "db": "SQLite Persistent"
    }

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
        INSERT INTO users (id, username, password_hash, name, phone, bio, created_at, is_banned)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """, (user_id, username_clean, hashed_pwd, req.name.strip(), req.phone.strip(), "В сети Aura 🛡", now_str))

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
    c.execute("SELECT id, username, password_hash, name, phone, bio, is_banned FROM users WHERE username = ?", (username_clean,))
    row = c.fetchone()
    conn.close()

    if not row or not verify_password(req.password, row[2]):
        raise HTTPException(status_code=401, detail="Неверный юзернейм или пароль")

    if row[6] == 1:
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован навсегда за нарушение безопасности.")

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

    if is_user_banned(user_id):
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован навсегда")

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
        c.execute("SELECT text, time FROM messages WHERE chat_id = ? ORDER BY rowid DESC LIMIT 1", (cid,))
        last_msg_row = c.fetchone()
        last_text = last_msg_row[0] if last_msg_row else "Чат создан"
        last_time = last_msg_row[1] if last_msg_row else ""

        display_name = cname
        avatar_letter = cname[0] if cname else "?"
        is_online = False
        status_text = ""
        partner_id = ""

        if ctype == "personal":
            c.execute("""
                SELECT u.name, u.username, u.id 
                FROM users u 
                JOIN chat_members cm ON u.id = cm.user_id 
                WHERE cm.chat_id = ? AND u.id != ?
            """, (cid, user_id))
            partner = c.fetchone()
            if partner:
                display_name = partner[0]
                avatar_letter = partner[0][0]
                partner_id = partner[2]
                is_online = partner[2] in manager.active_sockets
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
            "partner_id": partner_id,
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
    if not user_id or is_user_banned(user_id):
        raise HTTPException(status_code=401, detail="Недействительный токен")

    q = f"%{query.strip().replace('@', '').lower()}%"
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT id, username, name, phone, bio 
        FROM users 
        WHERE (username LIKE ? OR name LIKE ? OR phone LIKE ?) AND id != ? AND is_banned = 0
    """, (q, q, q, user_id))
    rows = c.fetchall()
    conn.close()

    return {"results": [{"id": r[0], "username": r[1], "name": r[2], "phone": r[3], "bio": r[4]} for r in rows]}

# ----------------- ЗАЩИЩЕННЫЙ WEBSOCKET С ПОЛНОЙ МАРШРУТИЗАЦИЕЙ ЗВОНКОВ -----------------
@app.websocket("/ws")
async def secure_websocket(websocket: WebSocket, token: str):
    user_id = decode_token(token)
    if not user_id or is_user_banned(user_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            if is_user_banned(user_id):
                await websocket.send_text(json.dumps({
                    "type": "user_banned",
                    "message": "🚨 Ваш аккаунт заблокирован навсегда за нарушение правил безопасности."
                }))
                await asyncio.sleep(0.05)
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                manager.disconnect(user_id)
                break

            data = json.loads(raw)
            action = data.get("action")

            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            elif action == "send_message":
                chat_id = data["chat_id"]
                text = data.get("text", "")
                is_voice = 1 if data.get("is_voice") else 0
                media_type = data.get("media_type", "none")
                media_url = data.get("media_url", "")
                time_str = datetime.now().strftime("%H:%M")
                msg_id = f"msg_{uuid.uuid4().hex[:10]}"

                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT 1 FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
                if not c.fetchone():
                    conn.close()
                    continue

                c.execute("SELECT name FROM users WHERE id = ?", (user_id,))
                sender_name = c.fetchone()[0]

                c.execute("""
                    INSERT INTO messages (id, chat_id, sender_id, text, time, is_voice, media_type, media_url) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (msg_id, chat_id, user_id, text, time_str, is_voice, media_type, media_url))
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
                        "media_type": media_type,
                        "media_url": media_url,
                        "reply_to_text": data.get("reply_to_text"),
                        "reply_to_sender": data.get("reply_to_sender"),
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

            # --- СИГНАЛИНГ ЗВОНКОВ (P2P WEBRTC CALLING) ---
            elif action == "call_offer":
                target_user_id = data["target_user_id"]
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT name, username FROM users WHERE id = ?", (user_id,))
                caller_info = c.fetchone()
                conn.close()

                await manager.send_to_user(target_user_id, {
                    "type": "incoming_call",
                    "caller_id": user_id,
                    "caller_name": caller_info[0],
                    "caller_username": caller_info[1],
                    "is_video": data.get("is_video", False),
                    "channel_id": data.get("channel_id", "")
                })

            elif action == "call_answer":
                caller_id = data["caller_id"]
                await manager.send_to_user(caller_id, {
                    "type": "call_accepted",
                    "user_id": user_id,
                })

            elif action == "call_reject":
                caller_id = data["caller_id"]
                await manager.send_to_user(caller_id, {
                    "type": "call_rejected",
                    "user_id": user_id,
                })

            elif action == "call_end":
                target_id = data["target_id"]
                await manager.send_to_user(target_id, {
                    "type": "call_ended",
                    "user_id": user_id,
                })

            elif action == "add_reaction":
                chat_id = data["chat_id"]
                msg_id = data["msg_id"]
                emoji = data["emoji"]

                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT reactions FROM messages WHERE id = ?", (msg_id,))
                row = c.fetchone()
                reactions_dict = json.loads(row[0]) if (row and row[0]) else {}

                reactions_dict[emoji] = reactions_dict.get(emoji, 0) + 1
                c.execute("UPDATE messages SET reactions = ? WHERE id = ?", (json.dumps(reactions_dict), msg_id))
                conn.commit()
                conn.close()

                await manager.broadcast_chat(chat_id, {
                    "type": "reaction_updated",
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "reactions": reactions_dict
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

# ----------------- ПАНЕЛЬ АДМИНИСТРАТОРА С МГНОВЕННЫМ БАНОМ -----------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(key: str = ""):
    if key != ADMIN_SECRET_KEY:
        return HTMLResponse("<h2>403 Доступ запрещен</h2>", status_code=403)
    
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Aura Telegram Production SOC</title>
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
            th { background: #0f172a; color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 11px; }
            .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
            .status-online { background: #10b981; box-shadow: 0 0 8px #10b981; }
            .status-offline { background: #64748b; }
            .status-banned { background: #ef4444; box-shadow: 0 0 8px #ef4444; }
            .btn-ban { background: #ef4444; color: white; border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 700; }
            .btn-unban { background: #10b981; color: white; border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 700; }
            .messages-log { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px; font-family: 'JetBrains Mono', monospace; font-size: 13px; max-height: 250px; overflow-y: auto; }
            .msg-entry { margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">🛡 Aura Telegram Full Production SOC <span class="badge-live">● LIVE</span></div>
            <div style="font-size: 13px; color: #94a3b8;">Автообновление: 3 сек</div>
        </div>

        <div class="stats-grid">
            <div class="stat-card"><div class="stat-label">Онлайн (Сокеты)</div><div class="stat-value" id="activeSockets" style="color: #10b981;">0</div></div>
            <div class="stat-card"><div class="stat-label">Всего пользователей</div><div class="stat-value" id="totalUsers" style="color: #38bdf8;">0</div></div>
            <div class="stat-card"><div class="stat-label">Заблокировано</div><div class="stat-value" id="bannedUsers" style="color: #ef4444;">0</div></div>
            <div class="stat-card"><div class="stat-label">Сообщений передано</div><div class="stat-value" id="totalMessages" style="color: #a855f7;">0</div></div>
        </div>

        <h3 style="margin: 16px 0;">Управление аккаунтами и перманентный бан</h3>
        <table>
            <thead><tr><th>Статус</th><th>User ID</th><th>Username</th><th>Имя</th><th>Телефон</th><th>Действие</th></tr></thead>
            <tbody id="usersTable"><tr><td colspan="6">Загрузка...</td></tr></tbody>
        </table>

        <h3 style="margin: 16px 0;">Лента сообщений и звонков</h3>
        <div class="messages-log" id="messagesLog"><div>Ожидание активности...</div></div>

        <script>
            const adminKey = new URLSearchParams(window.location.search).get('key');

            async function toggleBan(userId, banState) {
                const endpoint = banState ? '/admin/ban' : '/admin/unban';
                await fetch(`${endpoint}?user_id=${userId}&key=${adminKey}`, { method: 'POST' });
                refresh();
            }

            async function refresh() {
                try {
                    const res = await fetch(`/admin/dashboard?key=${adminKey}`);
                    const data = await res.json();
                    document.getElementById('activeSockets').innerText = data.active_sockets_online;
                    document.getElementById('totalUsers').innerText = data.total_registered_users;
                    document.getElementById('bannedUsers').innerText = data.users.filter(u => u.is_banned).length;
                    document.getElementById('totalMessages').innerText = data.total_messages_sent;

                    document.getElementById('usersTable').innerHTML = data.users.map(u => `
                        <tr>
                            <td>${u.is_banned ? '<span class="status-dot status-banned"></span><strong style="color:#ef4444">BANNED</strong>' : (u.is_online ? '<span class="status-dot status-online"></span>Online' : '<span class="status-dot status-offline"></span>Offline')}</td>
                            <td><code>${u.id}</code></td>
                            <td style="color:#38bdf8; font-weight:bold">${u.username}</td>
                            <td>${u.name}</td>
                            <td>${u.phone}</td>
                            <td>${u.is_banned ? `<button class="btn-unban" onclick="toggleBan('${u.id}', false)">РАЗБЛОКИРОВАТЬ</button>` : `<button class="btn-ban" onclick="toggleBan('${u.id}', true)">🔨 ЗАБАНИТЬ</button>`}</td>
                        </tr>
                    `).join('');

                    document.getElementById('messagesLog').innerHTML = data.recent_messages.map(m => `
                        <div class="msg-entry"><span><strong>[${m.time}]</strong> ${m.sender_id}: ${m.text}</span><span>${m.chat_id}</span></div>
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

    c.execute("SELECT id, type, name, created_by, pinned_msg_id FROM chats")
    chats = c.fetchall()

    c.execute("SELECT COUNT(*) FROM messages")
    total_messages = c.fetchone()[0]

    c.execute("SELECT id, chat_id, sender_id, text, time FROM messages ORDER BY rowid DESC LIMIT 25")
    recent_messages = c.fetchall()
    conn.close()

    return {
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
        "chats": [
            {
                "id": ch[0],
                "type": ch[1],
                "name": ch[2],
                "creator_id": ch[3],
                "pinned_msg_id": ch[4]
            }
            for ch in chats
        ],
        "recent_messages": [
            {
                "id": m[0],
                "chat_id": m[1],
                "sender_id": m[2],
                "text": m[3],
                "time": m[4]
            }
            for m in recent_messages
        ]
    }

@app.post("/admin/ban")
async def admin_ban_user(user_id: str, key: str):
    if key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

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
                "message": "🚨 Ваш аккаунт заблокирован навсегда администрацией Aura Telegram."
            }))
            await asyncio.sleep(0.05)
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:
            pass
        manager.disconnect(user_id)

    return {"status": "ok", "detail": f"Пользователь {user_id} заблокирован навсегда"}

@app.post("/admin/unban")
def admin_unban_user(user_id: str, key: str):
    if key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    return {"status": "ok", "detail": f"Пользователь {user_id} разблокирован"}
