"""客户端授权模块 —— 登录 / 校验 / token 持久化。

token 存到用户配置目录：
    Windows: %APPDATA%\\DouyinCommentMiner\\auth.json
    macOS:   ~/Library/Application Support/DouyinCommentMiner/auth.json
    Linux:   ~/.config/DouyinCommentMiner/auth.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

try:
    from client.config import SERVER_URL as _CONFIG_SERVER
except Exception:
    _CONFIG_SERVER = None

# 服务端地址 —— 打包前改 client/config.py 中的 SERVER_URL；也可通过环境变量覆盖
SERVER_URL = (
    os.environ.get("DOUYIN_MINER_SERVER")
    or _CONFIG_SERVER
    or "http://localhost:8000"
).rstrip("/")


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


class LicenseError(Exception):
    pass


class License:
    def __init__(self):
        self.server_url = SERVER_URL
        self.token: str | None = None
        self.username: str | None = None
        self.expires_at: str | None = None
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if AUTH_FILE.exists():
            try:
                d = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
                self.token = d.get("token")
                self.username = d.get("username")
                self.expires_at = d.get("expires_at")
            except Exception:
                self.token = None

    def _save(self):
        AUTH_FILE.write_text(json.dumps({
            "token": self.token,
            "username": self.username,
            "expires_at": self.expires_at,
            "server": self.server_url,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self):
        if AUTH_FILE.exists():
            AUTH_FILE.unlink()
        self.token = self.username = self.expires_at = None

    # ---------- 网络 ----------
    def _post(self, path, **kw):
        r = requests.post(f"{self.server_url}{path}", timeout=10, **kw)
        return r

    def _get(self, path, **kw):
        r = requests.get(f"{self.server_url}{path}", timeout=10, **kw)
        return r

    # ---------- API ----------
    def login(self, username: str, password: str) -> tuple[bool, str]:
        try:
            r = self._post("/api/login", json={"username": username, "password": password})
        except requests.exceptions.RequestException as e:
            return False, f"连不上服务器 {self.server_url}：{e}"
        if r.status_code != 200:
            try:
                return False, r.json().get("detail", f"HTTP {r.status_code}")
            except Exception:
                return False, f"HTTP {r.status_code}"
        d = r.json()
        self.token = d["token"]
        self.username = d["username"]
        self.expires_at = d["expires_at"]
        self._save()
        return True, "登录成功"

    def verify(self) -> tuple[bool, str]:
        """校验当前 token 是否还有效（心跳用）。"""
        if not self.token:
            return False, "未登录"
        try:
            r = self._get("/api/verify", params={"token": self.token})
        except requests.exceptions.RequestException as e:
            return False, f"连不上服务器：{e}"
        if r.status_code != 200:
            try:
                msg = r.json().get("detail", f"HTTP {r.status_code}")
            except Exception:
                msg = f"HTTP {r.status_code}"
            # 403/401 → token 已失效，清掉
            if r.status_code in (401, 403):
                self.clear()
            return False, msg
        d = r.json()
        self.expires_at = d.get("expires_at")
        self._save()
        return True, "ok"

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
