"""听潮 · 采集服务层

把引擎的 douyin_miner.run_batch（回调式）包成一个可被 HTTP/SSE 消费的 Job：
  · 后台线程跑 Playwright，主进程不阻塞
  · 事件总线广播 progress / hit / status / done / error / need_login
  · 每条命中实时写入本地线索池（跨任务自动去重）
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import douyin_miner as eng
from tingchao import leads_db

CONFIG_PATH = Path.home() / ".tingchao" / "config.json"
# 已接入的采集器；新增平台时在此登记并实现对应采集逻辑
READY_PLATFORMS = {"douyin"}
PLATFORM_CN = {"douyin": "抖音", "xhs": "小红书", "wechat": "视频号"}
DEFAULT_CONFIG = {
    "links": [], "keywords": ["多少钱", "怎么联系", "私信"],
    "range": "全部", "with_replies": False, "max_comments": 3000,
    "platform": "douyin", "headless": False,
}


# ------------------------------------------------------------------ 配置

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(patch: dict) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in patch.items() if k in DEFAULT_CONFIG})
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


# ------------------------------------------------------------------ 事件总线

class EventBus:
    """每个 SSE 连接一个队列；publish 非阻塞，队列满则丢弃最旧事件。"""

    def __init__(self, maxlen: int = 200):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._maxlen = maxlen

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxlen)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, type_: str, **data) -> None:
        evt = {"id": int(time.time() * 1000), "type": type_, **data}
        with self._lock:
            for q in list(self._subs):
                try:
                    q.put_nowait(evt)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(evt)
                    except Exception:
                        pass


# ------------------------------------------------------------------ 时间范围
# UI 传的是中文标签，而引擎 parse_range_spec() 只认 '24h'/'7d'/'30w' 这类紧凑写法，
# 且返回单个 datetime 或 None（不是 (since, until) 元组）。这里做一层转换，
# 否则要么崩（解包 None），要么时间筛选静默失效。
_RANGE_ALIAS = {
    "全部": None, "不限": None, "": None,
    "最近 24 小时": "24h", "最近 1 天": "1d", "昨天": "1d",
    "最近 7 天": "7d", "最近 30 天": "30d", "最近 90 天": "90d",
}


def resolve_range(spec: str):
    """把 UI 的时间范围标签解析为 (since, until)，供引擎使用。"""
    key = (spec or "").strip()
    compact = _RANGE_ALIAS.get(key, key.lower() if key else None)
    if not compact:
        return None, None
    since = eng.parse_range_spec(compact)
    if since is None and key:
        # 认不出来的写法：不静默忽略，退化成"全部"并在日志里提示
        pass
    return since, None


# ------------------------------------------------------------------ 采集任务

class CrawlManager:
    def __init__(self):
        self.bus = EventBus()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = "idle"          # idle / running / stopping
        self.job: dict = {}

    # -- 状态
    def snapshot(self) -> dict:
        return {"state": self.state, **self.job}

    def is_busy(self) -> bool:
        return self.state in ("running", "stopping")

    # -- 启动
    def start(self, links: list[str], keywords: list[str], *, range_spec: str = "全部",
              with_replies: bool = False, max_comments: int = 3000,
              platform: str = "douyin", headless: bool = False) -> tuple[bool, str]:
        if self.is_busy():
            return False, "已有任务在跑，请先停止"
        # 单次任务只允许一个平台，且只放行已接入的采集器
        if platform not in READY_PLATFORMS:
            return False, f"「{PLATFORM_CN.get(platform, platform)}」暂未开放，目前仅支持抖音"
        raw_lines = [l for l in (links or []) if l and l.strip()]
        if not raw_lines:
            return False, "请先填写至少一个视频/笔记链接"
        # 抖音「分享」复制出来的是整段文案（前面带口令乱码、后面带提示语），
        # 必须先像旧版 GUI 那样用 extract_links 抠出真实 URL，
        # 否则 page.goto(整段文案) 打不开 → 浏览器一闪而过、抓到 0 条。
        found_all, unparsed = [], []
        for line in raw_lines:
            got = eng.extract_links(line) or []
            (found_all if got else unparsed).extend(got or [line])
        links = list(dict.fromkeys(u.strip() for u in found_all if u.strip()))
        if not links:
            return False, "没识别到有效链接，请粘贴抖音分享链接或整段分享文案（每行一个）"
        if not keywords:
            return False, "请至少设置一个命中关键词"

        since, until = resolve_range(range_spec)
        self._stop.clear()
        self.job = {
            "job_id": uuid.uuid4().hex[:8], "platform": platform,
            "links": links, "keywords": keywords, "range": range_spec,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "collected": 0, "hits": 0, "new_leads": 0, "csv": None, "xlsx": None,
            "error": "",
        }
        self.state = "running"
        self.job["task_id"] = leads_db.task_start(links, keywords)
        self.bus.publish("start", job=self.job)
        if unparsed:
            self.bus.publish("log", msg=f"已忽略 {len(unparsed)} 行未含链接的文本：" + (unparsed[0][:24] + "…"))
        if len(links) < len(raw_lines):
            self.bus.publish("log", msg=f"从 {len(raw_lines)} 行文本中提取到 {len(links)} 个有效链接")
        self._thread = threading.Thread(
            target=self._worker,
            args=(links, keywords, since, until, with_replies, max_comments, platform, headless),
            daemon=True)
        self._thread.start()
        return True, "已开始抓取"

    def stop(self) -> tuple[bool, str]:
        if not self.is_busy():
            return False, "当前没有正在执行的任务"
        self.state = "stopping"
        self._stop.set()
        self.bus.publish("status", msg="正在停止…")
        return True, "已请求停止"

    # -- 工作线程
    def _worker(self, links, keywords, since, until, with_replies, max_comments,
                platform, headless):
        job = self.job

        def on_progress(n):
            job["collected"] = n
            self.bus.publish("progress", collected=n, hits=job["hits"])

        def on_hit(row):
            text = f"{row.get('昵称','')}\n{row.get('评论内容','')}"
            hit_kws = [k for k in keywords if k and k in text]
            is_new = leads_db.upsert_lead(platform, row, hit_kws,
                                          video_url=row.get("视频ID", ""))
            job["hits"] += 1
            if is_new:
                job["new_leads"] += 1
            self.bus.publish("hit", collected=job["collected"], hits=job["hits"],
                             new_leads=job["new_leads"], is_new=is_new,
                             lead={"platform": platform,
                                   "video_id": row.get("视频ID", ""),
                                   "nickname": row.get("昵称", ""),
                                   "comment": row.get("评论内容", ""),
                                   "comment_time": row.get("评论时间", ""),
                                   "keywords": ",".join(hit_kws),
                                   "profile_url": row.get("主页链接", "")})

        def on_login_required():
            self.bus.publish("need_login", msg="未检测到平台登录态，请先在「平台账号」登录")

        def log(msg):
            if msg:
                self.bus.publish("log", msg=str(msg).strip())

        out = str(Path.home() / ".tingchao" / "exports" /
                  f"名单_{datetime.now():%Y%m%d_%H%M%S}.csv")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        status, err = "done", ""
        try:
            rows, csv_path, xlsx_path = eng.run_batch(
                links, keywords, out=out, with_replies=with_replies,
                max_comments=max_comments, headless=headless, log=log,
                on_progress=on_progress, since=since, until=until,
                stop_event=self._stop, on_hit=on_hit,
                on_login_required=on_login_required)
            job["csv"], job["xlsx"] = csv_path, xlsx_path
            if csv_path is None and not rows:
                status = "stopped" if self._stop.is_set() else status
        except Exception as e:                       # 引擎异常不外泄，转成事件
            status, err = "error", f"{type(e).__name__}: {e}"
            self.bus.publish("error", msg=err)
        finally:
            job["error"] = err
            self.state = "idle"
            leads_db.task_finish(job["task_id"], status, job["collected"], job["hits"], err)
            self.bus.publish("done", status=status, collected=job["collected"],
                             hits=job["hits"], new_leads=job["new_leads"],
                             csv=job.get("csv"), xlsx=job.get("xlsx"), error=err)

    # -- 平台登录（打开浏览器扫码，后台线程执行）
    def platform_login(self, platform: str = "douyin") -> tuple[bool, str]:
        if platform != "douyin":
            return False, f"{platform} 采集器尚未接入（插件化改造后即可支持）"
        threading.Thread(target=self._login_worker, args=(platform,), daemon=True).start()
        return True, "已打开登录窗口，请扫码"

    def _login_worker(self, platform: str):
        self.bus.publish("status", msg="等待扫码登录…")
        try:
            ok = eng.login_interactive(log=lambda m: self.bus.publish("log", msg=str(m)))
            self.bus.publish("login_result", ok=bool(ok),
                             msg="登录成功，登录态已保存" if ok else "登录未完成")
        except Exception as e:
            self.bus.publish("login_result", ok=False, msg=f"登录出错：{e}")

    def platform_status(self, platform: str = "douyin") -> dict:
        if platform != "douyin":
            return {"platform": platform, "logged_in": False, "note": "尚未接入"}
        try:
            logged = bool(eng.check_login(log=None))
        except Exception:
            logged = False
        return {"platform": platform, "logged_in": logged,
                "profile_dir": getattr(eng, "PROFILE_DIR", "")}
