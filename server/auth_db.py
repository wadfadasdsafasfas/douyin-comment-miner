"""SQLite 用户/Token 存储。

用户表 users:
    username       主键
    password_hash  bcrypt 哈希
    expires_at     ISO8601，过期时间
    status         active / disabled
    note           备注（哪个客户、订单号等）
    created_at     创建时间

Token 表 tokens:
    token          主键（32 字节随机）
    username       关联用户
    expires_at     token 自身过期（默认 30 天，可滑动续期）
    created_at
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt

DB_PATH = Path.home() / ".douyin_miner_server.db"


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(username);
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def ensure_admin() -> str | None:
    """首次启动时确保 admin 账号存在，密码写到 credentials 文件。
    返回新生成的明文密码（已存在则返回 None）。"""
    with conn() as c:
        if c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            return None
        plain_pw = secrets.token_urlsafe(12)
        c.execute(
            "INSERT INTO users(username,password_hash,expires_at,status,note,created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("admin", hash_password(plain_pw), "2099-12-31T23:59:59",
             "active", "默认管理员（首次启动自动创建）", datetime.now().isoformat()),
        )
    # 密码写到受限文件
    cred_path = DB_PATH.with_suffix(".admin_credentials.txt")
    cred_path.write_text(
        f"用户名: admin\n密码:   {plain_pw}\n\n"
        f"⚠️ 这是管理员账号的密码，请立即保存并删除本文件！\n"
        f"忘记密码？删掉数据库 {DB_PATH} 后重启服务，会再次生成新密码。\n"
    )
    try:
        cred_path.chmod(0o600)
    except Exception:
        pass
    return plain_pw


# ---------- 用户 CRUD ----------
def list_users():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT username, expires_at, status, note, created_at FROM users ORDER BY created_at DESC"
        )]


def get_user(username: str):
    with conn() as c:
        r = c.execute(
            "SELECT username, expires_at, status, note, created_at FROM users WHERE username=?",
            (username,),
        ).fetchone()
        return dict(r) if r else None


def create_user(username: str, password: str, expires_at: str, note: str = "") -> tuple[bool, str]:
    if not username or not password:
        return False, "用户名/密码不能为空"
    if get_user(username):
        return False, f"用户 {username} 已存在"
    with conn() as c:
        c.execute(
            "INSERT INTO users(username,password_hash,expires_at,status,note,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (username, hash_password(password), expires_at, "active", note,
             datetime.now().isoformat()),
        )
    return True, "ok"


def update_expiry(username: str, expires_at: str) -> tuple[bool, str]:
    if not get_user(username):
        return False, f"用户 {username} 不存在"
    with conn() as c:
        c.execute("UPDATE users SET expires_at=? WHERE username=?", (expires_at, username))
    return True, "ok"


def set_status(username: str, status: str) -> tuple[bool, str]:
    if status not in ("active", "disabled"):
        return False, "status 必须是 active 或 disabled"
    if not get_user(username):
        return False, f"用户 {username} 不存在"
    with conn() as c:
        c.execute("UPDATE users SET status=? WHERE username=?", (status, username))
    return True, "ok"


def change_password(username: str, new_password: str) -> tuple[bool, str]:
    if not get_user(username):
        return False, f"用户 {username} 不存在"
    with conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE username=?",
                  (hash_password(new_password), username))
    return True, "ok"


def delete_user(username: str) -> tuple[bool, str]:
    if username == "admin":
        return False, "不能删管理员账号"
    with conn() as c:
        c.execute("DELETE FROM users WHERE username=?", (username,))
        c.execute("DELETE FROM tokens WHERE username=?", (username,))
    return True, "ok"


# ---------- app_config（版本配置 / 升级开关） ----------
def get_config(key: str, default: str | None = None) -> str | None:
    with conn() as c:
        r = c.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_config(key: str, value: str):
    with conn() as c:
        c.execute(
            "INSERT INTO app_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_all_config() -> dict:
    """返回所有 config；首次启动会 seed 默认值。"""
    with conn() as c:
        rows = c.execute("SELECT key, value FROM app_config").fetchall()
    cfg = {r["key"]: r["value"] for r in rows}
    # 首次启动 seed
    defaults = {
        "latest_version": "1.2.1",
        "min_version":   "1.2.0",
        "force_update":  "false",       # "true" / "false"
        "windows_url":   "",
        "macos_url":     "",
        "release_notes": "",
    }
    changed = False
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
            c2 = sqlite3.connect(DB_PATH)
            try:
                c2.execute("INSERT INTO app_config(key,value) VALUES(?,?)", (k, v))
                c2.commit()
            finally:
                c2.close()
            changed = True
    return cfg


# ---------- 登录 / Token ----------
def login(username: str, password: str) -> tuple[dict | None, str]:
    """返回 (token_info or None, message)。"""
    with conn() as c:
        row = c.execute(
            "SELECT username, password_hash, expires_at, status FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if not row:
        return None, "用户名或密码错误"
    if not verify_password(password, row["password_hash"]):
        return None, "用户名或密码错误"
    if row["status"] != "active":
        return None, "账号已被停用，请联系管理员"
    # 检查用户到期
    if row["expires_at"] < datetime.now().isoformat():
        return None, f"账号已到期（{row['expires_at'][:10]}），请联系管理员续期"
    # 生成 token，30 天有效（每次 verify 滑动续期）
    token = secrets.token_urlsafe(32)
    token_exp = (datetime.now() + timedelta(days=30)).isoformat()
    with conn() as c:
        # 老 token 清掉（一个用户同时只允许一个活跃 token）
        c.execute("DELETE FROM tokens WHERE username=?", (username,))
        c.execute(
            "INSERT INTO tokens(token, username, expires_at, created_at) VALUES (?,?,?,?)",
            (token, username, token_exp, datetime.now().isoformat()),
        )
    return {"token": token, "expires_at": token_exp,
            "user_expires_at": row["expires_at"], "username": username}, "ok"


def verify(token: str) -> tuple[dict | None, str]:
    if not token:
        return None, "缺少 token"
    with conn() as c:
        row = c.execute(
            "SELECT t.token, t.username, t.expires_at AS tok_exp, "
            "       u.expires_at AS user_exp, u.status "
            "FROM tokens t JOIN users u ON t.username = u.username "
            "WHERE t.token=?",
            (token,),
        ).fetchone()
    if not row:
        return None, "token 无效"
    now_iso = datetime.now().isoformat()
    if row["tok_exp"] < now_iso:
        return None, "token 已过期，请重新登录"
    if row["user_exp"] < now_iso:
        return None, f"账号已到期（{row['user_exp'][:10]}），请联系管理员续期"
    if row["status"] != "active":
        return None, "账号已被停用"
    # 滑动续期：把 token 过期时间延长 30 天
    new_exp = (datetime.now() + timedelta(days=30)).isoformat()
    with conn() as c:
        c.execute("UPDATE tokens SET expires_at=? WHERE token=?", (new_exp, token))
    return {
        "valid": True,
        "username": row["username"],
        "expires_at": row["user_exp"],
    }, "ok"


def logout(token: str):
    with conn() as c:
        c.execute("DELETE FROM tokens WHERE token=?", (token,))
