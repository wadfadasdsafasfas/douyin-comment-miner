"""听潮 · 本地 sidecar API

桌面壳（pywebview / Tauri）与前端 UI 之间唯一的边界。只监听 127.0.0.1，
不对外网暴露；云端通信全部委托给既有的 license.License。

启动：python -m tingchao.local_api --port 0   （0 = 随机端口，由壳读取）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import queue
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tingchao import crawler, leads_db
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
