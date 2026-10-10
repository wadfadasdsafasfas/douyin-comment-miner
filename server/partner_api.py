"""听潮 · 渠道代理 HTTP 接口

由 server.py 调 partner_api.mount(app, require_admin) 挂载：
  · /api/admin/partner/**   我方管理台用（沿用管理员鉴权）
  · /api/partner/**         渠道商后台用（独立 token，只允许看自己的数据）
  · /api/public/partner/**  专属链接落地页与自助下单（无需登录）

隔离原则：渠道端所有查询的 partner_id 一律取自会话 token，
绝不接受请求参数里传来的 partner_id。
"""
from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import auth_db
import partner_db

# token → partner_id（与管理员 token 一样是进程内的，重启需重新登录）
PARTNER_TOKENS: dict[str, int] = {}


class PartnerLogin(BaseModel):
    login_name: str
    password: str


class OrderIn(BaseModel):
    username: str
    customer_name: str
    months: int
    contact_phone: str = ""
    note: str = ""


class LeadIn(BaseModel):
    customer_name: str
    contact_phone: str


class PartnerCreate(BaseModel):
    name: str
    login_name: str
    password: str
    contact: str = ""
    phone: str = ""
    note: str = ""
    rate_first: float = 0.30
    rate_renew: float = 0.15


class PartnerPatch(BaseModel):
    name: str | None = None
    contact: str | None = None
    phone: str | None = None
    note: str | None = None
    status: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    tax_no: str | None = None
    rate_first: float | None = None
    rate_renew: float | None = None


class BillIn(BaseModel):
    bill_month: str


class SettleIn(BaseModel):
    action: str


class PriceIn(BaseModel):
    pro_price_month: float


def require_partner(x_partner_token: str | None = Header(default=None)) -> int:
    pid = PARTNER_TOKENS.get(x_partner_token or "")
    if not pid:
        raise HTTPException(401, "需要渠道商登录")
    p = partner_db.get_partner(pid)
    if not p or p["status"] != "active":
        PARTNER_TOKENS.pop(x_partner_token, None)
        raise HTTPException(403, "渠道账号已停用，请联系我方")
    return pid


def _prev_month() -> str:
    now = datetime.now()
    y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return f"{y:04d}-{m:02d}"


def mount(app: FastAPI, require_admin) -> None:
    """把三组路由挂到 app 上。require_admin 由 server.py 传入，避免循环依赖。"""

    # ---------------- 启动：建表 + 定时清扫 ----------------
    @app.on_event("startup")
    def _partner_startup():
        partner_db.init_partner_db()
        import asyncio

        async def _loop():
            while True:
                try:
                    n = partner_db.release_stale_customers()
                    if n:
                        print(f"[partner] 公海回收 {n} 个客户", flush=True)
                    # 每月 1～5 日自动出上月账单（幂等，可反复跑）
                    if datetime.now().day <= 5:
                        partner_db.generate_bills(_prev_month())
                except Exception as e:                       # 出错不能拖垮主服务
                    print(f"[partner] 定时任务异常：{e}", flush=True)
                await asyncio.sleep(6 * 3600)

        asyncio.create_task(_loop())

    # ================= 我方管理台 =================
    A = "/api/admin/partner"

    @app.get(A + "/partners")
    def admin_partners(_: str = Depends(require_admin)):
        return {"partners": partner_db.list_partners()}

    @app.post(A + "/partners")
    def admin_partner_create(body: PartnerCreate, _: str = Depends(require_admin)):
        ok, msg, p = partner_db.create_partner(
            body.name, body.login_name, body.password, body.contact, body.phone,
            body.note, body.rate_first, body.rate_renew)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "partner": p}

    @app.patch(A + "/partners/{pid}")
    def admin_partner_patch(pid: int, body: PartnerPatch, _: str = Depends(require_admin)):
        fields = {k: v for k, v in body.dict().items() if v is not None}
        if "status" in fields:
            st = fields.pop("status")
            partner_db.set_partner_status(pid, st)
        if fields:
            partner_db.update_partner(pid, fields)
        return {"ok": True, "partner": partner_db.get_partner(pid)}

    @app.get(A + "/leads")
    def admin_leads(status: str = "", _: str = Depends(require_admin)):
        return {"leads": partner_db.list_leads(None, status or None)}

    @app.get(A + "/orders")
    def admin_orders(status: str = "", _: str = Depends(require_admin)):
        return {"orders": partner_db.list_orders(None, status or None)}

    @app.post(A + "/orders/{order_no}/confirm")
    def admin_order_confirm(order_no: str, operator: str = "admin",
                            _: str = Depends(require_admin)):
        ok, msg = partner_db.confirm_order(order_no, operator)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "msg": msg}

    @app.post(A + "/orders/{order_no}/refund")
    def admin_order_refund(order_no: str, operator: str = "admin",
                           _: str = Depends(require_admin)):
        ok, msg = partner_db.refund_order(order_no, operator)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "msg": msg}

    @app.get(A + "/commissions")
    def admin_commissions(bill_month: str = "", partner_id: int = 0,
                          _: str = Depends(require_admin)):
        return {"commissions": partner_db.list_commissions(
            partner_id or None, bill_month or None)}

    @app.post(A + "/bills/generate")
    def admin_bills_generate(body: BillIn, _: str = Depends(require_admin)):
        ok, msg, n = partner_db.generate_bills(body.bill_month)
        return {"ok": ok, "msg": msg, "count": n}

    @app.get(A + "/bills")
    def admin_bills(_: str = Depends(require_admin)):
        return {"bills": partner_db.list_bills(None)}

    @app.post(A + "/bills/{bill_id}/settle")
    def admin_bill_settle(bill_id: int, body: SettleIn, _: str = Depends(require_admin)):
        ok, msg = partner_db.settle_bill(bill_id, body.action)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": ok, "msg": msg}

    @app.get(A + "/price")
    def admin_price(_: str = Depends(require_admin)):
        return partner_db.price_table()

    @app.patch(A + "/price")
    def admin_price_set(body: PriceIn, _: str = Depends(require_admin)):
        if body.pro_price_month <= 0:
            raise HTTPException(400, "价格必须大于 0")
        auth_db.set_config("pro_price_month", str(round(body.pro_price_month, 2)))
        return {"ok": True, "price": partner_db.price_table()}

    @app.post(A + "/sweep")
    def admin_sweep(_: str = Depends(require_admin)):
        return {"released": partner_db.release_stale_customers()}

    # ================= 渠道商后台 =================
    P = "/api/partner"

    @app.post(P + "/login")
    def partner_login(body: PartnerLogin):
        p = partner_db.get_partner_by_login(body.login_name.strip())
        if not p or not auth_db.verify_password(body.password, p["password_hash"]):
            raise HTTPException(401, "账号或密码错误")
        if p["status"] != "active":
            raise HTTPException(403, "渠道账号已停用，请联系我方")
        token = secrets.token_urlsafe(24)
        PARTNER_TOKENS[token] = p["id"]
        return {"ok": True, "token": token,
                "partner": {"id": p["id"], "name": p["name"], "code": p["code"]}}

    @app.post(P + "/logout")
    def partner_logout(x_partner_token: str | None = Header(default=None)):
        PARTNER_TOKENS.pop(x_partner_token or "", None)
        return {"ok": True}

    @app.get(P + "/me")
    def partner_me(pid: int = Depends(require_partner)):
        p = partner_db.get_partner(pid)
        return {"id": p["id"], "name": p["name"], "code": p["code"],
                "rate_first": p["rate_first"], "rate_renew": p["rate_renew"],
                "bank_account": p["bank_account"], "tax_no": p["tax_no"]}

    @app.get(P + "/price")
    def partner_price(pid: int = Depends(require_partner)):
        return partner_db.price_table()

    @app.get(P + "/overview")
    def partner_overview(pid: int = Depends(require_partner)):
        return partner_db.partner_overview(pid)

    @app.get(P + "/customers")
    def partner_customers(pid: int = Depends(require_partner)):
        return {"customers": partner_db.customers_of(pid)}

    @app.get(P + "/leads")
    def partner_leads(pid: int = Depends(require_partner)):
        return {"leads": partner_db.list_leads(pid)}

    @app.post(P + "/leads")
    def partner_lead_add(body: LeadIn, pid: int = Depends(require_partner)):
        ok, msg, row = partner_db.create_lead(pid, body.customer_name, body.contact_phone)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "msg": msg, "lead": row}

    @app.get(P + "/orders")
    def partner_orders(pid: int = Depends(require_partner)):
        return {"orders": partner_db.list_orders(pid)}

    @app.post(P + "/orders")
    def partner_order_add(body: OrderIn, pid: int = Depends(require_partner)):
        ok, msg, row = partner_db.create_order(
            pid, body.username, body.customer_name, body.months,
            body.contact_phone, "partner_manual", body.note)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "msg": msg, "order": row}

    @app.get(P + "/commissions")
    def partner_commissions(pid: int = Depends(require_partner)):
        return {"commissions": partner_db.list_commissions(pid)}

    @app.get(P + "/bills")
    def partner_bills(pid: int = Depends(require_partner)):
        return {"bills": partner_db.list_bills(pid),
                "commissions": partner_db.list_commissions(pid)}

    @app.post(P + "/bills/{bill_id}/confirm")
    def partner_bill_confirm(bill_id: int, pid: int = Depends(require_partner)):
        bills = [b for b in partner_db.list_bills(pid) if b["id"] == bill_id]
        if not bills:
            raise HTTPException(404, "账单不存在")          # 越权也回 404，不泄露资源是否存在
        ok, msg = partner_db.settle_bill(bill_id, "confirm")
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "msg": msg}

    @app.get(P + "/bills/{bill_month}/export.csv", response_class=PlainTextResponse)
    def partner_bill_export(bill_month: str, pid: int = Depends(require_partner)):
        rows = partner_db.list_commissions(pid, bill_month)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["订单号", "客户", "时长(月)", "实付金额", "比例", "返佣", "状态", "成交时间"])
        for r in rows:
            w.writerow([r.get("order_no") or "", r.get("customer_name") or "", r.get("months") or "",
                        r.get("order_amount") or "", r.get("rate"), r.get("amount"),
                        r.get("status"), ""])
        return PlainTextResponse(
            "\ufeff" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="commission-{bill_month}.csv"'})

    # ================= 专属链接（公开） =================
    @app.get("/api/public/partner/{code}")
    def public_landing(code: str):
        with auth_db.conn() as c:
            r = c.execute("SELECT name, code, status FROM partners WHERE code=?", (code,)).fetchone()
        if not r or r["status"] != "active":
            raise HTTPException(404, "渠道链接无效或已停用")
        return {"partner": r["name"], "code": code, "price": partner_db.price_table()}

    @app.post("/api/public/partner/{code}/orders")
    def public_order(code: str, body: OrderIn):
        with auth_db.conn() as c:
            r = c.execute("SELECT id, status FROM partners WHERE code=?", (code,)).fetchone()
        if not r or r["status"] != "active":
            raise HTTPException(404, "渠道链接无效或已停用")
        ok, msg, row = partner_db.create_order(
            r["id"], body.username, body.customer_name, body.months,
            body.contact_phone, "partner_link", body.note)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "msg": "已提交，等待渠道方完成付款即可开通", "order_no": row["order_no"]}
