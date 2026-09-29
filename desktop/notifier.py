"""系统通知封装（plyer + 窗口内 Toast fallback）。

支持 4 种触发：
    - hit      关键词命中（默认累计 ≤5 条逐条发，之后改 1 条汇总）
    - done     抓取完成
    - kicked   账号被踢下线
    - update   新版本发布

用法：
    notifier = Notifier(prefs)
    notifier.notify("hit", title="命中关键词", body="多少钱 · 评论 by 用户A")
    notifier.notify_hit_throttled(count=1)   # 自动节流
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from desktop import prefs


def _resolve_icon() -> str:
    """定位通知图标（开发态 vs 打包态）。"""
    candidates = [
        Path(__file__).resolve().parent / "assets" / "notify-icon.png",
        # PyInstaller 打包态：sys._MEIPASS/desktop/assets/...
        Path(getattr(sys, "_MEIPASS", "")) / "desktop" / "assets" / "notify-icon.png",
    ]
    for p in candidates:
        if p and p.exists():
            return str(p)
    return ""


class Notifier:
    def __init__(self, prefs_dict: dict | None = None):
        self.prefs = prefs_dict if prefs_dict is not None else prefs.load()
        self.icon_path = _resolve_icon()
        # hit 节流状态：单次抓取内累计
        self._hit_count_in_run = 0

    # ---------- 公开 API ----------
    def notify(self, kind: str, title: str, body: str, timeout: int = 5) -> bool:
        """根据 prefs 决定是否发通知。kind ∈ {hit, done, kicked, update}"""
        pref_key = f"notify_{kind}"
        if not self.prefs.get(pref_key, True):
            return False
        return self._send(title, body, timeout)

    def notify_hit_throttled(self, title: str = "命中关键词", body: str = "") -> bool:
        """命中节流：累计前 5 条逐条发，之后改 1 条汇总。"""
        self._hit_count_in_run += 1
        if self._hit_count_in_run <= 5:
            return self.notify("hit", title=title, body=body)
        # 后续不再逐条发
        return False

    def reset_hit_counter(self):
        self._hit_count_in_run = 0

    def flush_hit_summary(self, total: int) -> bool:
        """抓取结束时，如果本次累计 >5 条，发一条汇总通知。"""
        if self._hit_count_in_run > 5:
            return self.notify("hit", title="命中汇总", body=f"本次抓取累计命中 {self._hit_count_in_run} 条关键词（{total} 条评论）")
        return False

    # ---------- 底层 ----------
    def _send(self, title: str, body: str, timeout: int) -> bool:
        """直接走平台原生通知（更稳定，零额外依赖）。

        - macOS: osascript (Notification Center)
        - Windows: PowerShell + System.Windows.Forms.NotifyIcon
        - Linux: notify-send
        """
        return self._send_native(title, body, timeout)

    def _send_native(self, title: str, body: str, timeout: int) -> bool:
        import subprocess
        try:
            if sys.platform == "darwin":
                # AppleScript 转义双引号
                t = title.replace('"', "'")
                b = body.replace('"', "'").replace("\n", " ")
                script = f'display notification "{b}" with title "{t}" subtitle "听潮 · 抖音评论名单挖掘"'
                subprocess.Popen(["osascript", "-e", script])
                return True
            if sys.platform == "win32":
                t = title.replace('"', "'").replace("'", "''")
                b = body.replace('"', "'").replace("'", "''").replace("\n", " ")
                # PowerShell 弹气泡通知，5s 自动消失
                ps = (
                    "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null;"
                    "[reflection.assembly]::loadwithpartialname('System.Drawing') | Out-Null;"
                    "$n = New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon = [System.Drawing.SystemIcons]::Information;"
                    "$n.Visible = $true;"
                    f"$n.ShowBalloonTip({int(timeout)*1000}, '{t}', '{b}', [System.Windows.Forms.ToolTipIcon]::Info);"
                    f"Start-Sleep -Milliseconds {int(timeout)*1000}; $n.Dispose()"
                )
                # 用 STA 模式启动 powershell 窗口进程
                flags = 0x00000010  # CREATE_NO_WINDOW
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-STA", "-Command", ps],
                    creationflags=flags,
                )
                return True
            if sys.platform.startswith("linux"):
                subprocess.Popen(["notify-send", "-t", str(int(timeout * 1000)), title, body])
                return True
        except Exception as e:
            print(f"[notifier] native notify 失败: {e}")
        return False


# ---------- 自测 ----------
if __name__ == "__main__":
    n = Notifier()
    print(f"图标路径: {n.icon_path or '(未找到)'}")
    print(f"hit 开关: {n.prefs.get('notify_hit')}")
    print(f"发送测试通知...")
    n.notify("update", title="听潮 · 测试", body="如果你看到这条，通知通道通了。")
