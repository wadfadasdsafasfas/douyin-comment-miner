"""用户偏好读写：tray 行为 + 通知开关 + 开机自启。

文件位置：
    开发态：<project>/desktop/prefs.json
    打包态：<exe 所在目录>/prefs.json（PyInstaller --add-data 注入到 sys._MEIPASS，
            运行时读写应放到用户可写位置 —— 这里简化为 exe 同级）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


# 默认值
DEFAULTS: dict[str, Any] = {
    # 通知
    "notify_hit":      True,   # 关键词命中提醒
    "notify_done":     True,   # 抓取完成汇总
    "notify_kicked":   True,   # 账号被踢下线
    "notify_update":   True,   # 新版本发布
    # 行为
    "tray_minimize":   True,   # 点 × 时最小化到托盘
    "autostart":       False,  # 开机自启（与 OS 注册项双向同步）
    # 一次性提示
    "tray_first_hide_tip_shown": False,
}


def _prefs_path() -> Path:
    """定位 prefs.json 写入位置。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包态：写到 exe 同级
        return Path(sys.executable).parent / "prefs.json"
    # 开发态：项目根 desktop/prefs.json
    return Path(__file__).resolve().parent / "prefs.json"


def load() -> dict[str, Any]:
    """读取偏好，缺失项用默认值补全，文件损坏则回退默认。"""
    p = _prefs_path()
    data: dict[str, Any] = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            # 损坏 → 删除 + 退回默认
            try:
                p.unlink()
            except Exception:
                pass
            data = {}
    merged = {**DEFAULTS, **data}
    return merged


def save(prefs: dict[str, Any]) -> bool:
    """原子写：先写临时文件再 rename，避免半写状态。"""
    p = _prefs_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="prefs_", suffix=".json", dir=p.parent)
        try:
            os.write(fd, json.dumps(prefs, ensure_ascii=False, indent=2).encode("utf-8"))
            os.close(fd)
            os.replace(tmp, p)
            return True
        except Exception:
            os.close(fd) if not os.get_inheritable(fd) else None  # noqa
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    except Exception as e:
        print(f"[prefs] 写入失败: {e}")
        return False


def set_value(key: str, value: Any) -> dict[str, Any]:
    """便捷：读取 → 改一个值 → 保存 → 返回最新 prefs。"""
    prefs = load()
    prefs[key] = value
    save(prefs)
    return prefs
