"""听潮 · 本地 sidecar API

桌面壳（pywebview / Tauri）与前端 UI 之间唯一的边界。只监听 127.0.0.1，
不对外网暴露；云端通信全部委托给既有的 license.License。

启动：python -m tingchao.local_api --port 0   （0 = 随机端口，由壳读取）
"""
from __future__ import annotations
import platform

import argparse
import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tingchao import crawler, leads_db, updater
from license import License

WEB_DIR = Path(__file__).parent / "web"


async def _lifespan(_app: FastAPI):
    leads_db.init_db()
    yield


app = FastAPI(title="听潮 本地服务", version="1.0.0", lifespan=_lifespan)
lic = License()
mgr = crawler.CrawlManager()

# 本地 UI 与 sidecar 同源，但开发时可能用浏览器直连，放宽到 localhost
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- 模型

class LoginIn(BaseModel):
    username: str
    password: str
    kick_existing: bool = False


class CrawlIn(BaseModel):
    links: list[str]
    keywords: list[str]
    range: str = "全部"
    with_replies: bool = False
    max_comments: int = 3000
    platform: str = "douyin"
    headless: bool = False


class StatusIn(BaseModel):
    ids: list[int]
    status: str
    owner: str = ""
    note: str = ""


# ---------------------------------------------------------------- 基础

@app.get("/api/health")
def health():
    return {"ok": True, "service": "tingchao-sidecar",
            "version": lic.client_version(), "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/auth/me")
def auth_me():
    ok, msg, state = (lic.verify() if lic.token else (False, "未登录", "expired"))
    return {"logged_in": bool(lic.token) and ok, "state": state, "msg": msg,
            "username": lic.username, "expires_at": lic.expires_at,
            "days_left": lic.days_left(), "device_name": lic.device_name,
            "server_url": lic.server_url, "version": lic.client_version()}


class LoginResp(BaseModel):
    ok: bool
    msg: str
    conflict_device: str | None = None


@app.post("/api/auth/login", response_model=LoginResp)
def auth_login(body: LoginIn):
    ok, msg, existing = lic.login(body.username, body.password, body.kick_existing)
    if ok:
        mgr.bus.publish("auth", ok=True, username=lic.username)
    return LoginResp(ok=ok, msg=msg, conflict_device=existing)


@app.post("/api/auth/trial")
def auth_trial():
    info, err = lic.request_trial()
    if info is None:
        raise HTTPException(400, err)
    return {"ok": True, "trial": info}


@app.post("/api/auth/logout")
def auth_logout():
    lic.logout()
    return {"ok": True}


@app.get("/api/update/check")
def update_check():
    info, has, reason = lic.check_update()
    return {"info": info, "has_update": has, "reason": reason,
            "current": lic.client_version()}


# ---------------------------------------------------------------- 配置

@app.get("/api/config")
def config_get():
    return crawler.load_config()


@app.post("/api/config")
def config_save(patch: dict):
    return crawler.save_config(patch or {})


# ---------------------------------------------------------------- 采集

@app.post("/api/crawl/start")
def crawl_start(body: CrawlIn):
    ok, msg = mgr.start(body.links, body.keywords, range_spec=body.range,
                        with_replies=body.with_replies, max_comments=body.max_comments,
                        platform=body.platform, headless=body.headless)
    if ok:
        crawler.save_config({"links": body.links, "keywords": body.keywords,
                             "range": body.range, "with_replies": body.with_replies,
                             "max_comments": body.max_comments, "platform": body.platform})
    return {"ok": ok, "msg": msg, "state": mgr.state}


@app.post("/api/crawl/stop")
def crawl_stop():
    ok, msg = mgr.stop()
    return {"ok": ok, "msg": msg}


@app.get("/api/crawl/status")
def crawl_status():
    return mgr.snapshot()


@app.get("/api/crawl/events")
async def crawl_events(request: Request):
    """SSE：前端 EventSource 订阅，实时显示进度与命中。"""
    q = mgr.bus.subscribe()

    async def gen():
        try:
            yield f"data: {json.dumps({'type':'hello','job':mgr.snapshot()}, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.to_thread(_blocking_get, q, 15.0)
                except Exception:
                    break
                if evt is _PING:
                    yield ": ping\n\n"          # 保活，防代理断流
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            mgr.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_PING = object()


def _blocking_get(q: queue.Queue, timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return _PING


@app.get("/api/platforms")
def platforms():
    """平台采集器注册表（插件化入口：新增平台只需在此登记 + 实现采集器）。"""
    return {"items": [
        {"key": "douyin", "name": "抖音", "ready": True,
         "login": mgr.platform_status("douyin")},
        {"key": "xhs", "name": "小红书", "ready": False, "note": "采集中器待接入"},
        {"key": "wechat", "name": "视频号", "ready": False, "note": "采集中器待接入"},
    ]}


class PlatformLoginIn(BaseModel):
    platform: str = "douyin"


@app.post("/api/platforms/login")
def platform_login(body: PlatformLoginIn):
    ok, msg = mgr.platform_login(body.platform)
    return {"ok": ok, "msg": msg}


# ---------------------------------------------------------------- 线索池

@app.get("/api/leads")
def leads_list(platform: str = "all", status: str = "all", q: str = "",
               page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200)):
    return leads_db.list_leads(platform, status, q, page, size)


@app.post("/api/leads/status")
def leads_set_status(body: StatusIn):
    n = leads_db.set_status(body.ids, body.status, body.owner, body.note)
    return {"ok": True, "updated": n}


@app.get("/api/leads/export")
def leads_export(platform: str = "all", status: str = "all", q: str = "",
                 fmt: str = "xlsx"):
    out_dir = Path.home() / ".tingchao" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"线索名单_{datetime.now():%Y%m%d_%H%M%S}"
    if fmt == "csv":
        p = leads_db.export_csv(str(base) + ".csv", platform, status, q)
    else:
        p = leads_db.export_xlsx(str(base) + ".xlsx", platform, status, q) or \
            leads_db.export_csv(str(base) + ".csv", platform, status, q)
    return FileResponse(p, filename=Path(p).name)


@app.get("/api/stats")
def stats():
    return {"leads": leads_db.stats(), "crawl": mgr.snapshot()}


@app.get("/api/tasks")
def tasks(limit: int = 10):
    return {"items": leads_db.recent_tasks(limit)}


# ---------------------------------------------------------------- 自升级

UPD = {"stage": "idle", "progress": 0, "error": "", "zip": "", "info": None}
UPDATE_DIR = Path.home() / ".tingchao" / "updates"


def _update_url(info: dict) -> str:
    """后台只存一条 macos_url，但 mac 有 arm64 / x64 两种包；按本机架构纠正后缀。"""
    key = "macos_url" if sys.platform == "darwin" else "windows_url"
    url = (info or {}).get(key) or ""
    if sys.platform != "darwin" or not url:
        return url
    arch = "x64" if platform.machine().lower() in ("x86_64", "amd64") else "arm64"
    other = "arm64" if arch == "x64" else "x64"
    if f"-{other}" in url:
        url = url.replace(f"-{other}", f"-{arch}")
    elif "-universal" not in url and f"-{arch}" not in url:
        # URL 里没带架构标记时，插一个，保证 Intel / Apple Silicon 各取所需
        url = url.replace(".zip", f"-{arch}.zip").replace(".dmg", f"-{arch}.dmg")
    return url


@app.get("/api/update/info")
def update_info():
    info, has, reason = lic.check_update()
    UPD["info"] = info
    url = _update_url(info or {})
    return {"has_update": bool(has and url), "reason": reason, "current": lic.client_version(),
            "latest": (info or {}).get("version", ""), "notes": (info or {}).get("release_notes", ""),
            "url": url, "size_mb": (info or {}).get("size_mb", 0)}


class UpdateInstallIn(BaseModel):
    app_root: str = ""
    host_pid: int = 0


@app.post("/api/update/download")
def update_download():
    info, has, _ = lic.check_update()
    url = _update_url(info or {})
    if not url:
        raise HTTPException(400, "服务端未配置本平台的更新包地址")
    if UPD["stage"] == "downloading":
        return {"ok": True, "msg": "已在下载中"}

    UPD.update(stage="downloading", progress=0, error="", zip="")

    def cb(done, total):
        UPD["progress"] = round(done * 100 / total) if total else 0

    def worker():
        try:
            p = updater.download(url, UPDATE_DIR, cb)
            ok, layout, err = updater.verify_zip(p)
            if not ok:
                UPD.update(stage="error", error=err)
                return
            UPD.update(stage="ready", progress=100, zip=str(p))
        except Exception as e:
            UPD.update(stage="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "msg": "开始下载"}


@app.get("/api/update/state")
def update_state():
    return {k: v for k, v in UPD.items() if k != "info"}


@app.post("/api/update/install")
def update_install(body: UpdateInstallIn):
    """拉起分离子进程执行替换，随后由壳退出、更新器重启新版。"""
    if UPD["stage"] != "ready" or not UPD["zip"]:
        raise HTTPException(400, "更新包尚未下载完成")
    app_root = body.app_root or os.environ.get("TC_APP_ROOT", "")
    if not app_root or not Path(app_root).exists():
        raise HTTPException(400, "无法定位应用安装目录，请手动覆盖安装")
    me = sys.executable
    args = [me, "--apply-update", UPD["zip"], app_root]
    if body.host_pid:
        args += ["--wait-pid", str(body.host_pid)]
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=(sys.platform != "win32"))
    UPD["stage"] = "installing"
    return {"ok": True, "msg": "正在安装，应用即将重启"}


# ---------------------------------------------------------------- 静态 UI
# 前端资源走 /assets/*（Electron 与浏览器调试同源加载，免 CORS）；
# index.html 由 "/" 直接返回，pywebview/浏览器/Electron 都只认这一个入口。
app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")


@app.get("/")
@app.get("/index.html")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/favicon.svg")
def favicon():
    f = WEB_DIR / "logo" / "favicon.svg"
    if f.exists():
        return FileResponse(f)
    raise HTTPException(status_code=404, detail="no icon")


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    # 更新器模式：<sidecar> --apply-update <zip> <app_root> [--wait-pid N]
    if "--apply-update" in sys.argv:
        raise SystemExit(updater.run_cli(sys.argv[1:]))

    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="0 = 自动选空闲端口")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    port = a.port or _free_port()
    # Electron 主进程按行解析这一行拿到端口，勿改格式
    print(f"SIDECAR_PORT={port}", flush=True)
    uvicorn.run(app, host=a.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
