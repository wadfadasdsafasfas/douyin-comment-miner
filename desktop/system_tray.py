"""系统托盘封装（pystray）。

菜单项：
    👀 显示主窗口（default=True，window icon 单击触发）
    ▶ 开始抓取 / ⏹ 停止抓取（按 busy 态互斥显示）
    ─────
    🚀 开机自启（checked 状态实时读 autostart.is_enabled()）
    ─────
    ❌ 退出

注意：
    - pystray 跑独立守护线程；tray → tk 的所有调用必须走 app.after(0, ...)
    - 托盘图标分 idle / busy 两版（PNG），运行时切换
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from desktop import autostart


def _resolve_asset(filename: str) -> str:
    """打包态 / 开发态都能找到图标。"""
    candidates = [
        Path(__file__).resolve().parent / "assets" / filename,
        Path(getattr(__import__("sys"), "_MEIPASS", "")) / "desktop" / "assets" / filename,
    ]
    for p in candidates:
        if p and p.exists():
            return str(p)
    return ""


class SystemTray:
    def __init__(
        self,
        app,                                  # ctk.CTk 实例
        on_show: Callable,
        on_hide: Callable,
        on_quit: Callable,
        on_start_crawl: Optional[Callable] = None,
        on_stop_crawl: Optional[Callable] = None,
        title: str = "听潮 · 抖音评论名单挖掘",
    ):
        self.app = app
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_quit = on_quit
        self.on_start_crawl = on_start_crawl
        self.on_stop_crawl = on_stop_crawl
        self.title = title

        self._busy = False
        self._icon = None
        self._thread: Optional[threading.Thread] = None

        self._idle_path = _resolve_asset("tray-idle.png")
        self._busy_path = _resolve_asset("tray-busy.png")
        if not self._idle_path:
            raise FileNotFoundError("找不到 tray-idle.png，请先跑 tools/build_assets.py")

    # ---------- 生命周期 ----------
    def start(self):
        """启动托盘守护线程。"""
        from PIL import Image
        import pystray

        def _build_menu():
            return pystray.Menu(
                pystray.MenuItem("👀 显示主窗口", self._show, default=True),
                pystray.MenuItem(
                    "▶ 开始抓取", self._start_crawl,
                    visible=lambda i: not self._busy and self.on_start_crawl is not None,
                ),
                pystray.MenuItem(
                    "⏹ 停止抓取", self._stop_crawl,
                    visible=lambda i: self._busy and self.on_stop_crawl is not None,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "🚀 开机自启", self._toggle_autostart,
                    checked=lambda i: autostart.is_enabled(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("❌ 退出", self._quit),
            )

        self._icon = pystray.Icon(
            "TideSignal",
            icon=Image.open(self._idle_path),
            title=self.title,
            menu=_build_menu(),
        )
        # 单击托盘图标 = 显示窗口（macOS / Windows 默认行为）
        self._icon.on_activate = self._show_from_click

        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="tray")
        self._thread.start()

    def stop(self):
        """停止托盘（退出时调）。"""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ---------- 状态切换 ----------
    def set_busy(self, busy: bool):
        """切换托盘图标 idle ↔ busy；必须在 tk 主线程调（或用 after）。"""
        self._busy = busy
        if self._icon is None:
            return
        try:
            from PIL import Image
            path = self._busy_path if (busy and self._busy_path) else self._idle_path
            if path:
                self._icon.icon = Image.open(path)
        except Exception as e:
            print(f"[tray] 切图失败: {e}")

    def set_tooltip(self, text: str):
        """更新托盘提示文字（macOS 顶部菜单栏 hover）。"""
        if self._icon:
            try:
                self._icon.title = text
            except Exception:
                pass

    # ---------- 菜单回调（pystray 线程） ----------
    def _show(self, _icon, _item):
        self.app.after(0, self.on_show)

    def _show_from_click(self, _icon):
        self.app.after(0, self.on_show)

    def _quit(self, _icon, _item):
        self.app.after(0, self.on_quit)

    def _start_crawl(self, _icon, _item):
        if self.on_start_crawl:
            self.app.after(0, self.on_start_crawl)

    def _stop_crawl(self, _icon, _item):
        if self.on_stop_crawl:
            self.app.after(0, self.on_stop_crawl)

    def _toggle_autostart(self, _icon, _item):
        # 切换前先读 OS 真实状态（避免双重切换）
        if autostart.is_enabled():
            autostart.disable()
        else:
            autostart.enable()
        # 刷新菜单（让勾选状态立刻更新）
        if self._icon:
            try:
                self._icon.update_menu()
            except Exception:
                pass


# ---------- 自测 ----------
if __name__ == "__main__":
    print("tray 模块导入正常")
    print(f"idle 图标: {_resolve_asset('tray-idle.png') or '(missing)'}")
    print(f"busy 图标: {_resolve_asset('tray-busy.png') or '(missing)'}")
