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
from datetime import datetime, timedelta

import customtkinter as ctk

import douyin_miner as eng


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

        self.q = queue.Queue()
        self.busy = False
        self.logged_in = False
        self.last_file = None
        self.stop_event = threading.Event()
        self.outdir = ctk.StringVar(value=DEFAULT_OUTDIR)
        self._rowcount = 0

        self._build()
        self.after(100, self.poll)
        threading.Thread(target=self._check_login_startup, daemon=True).start()

    # ================= 布局 =================
    def _build(self):
        bar = ctk.CTkFrame(self, fg_color="#e6e9ee", corner_radius=0, height=30)
        bar.pack(fill="x"); bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="🐙  " + APP_TITLE, text_color="#333",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=12)

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
