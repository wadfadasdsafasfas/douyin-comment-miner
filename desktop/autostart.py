"""跨平台开机自启动 helper。

- macOS：写 ~/Library/LaunchAgents/com.douyinminer.tidesignal.plist
- Windows：写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run（无需管理员）
- Linux：写 ~/.config/autostart/douyinminer.desktop

所有实现都是「当前用户」范围，不需要 sudo / 管理员权限。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TideSignalDouyinMiner"
APP_DISPLAY = "听潮 · 抖音评论名单挖掘"
BUNDLE_ID = "com.douyinminer.tidesignal"


# ---------- 路径解析 ----------
def _exe_path() -> str:
    """返回自启动 LaunchAgent / Registry Run 应调用的可执行命令。

    - 打包态：sys.executable（.app/Contents/MacOS/xxx 或 xxx.exe）
    - 开发态：python -m douyin_miner_gui（在项目根跑）
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    # 开发态：假设项目根是 douyin_miner_gui.py 所在目录
    proj_root = Path(__file__).resolve().parent.parent
    py = sys.executable
    return f'"{py}" -m douyin_miner_gui --autostart'


# ---------- macOS LaunchAgent ----------
def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _mac_enable() -> bool:
    plist = _mac_plist()
    plist.parent.mkdir(parents=True, exist_ok=True)
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{BUNDLE_ID}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_exe_path().split('"')[1]}</string>
        <string>--autostart</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""
    try:
        plist.write_text(body, encoding="utf-8")
        # 加载到 launchd
        os.system(f'launchctl load -w "{plist}" >/dev/null 2>&1')
        return True
    except Exception as e:
        print(f"[autostart] macOS enable 失败: {e}")
        return False


def _mac_disable() -> bool:
    plist = _mac_plist()
    if not plist.exists():
        return True
    try:
        os.system(f'launchctl unload "{plist}" >/dev/null 2>&1')
        plist.unlink()
        return True
    except Exception as e:
        print(f"[autostart] macOS disable 失败: {e}")
        return False


def _mac_is_enabled() -> bool:
    return _mac_plist().exists()


# ---------- Windows 注册表 ----------
def _win_enable() -> bool:
    try:
        import winreg  # type: ignore
        cmd = _exe_path()
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[autostart] Windows enable 失败: {e}")
        return False


def _win_disable() -> bool:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[autostart] Windows disable 失败: {e}")
        return False


def _win_is_enabled() -> bool:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False


# ---------- Linux .desktop ----------
def _linux_desktop() -> Path:
    return Path.home() / ".config" / "autostart" / "douyinminer.desktop"


def _linux_enable() -> bool:
    p = _linux_desktop()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = f"""[Desktop Entry]
Type=Application
Name={APP_DISPLAY}
Exec={_exe_path()}
X-GNOME-Autostart-enabled=true
"""
    try:
        p.write_text(body, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[autostart] Linux enable 失败: {e}")
        return False


def _linux_disable() -> bool:
    p = _linux_desktop()
    if not p.exists():
        return True
    try:
        p.unlink()
        return True
    except Exception as e:
        print(f"[autostart] Linux disable 失败: {e}")
        return False


def _linux_is_enabled() -> bool:
    return _linux_desktop().exists()


# ---------- 公开 API ----------
def enable() -> bool:
    if sys.platform == "darwin":
        return _mac_enable()
    if sys.platform == "win32":
        return _win_enable()
    if sys.platform.startswith("linux"):
        return _linux_enable()
    print(f"[autostart] 未知平台 {sys.platform}，跳过")
    return False


def disable() -> bool:
    if sys.platform == "darwin":
        return _mac_disable()
    if sys.platform == "win32":
        return _win_disable()
    if sys.platform.startswith("linux"):
        return _linux_disable()
    return False


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_is_enabled()
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform.startswith("linux"):
        return _linux_is_enabled()
    return False


def toggle() -> bool:
    """切换自启状态，返回切换后状态。"""
    if is_enabled():
        disable()
        return False
    enable()
    return True


# ---------- 自测 ----------
if __name__ == "__main__":
    print(f"平台: {sys.platform}")
    print(f"当前状态: {'已启用' if is_enabled() else '未启用'}")
    print(f"执行命令: {_exe_path()}")
