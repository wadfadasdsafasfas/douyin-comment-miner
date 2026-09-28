#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音评论关键词名单挖掘 —— 图形界面 (CustomTkinter · 后台风格 · 实时结果)
--------------------------------------------------------------------------
评论监控：填链接 + 关键词，点开始抓取；命中的评论【边抓边实时追加】到下方列表，
不等全部跑完。登录在左侧「抖音账号」页完成。底层复用 douyin_miner.py（引擎）。

开发运行:  bash run_local.sh
打包:      build_windows.bat / 云端 build.yml（需 pip install customtkinter）
"""

import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import customtkinter as ctk
import requests

import douyin_miner as eng
from license import License


APP_TITLE = "抖音评论关键词名单挖掘"
DEFAULT_OUTDIR = os.path.join(os.path.expanduser("~"), "Documents", "抖音评论名单")

BG = "#eef1f5"
CARD = "#ffffff"
SIDE = "#1e2430"
SIDE_ON = "#5b46c9"
GREEN = "#0a7d3c"
GREEN_H = "#08612f"
BLUE = "#0a5bc4"
LINK = "#0a5bc4"
GRAY_BTN = "#eef1f4"
SUB = "#5b6770"
INK = "#1f2328"
LINE = "#e2e6ec"

COLS = [46, 96, 112, 330, 132, 56, 56]
HEADERS = ["序号", "视频ID", "用户名", "评论内容", "评论时间", "主页", "私信"]


def open_path(p):
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:
        _msg("error", "打不开", f"无法打开：\n{p}\n\n{e}")


def _trunc(s, n):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "…"


def _msg(kind, title, text):
    try:
        from tkinter import messagebox
        getattr(messagebox, {"warn": "showwarning", "error": "showerror",
                             "info": "showinfo"}[kind])(title, text)
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(1000, 640)
        self.configure(fg_color=BG)

        # 授权客户端
        self.license = License()

        self.q = queue.Queue()
        self.busy = False
        self.logged_in = False
        self.last_file = None
        self.stop_event = threading.Event()
        self.outdir = ctk.StringVar(value=DEFAULT_OUTDIR)
        self._rowcount = 0
        # 自动升级相关状态
        self.update_info: dict | None = None      # /api/latest 返回的完整 dict
        self.update_dismissed: bool = False      # 用户在登录页点了"稍后"
        self._update_dialog: ctk.CTkToplevel | None = None

        # 启动：先校验已存的 token，再决定显示登录页还是主界面
        self._boot()
        self.after(100, self.poll)

    # ================= 启动 / 授权 =================
    def _boot(self):
        # 启动后先在后台检测升级，避免阻塞 UI
        self.after(300, self._check_update_startup)

        if self.license.token:
            ok, msg = self.license.verify()
            if ok:
                self._build()
                self.after(100, self._heartbeat_loop)
                return
            self._show_login(msg or "登录已失效，请重新登录")
        else:
            self._show_login()

    def _show_login(self, error_msg: str = ""):
        """登录界面 —— 占满整个窗口，登录成功后再 build 主界面。"""
        # 清空窗口
        for w in self.winfo_children():
            w.destroy()

        self.geometry("480x560")
        self.minsize(420, 520)

        wrap = ctk.CTkFrame(self, fg_color=BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=40)

        ctk.CTkLabel(wrap, text="🐙", font=ctk.CTkFont(size=42)).pack(pady=(20, 4))
        ctk.CTkLabel(wrap, text=APP_TITLE, text_color="#1f2328",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 4))
        ctk.CTkLabel(wrap, text=f"授权服务器：{self.license.server_url}",
                     text_color=SUB, font=ctk.CTkFont(size=11)).pack(pady=(0, 24))

        card = ctk.CTkFrame(wrap, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color=LINE)
        card.pack(fill="x")

        ctk.CTkLabel(card, text="账号", text_color=SUB,
                     font=ctk.CTkFont(size=12), anchor="w").pack(fill="x", padx=18, pady=(18, 2))
        self.e_user = ctk.CTkEntry(card, height=38, corner_radius=8, border_color=LINE,
                                   placeholder_text="请输入账号")
        self.e_user.pack(fill="x", padx=18)

        ctk.CTkLabel(card, text="密码", text_color=SUB,
                     font=ctk.CTkFont(size=12), anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        self.e_pw = ctk.CTkEntry(card, height=38, corner_radius=8, border_color=LINE,
                                 placeholder_text="请输入密码", show="*")
        self.e_pw.pack(fill="x", padx=18)
        self.e_pw.bind("<Return>", lambda e: self._do_login())

        self.l_msg = ctk.CTkLabel(card, text=error_msg, text_color="#dc2626",
                                  font=ctk.CTkFont(size=12), anchor="w", wraplength=360)
        self.l_msg.pack(fill="x", padx=18, pady=(10, 0))

        self.b_login = ctk.CTkButton(card, text="登 录", height=42, corner_radius=8,
                                     fg_color="#4b6bff", hover_color="#6a86ff",
                                     text_color="#fff", font=ctk.CTkFont(size=14, weight="bold"),
                                     command=self._do_login)
        self.b_login.pack(fill="x", padx=18, pady=(18, 18))

        ctk.CTkLabel(wrap, text="没有账号？请联系销售开通。",
                     text_color=SUB, font=ctk.CTkFont(size=11)).pack(pady=(18, 0))
        ctk.CTkLabel(wrap, text="v1.0 · 合智云数",
                     text_color="#9aa3b1", font=ctk.CTkFont(size=10)).pack(side="bottom", pady=8)

        self.e_user.focus_set()

    def _do_login(self):
        u = self.e_user.get().strip()
        p = self.e_pw.get()
        if not u or not p:
            self.l_msg.configure(text="请输入账号和密码")
            return
        self.b_login.configure(state="disabled", text="登录中…")
        self.update()

        # 登录放后台线程，避免阻塞 UI
        def _worker():
            ok, msg = self.license.login(u, p)
            self.after(0, lambda: self._on_login_result(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_login_result(self, ok: bool, msg: str):
        if not ok:
            self.l_msg.configure(text=msg)
            self.b_login.configure(state="normal", text="登 录")
            self.e_pw.delete(0, "end")
            return
        # 登录成功：重建主界面
        self._build()
        self.after(100, self._heartbeat_loop)

    def _heartbeat_loop(self):
        """每 30 分钟调一次 /verify，账号被改期/停用时立刻锁界面。"""
        def _beat():
            ok, msg = self.license.verify()
            if not ok and self.busy is False:
                # 没在抓取时直接踢回登录页
                self.after(0, lambda: self._kick_to_login(msg))
        threading.Thread(target=_beat, daemon=True).start()
        self.after(30 * 60 * 1000, self._heartbeat_loop)

    # ================= 自动升级 =================
    def _check_update_startup(self):
        """启动后调用一次。强制升级场景下要禁用登录按钮。"""
        def _worker():
            info, has, reason = self.license.check_update()
            if info is None:
                return
            self.after(0, lambda: self._apply_update_info(info, has, reason))
        threading.Thread(target=_worker, daemon=True).start()

    def _apply_update_info(self, info: dict, has_update: bool, reason: str):
        self.update_info = info
        # 强制升级检查：min_version 逻辑已在 server 端处理；
        # 这里额外看一下：如果当前页是登录页，登录按钮没禁用就把状态传给 GUI
        # 实际禁用与否由 /api/login 返回的 426 + 客户端 catch 处理。

        if has_update and not self.update_dismissed:
            # 启动时立刻弹窗（升级时机）
            self._show_update_dialog()

    def _show_update_dialog(self):
        if self._update_dialog is not None and self._update_dialog.winfo_exists():
            return
        info = self.update_info or {}
        latest = info.get("version", "?")
        force = info.get("force_update", False)
        notes = info.get("release_notes", "")

        dlg = ctk.CTkToplevel(self)
        dlg.title("有新版本")
        dlg.geometry("480x340")
        dlg.transient(self)
        dlg.grab_set()
        self._update_dialog = dlg

        wrap = ctk.CTkFrame(dlg, fg_color=BG)
        wrap.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(wrap, text="🔔 发现新版本",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(4, 8))
        ctk.CTkLabel(wrap, text=f"当前版本：{self.license.client_version()}    最新版本：{latest}",
                     text_color=SUB).pack(pady=(0, 12))

        if force:
            ctk.CTkLabel(wrap, text="⚠️ 此版本为强制升级，必须更新才能继续使用。",
                         text_color="#dc2626").pack(pady=(0, 12))
        else:
            ctk.CTkLabel(wrap, text="建议升级以获得新功能 / 问题修复。",
                         text_color=SUB).pack(pady=(0, 12))

        if notes:
            notes_box = ctk.CTkTextbox(wrap, height=80, fg_color=CARD,
                                        border_width=1, border_color=LINE)
            notes_box.pack(fill="x", pady=(0, 12))
            notes_box.insert("1.0", notes)
            notes_box.configure(state="disabled")

        ctk.CTkLabel(wrap, text="更新过程会自动关闭当前程序并重新启动。",
                     text_color=SUB, font=ctk.CTkFont(size=11)).pack(pady=(4, 8))

        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))

        def do_update():
            dlg.destroy()
            self._do_download_and_apply()

        def do_later():
            self.update_dismissed = True
            dlg.destroy()

        ctk.CTkButton(bar, text="立即更新", height=40, command=do_update,
                     fg_color="#4b6bff", hover_color="#6a86ff").pack(side="left", expand=True, padx=(0, 6))
        if not force:
            ctk.CTkButton(bar, text="稍后", height=40, command=do_later,
                         fg_color=GRAY_BTN, hover_color="#dde1e6",
                         text_color=INK).pack(side="left", expand=True, padx=(6, 0))

    def _do_download_and_apply(self):
        info = self.update_info or {}
        sysname = sys.platform
        if sysname.startswith("win"):
            url = (info.get("downloads") or {}).get("windows", "")
            relpath = "DouyinCommentMiner.exe"
        elif sysname == "darwin":
            url = (info.get("downloads") or {}).get("macos", "")
            relpath = "DouyinCommentMiner.app"
        else:
            url = ""
            relpath = "DouyinCommentMiner"

        if not url:
            _msg("error", "升级失败", "服务端未提供本平台的下载链接。")
            return

        # 进度窗口
        prog = ctk.CTkToplevel(self)
        prog.title("正在下载更新")
        prog.geometry("460x160")
        prog.transient(self)
        prog.grab_set()
        ctk.CTkLabel(prog, text="正在下载新版本...", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(20, 8))
        prog_lbl = ctk.CTkLabel(prog, text="准备中...", text_color=SUB)
        prog_lbl.pack(pady=(0, 8))
        prog_bar = ctk.CTkProgressBar(prog, width=400)
        prog_bar.set(0)
        prog_bar.pack(pady=(0, 16))

        def _worker():
            try:
                # 选下载目的地：当前 exe 所在目录的 _update 子目录
                if getattr(sys, "frozen", False):
                    # PyInstaller --onedir 模式
                    exe_dir = Path(sys.executable).parent
                else:
                    exe_dir = Path(__file__).parent.resolve()
                update_dir = exe_dir / "_update"
                update_dir.mkdir(parents=True, exist_ok=True)
                zip_path = update_dir / "update.zip"

                ok = self._download_with_progress(url, zip_path, lambda done, total: self.after(0, lambda: _update_bar(done, total)))
                if not ok:
                    self.after(0, lambda: _fail("下载失败"))
                    return
                # 启 updater 子进程
                self.after(0, lambda: _spawn_updater(zip_path, update_dir, relpath))
            except Exception as e:
                self.after(0, lambda: _fail(str(e)))

        def _update_bar(done, total):
            if total > 0:
                prog_bar.set(done / total)
                mb_d = done / 1024 / 1024
                mb_t = total / 1024 / 1024
                prog_lbl.configure(text=f"{mb_d:.1f} / {mb_t:.1f} MB")

        def _spawn_updater(zip_path, update_dir, relpath):
            prog_lbl.configure(text="下载完成，准备重启...")
            prog_bar.set(1.0)
            self.update()
            time.sleep(0.3)
            self._launch_updater(zip_path, update_dir, relpath)

        def _fail(reason: str):
            prog.destroy()
            _msg("error", "升级失败", reason)

        threading.Thread(target=_worker, daemon=True).start()

    def _download_with_progress(self, url: str, dest: Path, cb) -> bool:
        try:
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                done = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            cb(done, total)
            return True
        except Exception:
            return False

    def _launch_updater(self, zip_path: Path, update_dir: Path, relpath: str):
        """启动 updater 子进程，然后退出主程序。
        relpath 是 update_dir 父目录下要启动的可执行路径。"""
        import updater as _upd
        # 找到 updater 自身（PyInstaller 打包时就在同 _internal 目录）
        updater_path = Path(_upd.__file__).resolve()
        wait_pid = os.getpid()

        if sys.platform.startswith("win"):
            # Windows: 子进程要落地成 .exe，所以用 python 调用 updater.py
            # 实际打包时会单独打 updater.exe 走 subprocess 直启
            cmd = [sys.executable, str(updater_path), "apply",
                   str(update_dir), str(zip_path), relpath,
                   "--wait-pid", str(wait_pid)]
            subprocess.Popen(cmd, creationflags=0x00000008)
        elif sys.platform == "darwin":
            # Mac: 同样的逻辑；如果是 frozen .app，则 updater 在 _internal 里
            cmd = [sys.executable, str(updater_path), "apply",
                   str(update_dir), str(zip_path), relpath,
                   "--wait-pid", str(wait_pid)]
            subprocess.Popen(cmd)
        else:
            cmd = [sys.executable, str(updater_path), "apply",
                   str(update_dir), str(zip_path), relpath,
                   "--wait-pid", str(wait_pid)]
            subprocess.Popen(cmd)

        # 短暂延迟让 updater 拿到主进程 pid，然后自杀
        self.after(500, self.destroy)

    def _kick_to_login(self, msg: str):
        try:
            self.stop_event.set()
        except Exception:
            pass
        _msg("warn", "账号已失效", f"{msg}\n请重新登录。")
        self._show_login(msg)

    def _do_logout(self):
        self.license.logout()
        self._show_login()

    # ================= 布局 =================
    def _build(self):
        # 清空窗口（登录界面 → 主界面切换时登录页 widget 必须先销毁，
        # 否则新主界面会 pack 叠加在登录页上，看起来还是登录页——但 token 已落盘）。
        for w in self.winfo_children():
            w.destroy()
        self.geometry("1180x760")
        self.minsize(1000, 640)

        bar = ctk.CTkFrame(self, fg_color="#e6e9ee", corner_radius=0, height=30)
        bar.pack(fill="x"); bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="🐙  " + APP_TITLE, text_color="#333",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12)

        # 右侧：账号 + 到期 + 退出
        days = self.license.days_left()
        exp = self.license.expiry_date()
        if days is not None and days < 7:
            exp_color = "#dc2626"
        else:
            exp_color = "#08612f"
        acct_text = f"👤 {self.license.username}   |   📅 到期 {exp}"
        ctk.CTkLabel(bar, text=acct_text, text_color=exp_color,
                     font=ctk.CTkFont(size=11)).pack(side="right", padx=8)
        ctk.CTkButton(bar, text="退出登录", width=68, height=22, corner_radius=4,
                      fg_color="#cfd6dd", hover_color="#b8c0c8", text_color="#1f2328",
                      font=ctk.CTkFont(size=11), command=self._do_logout).pack(side="right", padx=6)

        root = ctk.CTkFrame(self, fg_color=BG)
        root.pack(fill="both", expand=True)

        side = ctk.CTkFrame(root, fg_color=SIDE, width=160, corner_radius=0)
        side.pack(side="left", fill="y"); side.pack_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", pady=(18, 10))
        ctk.CTkLabel(brand, text="🐙", font=ctk.CTkFont(size=30)).pack()
        ctk.CTkLabel(brand, text="评论名单\n挖掘", text_color="#fff",
                     font=ctk.CTkFont(size=14, weight="bold"), justify="center").pack()
        self.nav_items = {}
        self._grp(side, "采集功能")
        self._nav(side, "monitor", "💬  评论监控", lambda: self._show("monitor"), on=True)
        self._grp(side, "账号")
        self._nav(side, "account", "👤  抖音账号", lambda: self._show("account"))
        self._grp(side, "帮助")
        self._nav(side, "help", "❓  使用说明", lambda: self._show("help"))
        ctk.CTkLabel(side, text="v1.0 · 合智云数", text_color="#5a6472",
                     font=ctk.CTkFont(size=10)).pack(side="bottom", pady=10)

        content = ctk.CTkFrame(root, fg_color=BG)
        content.pack(side="left", fill="both", expand=True, padx=16, pady=14)
        content.grid_rowconfigure(0, weight=1); content.grid_columnconfigure(0, weight=1)
        self.page = ctk.CTkFrame(content, fg_color=BG)
        self.page.grid(row=0, column=0, sticky="nsew")

        self._build_monitor(self.page)
        self._build_account(self.page)
        self._build_help(self.page)
        self._show("monitor")

    def _grp(self, parent, text):
        ctk.CTkLabel(parent, text=text, text_color="#6b7686", anchor="w",
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=16, pady=(12, 2))

    def _nav(self, parent, key, text, cmd, on=False):
        b = ctk.CTkButton(parent, text=text, anchor="w", height=38, corner_radius=0,
                          fg_color=SIDE_ON if on else "transparent", hover_color="#2a3140",
                          text_color="#fff" if on else "#aab4c3",
                          font=ctk.CTkFont(size=13), border_width=0, command=cmd)
        b.pack(fill="x", padx=(0, 8), pady=1)
        self.nav_items[key] = b

    def _show(self, which):
        for w in self.page.winfo_children():
            w.grid_forget()
        for k, b in self.nav_items.items():
            active = (k == which)
            b.configure(fg_color=SIDE_ON if active else "transparent",
                        text_color="#fff" if active else "#aab4c3")
        getattr(self, "frm_" + which).grid(sticky="nsew")

    # ---------- 评论监控页 ----------
    def _build_monitor(self, parent):
        f = ctk.CTkFrame(parent, fg_color=BG)
        self.frm_monitor = f
        f.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(f, text="💬  评论监控", text_color="#222", anchor="w",
                     font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))

        panel = ctk.CTkFrame(f, fg_color=CARD, corner_radius=10, border_width=1, border_color=LINE)
        panel.grid(row=1, column=0, sticky="ew")
        tb = ctk.CTkFrame(panel, fg_color="transparent"); tb.pack(fill="x", padx=14, pady=12)
        self.b_cfg = self._tbtn(tb, "⚙  配置", "#6a5cff", "#8a6bff", self.on_cfg)
        self.b_run = self._tbtn(tb, "▶  开始抓取", "#4b6bff", "#6a86ff", self.on_run)
        self.b_stop = self._tbtn(tb, "⏸  停止", "#cfd5dd", "#c3cad3", self.on_stop)
        self.b_stop.configure(text_color="#8a94a0", state="disabled")
        self.b_clear = self._tbtn(tb, "🗑  清空", "#7b5cff", "#9b7bff", self.on_clear)
        self.b_open = self._tbtn(tb, "打开表格", "#0a5bc4", "#084697", self.on_open)
        self.b_open.configure(state="disabled")

        # 配置面板（默认展开，方便填）
        self.cfg = ctk.CTkFrame(f, fg_color=CARD, corner_radius=10, border_width=1, border_color=LINE)
        self.cfg.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._build_cfg(self.cfg)

        self.status = ctk.CTkLabel(f, text="● 就绪", text_color="#e0663b", anchor="w",
                                   font=ctk.CTkFont(size=12))
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 4))

        tbl = ctk.CTkFrame(f, fg_color=CARD, corner_radius=10, border_width=1, border_color=LINE)
        tbl.grid(row=4, column=0, sticky="nsew")
        f.grid_rowconfigure(4, weight=1)
        head = ctk.CTkFrame(tbl, fg_color="#f3f5f8", corner_radius=0); head.pack(fill="x")
        self._mk_row(head, HEADERS, header=True)
        self.tbody = ctk.CTkScrollableFrame(tbl, fg_color=CARD, corner_radius=0)
        self.tbody.pack(fill="both", expand=True)
        self._empty_hint()

    def _tbtn(self, parent, text, fg, hover, cmd):
        b = ctk.CTkButton(parent, text=text, width=118, height=40, corner_radius=8,
                          fg_color=fg, hover_color=hover, text_color="#fff",
                          font=ctk.CTkFont(size=13, weight="bold"), command=cmd)
        b.pack(side="left", padx=(0, 12)); return b

    def _empty_hint(self):
        self._hint = ctk.CTkLabel(self.tbody, text="（填好链接和关键词，点「开始抓取」，命中结果会实时出现在这里）",
                                  text_color="#98a2b0", font=ctk.CTkFont(size=12))
        self._hint.pack(pady=40)

    def _build_cfg(self, p):
        pad = {"padx": 14, "pady": (8, 2)}
        ctk.CTkLabel(p, text="视频链接（一行一个，可粘分享文案）", text_color=SUB,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", **pad)
        self.links = ctk.CTkTextbox(p, height=74, corner_radius=8, border_width=1,
                                    border_color=LINE, fg_color="#fff", text_color=INK,
                                    font=ctk.CTkFont(size=13))
        self.links.pack(fill="x", padx=14)
        r2 = ctk.CTkFrame(p, fg_color="transparent"); r2.pack(fill="x")
        ctk.CTkLabel(r2, text="关键词（一行一个）", text_color=SUB,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", **pad)
        rr = ctk.CTkFrame(r2, fg_color="transparent"); rr.pack(fill="x", padx=14)
        self.kws = ctk.CTkTextbox(rr, width=520, height=52, corner_radius=8, border_width=1,
                                  border_color=LINE, fg_color="#fff", text_color=INK,
                                  font=ctk.CTkFont(size=13))
        self.kws.pack(side="left")
        right = ctk.CTkFrame(rr, fg_color="transparent"); right.pack(side="left", padx=16)
        ctk.CTkLabel(right, text="最多抓多少条/视频", text_color=SUB,
                     font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.maxc = ctk.CTkEntry(right, width=90, height=30, corner_radius=6, border_color=LINE)
        self.maxc.insert(0, "3000"); self.maxc.pack(anchor="w", pady=(4, 0))
        r3 = ctk.CTkFrame(p, fg_color="transparent"); r3.pack(fill="x", padx=14, pady=(10, 12))
        self.with_replies = ctk.CTkCheckBox(r3, text="连子回复一起抓", font=ctk.CTkFont(size=13),
                                            text_color=INK, fg_color=GREEN, hover_color=GREEN_H)
        self.with_replies.pack(side="left", padx=(0, 24))
        ctk.CTkLabel(r3, text="评论时间范围", text_color=SUB, font=ctk.CTkFont(size=12)).pack(side="left")
        self.preset = ctk.CTkOptionMenu(r3, width=118, height=30, corner_radius=6,
                                        values=["全部", "最近1天", "最近3天", "最近7天", "最近30天", "自定义"],
                                        command=self._on_preset)
        self.preset.set("全部"); self.preset.pack(side="left", padx=(8, 14))
        ctk.CTkLabel(r3, text="开始", text_color=SUB, font=ctk.CTkFont(size=12)).pack(side="left")
        self.start = ctk.CTkEntry(r3, width=104, height=30, corner_radius=6, border_color=LINE,
                                  placeholder_text="YYYY-MM-DD")
        self.start.pack(side="left", padx=(4, 12))
        ctk.CTkLabel(r3, text="结束", text_color=SUB, font=ctk.CTkFont(size=12)).pack(side="left")
        self.end = ctk.CTkEntry(r3, width=104, height=30, corner_radius=6, border_color=LINE,
                                placeholder_text="YYYY-MM-DD")
        self.end.pack(side="left", padx=(4, 12))
        self.dir_lbl = ctk.CTkLabel(r3, text="保存到：" + DEFAULT_OUTDIR, text_color=SUB,
                                    font=ctk.CTkFont(size=11))
        self.dir_lbl.pack(side="left")
        ctk.CTkButton(r3, text="选位置", width=72, height=28, corner_radius=6, fg_color=GRAY_BTN,
                      hover_color="#e2e8ee", text_color=INK, border_width=1, border_color="#cfd6dd",
                      font=ctk.CTkFont(size=12), command=self.on_pick_dir).pack(side="right")
        self._on_preset()

    def _on_preset(self, *_):
        custom = (self.preset.get() == "自定义")
        st = "normal" if custom else "disabled"
        self.start.configure(state=st); self.end.configure(state=st)

    # ---------- 结果行（实时追加） ----------
    def _mk_row(self, parent, values, header=False, url=None):
        for ci, (v, wpx) in enumerate(zip(values, COLS)):
            color = "#556" if header else INK
            lbl = ctk.CTkLabel(parent, text=str(v), width=wpx, anchor="w",
                               font=ctk.CTkFont(size=12, weight="bold" if header else "normal"),
                               text_color=(LINK if (not header and ci >= 5) else color))
            lbl.grid(row=0, column=ci, sticky="w", padx=6, pady=8)
            if not header and ci >= 5 and url:
                lbl.bind("<Button-1>", lambda e, u=url: open_path(u))
                lbl.configure(cursor="hand2")

    def _append_row(self, r):
        if self._hint:
            try:
                self._hint.destroy()
            except Exception:
                pass
            self._hint = None
        self._rowcount += 1
        rf = ctk.CTkFrame(self.tbody, fg_color=("#fafbfc" if self._rowcount % 2 == 0 else "#ffffff"),
                          corner_radius=0, height=36)
        rf.pack(fill="x"); rf.pack_propagate(False)
        vals = [str(self._rowcount), _trunc(r["视频ID"], 12), _trunc(r["昵称"], 12),
                _trunc(r["评论内容"], 40), _trunc(r.get("评论时间", ""), 16), "打开", "私信"]
        self._mk_row(rf, vals, url=r["主页链接"])
        try:
            self.tbody._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _clear_table(self):
        for w in self.tbody.winfo_children():
            w.destroy()
        self._rowcount = 0
        self._hint = None
        self._empty_hint()

    # ---------- 抖音账号页 ----------
    def _build_account(self, parent):
        f = ctk.CTkFrame(parent, fg_color=BG)
        self.frm_account = f
        ctk.CTkLabel(f, text="👤  抖音账号", text_color="#222", anchor="w",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", pady=(0, 12))
        card = ctk.CTkFrame(f, fg_color=CARD, corner_radius=10, border_width=1, border_color=LINE)
        card.pack(fill="x")
        ctk.CTkLabel(card, text="登录状态", text_color=SUB, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16, pady=(14, 2))
        self.acct_status = ctk.CTkLabel(card, text="● 检测中…", text_color="#e0663b", anchor="w",
                                        font=ctk.CTkFont(size=18, weight="bold"))
        self.acct_status.pack(anchor="w", padx=16, pady=(0, 10))
        row = ctk.CTkFrame(card, fg_color="transparent"); row.pack(anchor="w", padx=16, pady=(0, 16))
        self.b_acct_login = ctk.CTkButton(row, text="登录 / 重新绑定", width=150, height=40,
                                          corner_radius=8, fg_color=GREEN, hover_color=GREEN_H,
                                          text_color="#fff", font=ctk.CTkFont(size=13, weight="bold"),
                                          command=self.on_login)
        self.b_acct_login.pack(side="left", padx=(0, 12))
        self.b_acct_check = ctk.CTkButton(row, text="检测状态", width=110, height=40,
                                          corner_radius=8, fg_color=GRAY_BTN, hover_color="#e2e8ee",
                                          text_color=INK, border_width=1, border_color="#cfd6dd",
                                          font=ctk.CTkFont(size=13), command=self.on_check_login)
        self.b_acct_check.pack(side="left")
        ctk.CTkLabel(f, text="点「登录 / 重新绑定」会弹出浏览器，扫码登录后自动检测并关闭浏览器；\n"
                             "若打开时已登录，会立即关闭。抓取前会自动校验登录状态。",
                     text_color=SUB, anchor="w", justify="left",
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=4, pady=(14, 0))

    def _set_acct_status(self, logged_in):
        self.logged_in = logged_in
        try:
            self.acct_status.configure(text="● 已登录" if logged_in else "● 未登录",
                                       text_color="#0a7d3c" if logged_in else "#c0392b")
        except Exception:
            pass

    def on_check_login(self):
        self.acct_status.configure(text="● 检测中…", text_color="#e0663b")
        threading.Thread(target=self._check_login_startup, daemon=True).start()

    def _check_login_startup(self):
        try:
            ok = eng.check_login()
        except Exception:
            ok = False
        self.q.put(("login_state", ok))

    def on_login(self):
        self._show("account")
        if self.busy:
            return
        self.b_acct_login.configure(state="disabled"); self.b_acct_check.configure(state="disabled")
        self.acct_status.configure(text="● 等待登录…", text_color="#0a5bc4")
        threading.Thread(target=self._login_worker, daemon=True).start()

    def _login_worker(self):
        ok = False
        try:
            ok = eng.login_interactive(log=lambda m: None)
        except Exception as e:
            self.q.put(("status", (f"登录出错：{e}", "#c0392b")))
        finally:
            self.q.put(("login_state", ok))
            self.q.put(("login_done", None))

    # ---------- 使用说明页 ----------
    def _build_help(self, parent):
        f = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10, border_width=1, border_color=LINE)
        self.frm_help = f
        txt = (
            "使用说明\n\n"
            "1) 首次使用：左侧「抖音账号」点「登录 / 重新绑定」，扫码后自动检测并关闭浏览器（以后不用再扫）。\n\n"
            "2) 在「评论监控」配置里填视频链接（一行一个，可整段粘分享文案）、关键词（一行一个），"
            "可选时间范围、连子回复。\n\n"
            "3) 点「开始抓取」：命中的评论会【边抓边实时】出现在下方列表，不用等全部跑完；中途可「停止」。\n\n"
            "4) 点「打开表格」查看导出的文件（同时生成 .xlsx 可点超链接 和 .csv），默认存在 文档\\抖音评论名单。\n\n"
            "5) 下方列表和表格里「打开 / 私信」可点击，进入对方主页后再点“私信”联系。\n\n"
            "提示：本工具只做导名单 / 看反馈，不支持批量私信；频繁群发易封号，请文明使用。"
        )
        box = ctk.CTkTextbox(f, wrap="word", fg_color=CARD, text_color=INK, font=ctk.CTkFont(size=14))
        box.pack(fill="both", expand=True, padx=18, pady=16)
        box.insert("1.0", txt)
        box.configure(state="disabled")

    # ---------- 线程安全 UI ----------
    def set_status(self, m, color="#e0663b"):
        self.q.put(("status", (m, color)))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.status.configure(text=val[0], text_color=val[1])
                elif kind == "progress":
                    self.status.configure(text=f"● 抓取中… 已收集 {val} 条评论 / 已命中 {self._rowcount}",
                                          text_color="#0a7d3c")
                elif kind == "hit":
                    self._append_row(val)
                elif kind == "done":
                    self._finish(val)
                elif kind == "login_state":
                    self._set_acct_status(val)
                elif kind == "login_done":
                    self.b_acct_login.configure(state="normal")
                    self.b_acct_check.configure(state="normal")
                elif kind == "need_login":
                    self._set_busy(False)
                    self._set_acct_status(False)
                    self._show("account")
                    _msg("warn", "需要登录",
                         "还没有登录抖音账号（或登录已过期）。\n\n请先在左侧「抖音账号」页面登录，再回来抓取。")
        except queue.Empty:
            pass
        self.after(120, self.poll)

    def _set_busy(self, b):
        self.busy = b
        st = "disabled" if b else "normal"
        self.b_run.configure(state=st)
        self.b_stop.configure(state="normal" if b else "disabled")

    # ---------- 动作 ----------
    def on_cfg(self):
        if self.cfg.winfo_ismapped():
            self.cfg.grid_remove()
        else:
            self.cfg.grid()

    def on_pick_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.outdir.get() or DEFAULT_OUTDIR)
        if d:
            self.outdir.set(d)
            self.dir_lbl.configure(text="保存到：" + d)

    def on_clear(self):
        self._clear_table()
        self.last_file = None
        self.b_open.configure(state="disabled")
        self.set_status("● 已清空", "#e0663b")

    def on_stop(self):
        self.stop_event.set()
        self.set_status("● 正在停止…", "#e0663b")

    def _compute_range(self):
        now = datetime.now()
        p = self.preset.get()
        if p == "最近1天":
            return now - timedelta(days=1), None
        if p == "最近3天":
            return now - timedelta(days=3), None
        if p == "最近7天":
            return now - timedelta(days=7), None
        if p == "最近30天":
            return now - timedelta(days=30), None
        if p == "自定义":
            since = until = None
            s = self.start.get().strip(); e = self.end.get().strip()
            if s:
                since = datetime.strptime(s, "%Y-%m-%d")
            if e:
                until = datetime.strptime(e, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            return since, until
        return None, None

    def on_run(self):
        if self.busy:
            return
        raw_links = self.links.get("1.0", "end").strip()
        keywords = [k.strip() for k in self.kws.get("1.0", "end").splitlines() if k.strip()]
        links = []
        for line in raw_links.splitlines():
            line = line.strip()
            if not line:
                continue
            found = eng.extract_links(line)
            links.extend(found if found else [line])
        links = [l for l in links if "douyin.com" in l]
        if not links:
            _msg("warn", "提示", "请至少填一个有效的抖音链接")
            return
        if not keywords:
            _msg("warn", "提示", "请至少填一个关键词")
            return
        try:
            maxc = int(self.maxc.get().strip() or 3000)
        except ValueError:
            maxc = 3000
        try:
            since, until = self._compute_range()
        except ValueError as ve:
            _msg("warn", "时间范围", str(ve))
            return

        os.makedirs(self.outdir.get() or DEFAULT_OUTDIR, exist_ok=True)
        fname = f"抖音评论名单_{datetime.now():%Y%m%d_%H%M%S}.csv"
        out = os.path.join(self.outdir.get() or DEFAULT_OUTDIR, fname)

        self.stop_event = threading.Event()
        self._set_busy(True)
        self.b_open.configure(state="disabled")
        self.last_file = None
        self._clear_table()
        rng = f" / 时间 {self.preset.get()}" if (since or until) else ""
        self.set_status(f"● 抓取中：{len(links)} 个链接{rng}", "#0a7d3c")
        threading.Thread(target=self._run_worker,
                         args=(links, keywords, out, self.with_replies.get(), maxc, since, until),
                         daemon=True).start()

    def _run_worker(self, links, keywords, out, with_replies, maxc, since, until):
        try:
            rows, csv_path, xlsx_path = eng.run_batch(
                links, keywords, out=out, with_replies=with_replies, max_comments=maxc,
                log=lambda m: None, on_progress=lambda n: self.q.put(("progress", n)),
                since=since, until=until, stop_event=self.stop_event,
                on_hit=lambda r: self.q.put(("hit", r)),
                on_login_required=lambda: self.q.put(("need_login", None)))
            self.q.put(("done", (rows, csv_path, xlsx_path, len(rows))))
        except Exception as e:
            self.q.put(("status", (f"运行出错：{e}", "#c0392b")))
            self.q.put(("done", ([], None, None, -1)))

    def _finish(self, val):
        rows, csv_path, xlsx_path, count = val
        self._set_busy(False)
        if count < 0:
            self.set_status("● 出错，见提示", "#c0392b")
            return
        if count == 0:
            self.set_status("● 完成：没有命中，换关键词或确认链接能打开评论", "#e0663b")
            return
        self.last_file = xlsx_path or csv_path
        self.b_open.configure(state="normal")
        self.set_status(f"● 完成：{count} 条命中，已导出，点「打开表格」查看", "#0a7d3c")

    def on_open(self):
        if self.last_file and os.path.exists(self.last_file):
            open_path(self.last_file)
        elif os.path.isdir(self.outdir.get()):
            open_path(self.outdir.get())


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
