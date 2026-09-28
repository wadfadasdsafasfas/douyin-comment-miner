"""FastAPI 后端 — 用户登录、token 校验、用户管理（管理员）。

启动：
    python server.py             # 默认监听 0.0.0.0:8000
    python server.py --port 9000

管理员默认账号首次启动自动创建（admin / 随机密码），
写到 ~/.douyin_miner_server.db 同目录的 admin_credentials.txt。
"""

from __future__ import annotations

import argparse
import secrets
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import auth_db

app = FastAPI(title="DouyinCommentMiner 授权服务", version="1.0")

# CORS — 允许 Streamlit 管理后台跨域调用（8501 → 8000）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 请求/响应模型 ----------
class LoginIn(BaseModel):
    username: str
    password: str


class CreateUserIn(BaseModel):
    username: str
    password: str
    expires_at: str   # YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS
    note: str = ""


class ExpiryIn(BaseModel):
    expires_at: str


class StatusIn(BaseModel):
    status: str   # active / disabled


class PasswordIn(BaseModel):
    new_password: str


# ---------- 管理员鉴权 ----------
ADMIN_TOKENS: set[str] = set()  # 进程内内存保存（重启需重新登录）


def require_admin(x_admin_token: str | None = Header(default=None)):
    if not x_admin_token or x_admin_token not in ADMIN_TOKENS:
        raise HTTPException(401, "需要管理员登录")
    return x_admin_token


# ---------- 启动 ----------
@app.on_event("startup")
def _startup():
    auth_db.init_db()
    new_pw = auth_db.ensure_admin()
    if new_pw:
        print("=" * 60)
        print("⚠️  默认管理员账号已自动创建")
        print(f"   用户名: admin")
        print(f"   密码:   {new_pw}")
        print(f"   已同时写入: {auth_db.DB_PATH.parent / 'admin_credentials.txt'}")
        print("   请立即保存并删除该文件！")
        print("=" * 60)


# ---------- 客户端接口 ----------
@app.post("/api/login")
def login(body: LoginIn):
    info, msg = auth_db.login(body.username, body.password)
    if not info:
        raise HTTPException(401, msg)
    return {"valid": True, "token": info["token"],
            "expires_at": info["user_expires_at"],
            "username": info["username"], "message": "ok"}


@app.get("/api/verify")
def verify(token: str):
    info, msg = auth_db.verify(token)
    if not info:
        raise HTTPException(403, msg)
    return info


@app.post("/api/logout")
def logout(x_token: str = Header(default=None, alias="X-Token")):
    if x_token:
        auth_db.logout(x_token)
    return {"ok": True}


# ---------- 管理员登录 ----------
@app.post("/api/admin/login")
def admin_login(body: LoginIn):
    """管理员登录拿到 admin_token（独立于客户端 token）。"""
    info, msg = auth_db.login(body.username, body.password)
    if not info:
        raise HTTPException(401, msg)
    if info["username"] != "admin":
        raise HTTPException(403, "不是管理员账号")
    admin_token = secrets.token_urlsafe(32)
    ADMIN_TOKENS.add(admin_token)
    return {"admin_token": admin_token}


@app.post("/api/admin/logout")
def admin_logout(token: str = Depends(require_admin)):
    ADMIN_TOKENS.discard(token)
    return {"ok": True}


# ---------- 管理员 CRUD ----------
@app.get("/api/admin/users")
def admin_list_users(_: str = Depends(require_admin)):
    rows = auth_db.list_users()
    # 给前端加个"是否到期"字段
    now = datetime.now().isoformat()
    for r in rows:
        r["expired"] = r["expires_at"] < now
    return rows


@app.post("/api/admin/users")
def admin_create_user(body: CreateUserIn, _: str = Depends(require_admin)):
    ok, msg = auth_db.create_user(body.username, body.password,
                                  body.expires_at, body.note)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@app.patch("/api/admin/users/{username}/expiry")
def admin_update_expiry(username: str, body: ExpiryIn,
                        _: str = Depends(require_admin)):
    ok, msg = auth_db.update_expiry(username, body.expires_at)
    if not ok:
        raise HTTPException(404, msg)
    return {"ok": True}


@app.patch("/api/admin/users/{username}/status")
def admin_update_status(username: str, body: StatusIn,
                        _: str = Depends(require_admin)):
    ok, msg = auth_db.set_status(username, body.status)
    if not ok:
        raise HTTPException(404, msg)
    return {"ok": True}


@app.post("/api/admin/users/{username}/password")
def admin_change_password(username: str, body: PasswordIn,
                          _: str = Depends(require_admin)):
    ok, msg = auth_db.change_password(username, body.new_password)
    if not ok:
        raise HTTPException(404, msg)
    return {"ok": True}


@app.delete("/api/admin/users/{username}")
def admin_delete_user(username: str, _: str = Depends(require_admin)):
    ok, msg = auth_db.delete_user(username)
    if not ok:
        raise HTTPException(400, msg)
    # 让该用户的所有 token 失效
    with auth_db.conn() as c:
        c.execute("DELETE FROM tokens WHERE username=?", (username,))
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "ts": datetime.now().isoformat()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
