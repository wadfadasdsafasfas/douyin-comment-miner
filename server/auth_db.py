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
                is_trial INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                device_id   TEXT NOT NULL DEFAULT '',
                device_name TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(username);
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trial_log (
                ip           TEXT NOT NULL,
                username     TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                PRIMARY KEY (ip, requested_at)
            );
            CREATE INDEX IF NOT EXISTS idx_trial_ip ON trial_log(ip);
        """)
        # 旧库平滑升级：tokens 加 device_id/device_name 列
        cols = {r[1] for r in c.execute("PRAGMA table_info(tokens)").fetchall()}
        if "device_id" not in cols:
            c.execute("ALTER TABLE tokens ADD COLUMN device_id TEXT NOT NULL DEFAULT ''")
        if "device_name" not in cols:
            c.execute("ALTER TABLE tokens ADD COLUMN device_name TEXT NOT NULL DEFAULT ''")
        # 旧库：users 加 is_trial 列
        ucols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "is_trial" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN is_trial INTEGER NOT NULL DEFAULT 0")


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
            "INSERT INTO users(username,password_hash,expires_at,status,note,is_trial,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("admin", hash_password(plain_pw), "2099-12-31T23:59:59",
             "active", "默认管理员（首次启动自动创建）", 0, datetime.now().isoformat()),
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
            "SELECT username, expires_at, status, note, is_trial, created_at FROM users ORDER BY created_at DESC"
        )]


def get_user(username: str):
    with conn() as c:
        r = c.execute(
            "SELECT username, expires_at, status, note, is_trial, created_at FROM users WHERE username=?",
            (username,),
        ).fetchone()
        return dict(r) if r else None


def get_user_password_hash(username: str) -> str | None:
    """只取密码哈希（admin 校验等不需完整用户信息时用）。"""
    with conn() as c:
        r = c.execute(
            "SELECT password_hash FROM users WHERE username=?",
            (username,),
        ).fetchone()
        return r["password_hash"] if r else None


def create_user(username: str, password: str, expires_at: str, note: str = "",
                is_trial: int = 0) -> tuple[bool, str]:
    if not username or not password:
        return False, "用户名/密码不能为空"
    if get_user(username):
        return False, f"用户 {username} 已存在"
    with conn() as c:
        c.execute(
            "INSERT INTO users(username,password_hash,expires_at,status,note,is_trial,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (username, hash_password(password), expires_at, "active", note, is_trial,
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
        c.execute("DELETE FROM trial_log WHERE username=?", (username,))
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
def login(username: str, password: str, device_id: str = "",
          device_name: str = "", kick_existing: bool = False) -> tuple[dict | None, str, str | None]:
    """返回 (token_info or None, message, existing_device_name)。

    message == "ALREADY_LOGGED_IN" 时 existing_device_name 是当前已登录设备名。
    冲突时若 kick_existing=True，会先清掉旧 token 再生成新的。
    """
    with conn() as c:
        row = c.execute(
            "SELECT username, password_hash, expires_at, status FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if not row:
        return None, "用户名或密码错误", None
    if not verify_password(password, row["password_hash"]):
        return None, "用户名或密码错误", None
    if row["status"] != "active":
        return None, "账号已被停用，请联系管理员", None
    if row["expires_at"] < datetime.now().isoformat():
        return None, f"账号已到期（{row['expires_at'][:10]}），请联系管理员续期", None

    # 单设备检查：查所有现有 token
    with conn() as c:
        existing_list = c.execute(
            "SELECT token, device_id, device_name FROM tokens WHERE username=?",
            (username,),
        ).fetchall()
    # 分类：同设备 token（要删掉）/ 异设备 token（要踢掉）
    same_dev_tokens = []   # 重新登录
    other_dev_tokens = []  # 冲突
    for ex in existing_list:
        if ex["device_id"] == "__kicked__":
            # 已经被踢过的，不用管它，等过期自动清理
            continue
        if device_id and ex["device_id"] == device_id:
            same_dev_tokens.append(ex)
        else:
            other_dev_tokens.append(ex)
    if other_dev_tokens:
        if not kick_existing:
            existing_name = other_dev_tokens[0]["device_name"] or "未知设备"
            return None, "ALREADY_LOGGED_IN", existing_name
        # 踢掉：标记 __kicked__
        with conn() as c:
            for ex in other_dev_tokens:
                c.execute(
                    "UPDATE tokens SET device_id='__kicked__' WHERE token=?",
                    (ex["token"],),
                )

    # 生成 token，30 天有效
    token = secrets.token_urlsafe(32)
    token_exp = (datetime.now() + timedelta(days=30)).isoformat()
    with conn() as c:
        # 同设备二次登录：清掉旧 token（同 device_id）
        for ex in same_dev_tokens:
            c.execute("DELETE FROM tokens WHERE token=?", (ex["token"],))
        c.execute(
            "INSERT INTO tokens(token, username, expires_at, created_at, device_id, device_name)"
            " VALUES (?,?,?,?,?,?)",
            (token, username, token_exp, datetime.now().isoformat(),
             device_id or "", device_name or ""),
        )
    return ({"token": token, "expires_at": token_exp,
             "user_expires_at": row["expires_at"], "username": username},
            "ok", None)


def verify(token: str, device_id: str = "") -> tuple[dict | None, str, str]:
    """返回 (info or None, message, code)。
    code: "ok" / "DEVICE_KICKED" / "EXPIRED" / "DISABLED" / "INVALID"
    """
    if not token:
        return None, "缺少 token", "INVALID"
    with conn() as c:
        row = c.execute(
            "SELECT t.token, t.username, t.expires_at AS tok_exp, t.device_id, "
            "       u.expires_at AS user_exp, u.status "
            "FROM tokens t JOIN users u ON t.username = u.username "
            "WHERE t.token=?",
            (token,),
        ).fetchone()
    if not row:
        return None, "token 无效", "INVALID"
    now_iso = datetime.now().isoformat()
    if row["tok_exp"] < now_iso:
        return None, "token 已过期，请重新登录", "EXPIRED"
    # 单设备绑定：服务端存了 device_id 且与当前请求不一致 = 被另一台设备顶掉
    # "__kicked__" 表示被踢标记
    if row["device_id"] == "__kicked__":
        return None, "账号已被另一台设备登录", "DEVICE_KICKED"
    if row["device_id"] and row["device_id"] != device_id:
        return None, "账号已在另一台设备登录", "DEVICE_KICKED"
    if row["user_exp"] < now_iso:
        return None, f"账号已到期（{row['user_exp'][:10]}），请联系管理员续期", "EXPIRED"
    if row["status"] != "active":
        return None, "账号已被停用", "DISABLED"
    # 滑动续期：把 token 过期时间延长 30 天
    new_exp = (datetime.now() + timedelta(days=30)).isoformat()
    with conn() as c:
        c.execute("UPDATE tokens SET expires_at=? WHERE token=?", (new_exp, token))
    return {
        "valid": True,
        "username": row["username"],
        "expires_at": row["user_exp"],
    }, "ok", "ok"


# ---------- 试用账号（每 IP 每天 1 次） ----------
def create_trial_user(client_ip: str, hours: int = 3) -> tuple[dict | None, str]:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with conn() as c:
        # 同 IP 今天是否已申请过
        r = c.execute(
            "SELECT username, requested_at FROM trial_log WHERE ip=? AND requested_at >= ?",
            (client_ip, today_start),
        ).fetchone()
        if r:
            return None, "今天已经申请过试用账号，请明天再来"
    # 生成账号
    suffix = secrets.token_hex(3)  # 6 hex chars
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    username = f"trial_{ts}_{suffix}"
    password = secrets.token_urlsafe(9)
    expires = (datetime.now() + timedelta(hours=hours)).isoformat()
    note = f"自助试用（IP {client_ip}，3小时）"
    ok, msg = create_user(username, password, expires, note=note, is_trial=1)
    if not ok:
        return None, msg
    # 写 trial_log
    with conn() as c:
        c.execute(
            "INSERT INTO trial_log(ip, username, requested_at) VALUES (?,?,?)",
            (client_ip, username, datetime.now().isoformat()),
        )
    return {"username": username, "password": password, "expires_at": expires}, "ok"


def logout(token: str):
    with conn() as c:
        c.execute("DELETE FROM tokens WHERE token=?", (token,))
