"""听潮 · 渠道代理数据层

设计文档见 design/partner-channel.md。规则口径：
  · 官方价锁死，金额一律服务端按价目表计算，渠道端无改价入口
  · 首年 30%：同一客户自 first_paid_at 起 365 天内的全部实付按 rate_first
  · 续费 15%：365 天之后按 rate_renew
  · 按月结算：确认收款即写返佣流水，账单按月汇总
  · 公海回收：客户授权到期后连续 2 个月无新收款才解绑（年付客户在有效期内永不回收）

本模块只依赖 auth_db 的连接与建号能力，不改动原有授权逻辑。
"""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import auth_db

# 首年窗口（天）与公海回收宽限（天）
FIRST_YEAR_DAYS = 365
SEA_GRACE_DAYS = 60

# 可购买的时长（月）。价格 = 官方月价 × 月数，不做折扣
PURCHASE_MONTHS = (1, 3, 6, 12)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T")[:19])
    except ValueError:
        return None


@contextmanager
def db():
    """复用 auth_db 的库连接，保证与 users/tokens 在同一个 SQLite 事务里。"""
    with auth_db.conn() as c:
        yield c


# ---------------------------------------------------------------- 建表

DDL = """
CREATE TABLE IF NOT EXISTS partners (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    contact       TEXT DEFAULT '',
    phone         TEXT DEFAULT '',
    login_name    TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    rate_first    REAL NOT NULL DEFAULT 0.30,
    rate_renew    REAL NOT NULL DEFAULT 0.15,
    bank_name     TEXT DEFAULT '',
    bank_account  TEXT DEFAULT '',
    tax_no        TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS partner_leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id    INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    user_id       INTEGER,
    reported_at   TEXT NOT NULL,
    expire_at     TEXT NOT NULL,
    UNIQUE(customer_name, contact_phone)
);
CREATE INDEX IF NOT EXISTS idx_leads_partner ON partner_leads(partner_id, status);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no      TEXT UNIQUE NOT NULL,
    partner_id    INTEGER NOT NULL,
    username      TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    contact_phone TEXT DEFAULT '',
    plan          TEXT NOT NULL DEFAULT 'pro',
    months        INTEGER NOT NULL,
    amount        REAL NOT NULL,
    source        TEXT NOT NULL DEFAULT 'partner_manual',
    status        TEXT NOT NULL DEFAULT 'pending',
    is_first_year INTEGER,
    paid_at       TEXT,
    confirmed_by  TEXT,
    note          TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_partner ON orders(partner_id, status);

CREATE TABLE IF NOT EXISTS commissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL UNIQUE,
    partner_id  INTEGER NOT NULL,
    base_amount REAL NOT NULL,
    rate        REAL NOT NULL,
    amount      REAL NOT NULL,
    bill_month  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comm_partner_month ON commissions(partner_id, bill_month, status);

CREATE TABLE IF NOT EXISTS partner_bills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id   INTEGER NOT NULL,
    bill_month   TEXT NOT NULL,
    total_base   REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'draft',
    settled_at   TEXT,
    UNIQUE(partner_id, bill_month)
);
"""


def init_partner_db():
    """幂等建表 + 给 users 补渠道归属两列。启动时调用。"""
    with db() as c:
        c.executescript(DDL)
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "source_partner_id" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN source_partner_id INTEGER")
        if "first_paid_at" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN first_paid_at TEXT")


# ---------------------------------------------------------------- 价目表

def price_table() -> dict:
    """官方价目表（服务端唯一事实来源）。月价可在后台改，渠道不可改价。"""
    cfg = auth_db.get_all_config()
    try:
        unit = float(cfg.get("pro_price_month") or 268)
    except (TypeError, ValueError):
        unit = 268.0
    return {
        "unit_price": unit,
        "options": [{"months": m, "amount": round(unit * m, 2)} for m in PURCHASE_MONTHS],
    }


def calc_amount(months: int) -> float:
    pt = price_table()
    hit = next((o for o in pt["options"] if o["months"] == int(months)), None)
    if not hit:
        raise ValueError(f"不支持的时长：{months} 个月")
    return hit["amount"]


# ---------------------------------------------------------------- 渠道商

def create_partner(name: str, login_name: str, password: str, contact: str = "",
                   phone: str = "", note: str = "",
                   rate_first: float = 0.30, rate_renew: float = 0.15) -> tuple[bool, str, dict]:
    if not name.strip() or not login_name.strip():
        return False, "渠道名称与登录名必填", {}
    if len(password) < 6:
        return False, "初始密码至少 6 位", {}
    code = "P" + secrets.token_hex(3).upper()
    try:
        with db() as c:
            cur = c.execute(
                "INSERT INTO partners(code,name,contact,phone,login_name,password_hash,"
                "status,rate_first,rate_renew,note,created_at) VALUES(?,?,?,?,?,?, 'active',?,?,?,?)",
                (code, name.strip(), contact, phone, login_name.strip(),
                 auth_db.hash_password(password), float(rate_first), float(rate_renew),
                 note, now_iso()))
            pid = cur.lastrowid
    except sqlite3.IntegrityError:
        return False, "该登录名已存在", {}
    return True, "ok", get_partner(pid)


def get_partner(partner_id: int) -> dict:
    with db() as c:
        r = c.execute("SELECT * FROM partners WHERE id=?", (partner_id,)).fetchone()
    return dict(r) if r else {}


def get_partner_by_login(login_name: str) -> dict:
    with db() as c:
        r = c.execute("SELECT * FROM partners WHERE login_name=?", (login_name,)).fetchone()
    return dict(r) if r else {}


def list_partners() -> list[dict]:
    with db() as c:
        rows = c.execute("""
            SELECT p.*,
                   (SELECT COUNT(*) FROM users u WHERE u.source_partner_id = p.id) AS customer_count,
                   (SELECT COALESCE(SUM(o.amount),0) FROM orders o
                     WHERE o.partner_id = p.id AND o.status = 'paid') AS total_paid,
                   (SELECT COALESCE(SUM(cm.amount),0) FROM commissions cm
                     WHERE cm.partner_id = p.id AND cm.status = 'pending') AS pending_comm
            FROM partners p ORDER BY p.id DESC
        """).fetchall()
    return [dict(r) for r in rows]


def set_partner_status(partner_id: int, status: str) -> bool:
    if status not in ("active", "disabled"):
        return False
    with db() as c:
        c.execute("UPDATE partners SET status=? WHERE id=?", (status, partner_id))
    return True


def update_partner(partner_id: int, fields: dict) -> bool:
    allowed = {"name", "contact", "phone", "note", "bank_name", "bank_account",
               "tax_no", "rate_first", "rate_renew"}
    sets = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not sets:
        return False
    cols = ", ".join(f"{k}=?" for k in sets)
    with db() as c:
        c.execute(f"UPDATE partners SET {cols} WHERE id=?", (*sets.values(), partner_id))
    return True


# ---------------------------------------------------------------- 报备与归属

def create_lead(partner_id: int, customer_name: str, contact_phone: str) -> tuple[bool, str, dict]:
    """报备客户。同名同手机号已在保护期内：属于本渠道则续期，属于别的渠道则拒绝。"""
    if not customer_name.strip() or not contact_phone.strip():
        return False, "客户名称与联系手机号必填", {}
    now = datetime.now()
    expire = (now + timedelta(days=30)).isoformat(timespec="seconds")
    with db() as c:
        exist = c.execute(
            "SELECT * FROM partner_leads WHERE customer_name=? AND contact_phone=?",
            (customer_name.strip(), contact_phone.strip())).fetchone()
        if exist:
            alive = (exist["status"] == "pending"
                     and (parse_iso(exist["expire_at"]) or now) > now)
            if alive and exist["partner_id"] != partner_id:
                return False, f"该客户已被其他渠道报备，保护期内不可重复报备（到期日 {exist['expire_at'][:10]}）", {}
            c.execute("UPDATE partner_leads SET status='pending', expire_at=?, reported_at=? WHERE id=?",
                      (expire, now.isoformat(timespec="seconds"), exist["id"]))
            row = c.execute("SELECT * FROM partner_leads WHERE id=?", (exist["id"],)).fetchone()
            return True, "已延长保护期至 30 天后", dict(row)
        cur = c.execute(
            "INSERT INTO partner_leads(partner_id,customer_name,contact_phone,status,reported_at,expire_at)"
            " VALUES(?,?,?,'pending',?,?)",
            (partner_id, customer_name.strip(), contact_phone.strip(),
             now.isoformat(timespec="seconds"), expire))
        row = c.execute("SELECT * FROM partner_leads WHERE id=?", (cur.lastrowid,)).fetchone()
    return True, "报备成功，保护期 30 天", dict(row)


def list_leads(partner_id: int | None = None, status: str | None = None) -> list[dict]:
    q, args = "SELECT l.*, p.name AS partner_name FROM partner_leads l LEFT JOIN partners p ON p.id=l.partner_id WHERE 1=1", []
    if partner_id is not None:
        q += " AND l.partner_id=?"; args.append(partner_id)
    if status:
        q += " AND l.status=?"; args.append(status)
    q += " ORDER BY l.id DESC LIMIT 500"
    with db() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def resolve_lead(partner_id: int, customer_name: str, contact_phone: str) -> dict:
    """返回当前对该客户有归属权的报备记录（无则空）。"""
    now = datetime.now()
    with db() as c:
        rows = c.execute(
            "SELECT * FROM partner_leads WHERE customer_name=? AND contact_phone=?",
            (customer_name.strip(), contact_phone.strip())).fetchall()
    for r in rows:
        if r["status"] == "converted":
            return dict(r)
        if r["status"] == "pending" and (parse_iso(r["expire_at"]) or now) > now:
            return dict(r)
    return {}


# ---------------------------------------------------------------- 订单

def _gen_order_no() -> str:
    return "TC" + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2).upper()


def create_order(partner_id: int, username: str, customer_name: str, months: int,
                 contact_phone: str = "", source: str = "partner_manual",
                 note: str = "") -> tuple[bool, str, dict]:
    """代客下单。金额服务端算；归属冲突直接拒绝。"""
    username = (username or "").strip()
    customer_name = (customer_name or "").strip()
    if not username or len(username) < 3:
        return False, "客户登录账号至少 3 个字符（客户用它在听潮登录）", {}
    if not customer_name:
        return False, "客户名称必填", {}
    try:
        amount = calc_amount(months)
    except ValueError as e:
        return False, str(e), {}

    with db() as c:
        exist_user = c.execute(
            "SELECT username, source_partner_id FROM users WHERE username=?", (username,)).fetchone()
        if exist_user and exist_user["source_partner_id"] not in (None, "", partner_id):
            owner = c.execute("SELECT name FROM partners WHERE id=?",
                              (exist_user["source_partner_id"],)).fetchone()
            return False, f"账号 {username} 已归属渠道「{owner['name'] if owner else '官方'}」，不能重复开单", {}

        # 报备归属：别人在保护期内报备了同名同手机，就不能抢
        if contact_phone:
            lead = resolve_lead(partner_id, customer_name, contact_phone)
            if lead and lead["partner_id"] != partner_id:
                return False, "该客户在别的渠道报备保护期内，请联系我方仲裁", {}

        order_no = _gen_order_no()
        c.execute(
            "INSERT INTO orders(order_no,partner_id,username,customer_name,contact_phone,plan,"
            "months,amount,source,status,note,created_at)\n            VALUES(?,?,?,?,?,'pro',?,?,?,'pending',?,?)",
            (order_no, partner_id, username, customer_name, contact_phone, int(months),
             amount, source, note, now_iso()))
        row = c.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    return True, "下单成功，等待我方确认收款", dict(row)


def list_orders(partner_id: int | None = None, status: str | None = None,
                limit: int = 500) -> list[dict]:
    q = ("SELECT o.*, p.name AS partner_name FROM orders o "
         "LEFT JOIN partners p ON p.id=o.partner_id WHERE 1=1")
    args: list = []
    if partner_id is not None:
        q += " AND o.partner_id=?"; args.append(partner_id)
    if status:
        q += " AND o.status=?"; args.append(status)
    q += " ORDER BY o.id DESC LIMIT ?"; args.append(int(limit))
    with db() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def get_order(order_no: str) -> dict:
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    return dict(r) if r else {}


def confirm_order(order_no: str, operator: str = "admin") -> tuple[bool, str]:
    """确认收款：同一事务里建/绑账号、算到期、算返佣。"""
    now = datetime.now()
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
        if not o:
            return False, "订单不存在"
        if o["status"] != "pending":
            return False, f"订单当前状态为 {o['status']}，不能重复核销"

        partner = c.execute("SELECT * FROM partners WHERE id=?", (o["partner_id"],)).fetchone()
        if not partner:
            return False, "渠道商不存在"

        user = c.execute("SELECT * FROM users WHERE username=?", (o["username"],)).fetchone()
        expires_at = _plus_months(now, int(o["months"]))
        if user:
            if user["status"] != "active":
                return False, "该账号已被停用，请先在我方后台处理"
            # 续费：未到期则在剩余期上叠加，已过期则从现在起算
            old_exp = parse_iso(user["expires_at"])
            base = old_exp if (old_exp and old_exp > now) else now
            expires_at = _plus_months(base, int(o["months"]))
            c.execute("UPDATE users SET expires_at=?, source_partner_id=COALESCE(source_partner_id,?)"
                      " WHERE username=?", (expires_at.isoformat(timespec="seconds"),
                                            o["partner_id"], o["username"]))
            first_paid = user["first_paid_at"]
            if not first_paid:
                first_paid = now.isoformat(timespec="seconds")
                c.execute("UPDATE users SET first_paid_at=? WHERE username=?", (first_paid, o["username"]))
        else:
            init_pw = "tc" + secrets.token_hex(3)
            first_paid = now.isoformat(timespec="seconds")
            # 直接在本连接里插号：auth_db.create_user 会另开连接，外层事务未提交时会锁库
            c.execute(
                "INSERT INTO users(username,password_hash,expires_at,status,note,is_trial,created_at)"
                " VALUES(?,?,?,?,?,0,?)",
                (o["username"], auth_db.hash_password(init_pw),
                 expires_at.isoformat(timespec="seconds"), "active",
                 f"{o['customer_name']}｜渠道单 {order_no}", now_iso()))
            c.execute("UPDATE users SET source_partner_id=?, first_paid_at=? WHERE username=?",
                      (o["partner_id"], first_paid, o["username"]))
            init_pwd = init_pw
        # 首年窗口判定
        fp = parse_iso(first_paid)
        is_first = 1 if (fp and (now - fp).days < FIRST_YEAR_DAYS) else 0
        rate = float(partner["rate_first"] if is_first else partner["rate_renew"])
        commission = round(float(o["amount"]) * rate, 2)

        c.execute("UPDATE orders SET status='paid', paid_at=?, is_first_year=?, confirmed_by=? WHERE id=?",
                  (now.isoformat(timespec="seconds"), is_first, operator, o["id"]))
        c.execute("INSERT INTO commissions(order_id,partner_id,base_amount,rate,amount,bill_month,status,created_at)"
                  " VALUES(?,?,?,?,?,?, 'pending',?)",
                  (o["id"], o["partner_id"], float(o["amount"]), rate, commission,
                   now.strftime("%Y-%m"), now_iso()))
        if o["contact_phone"]:
            lead = c.execute("SELECT * FROM partner_leads WHERE customer_name=? AND contact_phone=? "
                             "AND partner_id=? ORDER BY id DESC LIMIT 1",
                             (o["customer_name"], o["contact_phone"], o["partner_id"])).fetchone()
            if lead:
                uid = c.execute("SELECT rowid FROM users WHERE username=?", (o["username"],)).fetchone()
                c.execute("UPDATE partner_leads SET status='converted', user_id=? WHERE id=?",
                          (uid["rowid"] if uid else None, lead["id"]))
    try:
        return True, ("已核销并开通授权，返佣已入账｜新客户初始密码 " + init_pwd)
    except NameError:
        return True, "已核销并续费，返佣已入账"


def refund_order(order_no: str, operator: str = "admin") -> tuple[bool, str]:
    """退款：订单转 refunded，返佣红冲为负数进当期账单。"""
    now = datetime.now()
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
        if not o:
            return False, "订单不存在"
        if o["status"] != "paid":
            return False, "只有已收款的订单可以退款"
        cm = c.execute("SELECT * FROM commissions WHERE order_id=?", (o["id"],)).fetchone()
        c.execute("UPDATE orders SET status='refunded', note=note||? WHERE id=?",
                  (f"｜{now[:10]} 由 {operator} 退款", o["id"]))
        if cm:
            c.execute("UPDATE commissions SET status='rejected' WHERE id=?", (cm["id"],))
            c.execute("INSERT INTO commissions(order_id,partner_id,base_amount,rate,amount,bill_month,status,created_at)"
                      " VALUES(?,?,?,?,?,?, 'pending',?)",
                      (f"{o['id']}000", o["partner_id"], -float(o["amount"]), float(cm["rate"]),
                       -float(cm["amount"]), now.strftime("%Y-%m"), now_iso()))
    return True, "已退款，返佣已红冲"


def _plus_months(base: datetime, months: int) -> datetime:
    m = base.month - 1 + int(months)
    year, month = base.year + m // 12, m % 12 + 1
    day = min(base.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return base.replace(year=year, month=month, day=day)


# ---------------------------------------------------------------- 返佣与账单

def list_commissions(partner_id: int | None = None, bill_month: str | None = None,
                     status: str | None = None) -> list[dict]:
    q = ("SELECT cm.*, o.order_no, o.customer_name, o.amount AS order_amount, o.months, "
         "p.name AS partner_name FROM commissions cm "
         "LEFT JOIN orders o ON o.id = cm.order_id "
         "LEFT JOIN partners p ON p.id = cm.partner_id WHERE 1=1")
    args: list = []
    if partner_id is not None:
        q += " AND cm.partner_id=?"; args.append(partner_id)
    if bill_month:
        q += " AND cm.bill_month=?"; args.append(bill_month)
    if status:
        q += " AND cm.status=?"; args.append(status)
    q += " ORDER BY cm.id DESC LIMIT 1000"
    with db() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def generate_bills(bill_month: str) -> tuple[bool, str, int]:
    """按月汇总待结返佣生成/刷新账单（幂等，可重复调用）。"""
    n = 0
    with db() as c:
        rows = c.execute(
            "SELECT partner_id, SUM(base_amount) AS b, SUM(amount) AS a FROM commissions "
            "WHERE bill_month=? AND status='pending' GROUP BY partner_id", (bill_month,)).fetchall()
        for r in rows:
            c.execute("""INSERT INTO partner_bills(partner_id,bill_month,total_base,total_amount,status)
                         VALUES(?,?,?,?,'draft')
                         ON CONFLICT(partner_id,bill_month) DO UPDATE SET
                           total_base=excluded.total_base, total_amount=excluded.total_amount""",
                      (r["partner_id"], bill_month, round(r["b"] or 0, 2), round(r["a"] or 0, 2)))
            n += 1
    return True, f"{bill_month} 账单已生成/刷新（{n} 个渠道）", n


def list_bills(partner_id: int | None = None) -> list[dict]:
    q = ("SELECT b.*, p.name AS partner_name, p.login_name FROM partner_bills b "
         "LEFT JOIN partners p ON p.id=b.partner_id WHERE 1=1")
    args: list = []
    if partner_id is not None:
        q += " AND b.partner_id=?"; args.append(partner_id)
    q += " ORDER BY b.bill_month DESC, b.id DESC"
    with db() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def settle_bill(bill_id: int, action: str) -> tuple[bool, str]:
    """confirm=渠道商确认，pay=我方已打款（同时把流水置为 settled）。"""
    with db() as c:
        b = c.execute("SELECT * FROM partner_bills WHERE id=?", (bill_id,)).fetchone()
        if not b:
            return False, "账单不存在"
        if action == "confirm":
            if b["status"] != "draft":
                return False, "该账单无需确认"
            c.execute("UPDATE partner_bills SET status='confirmed' WHERE id=?", (bill_id,))
            return True, "已确认，等待我方打款"
        if action == "pay":
            c.execute("UPDATE partner_bills SET status='paid', settled_at=? WHERE id=?",
                      (now_iso(), bill_id))
            c.execute("UPDATE commissions SET status='settled' "
                      "WHERE partner_id=? AND bill_month=? AND status='pending'",
                      (b["partner_id"], b["bill_month"]))
            return True, "已标记打款"
        if action == "dispute":
            c.execute("UPDATE partner_bills SET status='disputed' WHERE id=?", (bill_id,))
            return True, "已标记争议，请线下核对"
    return False, "未知操作"


# ---------------------------------------------------------------- 概览与公海

def partner_overview(partner_id: int) -> dict:
    month = datetime.now().strftime("%Y-%m")
    with db() as c:
        def one(sql, args=()):
            r = c.execute(sql, args).fetchone()
            return (r[0] if r else None) or 0
        return {
            "month": month,
            "month_paid_count": one("SELECT COUNT(*) FROM orders WHERE partner_id=? AND status='paid' "
                                    "AND paid_at LIKE ?", (partner_id, month + "%")),
            "month_paid_amount": round(one("SELECT SUM(amount) FROM orders WHERE partner_id=? "
                                           "AND status='paid' AND paid_at LIKE ?", (partner_id, month + "%")), 2),
            "month_commission": round(one("SELECT SUM(amount) FROM commissions WHERE partner_id=? "
                                          "AND bill_month=?", (partner_id, month)), 2),
            "pending_commission": round(one("SELECT SUM(amount) FROM commissions WHERE partner_id=? "
                                            "AND status='pending'", (partner_id,)), 2),
            "total_commission": round(one("SELECT SUM(amount) FROM commissions WHERE partner_id=?",
                                          (partner_id,)), 2),
            "customer_count": one("SELECT COUNT(*) FROM users WHERE source_partner_id=?", (partner_id,)),
            "lead_count": one("SELECT COUNT(*) FROM partner_leads WHERE partner_id=? AND status='pending'",
                              (partner_id,)),
        }


def customers_of(partner_id: int) -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT username, expires_at, status, note, first_paid_at, created_at FROM users "
            "WHERE source_partner_id=? ORDER BY expires_at ASC", (partner_id,)).fetchall()
    out = []
    now = datetime.now()
    for r in rows:
        d = dict(r)
        exp = parse_iso(d.get("expires_at"))
        fp = parse_iso(d.get("first_paid_at"))
        d["in_first_year"] = bool(fp and exp and (exp - fp).days <= FIRST_YEAR_DAYS)
        d["expired"] = bool(exp and exp < now)
        out.append(d)
    return out


def release_stale_customers() -> int:
    """公海回收：授权到期后连续 SEA_GRACE_DAYS 天没有新收款的客户，解除渠道归属。

    注意条件里必须先过期——年付客户在有效期内一年只成交一次，
    若只按"2 个月无成交"回收会把年付客户的续费返佣从渠道商手里清掉。
    """
    now = datetime.now()
    line = (now - timedelta(days=SEA_GRACE_DAYS)).isoformat(timespec="seconds")
    with db() as c:
        rows = c.execute(
            "SELECT username, expires_at FROM users WHERE source_partner_id IS NOT NULL "
            "AND expires_at IS NOT NULL AND expires_at < ?", (line,)).fetchall()
        n = 0
        for r in rows:
            last = c.execute("SELECT MAX(paid_at) AS m FROM orders WHERE username=? AND status='paid'",
                             (r["username"],)).fetchone()
            last_paid = (last["m"] if last else None) or ""
            if last_paid and last_paid >= line:
                continue
            c.execute("UPDATE users SET source_partner_id=NULL WHERE username=?", (r["username"],))
            c.execute("UPDATE partner_leads SET status='expired' "
                      "WHERE customer_name IN (SELECT customer_name FROM orders WHERE username=?) "
                      "AND status='pending'", (r["username"],))
            n += 1
    return n
