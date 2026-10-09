"""听潮 · 管理后台（FastAPI + Jinja2 全自定义 UI）。

启动：
    uvicorn admin:app --host 127.0.0.1 --port 8501
（systemd service 配在 /etc/systemd/system/douyin-auth-admin.service）

API 通过 BACKEND_URL 打到 server.py 的 FastAPI（默认 http://localhost:8000）。
nginx 反代 /admin → 127.0.0.1:8501。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
HERE = Path(__file__).parent

app = FastAPI(title="听潮 · 控制台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


class StripPrefixMiddleware:
    """模板里的资源与链接统一带 /admin 前缀（配合 nginx 按 /admin 反代），
    而路由注册时不带前缀。这里在 ASGI 层剥掉前缀，使后台在
    「直连 :8501」和「经 nginx /admin 访问」两种方式下都能正常打开。
    不加这层的话：/admin/static/*.css 与 /admin/users 都会 404，页面变成无样式裸 HTML。"""
    PREFIX = "/admin"

    def __init__(self, inner_app):
        self.app = inner_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path == self.PREFIX or path.startswith(self.PREFIX + "/"):
                stripped = path[len(self.PREFIX):] or "/"
                scope["path"] = stripped
                scope["raw_path"] = stripped.encode("utf-8")
                # 注意：这里不要设置 scope["root_path"]。Starlette 的 get_route_path()
                # 会再按 root_path 剥一次，导致 StaticFiles 挂载点匹配不上而 404。
        await self.app(scope, receive, send)


app.add_middleware(StripPrefixMiddleware)


# ---------- 后端调用 ----------
def api_get(path: str, token: str | None = None):
    headers = {"X-Admin-Token": token} if token else {}
    return requests.get(f"{BACKEND_URL}{path}", headers=headers, timeout=10)


def api_post(path: str, token: str | None = None, json=None):
    headers = {"X-Admin-Token": token} if token else {}
    return requests.post(f"{BACKEND_URL}{path}", headers=headers, json=json, timeout=10)


def api_patch(path: str, token: str | None = None, json=None):
    headers = {"X-Admin-Token": token} if token else {}
    return requests.patch(f"{BACKEND_URL}{path}", headers=headers, json=json, timeout=10)


def api_delete(path: str, token: str | None = None):
    headers = {"X-Admin-Token": token} if token else {}
    return requests.delete(f"{BACKEND_URL}{path}", headers=headers, timeout=10)


# ---------- 工具函数 ----------
def fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    try:
        return s[:10]
    except Exception:
        return s


def days_left(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        exp = datetime.fromisoformat(expires_at).date()
        return (exp - date.today()).days
    except Exception:
        return None


def status_label(user: dict) -> tuple[str, str]:
    """返回 (状态文字, 状态色 chip-class)"""
    if user["status"] != "active":
        return "已停用", "chip-gray"
    if user["expired"]:
        return "已到期", "chip-red"
    d = days_left(user["expires_at"])
    if d is not None and d <= 30:
        return f"剩 {d} 天", "chip-amber"
    return "正常", "chip-teal"


def require_token(request: Request) -> str | None:
    token = request.cookies.get("admin_token")
    if not token:
        return None
    # 验证 token 是否仍有效（每次都查会增加延迟；这里用一次预热：登录时已校验）
    return token


def common_ctx(request: Request, token: str | None, active_nav: str = "",
               extra: dict | None = None):
    # 注意：新版 Starlette signature 是 TemplateResponse(request, name, context)，
    # context 里**不能再含 "request" 键**（否则 dict 会变成 template name → unhashable）。
    ctx = {
        "active_nav": active_nav,
        "backend": BACKEND_URL,
    }
    if extra:
        ctx.update(extra)
    return ctx


# ============================================================
#  登录 / 退出
# ============================================================
@app.get("/", response_class=HTMLResponse)
def page_login(request: Request, msg: str = "", ok: bool = False):
    return templates.TemplateResponse(request, "login.html", common_ctx(
        request, None, extra={"msg": msg, "ok": ok}))


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        r = requests.post(f"{BACKEND_URL}/api/admin/login",
                          json={"username": username, "password": password}, timeout=10)
    except requests.exceptions.RequestException as e:
        return templates.TemplateResponse(request, "login.html", common_ctx(
            request, None, extra={"msg": f"连不上后端 {BACKEND_URL}：{e}", "ok": False}),
            status_code=503)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", "登录失败")
        except Exception:
            detail = "登录失败"
        return templates.TemplateResponse(request, "login.html", common_ctx(
            request, None, extra={"msg": detail, "ok": False}),
            status_code=401)
    token = r.json()["admin_token"]
    resp = RedirectResponse(url="/admin/dashboard", status_code=303)
    resp.set_cookie("admin_token", token, httponly=True, samesite="lax", max_age=8 * 3600)
    return resp


@app.post("/logout")
def logout(request: Request):
    token = request.cookies.get("admin_token")
    if token:
        try:
            api_post("/api/admin/logout", token=token)
        except Exception:
            pass
    resp = RedirectResponse(url="/admin/", status_code=303)
    resp.delete_cookie("admin_token")
    return resp


def _redirect_login():
    return RedirectResponse(url="/admin/", status_code=303)


# ============================================================
#  仪表盘
# ============================================================
@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard(request: Request):
    token = require_token(request)
    if not token:
        return _redirect_login()

    users_r = api_get("/api/admin/users", token=token)
    if users_r.status_code != 200:
        if users_r.status_code == 401:
            return _redirect_login()
        raise HTTPException(users_r.status_code, users_r.text)
    users = users_r.json()

    today = date.today()
    total = len(users)
    active = sum(1 for u in users if u["status"] == "active" and not u["expired"])
    disabled = sum(1 for u in users if u["status"] != "active")
    expiring = 0
    for u in users:
        if u["status"] == "active" and not u["expired"]:
            d = days_left(u["expires_at"])
            if d is not None and d <= 30:
                expiring += 1

    # 即将到期列表（top 5）
    expiring_list = []
    for u in users:
        if u["status"] == "active" and not u["expired"]:
            d = days_left(u["expires_at"])
            if d is not None and 0 < d <= 60:
                expiring_list.append({**u, "days_left": d})
    expiring_list.sort(key=lambda x: x["days_left"])
    expiring_list = expiring_list[:5]

    # 最近创建的 top 5
    recent = sorted(users, key=lambda u: u.get("created_at", ""), reverse=True)[:5]

    return templates.TemplateResponse(request, "dashboard.html", common_ctx(
        request, token, active_nav="dashboard",
        extra={
            "kpi_total": total,
            "kpi_active": active,
            "kpi_expiring": expiring,
            "kpi_disabled": disabled,
            "expiring_list": expiring_list,
            "recent_list": recent,
        }))


# ============================================================
#  用户管理
# ============================================================
@app.get("/users", response_class=HTMLResponse)
def page_users(request: Request, msg: str = "", ok: bool = False,
               action: str = "", target: str = ""):
    token = require_token(request)
    if not token:
        return _redirect_login()

    users_r = api_get("/api/admin/users", token=token)
    if users_r.status_code != 200:
        if users_r.status_code == 401:
            return _redirect_login()
        raise HTTPException(users_r.status_code, users_r.text)
    users = users_r.json()

    rows = []
    for u in users:
        is_admin = u["username"] == "admin"
        text, chip_cls = status_label(u)
        rows.append({
            "username": u["username"],
            "is_admin": is_admin,
            "expires_at": fmt_date(u["expires_at"]),
            "days_left": days_left(u["expires_at"]),
            "status_text": text,
            "status_chip": chip_cls,
            "created_at": fmt_date(u.get("created_at")),
            "note": u.get("note", ""),
        })
    # 排序：管理员最前，再按到期日升序
    rows.sort(key=lambda r: (not r["is_admin"], r["expires_at"]))

    # 快捷到期日选项（写死，模板里 select 用）
    today = date.today()
    quick_options = [
        ("自定义", None),
        ("+30 天", (today + timedelta(days=30)).isoformat()),
        ("+90 天", (today + timedelta(days=90)).isoformat()),
        ("+1 年",  (today + timedelta(days=365)).isoformat()),
        ("+3 年",  (today + timedelta(days=365 * 3)).isoformat()),
    ]

    return templates.TemplateResponse(request, "users.html", common_ctx(
        request, token, active_nav="users",
        extra={
            "rows": rows,
            "msg": msg,
            "ok": ok,
            "action": action,
            "target": target,
            "quick_options": quick_options,
            "today": today.isoformat(),
            "default_expiry": (today + timedelta(days=30)).isoformat(),
        }))


# ---------- 创建用户 ----------
@app.post("/users/create")
def users_create_action(request: Request, username: str = Form(...),
                       password: str = Form(...),
                       expires_at: str = Form(...),
                       note: str = Form("")):
    token = request.cookies.get("admin_token")
    if not token:
        return _redirect_login()
    r = api_post("/api/admin/users", token=token,
                 json={"username": username, "password": password,
                       "expires_at": f"{expires_at}T23:59:59", "note": note})
    if r.status_code == 200:
        return RedirectResponse(url="/admin/users?ok=1&action=create&target=" + username,
                                status_code=303)
    try:
        detail = r.json().get("detail", "创建失败")
    except Exception:
        detail = "创建失败"
    return RedirectResponse(url=f"/admin/users?ok=0&action=create&target={username}",
                            status_code=303)


@app.post("/users/{username}/expiry")
def users_change_expiry(request: Request, username: str,
                        new_expires: str = Form(...)):
    token = request.cookies.get("admin_token")
    if not token:
        return _redirect_login()
    if not new_expires:
        return RedirectResponse(url=f"/admin/users?ok=0&action=expiry&target={username}",
                                status_code=303)
    payload = {"expires_at": f"{new_expires}T23:59:59"}
    r = api_patch(f"/api/admin/users/{username}/expiry", token=token, json=payload)
    if r.status_code == 200:
        return RedirectResponse(url=f"/admin/users?ok=1&action=expiry&target={username}",
                                status_code=303)
    return RedirectResponse(url=f"/admin/users?ok=0&action=expiry&target={username}",
                            status_code=303)


@app.post("/users/{username}/status")
def users_toggle_status(request: Request, username: str):
    token = request.cookies.get("admin_token")
    if not token:
        return _redirect_login()
    # 先查当前状态
    users_r = api_get("/api/admin/users", token=token)
    if users_r.status_code != 200:
        return RedirectResponse(url=f"/admin/users?ok=0&action=status&target={username}",
                                status_code=303)
    target = next((u for u in users_r.json() if u["username"] == username), None)
    if not target:
        return RedirectResponse(url=f"/admin/users?ok=0&action=status&target={username}",
                                status_code=303)
    new_status = "active" if target["status"] != "active" else "disabled"
    r = api_patch(f"/api/admin/users/{username}/status", token=token,
                  json={"status": new_status})
    if r.status_code == 200:
        return RedirectResponse(url=f"/admin/users?ok=1&action=status&target={username}",
                                status_code=303)
    return RedirectResponse(url=f"/admin/users?ok=0&action=status&target={username}",
                            status_code=303)


@app.post("/users/{username}/password")
def users_change_password(request: Request, username: str,
                          new_password: str = Form(...)):
    token = request.cookies.get("admin_token")
    if not token:
        return _redirect_login()
    r = api_post(f"/api/admin/users/{username}/password", token=token,
                 json={"new_password": new_password})
    if r.status_code == 200:
        return RedirectResponse(url=f"/admin/users?ok=1&action=password&target={username}",
                                status_code=303)
    return RedirectResponse(url=f"/admin/users?ok=0&action=password&target={username}",
                            status_code=303)


@app.post("/users/{username}/delete")
def users_delete(request: Request, username: str):
    token = request.cookies.get("admin_token")
    if not token:
        return _redirect_login()
    r = api_delete(f"/api/admin/users/{username}", token=token)
    if r.status_code == 200:
        return RedirectResponse(url=f"/admin/users?ok=1&action=delete&target={username}",
                                status_code=303)
    return RedirectResponse(url=f"/admin/users?ok=0&action=delete&target={username}",
                            status_code=303)


# ============================================================
#  版本配置 / 自动升级
# ============================================================
@app.get("/config", response_class=HTMLResponse)
def page_config(request: Request, msg: str = "", ok: bool = False):
    token = require_token(request)
    if not token:
        return _redirect_login()

    cfg_r = api_get("/api/admin/config", token=token)
    if cfg_r.status_code != 200:
        if cfg_r.status_code == 401:
            return _redirect_login()
        raise HTTPException(cfg_r.status_code, cfg_r.text)
    cfg = cfg_r.json()

    return templates.TemplateResponse(request, "config.html", common_ctx(
        request, token, active_nav="config",
        extra={
            "cfg": cfg,
            "msg": msg,
            "ok": ok,
        }))


@app.post("/config")
def config_save(request: Request,
                latest_version: str = Form(...),
                min_version: str = Form(...),
                force_update: str = Form("false"),
                windows_url: str = Form(""),
                macos_url: str = Form(""),
                release_notes: str = Form("")):
    token = request.cookies.get("admin_token")
    if not token:
        return _redirect_login()
    payload = {
        "latest_version": latest_version,
        "min_version": min_version,
        "force_update": "true" if force_update == "true" else "false",
        "windows_url": windows_url,
        "macos_url": macos_url,
        "release_notes": release_notes,
    }
    r = api_post("/api/admin/config", token=token, json=payload)
    if r.status_code == 200:
        return RedirectResponse(url="/admin/config?ok=1", status_code=303)
    try:
        detail = r.json().get("detail", "保存失败")
    except Exception:
        detail = "保存失败"
    return RedirectResponse(url=f"/admin/config?ok=0", status_code=303)


# ============================================================
#  帮助 / 关于
# ============================================================
@app.get("/help", response_class=HTMLResponse)
def page_help(request: Request):
    token = require_token(request)
    if not token:
        return _redirect_login()
    return templates.TemplateResponse(request, "help.html", common_ctx(
        request, token, active_nav="help"))


# 健康检查
@app.get("/health")
def health():
    return {"ok": True, "backend": BACKEND_URL}