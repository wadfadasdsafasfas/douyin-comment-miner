"""Streamlit 管理后台 — 用户管理 / 改期 / 启停。

启动：
    streamlit run admin.py
默认浏览器打开 http://localhost:8501

所有 API 请求打到 server.py 的 FastAPI（默认 http://localhost:8000）。
可通过环境变量 BACKEND_URL 改：
    BACKEND_URL=https://your-ngrok-url streamlit run admin.py
"""

import os
from datetime import datetime, timedelta

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")


def api(method: str, path: str, json=None, params=None, admin_token: str | None = None):
    headers = {}
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    try:
        r = requests.request(method, f"{BACKEND_URL}{path}",
                             json=json, params=params, headers=headers, timeout=10)
        if r.status_code == 401:
            st.session_state.pop("admin_token", None)
            st.error("管理员登录已失效，请重新登录")
            st.rerun()
        return r
    except requests.exceptions.RequestException as e:
        st.error(f"连不上后端 {BACKEND_URL}：{e}")
        st.stop()


st.set_page_config(page_title="听潮 · 控制台",
                   page_icon="🐙", layout="wide")

# ===== 品牌色 CSS 注入（听潮 设计 token） =====
# 颜色全部对齐客户端 GUI 的 token（见 douyin_miner_gui.py）
_BRAND = "#FF4D4D"
_BRAND_INK = "#DE3232"
_BRAND_SOFT = "#FFEDEC"
_INK = "#171A26"
_INK2 = "#5A6072"
_INK3 = "#9AA0B4"
_LINE = "#EBEBF2"
_LINE2 = "#DEDEE9"
_BG = "#F5F5FA"
_SURFACE2 = "#F8F8FC"
_TEAL = "#0FB5A5"
_TEAL_SOFT = "#E2F7F4"
_AMBER = "#FFB020"
_AMBER_SOFT = "#FFF4DC"

st.markdown(f"""
<style>
  /* 整体页面 */
  .stApp {{ background: {_BG}; }}
  [data-testid="stSidebar"] {{ background: {_SURFACE2}; }}
  /* 主文字 / 标题 */
  h1, h2, h3, p, span, label, .stMarkdown, .stText, .stCaption {{
    color: {_INK} !important;
  }}
  .stCaption, small {{ color: {_INK3} !important; }}
  /* 输入框 / 表单 */
  .stTextInput input, .stTextArea textarea, .stDateInput input,
  .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div {{
    background: #FFFFFF !important;
    border: 1px solid {_LINE2} !important;
    border-radius: 10px !important;
    color: {_INK} !important;
  }}
  /* 主按钮（form_submit_button type=primary） */
  .stFormSubmitButton button, button[kind="primary"] {{
    background: {_BRAND} !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
  }}
  .stFormSubmitButton button:hover, button[kind="primary"]:hover {{
    background: {_BRAND_INK} !important;
  }}
  /* 次按钮 */
  button[kind="secondary"], .stButton button {{
    background: #FFFFFF !important;
    color: {_INK2} !important;
    border: 1px solid {_LINE2} !important;
    border-radius: 10px !important;
  }}
  /* expander 卡片化 */
  details[data-testid="stExpander"] {{
    background: #FFFFFF !important;
    border: 1px solid {_LINE} !important;
    border-radius: 14px !important;
    box-shadow: 0 1px 2px rgba(23,26,38,.05);
    padding: 4px 8px;
  }}
  details[data-testid="stExpander"] summary {{
    color: {_INK} !important;
    font-weight: 700 !important;
  }}
  /* st.metric KPI 卡片 */
  [data-testid="stMetric"] {{
    background: #FFFFFF;
    border: 1px solid {_LINE};
    border-radius: 14px;
    padding: 18px 20px;
    box-shadow: 0 1px 2px rgba(23,26,38,.05);
  }}
  [data-testid="stMetric"] label {{ color: {_INK3} !important; font-size: 12.5px !important; }}
  [data-testid="stMetricValue"] {{ color: {_INK} !important; font-size: 26px !important; font-weight: 700 !important; }}
  /* st.popover / st.form 容器 */
  [data-testid="stPopover"], [data-testid="stForm"] {{
    background: #FFFFFF !important;
    border: 1px solid {_LINE} !important;
    border-radius: 12px !important;
  }}
  /* checkbox 颜色 */
  .stCheckbox label {{ color: {_INK} !important; }}
  /* 标签页 */
  .stTabs [data-baseweb="tab-list"] button {{
    color: {_INK2} !important;
    border-radius: 9px !important;
  }}
  .stTabs [aria-selected="true"] {{
    color: {_BRAND_INK} !important;
    background: {_BRAND_SOFT} !important;
  }}
  /* 成功 / 错误 */
  .stAlert {{ border-radius: 12px !important; }}
  /* divider */
  hr {{ border-color: {_LINE} !important; }}
</style>
""", unsafe_allow_html=True)

# ---------- 登录态 ----------
if "admin_token" not in st.session_state:
    st.session_state.admin_token = None


def login_page():
    st.title("🔐 管理员登录")
    st.caption(f"后端：{BACKEND_URL}")
    with st.form("login"):
        u = st.text_input("用户名", value="admin")
        p = st.text_input("密码", type="password")
        ok = st.form_submit_button("登录", use_container_width=True)
        if ok:
            r = api("POST", "/api/admin/login", json={"username": u, "password": p})
            if r.status_code == 200:
                st.session_state.admin_token = r.json()["admin_token"]
                st.success("登录成功")
                st.rerun()
            else:
                st.error(r.json().get("detail", "登录失败"))


def main_page():
    admin_token = st.session_state.admin_token

    # 顶栏（听潮品牌）
    c1, c2 = st.columns([6, 1])
    with c1:
        st.markdown(
            "<h1 style='margin-bottom:0'>🐙 听潮 · 控制台</h1>"
            "<small style='color:#9AA0B4;font-weight:600;letter-spacing:.14em'>TIDE SIGNAL · 用户管理</small>",
            unsafe_allow_html=True,
        )
    with c2:
        if st.button("退出登录"):
            api("POST", "/api/admin/logout", admin_token=admin_token)
            st.session_state.admin_token = None
            st.rerun()

    st.write("")  # 间距

    # ===== KPI 4 卡片（HTML .kpis） — 从用户列表算出来 =====
    try:
        users_r = api("GET", "/api/admin/users", admin_token=admin_token)
        users = users_r.json() if users_r.status_code == 200 else []
    except Exception:
        users = []

    if users:
        from datetime import date
        today = date.today()
        total = len(users)
        active = sum(1 for u in users if u["status"] == "active" and not u["expired"])
        disabled = sum(1 for u in users if u["status"] != "active")
        expiring = 0
        for u in users:
            if u["status"] == "active" and not u["expired"]:
                try:
                    exp_d = date.fromisoformat(u["expires_at"][:10])
                    if (exp_d - today).days <= 30:
                        expiring += 1
                except Exception:
                    pass
        kc1, kc2, kc3, kc4 = st.columns(4)
        with kc1: st.metric("用户总数", total)
        with kc2: st.metric("活跃授权", active)
        with kc3: st.metric("30 天内到期", expiring, delta_color="off")
        with kc4: st.metric("已停用", disabled)

    st.write("")

    # ===== 版本配置 / 自动升级 =====
    with st.expander("📌 版本配置 & 自动升级", expanded=False):
        cfg_r = api("GET", "/api/admin/config", admin_token=admin_token)
        cfg = cfg_r.json() if cfg_r.status_code == 200 else {}
        with st.form("version_config"):
            cc1, cc2 = st.columns(2)
            with cc1:
                latest = st.text_input("最新版本号",
                                       value=cfg.get("latest_version", "1.0.0"),
                                       help="客户端启动时检测到这个版本就提示升级")
                minv = st.text_input("最低允许版本",
                                     value=cfg.get("min_version", "0.0.0"),
                                     help="低于这个版本登录会被拒绝（强制升级）")
                force = st.checkbox("强制升级",
                                    value=cfg.get("force_update", "false") == "true",
                                    help="勾选后，低于最低版本的客户端无法登录")
            with cc2:
                win_url = st.text_input("Windows zip URL",
                                        value=cfg.get("windows_url", ""),
                                        help="通常由 GitHub Actions 自动写入，可手动改")
                mac_url = st.text_input("macOS zip URL",
                                        value=cfg.get("macos_url", ""))
            notes = st.text_area("更新说明（可选，会显示在客户端弹窗里）",
                                 value=cfg.get("release_notes", ""), height=80)
            if st.form_submit_button("保存配置", use_container_width=True):
                payload = {
                    "latest_version": latest,
                    "min_version": minv,
                    "force_update": "true" if force else "false",
                    "windows_url": win_url,
                    "macos_url": mac_url,
                    "release_notes": notes,
                }
                r = api("POST", "/api/admin/config",
                        json=payload, admin_token=admin_token)
                if r.status_code == 200:
                    st.success("✅ 已保存")
                    st.rerun()
                else:
                    st.error(r.json().get("detail", "保存失败"))

    st.divider()

    # 拉用户列表
    r = api("GET", "/api/admin/users", admin_token=admin_token)
    users = r.json() if r.status_code == 200 else []

    # ===== 创建用户 =====
    with st.expander("➕ 创建新用户", expanded=False):
        with st.form("create"):
            c1, c2 = st.columns(2)
            with c1:
                username = st.text_input("用户名")
                password = st.text_input("初始密码", value="changeme123")
                note = st.text_input("备注（哪个客户/订单号）")
            with c2:
                days = st.number_input("有效天数", min_value=1, max_value=3650, value=30)
                expiry_date = st.date_input("或选到期日期",
                                           value=datetime.now().date() + timedelta(days=days))
            # 到期时间 = 日期的 23:59:59
            expires_at = f"{expiry_date}T23:59:59"
            submitted = st.form_submit_button("创建")
            if submitted:
                r = api("POST", "/api/admin/users",
                        json={"username": username, "password": password,
                              "expires_at": expires_at, "note": note},
                        admin_token=admin_token)
                if r.status_code == 200:
                    st.success(f"✅ 用户 {username} 已创建，到期 {expiry_date}")
                    st.rerun()
                else:
                    st.error(r.json().get("detail", "创建失败"))

    # ===== 用户列表 =====
    if not users:
        st.info("还没有用户")
        return

    st.divider()
    st.subheader(f"共 {len(users)} 个用户")

    for u in users:
        is_admin = u["username"] == "admin"
        is_expired = u["expired"]
        is_disabled = u["status"] != "active"

        badge = "🟢 正常" if (not is_expired and not is_disabled) else \
                "🟡 已停用" if is_disabled else "🔴 已到期"

        with st.container(border=True):
            c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 2, 2, 1.5, 1.5])
            with c1:
                st.markdown(f"**{u['username']}** {'（管理员）' if is_admin else ''}")
                if u.get("note"):
                    st.caption(u["note"])
            with c2:
                st.markdown(f"到期：`{u['expires_at'][:10]}`")
            with c3:
                st.markdown(f"状态：{badge}")
            with c4:
                st.markdown(f"创建：`{u['created_at'][:10]}`")
            with c5:
                # 改到期
                with st.popover("📅 改期"):
                    with st.form(f"exp_{u['username']}"):
                        new_exp_date = st.date_input(
                            "新到期日期",
                            value=datetime.fromisoformat(u["expires_at"]).date()
                            if not is_expired
                            else datetime.now().date(),
                            key=f"d_{u['username']}",
                        )
                        days_opt = st.selectbox("或快捷选",
                                                ["自定义", "+30天", "+90天", "+1年", "+3年"],
                                                key=f"q_{u['username']}")
                        if days_opt == "+30天":
                            final = datetime.now().date() + timedelta(days=30)
                        elif days_opt == "+90天":
                            final = datetime.now().date() + timedelta(days=90)
                        elif days_opt == "+1年":
                            final = datetime.now().date() + timedelta(days=365)
                        elif days_opt == "+3年":
                            final = datetime.now().date() + timedelta(days=365*3)
                        else:
                            final = new_exp_date
                        if st.form_submit_button("保存"):
                            r = api("PATCH", f"/api/admin/users/{u['username']}/expiry",
                                    json={"expires_at": f"{final}T23:59:59"},
                                    admin_token=admin_token)
                            if r.status_code == 200:
                                st.success(f"已更新到 {final}")
                                st.rerun()
            with c6:
                if is_admin:
                    st.caption("—")
                else:
                    with st.popover("⚙️"):
                        # 启停
                        new_status = "active" if is_disabled else "disabled"
                        if st.button(f"{'启用' if is_disabled else '停用'}",
                                     key=f"st_{u['username']}"):
                            r = api("PATCH", f"/api/admin/users/{u['username']}/status",
                                    json={"status": new_status},
                                    admin_token=admin_token)
                            if r.status_code == 200:
                                st.rerun()
                        # 改密码
                        with st.form(f"pw_{u['username']}"):
                            new_pw = st.text_input("新密码", key=f"np_{u['username']}",
                                                   type="password")
                            if st.form_submit_button("改密码"):
                                r = api("POST", f"/api/admin/users/{u['username']}/password",
                                        json={"new_password": new_pw},
                                        admin_token=admin_token)
                                if r.status_code == 200:
                                    st.success("密码已改")
                        # 删除
                        if st.button("🗑️ 删除", key=f"del_{u['username']}"):
                            r = api("DELETE", f"/api/admin/users/{u['username']}",
                                    admin_token=admin_token)
                            if r.status_code == 200:
                                st.success("已删除")
                                st.rerun()


if st.session_state.admin_token:
    main_page()
else:
    login_page()
