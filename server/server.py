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
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import auth_db

app = FastAPI(title="DouyinCommentMiner 授权服务", version="1.0")

# 信任 nginx 反向代理的 X-Forwarded-* headers，让 request.base_url 用公网 host
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

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
    client_version: str = "0.0.0"   # 客户端版本号（强制升级检查）
    device_id: str = ""             # 客户端生成的 UUID
    device_name: str = ""           # 显示用
    kick_existing: bool = False     # 是否挤掉旧设备


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


class ConfigIn(BaseModel):
    """admin 设置版本配置"""
    latest_version: str | None = None
    min_version: str | None = None
    force_update: str | None = None   # "true" / "false"
    windows_url: str | None = None
    macos_url: str | None = None
    release_notes: str | None = None


# ---------- 版本号工具 ----------
def _ver_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)


def _ver_lt(a: str, b: str) -> bool:
    return _ver_tuple(a) < _ver_tuple(b)


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
    # 强制升级检查
    cfg = auth_db.get_all_config()
    min_ver = cfg.get("min_version", "0.0.0")
    force = cfg.get("force_update", "false") == "true"
    if min_ver != "0.0.0" and _ver_lt(body.client_version, min_ver):
        msg = (f"客户端版本过低（当前 {body.client_version}，要求 ≥ {min_ver}），"
               f"请升级后重试。")
        if force:
            # 强制升级：直接 426 拒绝
            raise HTTPException(426, msg)
        # 仅提示：把警告塞进响应里，客户端顶栏显示
        info, dbmsg, existing = auth_db.login(
            body.username, body.password, body.device_id, body.device_name,
            kick_existing=body.kick_existing)
        if msg == "ALREADY_LOGGED_IN" and existing is not None:
            raise HTTPException(409, detail={
                "code": "ALREADY_LOGGED_IN",
                "message": f"账号已在另一台设备登录（{existing}）",
                "existing_device": existing,
            })
        if not info:
            raise HTTPException(401, dbmsg)
        return {"valid": True, "token": info["token"],
                "expires_at": info["user_expires_at"],
                "username": info["username"], "message": "ok",
                "warn": msg, "min_version": min_ver}
    info, msg, existing = auth_db.login(
        body.username, body.password, body.device_id, body.device_name,
        kick_existing=body.kick_existing)
    if msg == "ALREADY_LOGGED_IN" and existing is not None:
        raise HTTPException(409, detail={
            "code": "ALREADY_LOGGED_IN",
            "message": f"账号已在另一台设备登录（{existing}）",
            "existing_device": existing,
        })
    if not info:
        raise HTTPException(401, msg)
    return {"valid": True, "token": info["token"],
            "expires_at": info["user_expires_at"],
            "username": info["username"], "message": "ok"}


@app.get("/api/verify")
def verify(token: str, device_id: str = ""):
    info, msg, code = auth_db.verify(token, device_id)
    if not info:
        # DEVICE_KICKED 是预期内的"被踢"，前端要区分对待
        detail = {"code": code, "message": msg}
        raise HTTPException(403, detail=detail)
    return info


@app.post("/api/trial")
def trial(request: Request):
    """自助申请 3 小时试用账号，每 IP 每天 1 次。"""
    ip = request.client.host or "unknown"
    info, msg = auth_db.create_trial_user(ip)
    if not info:
        raise HTTPException(429, msg)
    return info


@app.post("/api/logout")
def logout(x_token: str = Header(default=None, alias="X-Token")):
    if x_token:
        auth_db.logout(x_token)
    return {"ok": True}


# ---------- 管理员登录 ----------
@app.post("/api/admin/login")
def admin_login(body: LoginIn):
    """管理员登录拿到 admin_token（独立于客户端 token，不计入单设备限制）。"""
    # admin 不走设备绑定：直接验证密码 + 是否 admin
    user = auth_db.get_user(body.username)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    if not auth_db.verify_password(body.password, auth_db.get_user_password_hash(body.username)):
        raise HTTPException(401, "用户名或密码错误")
    if user["username"] != "admin":
        raise HTTPException(403, "不是管理员账号")
    if user["status"] != "active":
        raise HTTPException(403, "账号已停用")
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


# ---------- 升级相关（无需 token） ----------
@app.get("/api/latest")
def latest_version():
    """客户端启动时调用，检测是否有新版本 / 是否需要强制升级"""
    cfg = auth_db.get_all_config()
    return {
        "version":    cfg.get("latest_version", "1.0.0"),
        "min_version": cfg.get("min_version", "0.0.0"),
        "force_update": cfg.get("force_update", "false") == "true",
        "release_notes": cfg.get("release_notes", ""),
        "downloads": {
            "windows": cfg.get("windows_url", ""),
            "macos":   cfg.get("macos_url", ""),
        },
        # 顶层冗余一份，兼容 1.0.0 客户端（local_api._update_url 读的是平铺键）
        "windows_url": cfg.get("windows_url", ""),
        "macos_url":   cfg.get("macos_url", ""),
    }


# ---------- 管理员：版本配置 ----------
@app.post("/api/admin/config")
def admin_set_config(body: ConfigIn, _: str = Depends(require_admin)):
    """admin 在后台手动设置版本号 / 升级开关 / 下载链接"""
    for k, v in body.dict(exclude_none=True).items():
        if v is not None:
            auth_db.set_config(k, v)
    return {"ok": True, "config": auth_db.get_all_config()}


@app.get("/api/admin/config")
def admin_get_config(_: str = Depends(require_admin)):
    return auth_db.get_all_config()


# 升级包存放目录（与 nginx alias 对应）
DOWNLOADS_DIR = Path("/opt/app/downloads")


@app.post("/api/admin/upload-zip")
def admin_upload_zip(
    request: Request,
    file: UploadFile = File(...),
    platform: str = Form(...),   # "windows" / "macos"
    version: str = Form(...),    # e.g. "1.3.0"（无 v 前缀）
    _: str = Depends(require_admin),
):
    """admin 上传 zip 升级包 + 自动写最新版本号。"""
    if platform not in ("windows", "macos"):
        raise HTTPException(400, "platform 必须是 windows 或 macos")
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # 文件名：DouyinCommentMiner-{platform}-v{version}.zip
    fname = f"DouyinCommentMiner-{platform}-v{version}.zip"
    dest = DOWNLOADS_DIR / fname

    # 流式写盘
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # 完整下载 URL（用 request 拼出 scheme://host）
    base = str(request.base_url).rstrip("/")
    full_url = f"{base}/downloads/{fname}"

    # 同步写 app_config：latest_version / windows_url / macos_url
    url_key = "windows_url" if platform == "windows" else "macos_url"
    auth_db.set_config("latest_version", version)
    auth_db.set_config(url_key, full_url)

    return {"ok": True, "filename": fname, "size": dest.stat().st_size,
            "url": full_url,
            "config": auth_db.get_all_config()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
