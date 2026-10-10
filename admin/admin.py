"""听潮 · 管理后台（FastAPI + Jinja2 全自定义 UI）。

启动：
    uvicorn admin:app --host 127.0.0.1 --port 8501
（systemd service 配在 /etc/systemd/system/douyin-auth-admin.service）

API 通过 BACKEND_URL 打到 server.py 的 FastAPI（默认 http://localhost:8000）。
nginx 反代 /admin → 127.0.0.1:8501。
"""

from __future__ import annotations

import os
from urllib.parse import quote
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
HERE = Path(__file__).parent

app = FastAPI(title="听潮 · 控制台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
# 渠道商后台走独立前缀，保证在任何域名（主站 /p/、partner 子域、/admin/p/）下都能取到样式
app.mount("/p/static", StaticFiles(directory=HERE / "static"), name="pstatic")
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
    """到期/创建时间统一显示成 YYYY-MM-DD HH:MM:SS（无时间部分则补 23:59:59）。"""
    if not s:
        return "—"
    try:
        if "T" in s:
            return s.replace("T", " ")[:19]
        return f"{s[:10]} 23:59:59"
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
    # datetime-local 提交带时间（精确到秒），纯日期则按当天 23:59:59
    normalized = new_expires if "T" in new_expires else f"{new_expires}T23:59:59"
    payload = {"expires_at": normalized}
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



# ============================================================
#  渠道代理：我方管理台 + 渠道商后台
#  数据与规则都在后端 partner_db/partner_api，这里只做渲染与转发
# ============================================================

def api_partner(path: str, method: str = "GET", token: str | None = None,
                json=None, data=None):
    headers = {"X-Partner-Token": token} if token else {}
    return requests.request(method, f"{BACKEND_URL}{path}", headers=headers,
                            json=json, data=data, timeout=12)


def require_partner(request: Request):
    """返回 (token, partner_dict)；未登录或已停用返回 (None, None)。"""
    tok = request.cookies.get("partner_token")
    if not tok:
        return None, None
    r = api_partner("/api/partner/me", token=tok)
    if r.status_code != 200:
        return None, None
    return tok, r.json()


def p_ctx(partner: dict, nav: str, nav_title: str, extra: dict | None = None) -> dict:
    ctx = {
        "partner_name": partner.get("name", "渠道中心"),
        "partner_code": partner.get("code", ""),
        "rate_first": round(float(partner.get("rate_first", 0.3)) * 100),
        "rate_renew": round(float(partner.get("rate_renew", 0.15)) * 100),
        "nav": nav, "nav_title": nav_title,
    }
    if extra:
        ctx.update(extra)
    return ctx


def _last_month() -> str:
    today = date.today()
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{y:04d}-{m:02d}"


# ---------------- 我方 · 渠道商管理 ----------------
@app.get("/partners", response_class=HTMLResponse)
def page_partners(request: Request, msg: str = "", ok: bool = False):
    token = require_token(request)
    if not token:
        return _redirect_login()
    partners = api_get("/api/admin/partner/partners", token).json().get("partners", [])
    price = api_get("/api/admin/partner/price", token).json()
    return templates.TemplateResponse(request, "partners.html", common_ctx(
        request, token, active_nav="partners",
        extra={"partners": partners, "price": price, "msg": msg, "ok": ok, "err": msg if not ok else ""}))


@app.post("/partners/create")
def partners_create(request: Request, name: str = Form(...), login_name: str = Form(...),
                    password: str = Form(...), contact: str = Form(""), phone: str = Form(""),
                    note: str = Form(""), rate_first: float = Form(0.30),
                    rate_renew: float = Form(0.15)):
    token = require_token(request)
    if not token:
        return _redirect_login()
    r = api_post("/api/admin/partner/partners", token, {
        "name": name, "login_name": login_name, "password": password,
        "contact": contact, "phone": phone, "note": note,
        "rate_first": rate_first, "rate_renew": rate_renew})
    if r.status_code != 200:
        return RedirectResponse(url=f"/admin/partners?ok=0&msg={quote(r.json().get('detail','创建失败'))}",
                                status_code=303)
    return RedirectResponse(url="/admin/partners?ok=1&msg=" + quote("渠道商已创建"), status_code=303)


@app.post("/partners/{pid}/status")
def partners_status(request: Request, pid: int, status: str = Form(...)):
    token = require_token(request)
    if not token:
        return _redirect_login()
    api_patch(f"/api/admin/partner/partners/{pid}", token, {"status": status})
    return RedirectResponse(url="/admin/partners?ok=1&msg=" + quote("状态已更新"), status_code=303)


@app.post("/partners/price")
def partners_price(request: Request, pro_price_month: float = Form(...)):
    token = require_token(request)
    if not token:
        return _redirect_login()
    r = api_patch("/api/admin/partner/price", token, {"pro_price_month": pro_price_month})
    ok = r.status_code == 200
    msg = "价目表已更新" if ok else quote(r.json().get("detail", "更新失败"))
    return RedirectResponse(url=f"/admin/partners?ok={1 if ok else 0}&msg={msg}", status_code=303)


@app.get("/partner/orders", response_class=HTMLResponse)
def page_partner_orders(request: Request, status: str = "", msg: str = ""):
    token = require_token(request)
    if not token:
        return _redirect_login()
    orders = api_get("/api/admin/partner/orders" + (f"?status={status}" if status else ""),
                     token).json().get("orders", [])
    return templates.TemplateResponse(request, "partner_orders.html", common_ctx(
        request, token, active_nav="porders",
        extra={"orders": orders, "status": status, "msg": msg}))


@app.post("/partner/orders/{order_no}/confirm")
def action_order_confirm(request: Request, order_no: str):
    token = require_token(request)
    if not token:
        return _redirect_login()
    r = api_post(f"/api/admin/partner/orders/{order_no}/confirm", token)
    ok = r.status_code == 200
    msg = r.json().get("msg", "") if ok else r.json().get("detail", "核销失败")
    return RedirectResponse(url="/admin/partner/orders?ok=" + ("1" if ok else "0")
                            + "&msg=" + quote(msg), status_code=303)


@app.post("/partner/orders/{order_no}/refund")
def action_order_refund(request: Request, order_no: str):
    token = require_token(request)
    if not token:
        return _redirect_login()
    r = api_post(f"/api/admin/partner/orders/{order_no}/refund", token)
    ok = r.status_code == 200
    msg = r.json().get("msg", "") if ok else r.json().get("detail", "退款失败")
    return RedirectResponse(url="/admin/partner/orders?msg=" + quote(msg), status_code=303)


@app.get("/partner/bills", response_class=HTMLResponse)
def page_partner_bills(request: Request, msg: str = ""):
    token = require_token(request)
    if not token:
        return _redirect_login()
    bills = api_get("/api/admin/partner/bills", token).json().get("bills", [])
    return templates.TemplateResponse(request, "partner_bills.html", common_ctx(
        request, token, active_nav="pbills",
        extra={"bills": bills, "last_month": _last_month(), "msg": msg}))


@app.post("/partner/bills/generate")
def action_bills_generate(request: Request, bill_month: str = Form(...)):
    token = require_token(request)
    if not token:
        return _redirect_login()
    r = api_post("/api/admin/partner/bills/generate", token, {"bill_month": bill_month})
    return RedirectResponse(url="/admin/partner/bills?msg=" + quote(r.json().get("msg", "")),
                            status_code=303)


@app.post("/partner/bills/{bill_id}/settle")
def action_bill_settle(request: Request, bill_id: int, action: str = Form(...)):
    token = require_token(request)
    if not token:
        return _redirect_login()
    r = api_post(f"/api/admin/partner/bills/{bill_id}/settle", token, {"action": action})
    msg = r.json().get("msg", "") if r.status_code == 200 else r.json().get("detail", "操作失败")
    return RedirectResponse(url="/admin/partner/bills?msg=" + quote(msg), status_code=303)


# ---------------- 渠道商后台 ----------------
@app.get("/p/login", response_class=HTMLResponse)
def p_page_login(request: Request, msg: str = ""):
    return templates.TemplateResponse(request, "p_login.html", {"msg": msg})


@app.post("/p/login")
def p_login(request: Request, login_name: str = Form(...), password: str = Form(...)):
    r = api_partner("/api/partner/login", "POST",
                    json={"login_name": login_name, "password": password})
    if r.status_code != 200:
        return RedirectResponse(url="/p/login?msg=" + quote(r.json().get("detail", "登录失败")),
                                status_code=303)
    resp = RedirectResponse(url="/p/", status_code=303)
    resp.set_cookie("partner_token", r.json()["token"], httponly=True, samesite="lax", max_age=7 * 86400)
    return resp


@app.post("/p/logout")
def p_logout(request: Request):
    tok = request.cookies.get("partner_token")
    if tok:
        api_partner("/api/partner/logout", "POST", token=tok)
    resp = RedirectResponse(url="/p/login", status_code=303)
    resp.delete_cookie("partner_token")
    return resp


@app.get("/p/", response_class=HTMLResponse)
def p_dashboard(request: Request):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    ov = api_partner("/api/partner/overview", token=tok).json()
    return templates.TemplateResponse(request, "p_dashboard.html",
                                      p_ctx(partner, "dash", "经营概览", {"ov": ov}))


@app.get("/p/orders", response_class=HTMLResponse)
def p_orders(request: Request, msg: str = "", ok: bool = False):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    orders = api_partner("/api/partner/orders", token=tok).json().get("orders", [])
    price = api_partner("/api/partner/price", token=tok).json()
    return templates.TemplateResponse(request, "p_orders.html", p_ctx(
        partner, "orders", "开单与订单",
        {"orders": orders, "price": price, "err": msg if not ok else "", "msg": msg, "ok": ok}))


@app.post("/p/orders/create")
def p_order_create(request: Request, username: str = Form(...), customer_name: str = Form(...),
                   months: int = Form(1), contact_phone: str = Form("")):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    r = api_partner("/api/partner/orders", "POST", token=tok, json={
        "username": username, "customer_name": customer_name,
        "months": months, "contact_phone": contact_phone})
    ok = r.status_code == 200
    msg = r.json().get("msg", "已提交") if ok else r.json().get("detail", "下单失败")
    return RedirectResponse(url=f"/p/orders?ok={1 if ok else 0}&msg=" + quote(msg), status_code=303)


@app.get("/p/customers", response_class=HTMLResponse)
def p_customers(request: Request, msg: str = "", ok: bool = False):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    customers = api_partner("/api/partner/customers", token=tok).json().get("customers", [])
    leads = api_partner("/api/partner/leads", token=tok).json().get("leads", [])
    return templates.TemplateResponse(request, "p_customers.html", p_ctx(
        partner, "customers", "客户与报备",
        {"customers": customers, "leads": leads, "err": msg if not ok else "", "msg": msg, "ok": ok}))


@app.post("/p/leads/create")
def p_lead_create(request: Request, customer_name: str = Form(...), contact_phone: str = Form(...)):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    r = api_partner("/api/partner/leads", "POST", token=tok, json={
        "customer_name": customer_name, "contact_phone": contact_phone})
    ok = r.status_code == 200
    msg = r.json().get("msg", "已报备") if ok else r.json().get("detail", "报备失败")
    return RedirectResponse(url=f"/p/customers?ok={1 if ok else 0}&msg=" + quote(msg), status_code=303)


@app.get("/p/bills", response_class=HTMLResponse)
def p_bills(request: Request, msg: str = "", ok: bool = False):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    data = api_partner("/api/partner/bills", token=tok).json()
    return templates.TemplateResponse(request, "p_bills.html", p_ctx(
        partner, "bills", "返佣账单",
        {"bills": data.get("bills", []), "commissions": data.get("commissions", []),
         "last_month": _last_month(), "err": msg if not ok else "", "msg": msg, "ok": ok}))


@app.post("/p/bills/{bill_id}/confirm")
def p_bill_confirm(request: Request, bill_id: int):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    r = api_partner(f"/api/partner/bills/{bill_id}/confirm", "POST", token=tok)
    ok = r.status_code == 200
    msg = r.json().get("msg", "") if ok else r.json().get("detail", "操作失败")
    return RedirectResponse(url=f"/p/bills?ok={1 if ok else 0}&msg=" + quote(msg), status_code=303)


@app.get("/p/bills/{bill_month}/export.csv")
def p_bill_export(request: Request, bill_month: str):
    tok, _ = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    r = api_partner(f"/api/partner/bills/{bill_month}/export.csv", token=tok)
    resp = Response(content=r.content, media_type="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="commission-{bill_month}.csv"'
    return resp


@app.get("/p/profile", response_class=HTMLResponse)
def p_profile(request: Request):
    tok, partner = require_partner(request)
    if not tok:
        return RedirectResponse(url="/p/login", status_code=303)
    return templates.TemplateResponse(request, "p_profile.html",
                                      p_ctx(partner, "profile", "结算资料", {"partner": partner}))


# 健康检查
@app.get("/health")
def health():
    return {"ok": True, "backend": BACKEND_URL}