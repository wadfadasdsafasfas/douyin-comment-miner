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


st.set_page_config(page_title="DouyinCommentMiner 管理后台",
                   page_icon="🔐", layout="wide")

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

    # 顶栏
    c1, c2 = st.columns([6, 1])
    with c1:
        st.title("👥 用户管理")
    with c2:
        if st.button("退出登录"):
            api("POST", "/api/admin/logout", admin_token=admin_token)
            st.session_state.admin_token = None
            st.rerun()

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
