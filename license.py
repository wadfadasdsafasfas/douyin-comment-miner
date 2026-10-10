"""客户端授权模块 —— 登录 / 校验 / token 持久化。

token 存到用户配置目录：
    Windows: %APPDATA%\\DouyinCommentMiner\\auth.json
    macOS:   ~/Library/Application Support/DouyinCommentMiner/auth.json
    Linux:   ~/.config/DouyinCommentMiner/auth.json
"""

from __future__ import annotations

import json
import os
import platform
import sys
import uuid
from datetime import datetime
from pathlib import Path

import requests

try:
    from server_url import SERVER_URL as _CONFIG_SERVER
except Exception:
    _CONFIG_SERVER = None

# 客户端版本号 —— 每次发版手工 bump（与 Git tag 一致）
__version__ = "1.0.5"

# 服务端地址 —— 打包前改 server_url.py 中的 SERVER_URL；也可通过环境变量覆盖
SERVER_URL = (
    os.environ.get("DOUYIN_MINER_SERVER")
    or _CONFIG_SERVER
    or "http://localhost:8000"
).rstrip("/")


# ---------- 版本号工具 ----------
def _ver_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().split(".") if x.isdigit())
    except Exception:
        return (0,)


def _ver_lt(a: str, b: str) -> bool:
    return _ver_tuple(a) < _ver_tuple(b)


def _config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    d = base / "DouyinCommentMiner"
    d.mkdir(parents=True, exist_ok=True)
    return d


AUTH_FILE = _config_dir() / "auth.json"


def _make_device_name() -> str:
    """生成一个简短可读的设备名，用于「已在另一台设备登录」提示。"""
    sysname = platform.system() or "Unknown"
    node = platform.node() or ""
    # Mac 上 node 通常是 "MacBook-Pro.local" / "Wei-MBP" 这种
    name = f"{sysname}-{node}".replace(".local", "").strip("-")
    if not name or name == sysname:
        name = f"{sysname}-device"
    return name[:40]


class LicenseError(Exception):
    pass


class License:
    def __init__(self):
        self.server_url = SERVER_URL
        self.token: str | None = None
        self.username: str | None = None
        self.expires_at: str | None = None
        self.device_id: str = ""
        self.device_name: str = ""
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if AUTH_FILE.exists():
            try:
                d = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
                self.token = d.get("token")
                self.username = d.get("username")
                self.expires_at = d.get("expires_at")
                self.device_id = d.get("device_id") or ""
                self.device_name = d.get("device_name") or ""
            except Exception:
                self.token = None
        # 首次启动：生成稳定的设备标识并写回
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            self.device_name = _make_device_name()
            self._save()

    def _save(self):
        AUTH_FILE.write_text(json.dumps({
            "token": self.token,
            "username": self.username,
            "expires_at": self.expires_at,
            "server": self.server_url,
            "device_id": self.device_id,
            "device_name": self.device_name,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self):
        if AUTH_FILE.exists():
            AUTH_FILE.unlink()
        self.token = self.username = self.expires_at = None
        # device_id 不清，下次启动还是同一台设备

    # ---------- 网络 ----------
    def _post(self, path, **kw):
        kw.setdefault("timeout", 10)
        return requests.post(f"{self.server_url}{path}", **kw)

    def _get(self, path, **kw):
        kw.setdefault("timeout", 10)
        return requests.get(f"{self.server_url}{path}", **kw)

    # ---------- API ----------
    def login(self, username: str, password: str, kick_existing: bool = False
              ) -> tuple[bool, str, str | None]:
        """返回 (ok, msg, existing_device)。
        msg == "DEVICE_CONFLICT" 时 existing_device 是已登录的设备名。"""
        try:
            r = self._post("/api/login", json={
                "username": username,
                "password": password,
                "client_version": __version__,
                "device_id": self.device_id,
                "device_name": self.device_name,
                "kick_existing": kick_existing,
            })
        except requests.exceptions.RequestException as e:
            return False, f"连不上服务器 {self.server_url}：{e}", None
        if r.status_code == 409:
            # 单设备冲突
            existing = "未知设备"
            try:
                detail = r.json().get("detail")
                if isinstance(detail, dict):
                    existing = detail.get("existing_device", existing)
            except Exception:
                pass
            return False, "DEVICE_CONFLICT", existing
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
                if isinstance(detail, dict):
                    return False, detail.get("message", f"HTTP {r.status_code}"), None
                return False, str(detail) or f"HTTP {r.status_code}", None
            except Exception:
                return False, f"HTTP {r.status_code}", None
        d = r.json()
        self.token = d["token"]
        self.username = d["username"]
        self.expires_at = d["expires_at"]
        self._save()
        warn = d.get("warn")
        return True, warn if warn else "登录成功", None

    def verify(self) -> tuple[bool, str, str]:
        """校验当前 token；返回 (ok, msg, state)。
        state: ok / kicked / expired / invalid / disabled / network_error
        'kicked' 表示被另一台设备顶掉（前端要强提示并回登录页）。"""
        if not self.token:
            return False, "未登录", "expired"
        try:
            r = self._get("/api/verify", params={
                "token": self.token,
                "device_id": self.device_id,
            })
        except requests.exceptions.RequestException as e:
            return False, f"连不上服务器：{e}", "network_error"
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = None
            # 优先识别 DEVICE_KICKED
            if isinstance(detail, dict) and detail.get("code") == "DEVICE_KICKED":
                self.clear()
                return False, detail.get("message", "账号已被另一台设备登录"), "kicked"
            if isinstance(detail, dict):
                msg = detail.get("message", f"HTTP {r.status_code}")
            elif isinstance(detail, str):
                msg = detail
            else:
                msg = f"HTTP {r.status_code}"
            # 401/403 → token 失效
            if r.status_code in (401, 403):
                self.clear()
                return False, msg, "invalid"
            return False, msg, "invalid"
        d = r.json()
        self.expires_at = d.get("expires_at")
        self._save()
        return True, "ok", "ok"

    def change_password(self, old_pw: str, new_pw: str) -> tuple[bool, str]:
        """客户端改自己密码（可选功能，需要后端支持，先做存根）。"""
        return False, "请到管理后台修改密码"

    def logout(self):
        if self.token:
            try:
                self._post("/api/logout", headers={"X-Token": self.token})
            except Exception:
                pass
        self.clear()

    # ---------- 试用账号 ----------
    def request_trial(self) -> tuple[dict | None, str]:
        """调用 /api/trial，返回 (info, error_msg)。"""
        try:
            r = self._post("/api/trial", timeout=10)
        except requests.exceptions.RequestException as e:
            return None, f"连不上服务器 {self.server_url}：{e}"
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
                if isinstance(detail, dict):
                    return None, detail.get("message", f"HTTP {r.status_code}")
                return None, str(detail) or f"HTTP {r.status_code}"
            except Exception:
                return None, f"HTTP {r.status_code}"
        return r.json(), "ok"

    # ---------- 自动升级 ----------
    def check_update(self) -> tuple[dict | None, bool, str]:
        """调用 /api/latest，返回 (info, has_update, reason)
           info    : 服务端返回的 dict；连不上时为 None
           has_update : True = 有新版本
           reason  : "ok" / "network_error" / "newer" / "force"
        """
        try:
            r = self._get("/api/latest", timeout=5)
        except requests.exceptions.RequestException:
            return None, False, "network_error"
        if r.status_code != 200:
            return None, False, f"http_{r.status_code}"
        info = r.json()
        latest = info.get("version", "0.0.0")
        if _ver_lt(__version__, latest):
            return info, True, "ok"
        return info, False, "ok"

    def client_version(self) -> str:
        return __version__

    # ---------- 显示 ----------
    def expiry_date(self) -> str:
        if not self.expires_at:
            return "—"
        try:
            return self.expires_at[:10]
        except Exception:
            return self.expires_at

    def days_left(self) -> int | None:
        if not self.expires_at:
            return None
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return (exp.date() - datetime.now().date()).days
        except Exception:
            return None