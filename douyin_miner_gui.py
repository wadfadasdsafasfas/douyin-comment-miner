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
import tkinter as tk

import douyin_miner as eng
from license import License


APP_TITLE = "听潮 · 抖音评论名单挖掘"
DEFAULT_OUTDIR = os.path.join(os.path.expanduser("~"), "Documents", "抖音评论名单")


# ============================================================
#  小工具：chips 输入框 + 与旧 Textbox 兼容的 proxy
# ============================================================
class _ProxyCheck:
    """兼容旧代码 self.with_replies.get() —— 返回布尔值。"""
    def __init__(self, value: bool = True):
        self.value = value
    def get(self) -> bool:
        return self.value


class _ProxyEmpty:
    """占位：旧 self.start / self.end 已被弹窗 chips 版替代。"""
    def get(self, *_a, **_kw) -> str:
        return ""
    def configure(self, *_a, **_kw): pass


class _ProxyDirLabel:
    """兼容旧 dir_lbl.configure(text=...) —— 同步到弹窗里的 _cfg_dir_lbl。"""
    def __init__(self, app):
        self._app = app
    def configure(self, text=None, **_kw):
        if text:
            try:
                self._app._cfg_dir_lbl.configure(text=text)
            except Exception:
                pass


class _ChipsTextProxy:
    """让 on_run 仍可 self.kws.get("1.0","end") —— 返回换行分隔的关键词。"""
    def __init__(self, chips):
        self._chips = chips
    def get(self, *_args, **_kw) -> str:
        return "\n".join(self._chips.get())


class ChipsEntry(ctk.CTkFrame):
    """HTML 风格的 chips 输入：每个关键词是一个红底小标签 + × 按钮，
    末尾一个 input，回车触发添加，点 × 删除。

    用法：
        c = ChipsEntry(parent)
        c.set_keywords(["多少钱", "怎么联系"])
        c.get() -> ["多少钱", "怎么联系"]
    """
    def __init__(self, parent, **kw):
        super().__init__(parent, fg_color=CARD, corner_radius=10,
                         border_width=1, border_color=LINE2, **kw)
        # 用 grid 让 chips 自动换行
        self.grid_columnconfigure(0, weight=1)
        self._input = ctk.CTkEntry(self, height=32, corner_radius=6,
                                   border_width=0, fg_color="transparent",
                                   text_color=INK,
                                   placeholder_text="输入关键词后回车",
                                   placeholder_text_color=INK3,
                                   font=ctk.CTkFont(size=14))
        self._input.grid(row=0, column=0, sticky="ew", padx=(8, 6), pady=6)
        self._input.bind("<Return>", self._on_enter)
        self._chip_widgets = []  # list of CTkFrame
        # 占位 row=0 col=0 给 input；chips 从 row=1 开始往上叠
        self._next_row = 1
        self._next_col = 0
        self._max_cols = 1
        self.bind("<Configure>", self._relayout)

    def _on_enter(self, _e=None):
        v = self._input.get().strip()
        if not v:
            return "break"
        self.add(v)
        self._input.delete(0, "end")
        return "break"

    def add(self, text: str):
        # 已存在就不重加
        for w, t in self._chip_widgets:
            if t == text:
                return
        chip = ctk.CTkFrame(self, fg_color=BRAND_SOFT, corner_radius=8,
                            border_width=0)
        ctk.CTkLabel(chip, text=text, text_color=BRAND_INK,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left", padx=(10, 4), pady=4)
        x_btn = ctk.CTkLabel(chip, text="×", text_color=BRAND_INK,
                             font=ctk.CTkFont(size=14, weight="bold"),
                             cursor="hand2")
        x_btn.pack(side="left", padx=(0, 8), pady=4)
        x_btn.bind("<Button-1>", lambda e, w=chip: self._remove_widget(w))
        self._chip_widgets.append((chip, text))
        self._relayout()

    def _remove_widget(self, widget):
        for i, (w, t) in enumerate(self._chip_widgets):
            if w is widget:
                w.destroy()
                self._chip_widgets.pop(i)
                break
        self._relayout()

    def _relayout(self, *_):
        # 重新布局 chips 到第一行，input 单独占最后一行
        for w, _ in self._chip_widgets:
            w.grid_forget()
        self._input.grid_forget()
        width = max(200, self.winfo_width())
        # 粗略估算每 chip 宽度（中文 14px * 字符数 + padding）
        x = 8
        row = 0
        col = 0
        # 当前为简单实现：每个 chip 占满一行（不够优雅但稳）
        for w, t in self._chip_widgets:
            w.grid(row=row, column=0, sticky="w", padx=(8, 0), pady=(6, 0))
            row += 1
        # input 放最后
        self._input.grid(row=row, column=0, sticky="ew", padx=(8, 6), pady=6)
        # 行数 = chips + input
        for r in range(row + 1):
            self.grid_rowconfigure(r, weight=0)

    def set_keywords(self, kws: list[str]):
        for w, _ in self._chip_widgets:
            w.destroy()
        self._chip_widgets.clear()
        for k in kws:
            self.add(k)

    def get(self) -> list[str]:
        return [t for _, t in self._chip_widgets]

# ===== 设计 Token（来自 听潮-ui-多彩数据版.html） =====
# 表面 / 背景
BG = "#F5F5FA"          # --bg
CARD = "#FFFFFF"        # --surface
SURFACE2 = "#F8F8FC"    # --surface-2（侧栏 / 表头 / chip 容器）
# 文字
INK = "#171A26"         # --ink
INK2 = "#5A6072"        # --ink-2
INK3 = "#9AA0B4"        # --ink-3（label / placeholder / 弱化）
# 描边
LINE = "#EBEBF2"        # --line
LINE2 = "#DEDEE9"       # --line-2
# 主色（红橙渐变 / 单值用 brand）
BRAND = "#FF4D4D"       # --brand
BRAND2 = "#FF8A5B"      # --brand-2
BRAND_INK = "#DE3232"   # --brand-ink（红字）
BRAND_SOFT = "#FFEDEC"  # --brand-soft
# 紫色
VIOLET = "#7C5CFF"
VIOLET_SOFT = "#F0ECFF"
# 青绿（在线 / 抓取中）
TEAL = "#0FB5A5"
TEAL_SOFT = "#E2F7F4"
# 琥珀（即将到期 / 警告）
AMBER = "#FFB020"
AMBER_SOFT = "#FFF4DC"

# 业务别名（兼容旧代码）
GREEN = TEAL            # "已登录 / 在线" 用青绿
GREEN_H = "#0A8A7D"
BLUE = "#0a5bc4"
LINK = BRAND_INK        # 链接用主色红
GRAY_BTN = "#F0F0F6"    # 中性灰按钮（HTML .btn-ghost）
SIDE = SURFACE2         # 侧栏
SIDE_ON = BRAND_SOFT    # 选中态用 brand-soft
SIDE_ON_FG = BRAND_INK  # 选中态文字

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

        # === L2 桌面级：托盘 + 通知 + 关闭拦截 ===
        from desktop import prefs
        self._prefs = prefs.load()
        self._tray = None        # SystemTray 实例（启动后赋值）
        self._notifier = None    # Notifier 实例
        # 拦截窗口关闭按钮：× → 最小化到托盘（除非 prefs 关了）
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # === L2 END ===

        # 启动：先校验已存的 token，再决定显示登录页还是主界面
        self._boot()
        self.after(100, self.poll)

    # ================= L2 桌面级：托盘 + 通知 + 关闭拦截 =================
    def _init_desktop(self):
        """在登录成功 / 主界面建立后初始化托盘和通知器。"""
        if self._tray is not None:
            return
        try:
            from desktop.system_tray import SystemTray
            from desktop.notifier import Notifier
            self._notifier = Notifier(self._prefs)
            self._tray = SystemTray(
                app=self,
                on_show=self._tray_show_window,
                on_hide=self._tray_hide_window,
                on_quit=self._real_quit,
                on_start_crawl=self._tray_start_crawl,
                on_stop_crawl=self._tray_stop_crawl,
            )
            self._tray.start()
            # 通知器 hit 节流：每次开始抓取时重置
            self._tray.set_tooltip(f"{APP_TITLE} · 已就绪")
        except Exception as e:
            print(f"[L2] 托盘初始化失败（不影响主程序）: {e}")

    def _on_close(self):
        """窗口 × 按钮：默认最小化到托盘（除非用户关闭了开关）。"""
        if self._tray is None or not self._prefs.get("tray_minimize", True):
            # 没托盘 OR 用户关了开关 → 真正退出
            self._real_quit()
            return
        # 隐藏到托盘
        self._tray_hide_window()
        # 首次提示
        if not self._prefs.get("tray_first_hide_tip_shown", False):
            self._prefs["tray_first_hide_tip_shown"] = True
            from desktop import prefs as _prefs_mod
            _prefs_mod.save(self._prefs)
            self._notifier and self._notifier.notify(
                "hit",  # 用 hit 通道，只是个提示用现有开关
                title="已最小化到托盘",
                body="右键托盘图标可恢复窗口或退出程序",
            )

    def _tray_show_window(self):
        """托盘菜单『显示主窗口』：取消最小化 + 置顶 + 聚焦。"""
        try:
            self.after(0, self.deiconify)
            self.after(0, lambda: (self.lift(), self.focus_force()))
        except Exception:
            pass

    def _tray_hide_window(self):
        """隐藏到托盘（不退出进程）。"""
        try:
            self.withdraw()
        except Exception:
            pass

    def _tray_start_crawl(self):
        """托盘菜单『开始抓取』：直接调 on_run。"""
        if not self.logged_in:
            return
        try:
            self.on_run()
        except Exception as e:
            print(f"[L2] tray start_crawl: {e}")

    def _tray_stop_crawl(self):
        """托盘菜单『停止抓取』。"""
        try:
            self.on_stop()
        except Exception as e:
            print(f"[L2] tray stop_crawl: {e}")

    def _real_quit(self):
        """真正退出：停托盘、停 worker、销毁窗口。"""
        try:
            if self._tray:
                self._tray.stop()
        except Exception:
            pass
        try:
            self.stop_event.set()
        except Exception:
            pass
        try:
            self.quit()
            self.destroy()
        except Exception:
            pass

    # ================= L2 END =================

    # ================= 启动 / 授权 =================
    def _boot(self):
        # 启动后先在后台检测升级，避免阻塞 UI
        self.after(300, self._check_update_startup)

        if self.license.token:
            ok, msg, state = self.license.verify()
            if ok:
                self._build()
                self.after(100, self._heartbeat_loop)
                return
            extra = ""
            if state == "kicked":
                extra = "\n（另一台设备登录了你的账号）"
            self._show_login(f"{msg}{extra}" or "登录已失效，请重新登录")
        else:
            self._show_login()

    def _show_login(self, error_msg: str = ""):
        """登录界面 —— HTML ② 分屏式（左侧渐变 hero + 右侧表单）。"""
        # 清空窗口
        for w in self.winfo_children():
            w.destroy()

        self.geometry("920x620")
        self.minsize(820, 580)
        self.configure(fg_color=CARD)  # 主窗口白底，让分屏阴影干净

        stage = ctk.CTkFrame(self, fg_color=CARD, corner_radius=20,
                             border_width=1, border_color=LINE)
        stage.pack(fill="both", expand=True, padx=20, pady=20)

        # 让左右各占固定比例，左 47%
        stage.grid_columnconfigure(0, weight=47, uniform="login")
        stage.grid_columnconfigure(1, weight=53, uniform="login")
        stage.grid_rowconfigure(0, weight=1)

        # =================== 左侧 hero ===================
        left = ctk.CTkFrame(stage, fg_color=BRAND2, corner_radius=20)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 0))
        left.grid_propagate(False)

        # 渐变背景（横向：橙 → 红 → 紫）
        grad_canvas = tk.Canvas(left, highlightthickness=0, bd=0)
        grad_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_login_gradient(grad_canvas)

        # 装饰圆（HTML .ring r1/r2/r3）
        deco = tk.Canvas(left, highlightthickness=0, bd=0)
        deco.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_login_decor(deco)

        # 内容层（透明 frame）
        content = ctk.CTkFrame(left, fg_color="transparent")
        content.place(relx=0, rely=0, relwidth=1, relheight=1)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(99, weight=1)  # 中间撑开，让 lg-feats 落到底部

        # logo 标记（用 emoji + 圆角白底模拟 HTML .mark）
        logo_box = ctk.CTkFrame(content, fg_color="#ffffff",
                                width=64, height=64, corner_radius=14)
        logo_box.grid(row=0, column=0, sticky="w", padx=42, pady=(46, 0))
        logo_box.grid_propagate(False)
        ctk.CTkLabel(logo_box, text="🐙", text_color="#fff",
                     font=ctk.CTkFont(size=34)).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(content, text="听潮", text_color="#fff",
                     font=ctk.CTkFont(size=38, weight="bold")).grid(
            row=1, column=0, sticky="w", padx=42, pady=(24, 0))
        ctk.CTkLabel(content, text="TIDE SIGNAL", text_color="#fff",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=2, column=0, sticky="w", padx=42, pady=(6, 0))

        ctk.CTkLabel(content, text="潮声之下，皆是商机",
                     text_color="#fff",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=3, column=0, sticky="w", padx=42, pady=(36, 0))

        # 卖点 3 行（HTML .lg-feat）
        feats = ctk.CTkFrame(content, fg_color="transparent")
        feats.grid(row=99, column=0, sticky="sew", padx=42, pady=(0, 36))
        feats.grid_columnconfigure(0, weight=1)
        for text in (
            "抖音 / 小红书评论实时抓取 · 关键词秒级命中",
            "自动去重成名单 · 主页 / 私信一键触达",
            "采集趋势与命中构成 · 全程可视化",
        ):
            self._login_feat(feats, text)

        # =================== 右侧表单 ===================
        right = ctk.CTkFrame(stage, fg_color=CARD, corner_radius=20)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)

        # 表单垂直居中
        form = ctk.CTkFrame(right, fg_color="transparent", width=360)
        form.place(relx=0.5, rely=0.5, anchor="center")
        form.grid_columnconfigure(0, weight=1)

        # 服务器状态 pill
        srv = ctk.CTkFrame(form, fg_color=SURFACE2, corner_radius=999,
                           border_width=1, border_color=LINE, height=28)
        srv.grid(row=0, column=0, sticky="w")
        srv.grid_propagate(False)
        srv_dot = ctk.CTkFrame(srv, fg_color=TEAL, width=7, height=7, corner_radius=4)
        srv_dot.pack(side="left", padx=(12, 6), pady=10)
        ctk.CTkLabel(srv, text=f"授权服务器 · {self.license.server_url}",
                     text_color=INK3, font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 12), pady=6)

        ctk.CTkLabel(form, text="欢迎回来", text_color=INK,
                     font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=1, column=0, sticky="w", pady=(24, 0))
        ctk.CTkLabel(form, text="登录后进入评论监控工作台",
                     text_color=INK3, font=ctk.CTkFont(size=13)).grid(
            row=2, column=0, sticky="w", pady=(6, 22))

        # 账号
        ctk.CTkLabel(form, text="账号", text_color=INK2,
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=3, column=0, sticky="w", pady=(0, 8))
        self.e_user = ctk.CTkEntry(form, height=50, corner_radius=12,
                                   border_width=1, border_color=LINE2,
                                   fg_color=CARD, text_color=INK,
                                   placeholder_text="请输入账号",
                                   placeholder_text_color=INK3,
                                   font=ctk.CTkFont(size=14))
        self.e_user.grid(row=4, column=0, sticky="ew")

        # 密码
        ctk.CTkLabel(form, text="密码", text_color=INK2,
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=5, column=0, sticky="w", pady=(16, 8))
        self.e_pw = ctk.CTkEntry(form, height=50, corner_radius=12,
                                 border_width=1, border_color=LINE2,
                                 fg_color=CARD, text_color=INK,
                                 placeholder_text="请输入密码", show="*",
                                 placeholder_text_color=INK3,
                                 font=ctk.CTkFont(size=14))
        self.e_pw.grid(row=6, column=0, sticky="ew")
        self.e_pw.bind("<Return>", lambda e: self._do_login())

        # 错误 / 提示
        self.l_msg = ctk.CTkLabel(form, text=error_msg, text_color="#dc2626",
                                  font=ctk.CTkFont(size=12), wraplength=320)
        self.l_msg.grid(row=7, column=0, sticky="w", pady=(10, 0))

        # 登录按钮（主色）
        self.b_login = ctk.CTkButton(form, text="登 录", height=52, corner_radius=12,
                                     fg_color=BRAND, hover_color=BRAND_INK,
                                     text_color="#fff",
                                     font=ctk.CTkFont(size=16, weight="bold"),
                                     command=self._do_login)
        self.b_login.grid(row=8, column=0, sticky="ew", pady=(16, 0))

        # 试用链接
        foot = ctk.CTkFrame(form, fg_color="transparent")
        foot.grid(row=9, column=0, sticky="ew", pady=(20, 0))
        ctk.CTkLabel(foot, text="没有账号？", text_color=INK3,
                     font=ctk.CTkFont(size=13)).pack(side="left")
        self.b_trial = ctk.CTkButton(
            foot, text="申请 3 小时试用",
            fg_color="transparent", text_color=BRAND_INK,
            hover_color=BRAND_SOFT, font=ctk.CTkFont(size=13, weight="bold", underline=True),
            height=24, width=120, corner_radius=6,
            command=self._on_trial_click,
        )
        self.b_trial.pack(side="left", padx=(4, 0))

        # 版本号底部
        ctk.CTkLabel(right, text="V1.0 · 层峰科技",
                     text_color=INK3, font=ctk.CTkFont(size=10)).place(
            relx=0.5, rely=0.96, anchor="center")

        self.e_user.focus_set()

    def _draw_login_gradient(self, canvas: tk.Canvas):
        """在 canvas 上画横向三色渐变（左 47% 那块）。"""
        canvas.update_idletasks()
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 2 or h < 2:
            # 还没布局好，等下一次
            canvas.after(50, lambda: self._draw_login_gradient(canvas))
            return
        # 横线插值 #FF8A5B → #FF3D6E → #7C5CFF
        c1 = (0xFF, 0x8A, 0x5B)
        c2 = (0xFF, 0x3D, 0x6E)
        c3 = (0x7C, 0x5C, 0xFF)
        # 每像素画一根竖线
        for x in range(w):
            t = x / max(1, w - 1)
            if t < 0.5:
                k = t / 0.5
                r = int(c1[0] + (c2[0] - c1[0]) * k)
                g = int(c1[1] + (c2[1] - c1[1]) * k)
                b = int(c1[2] + (c2[2] - c1[2]) * k)
            else:
                k = (t - 0.5) / 0.5
                r = int(c2[0] + (c3[0] - c2[0]) * k)
                g = int(c2[1] + (c3[1] - c2[1]) * k)
                b = int(c2[2] + (c3[2] - c2[2]) * k)
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_line(x, 0, x, h, fill=color, width=1)

    def _draw_login_decor(self, canvas: tk.Canvas):
        """装饰圆（HTML .ring r1/r2/r3）。tk 不支持 8 位 hex，用粗细模拟透明度。"""
        canvas.update_idletasks()
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 2 or h < 2:
            canvas.after(50, lambda: self._draw_login_decor(canvas))
            return
        # r1：右下方大圆（细）
        canvas.create_oval(w - 160, h - 140, w + 260, h + 280,
                           outline="#ffffff", width=2)
        # r2：右下中等（粗）
        canvas.create_oval(w - 40, h - 40, w + 260, h + 260,
                           outline="#ffffff", width=20)
        # r3：左上小圆（更粗）
        canvas.create_oval(-70, 120, 110, 300,
                           outline="#ffffff", width=30)

    def _login_feat(self, parent, text: str):
        """HTML .lg-feat 一行：图标方块 + 文字。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=6)
        ic = ctk.CTkFrame(row, fg_color="#ffffff", width=30, height=30, corner_radius=9)
        ic.pack(side="left")
        ic.pack_propagate(False)
        ctk.CTkLabel(ic, text="✓", text_color="#fff",
                     font=ctk.CTkFont(size=15, weight="bold")).place(
            relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(row, text=text, text_color="#ffffff",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(10, 0))

    def _on_trial_click(self):
        self.b_trial.configure(state="disabled", text="申请中…")

        def _worker():
            info, err = self.license.request_trial()
            self.after(0, lambda: self._on_trial_result(info, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_trial_result(self, info, err: str):
        self.b_trial.configure(state="normal", text="申请 3 小时试用")
        if not info:
            _msg("warn", "申请失败", err)
            return
        u = info.get("username", "")
        p = info.get("password", "")
        exp = (info.get("expires_at") or "")[:16].replace("T", " ")
        self.e_user.delete(0, "end"); self.e_user.insert(0, u)
        self.e_pw.delete(0, "end"); self.e_pw.insert(0, p)
        self.l_msg.configure(
            text=f"✅ 已生成试用账号（到期 {exp}），点登录即可",
            text_color="#0a7d3c",
        )
        self.e_pw.focus_set()

    def _do_login(self, kick_existing: bool = False):
        u = self.e_user.get().strip()
        p = self.e_pw.get()
        if not u or not p:
            self.l_msg.configure(text="请输入账号和密码")
            return
        self.b_login.configure(state="disabled", text="登录中…")
        self.update()

        # 登录放后台线程，避免阻塞 UI
        def _worker():
            ok, msg, existing = self.license.login(u, p, kick_existing=kick_existing)
            if msg == "DEVICE_CONFLICT":
                self.after(0, lambda: self._show_conflict_dialog(existing or "未知设备", u, p))
                return
            self.after(0, lambda: self._on_login_result(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_conflict_dialog(self, existing_device: str, username: str, password: str):
        """设备冲突弹窗：挤下线重试 / 取消。"""
        # 先恢复登录按钮
        self.b_login.configure(state="normal", text="登 录")
        try:
            self.l_msg.configure(text="")
        except Exception:
            pass
        dlg = ctk.CTkToplevel(self)
        dlg.title("账号已在另一台设备登录")
        dlg.geometry("460x280")
        dlg.configure(fg_color=BG)
        dlg.transient(self)
        card = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=20,
                            border_width=1, border_color=LINE)
        card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text="⚠️ 账号已在另一台设备登录",
                     text_color=INK, font=ctk.CTkFont(size=16, weight="bold")).pack(
            pady=(28, 8))
        ctk.CTkLabel(card, text=f"当前登录设备：\n{existing_device}",
                     text_color=INK2, font=ctk.CTkFont(size=13),
                     justify="center").pack(pady=(0, 8))
        ctk.CTkLabel(card, text="如要继续，请选择「挤下线」；\n原设备若在抓取会被暂停。",
                     text_color=INK3, font=ctk.CTkFont(size=12),
                     justify="center").pack(pady=(0, 18))
        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.pack(fill="x", padx=28, pady=(0, 24))
        bar.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            bar, text="挤下线重试", height=44, corner_radius=10,
            fg_color=BRAND, hover_color=BRAND_INK,
            text_color="#fff", font=ctk.CTkFont(size=14, weight="bold"),
            command=lambda: (dlg.destroy(), self._do_login(kick_existing=True)),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            bar, text="取消", height=44, corner_radius=10,
            fg_color=CARD, hover_color=SURFACE2,
            text_color=INK2, border_width=1, border_color=LINE2,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=dlg.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        # 居中 + grab
        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()

    def _on_login_result(self, ok: bool, msg: str):
        if not ok:
            self.l_msg.configure(text=msg)
            self.b_login.configure(state="normal", text="登 录")
            self.e_pw.delete(0, "end")
            return
        # 登录成功：重建主界面
        self._build()
        self.after(100, self._heartbeat_loop)
        # L2：登录成功后才挂托盘（登录页不需要托盘）
        self.after(50, self._init_desktop)

    def _heartbeat_loop(self):
        """每 30 分钟调一次 /verify，账号被改期/停用/被踢时立刻锁界面。"""
        def _beat():
            ok, msg, state = self.license.verify()
            if not ok:
                self.after(0, lambda: self._handle_session_lost(msg, state))
        threading.Thread(target=_beat, daemon=True).start()
        self.after(30 * 60 * 1000, self._heartbeat_loop)

    def _handle_session_lost(self, msg: str, state: str):
        """心跳失败统一处理：被踢 / 过期 / 停用。
        busy=True 时也会强制回登录页（抓取由 stop_event 暂停，结果已落盘）。"""
        try:
            self.stop_event.set()
        except Exception:
            pass
        extra = ""
        if state == "kicked":
            extra = "\n（另一台设备登录了你的账号）"
        if self.busy:
            extra += "\n当前抓取已暂停，结果已保存到文件。"
        _msg("warn", "账号已失效", f"{msg}{extra}\n请重新登录。")
        # L2：系统通知（被踢 / 失效双通道）
        if self._notifier:
            self._notifier.notify("kicked", title="账号已失效",
                                  body=f"{msg}{extra}")
        self._show_login(f"{msg}{extra}")

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
            # L2：发一条系统通知（窗口不在前台时尤其有用）
            if self._notifier:
                latest = info.get("version", "新版本")
                self._notifier.notify(
                    "update", title=f"听潮 v{latest} 已发布",
                    body="点击主窗口中的升级提示查看更新内容"
                )
            # 启动时立刻弹窗（升级时机）
            self._show_update_dialog()

    def _show_update_dialog(self):
        """自动升级弹窗（HTML .modal 视觉重写）。"""
        if self._update_dialog is not None and self._update_dialog.winfo_exists():
            return
        info = self.update_info or {}
        latest = info.get("version", "?")
        force = info.get("force_update", False)
        notes = info.get("release_notes", "")

        dlg = ctk.CTkToplevel(self)
        dlg.title("发现新版本")
        dlg.geometry("520x440")
        dlg.configure(fg_color=BG)
        dlg.transient(self)
        self._update_dialog = dlg

        # 白卡 wrap
        wrap = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=20,
                            border_width=1, border_color=LINE)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(3, weight=1)

        # 顶部：图标 + 标题 + 副标题
        head = ctk.CTkFrame(wrap, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 0))
        # 大圆渐变图标
        icon = ctk.CTkFrame(head, fg_color=BRAND, width=44, height=44, corner_radius=14)
        icon.pack(side="left")
        icon.pack_propagate(False)
        ctk.CTkLabel(icon, text="🔔", text_color="#fff",
                     font=ctk.CTkFont(size=22)).place(relx=0.5, rely=0.5, anchor="center")
        head_t = ctk.CTkFrame(head, fg_color="transparent")
        head_t.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(head_t, text="发现新版本", text_color=INK,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(head_t, text=f"当前版本：{self.license.client_version()}   ·   最新版本：{latest}",
                     text_color=INK3, font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))

        # 警告条
        if force:
            warn = ctk.CTkFrame(wrap, fg_color=BRAND_SOFT, corner_radius=10,
                                border_width=1, border_color="#FBC9C9")
            warn.grid(row=1, column=0, sticky="ew", padx=24, pady=(18, 0))
            ctk.CTkLabel(warn, text="⚠️  此版本为强制升级，必须更新才能继续使用。",
                         text_color=BRAND_INK, font=ctk.CTkFont(size=13, weight="bold")).pack(
                anchor="w", padx=14, pady=10)
        else:
            ctk.CTkLabel(wrap, text="建议升级以获得新功能 / 问题修复。",
                         text_color=INK3, font=ctk.CTkFont(size=13)).grid(
                row=1, column=0, sticky="w", padx=24, pady=(18, 0))

        # 更新说明
        if notes:
            notes_box = ctk.CTkTextbox(wrap, fg_color=SURFACE2,
                                       border_width=1, border_color=LINE,
                                       text_color=INK,
                                       font=ctk.CTkFont(size=13))
            notes_box.grid(row=2, column=0, sticky="nsew", padx=24, pady=(14, 12))
            notes_box.insert("1.0", "📌 更新说明\n\n" + notes)
            notes_box.configure(state="disabled")

        ctk.CTkLabel(wrap, text="更新过程会自动关闭当前程序并重新启动。",
                     text_color=INK3, font=ctk.CTkFont(size=11)).grid(
            row=3, column=0, sticky="sw", padx=24)

        # 底部按钮
        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 22))
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=1)
        if not force:
            ctk.CTkButton(bar, text="稍后", height=44, corner_radius=10,
                          fg_color=CARD, hover_color=SURFACE2,
                          text_color=INK2, border_width=1, border_color=LINE2,
                          font=ctk.CTkFont(size=14, weight="bold"),
                          command=lambda: (setattr(self, "update_dismissed", True), dlg.destroy())).grid(
                row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(bar, text="立即更新", height=44, corner_radius=10,
                      fg_color=BRAND, hover_color=BRAND_INK,
                      text_color="#fff", font=ctk.CTkFont(size=14, weight="bold"),
                      command=lambda: (dlg.destroy(), self._do_download_and_apply())).grid(
            row=0, column=1, sticky="ew", padx=(8, 0))

        # 居中
        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()

    def _do_download_and_apply(self):
        info = self.update_info or {}
        sysname = sys.platform
        if sysname.startswith("win"):
            url = (info.get("downloads") or {}).get("windows", "")
            relpath = "DouyinCommentMiner.exe"
        elif sysname == "darwin":
            url = (info.get("downloads") or {}).get("macos", "")
            # 新 zip 结构：install_root/DouyinCommentMiner/DouyinCommentMiner.app
            # （v1.3.4 起 macOS zip 加了包裹层，跟 Windows 对齐）
            relpath = "DouyinCommentMiner/DouyinCommentMiner.app"
        else:
            url = ""
            relpath = "DouyinCommentMiner"

        if not url:
            _msg("error", "升级失败", "服务端未提供本平台的下载链接。")
            return

        # 进度窗口（白卡 + 主色进度条）
        prog = ctk.CTkToplevel(self)
        prog.title("正在下载更新")
        prog.geometry("480x200")
        prog.configure(fg_color=BG)
        prog.transient(self)
        card = ctk.CTkFrame(prog, fg_color=CARD, corner_radius=20,
                            border_width=1, border_color=LINE)
        card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text="正在下载新版本...",
                     text_color=INK, font=ctk.CTkFont(size=16, weight="bold")).pack(
            pady=(28, 8))
        prog_lbl = ctk.CTkLabel(card, text="准备中...", text_color=INK3,
                                font=ctk.CTkFont(size=13))
        prog_lbl.pack(pady=(0, 12))
        prog_bar = ctk.CTkProgressBar(card, width=400, height=8, corner_radius=999,
                                       fg_color="#EEEDF5", progress_color=BRAND,
                                       border_width=0)
        prog_bar.set(0)
        prog_bar.pack(pady=(0, 28))
        prog.grab_set()

        def _worker():
            try:
                # 选下载目的地：当前 exe 所在目录的 _update 子目录
                if getattr(sys, "frozen", False):
                    # PyInstaller --onedir 模式
                    exe_dir = Path(sys.executable).parent
                    if sys.platform == "darwin":
                        # sys.executable = .app/Contents/MacOS/DouyinCommentMiner
                        # exe_dir = .app/Contents/MacOS/   (.parent)
                        #          .app/Contents/           (.parent.parent)
                        #          .app/                    (.parent.parent.parent) ← .app 根
                        # 把 _update/ 放在 .app/ 根，让 updater 解压时能覆盖整个 .app
                        exe_dir = exe_dir.parent.parent.parent
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

    def _do_logout(self):
        self.license.logout()
        self._show_login()

    # ================= 布局 =================
    def _build(self):
        """主界面布局：HTML ③ 风格（侧栏 + 顶部 + 主区）。"""
        # 清空窗口（登录界面 → 主界面切换时登录页 widget 必须先销毁）
        for w in self.winfo_children():
            w.destroy()
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.configure(fg_color=BG)

        # =================== 侧栏（238px） ===================
        self.side = ctk.CTkFrame(self, fg_color=SIDE, width=238, corner_radius=0)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)

        # 品牌区
        brand = ctk.CTkFrame(self.side, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(20, 6))
        brand.grid_columnconfigure(1, weight=1)
        # logo 方块
        brand_logo = ctk.CTkFrame(brand, fg_color=BRAND, width=36, height=36, corner_radius=10)
        brand_logo.grid(row=0, column=0, sticky="w")
        brand_logo.grid_propagate(False)
        ctk.CTkLabel(brand_logo, text="🐙", text_color="#fff",
                     font=ctk.CTkFont(size=20)).place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(brand, text="听潮", text_color=INK,
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=1, sticky="w", padx=(10, 0))
        ctk.CTkLabel(brand, text="TIDE SIGNAL", text_color=INK3,
                     font=ctk.CTkFont(size=10, weight="bold")).grid(
            row=1, column=1, sticky="w", padx=(10, 0), pady=(2, 0))

        ctk.CTkLabel(self.side, text="潮声之下，皆是商机",
                     text_color=INK3, font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=16, pady=(0, 14))

        # nav 分组
        self.nav_items = {}
        self._grp(self.side, "采集")
        self._nav(self.side, "monitor", "💬  评论监控",
                  lambda: self._show("monitor"), on=True)
        self._grp(self.side, "账号")
        self._nav(self.side, "account", "👤  抖音账号",
                  lambda: self._show("account"))
        self._grp(self.side, "帮助")
        self._nav(self.side, "help", "❓  使用说明",
                  lambda: self._show("help"))
        self._nav(self.side, "settings", "⚙  设置",
                  lambda: self._show("settings"))

        # side-promo（剩余天数）
        promo = ctk.CTkFrame(self.side, fg_color=CARD, corner_radius=12,
                             border_width=1, border_color=LINE)
        promo.pack(fill="x", padx=14, pady=(20, 0))
        ctk.CTkLabel(promo, text="授权剩余", text_color=INK3,
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14, pady=(12, 0))
        days = self.license.days_left()
        if days is None:
            days_text = "—"
            pct = 0
        elif days > 3650:
            days_text = "永久"
            pct = 100
        else:
            days_text = f"{days} 天"
            pct = max(0, min(100, int(days / 365 * 100)))
        ctk.CTkLabel(promo, text=days_text, text_color=INK,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=14, pady=(2, 8))
        # 进度条（HTML .bar）
        bar_track = ctk.CTkFrame(promo, fg_color="#EEEDF5",
                                 height=6, corner_radius=999)
        bar_track.pack(fill="x", padx=14)
        bar_track.pack_propagate(False)
        bar_fill = ctk.CTkFrame(bar_track, fg_color=BRAND,
                                height=6, corner_radius=999)
        bar_fill.place(relx=0, rely=0, relheight=1, relwidth=pct / 100.0)
        exp = self.license.expiry_date()
        ctk.CTkLabel(promo, text=f"到期 {exp} · {self.license.username or ''}",
                     text_color=INK3, font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=14, pady=(8, 14))

        ctk.CTkLabel(self.side, text="V1.0 · 层峰科技",
                     text_color=INK3, font=ctk.CTkFont(size=10)).pack(side="bottom", pady=12)

        # =================== 主区（侧栏右） ===================
        main = ctk.CTkFrame(self, fg_color=BG)
        main.pack(side="left", fill="both", expand=True)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # ---- 顶部（面包屑 + 右侧到期 + 头像 + 退出） ----
        top = ctk.CTkFrame(main, fg_color=CARD, corner_radius=0, height=56,
                           border_width=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        # 顶部底边线（用 1px 高的 frame 模拟）
        sep = ctk.CTkFrame(top, fg_color=LINE, height=1)
        sep.pack(side="bottom", fill="x")
        # 面包屑
        self._crumb = ctk.CTkLabel(top, text="采集 / 评论监控",
                                   text_color=INK3, font=ctk.CTkFont(size=13))
        self._crumb.pack(side="left", padx=28)
        # 右侧
        top_r = ctk.CTkFrame(top, fg_color="transparent")
        top_r.pack(side="right", padx=20)
        days = self.license.days_left()
        exp_color = BRAND_INK if (days is not None and days < 7) else INK2
        exp = self.license.expiry_date()
        ctk.CTkLabel(top_r, text=f"📅 到期 {exp}", text_color=exp_color,
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 12))
        # 头像圆（用户名首字符）
        uname = self.license.username or "?"
        av = ctk.CTkFrame(top_r, fg_color=BRAND, width=32, height=32, corner_radius=16)
        av.pack(side="left")
        av.pack_propagate(False)
        ctk.CTkLabel(av, text=uname[0].upper(), text_color="#fff",
                     font=ctk.CTkFont(size=13, weight="bold")).place(
            relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(top_r, text=uname, text_color=INK,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left", padx=(8, 12))
        ctk.CTkButton(top_r, text="退出登录", height=30, corner_radius=9,
                      fg_color=CARD, hover_color=SURFACE2, text_color=INK2,
                      border_width=1, border_color=LINE2,
                      font=ctk.CTkFont(size=12),
                      command=self._do_logout).pack(side="left")

        # ---- 内容页容器 ----
        self.page = ctk.CTkFrame(main, fg_color=BG)
        self.page.grid(row=1, column=0, sticky="nsew")
        self.page.grid_rowconfigure(0, weight=1)
        self.page.grid_columnconfigure(0, weight=1)

        self._build_monitor(self.page)
        self._build_account(self.page)
        self._build_help(self.page)
        self._build_settings(self.page)
        self._show("monitor")

    def _grp(self, parent, text):
        ctk.CTkLabel(parent, text=text.upper(), text_color=INK3,
                     anchor="w", font=ctk.CTkFont(size=10, weight="bold")).pack(
            fill="x", padx=20, pady=(16, 6))

    def _nav(self, parent, key, text, cmd, on=False):
        b = ctk.CTkButton(parent, text=text, anchor="w", height=38, corner_radius=11,
                          fg_color=SIDE_ON if on else "transparent",
                          hover_color=SURFACE2,
                          text_color=SIDE_ON_FG if on else INK2,
                          font=ctk.CTkFont(size=14, weight="bold" if on else "normal"),
                          border_width=0, command=cmd)
        b.pack(fill="x", padx=12, pady=2)
        self.nav_items[key] = b

    def _show(self, which):
        # 更新面包屑
        crumb_map = {
            "monitor":  "采集 / 评论监控",
            "account":  "账号 / 抖音账号",
            "help":     "帮助 / 使用说明",
            "settings": "帮助 / 设置",
        }
        try:
            self._crumb.configure(text=crumb_map.get(which, ""))
        except Exception:
            pass
        for w in self.page.winfo_children():
            w.grid_forget()
        for k, b in self.nav_items.items():
            active = (k == which)
            b.configure(fg_color=SIDE_ON if active else "transparent",
                        text_color=SIDE_ON_FG if active else INK2,
                        font=ctk.CTkFont(size=14, weight="bold" if active else "normal"))
        getattr(self, "frm_" + which).grid(row=0, column=0, sticky="nsew")

    # ---------- 评论监控页 ----------
    def _build_monitor(self, parent):
        f = ctk.CTkFrame(parent, fg_color=BG)
        self.frm_monitor = f
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)  # 表格区撑满

        # ===== 顶部：标题 + tabs（全部/抖音 占位） =====
        page_h = ctk.CTkFrame(f, fg_color="transparent")
        page_h.grid(row=0, column=0, sticky="ew", pady=(20, 14), padx=24)
        page_h.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(page_h, text="💬  评论监控", text_color=INK,
                     font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w")
        # tabs（HTML .ptabs，全部 / 抖音；小红书静态占位）
        tabs = ctk.CTkFrame(page_h, fg_color=SURFACE2, corner_radius=11,
                            border_width=1, border_color=LINE)
        tabs.grid(row=0, column=1, sticky="e")
        self._mk_ptab(tabs, "all", "全部", on=True)
        self._mk_ptab(tabs, "douyin", "抖音", dot_color="#171A26")
        self._mk_ptab(tabs, "xhs", "小红书", dot_color="#E8153C")  # 静态

        # ===== stat-strip 4 卡 =====
        strip = ctk.CTkFrame(f, fg_color="transparent")
        strip.grid(row=1, column=0, sticky="ew", padx=24)
        strip.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")
        self.stat_total = self._mk_stat(strip, "已收集评论", "0", brand=False)
        self.stat_hit = self._mk_stat(strip, "关键词命中", "0", brand=True)
        self.stat_user = self._mk_stat(strip, "去重用户", "0", brand=False)
        self.stat_vid = self._mk_stat(strip, "监控视频", "0", brand=False)

        # ===== 工具栏 actionbar =====
        action = ctk.CTkFrame(f, fg_color=SURFACE2, corner_radius=12,
                               border_width=1, border_color=LINE)
        action.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 12))
        # 不允许 grid expand（这一行 stat-strip 不允许表格拉长，下面改成 row=4 给表格）
        f.grid_rowconfigure(2, weight=0)

        self.b_cfg = ctk.CTkButton(action, text="⚙  配置", height=42, corner_radius=10,
                                   fg_color=CARD, hover_color=BG,
                                   border_width=1, border_color=LINE2,
                                   text_color=INK, font=ctk.CTkFont(size=14, weight="bold"),
                                   command=self._open_cfg_modal)
        self.b_cfg.pack(side="left", padx=(14, 8), pady=8)
        self.b_run = ctk.CTkButton(action, text="▶  开始抓取", height=42, corner_radius=10,
                                   fg_color=BRAND, hover_color=BRAND_INK,
                                   text_color="#fff", font=ctk.CTkFont(size=14, weight="bold"),
                                   command=self.on_run)
        self.b_run.pack(side="left", padx=4, pady=8)
        self.b_stop = ctk.CTkButton(action, text="⏸  停止", height=42, corner_radius=10,
                                    fg_color=CARD, hover_color=BG,
                                    border_width=1, border_color=LINE2,
                                    text_color=INK3, font=ctk.CTkFont(size=14, weight="bold"),
                                    command=self.on_stop, state="disabled")
        self.b_stop.pack(side="left", padx=4, pady=8)
        # 分隔
        ctk.CTkFrame(action, fg_color=LINE2, width=1, height=28).pack(
            side="left", padx=8, pady=8)
        self.b_clear = ctk.CTkButton(action, text="🗑  清空", height=42, corner_radius=10,
                                     fg_color=CARD, hover_color=BRAND_SOFT,
                                     border_width=1, border_color=LINE2,
                                     text_color=BRAND_INK,
                                     font=ctk.CTkFont(size=14, weight="bold"),
                                     command=self.on_clear)
        self.b_clear.pack(side="left", padx=4, pady=8)
        self.b_open = ctk.CTkButton(action, text="📋  打开表格", height=42, corner_radius=10,
                                    fg_color=VIOLET, hover_color="#6B4DEB",
                                    text_color="#fff",
                                    font=ctk.CTkFont(size=14, weight="bold"),
                                    command=self.on_open, state="disabled")
        self.b_open.pack(side="left", padx=4, pady=8)

        # live 指示器（HTML .live）
        live_box = ctk.CTkFrame(action, fg_color="transparent")
        live_box.pack(side="right", padx=14)
        self._live_dot = ctk.CTkLabel(live_box, text="●", text_color=INK3,
                                      font=ctk.CTkFont(size=12))
        self._live_dot.pack(side="left")
        self._live_txt = ctk.CTkLabel(live_box, text="未开始",
                                      text_color=INK2,
                                      font=ctk.CTkFont(size=13))
        self._live_txt.pack(side="left", padx=(6, 0))

        # ===== 状态条（在表格上方，原 status） =====
        self.status = ctk.CTkLabel(f, text="● 就绪",
                                   text_color="#e0663b", anchor="w",
                                   font=ctk.CTkFont(size=12))
        self.status.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 6))

        # ===== 实时表格 =====
        tbl = ctk.CTkFrame(f, fg_color=CARD, corner_radius=12,
                           border_width=1, border_color=LINE)
        tbl.grid(row=4, column=0, sticky="nsew", padx=24, pady=(0, 20))
        f.grid_rowconfigure(4, weight=1)
        head = ctk.CTkFrame(tbl, fg_color=SURFACE2, corner_radius=0, height=38)
        head.pack(fill="x")
        head.pack_propagate(False)
        self._mk_row(head, HEADERS, header=True)
        self.tbody = ctk.CTkScrollableFrame(tbl, fg_color=CARD, corner_radius=0)
        self.tbody.pack(fill="both", expand=True)
        self._empty_hint()

        # ===== 配置弹窗（按 HTML .modal 风格） =====
        self._build_cfg_modal()
        # 让 _on_preset 绑定起来（chips 等弹窗组件引用）
        self._bind_cfg_widgets()

    def _mk_ptab(self, parent, key: str, label: str, on=False, dot_color: str = ""):
        """顶部 tabs：全部 / 抖音 / 小红书（静态占位）。"""
        b = ctk.CTkButton(parent, text=("● " + label) if dot_color else label,
                          height=34, corner_radius=8,
                          fg_color=CARD if on else "transparent",
                          hover_color=CARD,
                          text_color=INK if on else INK2,
                          font=ctk.CTkFont(size=13, weight="bold" if on else "normal"),
                          border_width=0, command=lambda: None)
        b.pack(side="left", padx=4, pady=4)

    def _mk_stat(self, parent, label: str, value: str, brand: bool = False):
        """HTML .stat：白卡 + label + 大数字。"""
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color=LINE)
        card.grid(row=0, column=parent.grid_size()[0] - 1, sticky="ew",
                  padx=(0, 12))
        ctk.CTkLabel(card, text=label, text_color=INK3,
                     font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=18, pady=(15, 0))
        val = ctk.CTkLabel(card, text=value, text_color=BRAND_INK if brand else INK,
                           font=ctk.CTkFont(size=26, weight="bold"))
        val.pack(anchor="w", padx=18, pady=(4, 15))
        return val

    def _empty_hint(self):
        self._hint = ctk.CTkLabel(
            self.tbody,
            text="（填好链接和关键词，点「开始抓取」，命中结果会实时出现在这里）",
            text_color=INK3, font=ctk.CTkFont(size=12))
        self._hint.pack(pady=40)

    def _open_cfg_modal(self):
        """打开配置弹窗，重置字段为当前值。"""
        # 链接 + 关键词：从 textbox 写到 chips
        try:
            self._cfg_links.delete("1.0", "end")
            self._cfg_links.insert("1.0", self.links.get("1.0", "end").strip())
        except Exception:
            pass
        try:
            current_kws = [k for k in self.kws.get("1.0", "end").splitlines() if k.strip()]
            self._cfg_chips.set_keywords(current_kws)
        except Exception:
            pass
        try:
            self._cfg_maxc.delete(0, "end")
            self._cfg_maxc.insert(0, self.maxc.get())
        except Exception:
            pass
        try:
            self._cfg_dir_lbl.configure(text="保存到：" + self.outdir.get())
        except Exception:
            pass
        # 居中
        self._cfg_modal.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - self._cfg_modal.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - self._cfg_modal.winfo_height()) // 2
        self._cfg_modal.geometry(f"+{x}+{y}")
        self._cfg_modal.grab_set()
        self._cfg_modal.focus_set()

    def _build_cfg_modal(self):
        """配置弹窗（HTML .modal 风格）。"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("抓取配置")
        dlg.geometry("520x680")
        dlg.configure(fg_color=BG)
        self._cfg_modal = dlg
        # 内容
        wrap = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=20,
                            border_width=1, border_color=LINE)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        # 顶部
        top = ctk.CTkFrame(wrap, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(22, 6))
        ctk.CTkLabel(top, text="抓取配置", text_color=INK,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(top, text="设置关键词、平台与监控范围",
                     text_color=INK3, font=ctk.CTkFont(size=13)).pack(
            anchor="w", pady=(2, 0))

        # 表单
        body = ctk.CTkFrame(wrap, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(8, 8))

        def field(parent, label):
            ctk.CTkLabel(parent, text=label, text_color=INK2,
                         font=ctk.CTkFont(size=13, weight="bold")).pack(
                anchor="w", pady=(0, 6))

        # 监控平台
        field(body, "监控平台")
        self._cfg_plat = ctk.CTkOptionMenu(body, width=480, height=44, corner_radius=10,
                                           values=["抖音", "小红书", "抖音 + 小红书"],
                                           fg_color=CARD, button_color=BRAND,
                                           button_hover_color=BRAND_INK,
                                           text_color=INK,
                                           dropdown_fg_color=CARD,
                                           dropdown_hover_color=SURFACE2,
                                           dropdown_text_color=INK,
                                           font=ctk.CTkFont(size=14))
        self._cfg_plat.set("抖音 + 小红书")
        self._cfg_plat.pack(fill="x", pady=(0, 14))

        # 关键词 chips
        field(body, "命中关键词（回车添加）")
        self._cfg_chips = ChipsEntry(body)
        self._cfg_chips.pack(fill="x", pady=(0, 14))

        # 链接 textarea
        field(body, "视频 / 笔记链接（每行一个）")
        self._cfg_links = ctk.CTkTextbox(body, height=120, corner_radius=10,
                                         border_width=1, border_color=LINE2,
                                         fg_color=CARD, text_color=INK,
                                         font=ctk.CTkFont(size=13))
        self._cfg_links.pack(fill="x", pady=(0, 14))

        # 时间范围 + 抓取深度
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(0, 14))
        row.grid_columnconfigure((0, 1), weight=1)
        c1 = ctk.CTkFrame(row, fg_color="transparent")
        c1.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        field(c1, "时间范围")
        self._cfg_preset = ctk.CTkOptionMenu(
            c1, height=44, corner_radius=10,
            values=["全部", "最近1天", "最近3天", "最近7天", "最近30天", "自定义"],
            fg_color=CARD, button_color=BRAND, button_hover_color=BRAND_INK,
            text_color=INK, font=ctk.CTkFont(size=13))
        self._cfg_preset.set("全部")
        self._cfg_preset.pack(fill="x")
        c2 = ctk.CTkFrame(row, fg_color="transparent")
        c2.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        field(c2, "抓取深度（条）")
        self._cfg_maxc = ctk.CTkEntry(c2, height=44, corner_radius=10,
                                      border_width=1, border_color=LINE2,
                                      fg_color=CARD, text_color=INK,
                                      font=ctk.CTkFont(size=14))
        self._cfg_maxc.insert(0, "3000")
        self._cfg_maxc.pack(fill="x")

        # 保存目录
        dir_row = ctk.CTkFrame(body, fg_color="transparent")
        dir_row.pack(fill="x")
        self._cfg_dir_lbl = ctk.CTkLabel(dir_row, text="保存到：" + DEFAULT_OUTDIR,
                                         text_color=INK3,
                                         font=ctk.CTkFont(size=12))
        self._cfg_dir_lbl.pack(side="left")
        ctk.CTkButton(dir_row, text="选位置", width=86, height=32, corner_radius=8,
                      fg_color=CARD, hover_color=SURFACE2,
                      text_color=INK, border_width=1, border_color=LINE2,
                      font=ctk.CTkFont(size=13),
                      command=self.on_pick_dir).pack(side="right")

        # 底部按钮
        bot = ctk.CTkFrame(wrap, fg_color="transparent")
        bot.pack(fill="x", padx=24, pady=(8, 22))
        ctk.CTkButton(bot, text="取消", height=44, corner_radius=10,
                      fg_color=CARD, hover_color=SURFACE2,
                      text_color=INK2, border_width=1, border_color=LINE2,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=dlg.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bot, text="保存配置", height=44, corner_radius=10,
                      fg_color=BRAND, hover_color=BRAND_INK,
                      text_color="#fff", font=ctk.CTkFont(size=14, weight="bold"),
                      command=self._save_cfg).pack(side="right")

    def _bind_cfg_widgets(self):
        """让 on_run / on_pick_dir 引用弹窗里的字段（兼容旧引用）。

        弹窗的字段就是真实数据源：self.links / self.kws / self.maxc /
        self.preset / self.with_replies 都是从弹窗组件来的别名。
        """
        self.links = self._cfg_links
        self.kws = _ChipsTextProxy(self._cfg_chips)
        self.maxc = self._cfg_maxc
        self.preset = self._cfg_preset
        self.with_replies = _ProxyCheck(value=True)
        # 占位字段：旧 start/end 字段已被 chips 弹窗版替代
        self.start = _ProxyEmpty()
        self.end = _ProxyEmpty()
        self.dir_lbl = _ProxyDirLabel(self)
        self._cfg_modal.protocol("WM_DELETE_WINDOW", lambda: self._cfg_modal.destroy())

    def _save_cfg(self):
        """保存配置：把弹窗里的字段同步回主面板的隐藏字段。"""
        # 把 links / keywords 写回主面板兼容旧代码引用
        # self.links / self.kws 是占位对象，所以需要真字段
        self._main_links_text = self._cfg_links.get("1.0", "end").strip()
        self._main_kws_list = self._cfg_chips.get()
        self.outdir_str = self.outdir.get()
        self._cfg_modal.destroy()

    def on_pick_dir(self):
        """兼容旧引用：保存目录选择。"""
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.outdir.get() or DEFAULT_OUTDIR)
        if d:
            self.outdir.set(d)
            try:
                self._cfg_dir_lbl.configure(text="保存到：" + d)
            except Exception:
                pass
            try:
                self.dir_lbl.configure(text="保存到：" + d)
            except Exception:
                pass

    def _on_preset(self, *_):
        """兼容旧引用；弹窗里的时间范围由 chips 弹窗自管。"""
        try:
            custom = (self.preset.get() == "自定义")
        except Exception:
            return
        # 旧 start/end 输入已废弃（弹窗里没有），这里 no-op

    # ---------- 结果行（实时追加） ----------
    def _mk_row(self, parent, values, header=False, url=None):
        for ci, (v, wpx) in enumerate(zip(values, COLS)):
            color = INK3 if header else INK
            lbl = ctk.CTkLabel(parent, text=str(v), width=wpx, anchor="w",
                               font=ctk.CTkFont(size=12, weight="bold" if header else "normal"),
                               text_color=(BRAND_INK if (not header and ci >= 5) else color))
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
        # 隔行换色（浅灰 / 白）
        bg = SURFACE2 if self._rowcount % 2 == 0 else CARD
        rf = ctk.CTkFrame(self.tbody, fg_color=bg,
                          corner_radius=0, height=36)
        rf.pack(fill="x"); rf.pack_propagate(False)
        vals = [str(self._rowcount), _trunc(r["视频ID"], 12), _trunc(r["昵称"], 12),
                _trunc(r["评论内容"], 40), _trunc(r.get("评论时间", ""), 16), "打开", "私信"]
        self._mk_row(rf, vals, url=r["主页链接"])
        # L2：命中系统通知（节流：累计前 5 条逐条发，之后不发）
        if self._notifier:
            kw = r.get("关键词", "")
            who = r.get("昵称", "")
            text = r.get("评论内容", "")[:30]
            body = f"「{kw}」 · {who}: {text}" if kw else f"{who}: {text}"
            self._notifier.notify_hit_throttled(title="命中关键词", body=body)
        try:
            self.tbody._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass
        # 同步 stat-strip 数字
        self._sync_stats()

    def _clear_table(self):
        for w in self.tbody.winfo_children():
            w.destroy()
        self._rowcount = 0
        self._hint = None
        self._empty_hint()
        self._sync_stats()

    def _sync_stats(self):
        """把 stat-strip 4 个数字同步到当前抓取进度。"""
        try:
            self.stat_hit.configure(text=str(self._rowcount))
        except Exception:
            pass
        # 去重用户（按 昵称 去重）
        try:
            users = set()
            for w in self.tbody.winfo_children():
                for child in w.winfo_children():
                    if isinstance(child, ctk.CTkLabel):
                        txt = child.cget("text")
                        if txt and txt[0].isdigit() is False:
                            users.add(txt)
            self.stat_user.configure(text=str(len(users)))
        except Exception:
            pass

    def _set_live(self, text: str, running: bool = False):
        """更新 live 指示器（HTML .live）。"""
        try:
            self._live_dot.configure(text_color=TEAL if running else INK3)
            self._live_txt.configure(text=text)
        except Exception:
            pass
        # L2：托盘同步状态
        if self._tray:
            self._tray.set_busy(running)
            if running:
                self._tray.set_tooltip(f"{APP_TITLE} · 抓取中…")

    # ---------- 抖音账号页 ----------
    def _build_account(self, parent):
        f = ctk.CTkFrame(parent, fg_color=BG)
        self.frm_account = f
        f.grid_columnconfigure(0, weight=1)

        # 顶部标题
        head = ctk.CTkFrame(f, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        ctk.CTkLabel(head, text="👤  抖音账号", text_color=INK,
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")

        # 状态卡
        card = ctk.CTkFrame(f, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color=LINE)
        card.grid(row=1, column=0, sticky="ew", padx=24)
        # 顶部：状态 pill
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(20, 0))
        ctk.CTkLabel(top, text="登录状态", text_color=INK3,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.acct_status = ctk.CTkLabel(card, text="● 检测中…", text_color="#e0663b",
                                        font=ctk.CTkFont(size=22, weight="bold"))
        self.acct_status.pack(anchor="w", padx=20, pady=(8, 4))
        # 按钮行
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(anchor="w", padx=20, pady=(12, 24))
        self.b_acct_login = ctk.CTkButton(row, text="登录 / 重新绑定", height=44,
                                          corner_radius=10, fg_color=BRAND,
                                          hover_color=BRAND_INK,
                                          text_color="#fff",
                                          font=ctk.CTkFont(size=14, weight="bold"),
                                          command=self.on_login)
        self.b_acct_login.pack(side="left", padx=(0, 12))
        self.b_acct_check = ctk.CTkButton(row, text="检测状态", height=44, width=110,
                                          corner_radius=10, fg_color=CARD,
                                          hover_color=SURFACE2,
                                          text_color=INK2, border_width=1,
                                          border_color=LINE2,
                                          font=ctk.CTkFont(size=14),
                                          command=self.on_check_login)
        self.b_acct_check.pack(side="left")

        # 说明卡
        note = ctk.CTkFrame(f, fg_color=SURFACE2, corner_radius=12,
                            border_width=1, border_color=LINE)
        note.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 20))
        ctk.CTkLabel(note, text="点「登录 / 重新绑定」会弹出浏览器，扫码登录后自动检测并关闭浏览器；\n"
                                "若打开时已登录，会立即关闭。抓取前会自动校验登录状态。",
                     text_color=INK2, anchor="w", justify="left",
                     font=ctk.CTkFont(size=13)).pack(anchor="w", padx=20, pady=18)

    def _set_acct_status(self, logged_in):
        self.logged_in = logged_in
        try:
            self.acct_status.configure(text="● 已登录" if logged_in else "● 未登录",
                                       text_color=TEAL if logged_in else "#dc2626")
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
        f = ctk.CTkFrame(parent, fg_color=BG)
        self.frm_help = f
        f.grid_columnconfigure(0, weight=1)

        # 顶部
        head = ctk.CTkFrame(f, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        ctk.CTkLabel(head, text="❓  使用说明", text_color=INK,
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")

        card = ctk.CTkFrame(f, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color=LINE)
        card.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 20))
        f.grid_rowconfigure(1, weight=1)
        txt = (
            "使用说明\n\n"
            "1) 首次使用：左侧「抖音账号」点「登录 / 重新绑定」，扫码后自动检测并关闭浏览器（以后不用再扫）。\n\n"
            "2) 在「评论监控」点「配置」弹窗：填视频链接（一行一个，可整段粘分享文案）、"
            "关键词（回车添加 chip）、可选时间范围 / 抓取深度。\n\n"
            "3) 点「开始抓取」：命中的评论会【边抓边实时】出现在下方列表，不用等全部跑完；中途可「停止」。\n\n"
            "4) 点「打开表格」查看导出的文件（同时生成 .xlsx 可点超链接 和 .csv），默认存在 文档\\抖音评论名单。\n\n"
            "5) 下方列表里「打开 / 私信」可点击，进入对方主页后再点私信联系。\n\n"
            "提示：本工具只做导名单 / 看反馈，不支持批量私信；频繁群发易封号，请文明使用。"
        )
        box = ctk.CTkTextbox(card, wrap="word", fg_color=CARD,
                             text_color=INK2,
                             font=ctk.CTkFont(size=14))
        box.pack(fill="both", expand=True, padx=24, pady=20)
        box.insert("1.0", txt)
        box.configure(state="disabled")

    # ---------- 设置页（L2 桌面级） ----------
    def _build_settings(self, parent):
        """通知 + 行为 + 关于三个卡片。"""
        from desktop import prefs, autostart

        f = ctk.CTkFrame(parent, fg_color=BG)
        self.frm_settings = f
        f.grid_columnconfigure(0, weight=1)

        # 顶部标题
        head = ctk.CTkFrame(f, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        ctk.CTkLabel(head, text="⚙  设置", text_color=INK,
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        ctk.CTkLabel(head, text="偏好自动保存 · 重启应用后生效",
                     text_color=INK3, font=ctk.CTkFont(size=12)).pack(
            side="left", padx=(12, 0))

        # 内容滚动容器
        body = ctk.CTkScrollableFrame(f, fg_color=BG)
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 20))
        f.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # ---------- 卡片 1：通知 ----------
        def _mk_card(parent, title, sub):
            c = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12,
                             border_width=1, border_color=LINE)
            ctk.CTkLabel(c, text=title, text_color=INK,
                         font=ctk.CTkFont(size=15, weight="bold")).pack(
                anchor="w", padx=20, pady=(16, 2))
            ctk.CTkLabel(c, text=sub, text_color=INK3,
                         font=ctk.CTkFont(size=12)).pack(
                anchor="w", padx=20, pady=(0, 8))
            return c

        card1 = _mk_card(body, "🔔  通知",
                         "抓取命中 / 完成 / 账号异常 / 新版本时通过系统通知中心推送")
        card1.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        # 4 个通知开关
        def _mk_switch_row(parent, label, pref_key):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=8)
            ctk.CTkLabel(row, text=label, text_color=INK2,
                         font=ctk.CTkFont(size=13)).pack(side="left")
            sw = ctk.CTkSwitch(
                row, text="", width=46, height=24,
                progress_color=BRAND, button_color=BRAND_INK,
                fg_color=SURFACE2, button_hover_color=BRAND,
                border_width=0,
                command=lambda: self._toggle_pref(pref_key, sw.get()),
            )
            sw.pack(side="right")
            if self._prefs.get(pref_key, True):
                sw.select()
            return sw

        _mk_switch_row(card1, "关键词命中提醒", "notify_hit")
        _mk_switch_row(card1, "抓取完成汇总",   "notify_done")
        _mk_switch_row(card1, "账号被踢下线",   "notify_kicked")
        _mk_switch_row(card1, "新版本发布",     "notify_update")

        # ---------- 卡片 2：行为 ----------
        card2 = _mk_card(body, "🎯  行为",
                         "关闭按钮 + 开机自启")
        card2.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        # 关闭时最小化到托盘
        row_min = ctk.CTkFrame(card2, fg_color="transparent")
        row_min.pack(fill="x", padx=20, pady=8)
        ctk.CTkLabel(row_min, text="点 × 时最小化到托盘（不退出）",
                     text_color=INK2, font=ctk.CTkFont(size=13)).pack(side="left")
        sw_min = ctk.CTkSwitch(
            row_min, text="", width=46, height=24,
            progress_color=BRAND, button_color=BRAND_INK,
            fg_color=SURFACE2, button_hover_color=BRAND,
            border_width=0,
            command=lambda: self._toggle_pref("tray_minimize", sw_min.get()),
        )
        sw_min.pack(side="right")
        if self._prefs.get("tray_minimize", True):
            sw_min.select()

        # 开机自启
        row_auto = ctk.CTkFrame(card2, fg_color="transparent")
        row_auto.pack(fill="x", padx=20, pady=8)
        ctk.CTkLabel(row_auto, text="开机自动启动（登录后默认隐藏窗口）",
                     text_color=INK2, font=ctk.CTkFont(size=13)).pack(side="left")
        sw_auto = ctk.CTkSwitch(
            row_auto, text="", width=46, height=24,
            progress_color=BRAND, button_color=BRAND_INK,
            fg_color=SURFACE2, button_hover_color=BRAND,
            border_width=0,
            command=lambda: self._toggle_autostart(sw_auto.get()),
        )
        sw_auto.pack(side="right")
        # 与 OS 真实状态对齐
        try:
            if autostart.is_enabled():
                sw_auto.select()
        except Exception:
            pass

        # ---------- 卡片 3：关于 ----------
        card3 = _mk_card(body, "ℹ  关于", f"听潮 · 抖音评论名单挖掘")
        card3.grid(row=2, column=0, sticky="ew")
        # 版本号
        try:
            from license import __version__
            ver = __version__
        except Exception:
            ver = "unknown"
        info_row = ctk.CTkFrame(card3, fg_color="transparent")
        info_row.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(info_row, text=f"当前版本：v{ver}",
                     text_color=INK2, font=ctk.CTkFont(size=13)).pack(side="left")
        ctk.CTkButton(
            info_row, text="检查更新", height=32, corner_radius=9,
            fg_color=CARD, hover_color=SURFACE2, text_color=INK2,
            border_width=1, border_color=LINE2,
            font=ctk.CTkFont(size=12),
            command=self._check_update_startup,
        ).pack(side="right")
        # 二维码/链接占位
        ctk.CTkLabel(card3, text="潮声之下，皆是商机 · TideSignal",
                     text_color=INK3, font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=20, pady=(0, 16))

    def _toggle_pref(self, key: str, value: bool):
        """开关切换：写 prefs.json，立即生效。"""
        from desktop import prefs
        self._prefs[key] = bool(value)
        prefs.save(self._prefs)
        # notifier 引用了 prefs，重新读一次让它下次发通知时用新值
        if self._notifier:
            self._notifier.prefs = self._prefs

    def _toggle_autostart(self, value: bool):
        """开机自启开关：写 prefs + OS 注册项双写。"""
        from desktop import autostart
        ok = False
        try:
            if value:
                ok = autostart.enable()
            else:
                ok = autostart.disable()
        except Exception as e:
            ok = False
            print(f"[autostart] toggle 失败: {e}")
        # 同步 prefs（即使 OS 失败，prefs 也记下用户意图）
        self._toggle_pref("autostart", bool(value))
        # 反馈
        if not ok:
            _msg("warn", "开机自启设置失败",
                 "可能是权限不足（macOS 需要「系统设置-通用-登录项」允许；Windows 需要当前用户写权限）")

    # ---------- 线程安全 UI ----------
    def set_status(self, m, color="#e0663b"):
        self.q.put(("status", (m, color)))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.status.configure(text=val[0], text_color=val[1])
                    # 同步 live 指示器
                    txt = val[0] if isinstance(val[0], str) else ""
                    if "抓取中" in txt:
                        self._set_live(f"抓取中 · 命中 {self._rowcount}", running=True)
                    elif "完成" in txt or "命中" in txt:
                        self._set_live(f"已停止 · 命中 {self._rowcount}", running=False)
                    elif "出错" in txt or "失败" in txt:
                        self._set_live("出错", running=False)
                    elif "停止" in txt:
                        self._set_live(f"已停止 · 命中 {self._rowcount}", running=False)
                elif kind == "progress":
                    self.status.configure(text=f"● 抓取中… 已收集 {val} 条评论 / 已命中 {self._rowcount}",
                                          text_color=TEAL)
                    self._set_live(f"抓取中 · 已收集 {val} / 命中 {self._rowcount}",
                                   running=True)
                    try:
                        self.stat_total.configure(text=str(val))
                    except Exception:
                        pass
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
        """兼容旧引用：打开配置弹窗。"""
        self._open_cfg_modal()

    def on_pick_dir(self):
        """保留旧函数签名（兼容旧引用）；新逻辑已移到 _open_cfg_modal 弹窗内。"""
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.outdir.get() or DEFAULT_OUTDIR)
        if d:
            self.outdir.set(d)
            try:
                self._cfg_dir_lbl.configure(text="保存到：" + d)
            except Exception:
                pass
            try:
                self.dir_lbl.configure(text="保存到：" + d)
            except Exception:
                pass

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
        # 自定义日期范围已从弹窗删除（chips 弹窗版只有"全部 / 1天/3天/7天/30天"）；
        # 若需要自定义，后期再加回弹窗里的 date 输入
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
        # L2：托盘切回 idle
        if self._tray:
            self._tray.set_busy(False)
            self._tray.set_tooltip(f"{APP_TITLE} · 已就绪")
        if count < 0:
            self.set_status("● 出错，见提示", "#dc2626")
            if self._notifier:
                self._notifier.notify("done", title="抓取出错",
                                      body="运行过程中发生异常，请在主窗口查看详情")
            return
        if count == 0:
            self.set_status("● 完成：没有命中，换关键词或确认链接能打开评论", "#e0663b")
            if self._notifier:
                self._notifier.notify("done", title="抓取完成",
                                      body="本次没有命中关键词")
            self._notifier and self._notifier.reset_hit_counter()
            return
        self.last_file = xlsx_path or csv_path
        self.b_open.configure(state="normal")
        self.set_status(f"● 完成：{count} 条命中，已导出，点「打开表格」查看", TEAL)
        # L2：抓取完成通知（命中汇总）
        if self._notifier:
            # 如果累计 > 5 条命中，先发汇总
            self._notifier.flush_hit_summary(count)
            self._notifier.notify("done", title=f"抓取完成 · {count} 条命中",
                                  body=f"文件已保存到 {self.last_file}")
            self._notifier.reset_hit_counter()

    def on_open(self):
        if self.last_file and os.path.exists(self.last_file):
            open_path(self.last_file)
        elif os.path.isdir(self.outdir.get()):
            open_path(self.outdir.get())


def main():
    import sys as _sys
    app = App()
    # L2：开机自启模式下默认隐藏窗口（让 OS 启动时不闪窗）
    if "--autostart" in _sys.argv:
        try:
            app.withdraw()
        except Exception:
            pass
    app.mainloop()


if __name__ == "__main__":
    main()
