#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音评论关键词名单挖掘 —— 核心引擎 + 命令行入口
------------------------------------------------
既能被 douyin_miner_gui.py（图形界面）当库调用，也能命令行直接用。

对外主要接口：
  open_login_window(log=...)          打开浏览器让用户登录，关窗后自动保存登录态
  run_batch(links, keywords, out, ...) 抓取 + 导出，返回 (rows, csv_path, xlsx_path)

命令行示例见文件底部 __main__。只做「导名单 / 看反馈」，不做私信 / 批量触达。
"""

import argparse
import csv
import os
import random
import re
import sys
import threading
import time
from datetime import datetime, timedelta

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("未安装 playwright，请先执行:  python3 -m pip install playwright")


# ---------- 页面内执行：提取评论项 (昵称 / 主页 / 正文 / 发布时间) ----------
GRAB_JS = r"""
() => {
  const out = [];
  const TIME_RE = /(刚刚|秒前|分钟前|小时前|天前|周前|个月前|年前|今天|昨天|前天|^\d{4}[-/]\d{1,2}[-/]\d{1,2})/;
  document.querySelectorAll('[data-e2e="comment-item"]').forEach(it => {
    const a = it.querySelector('a[href*="/user/"]');
    if (!a) return;
    const name = (a.innerText || '').trim();
    const profile = a.href;                       // 已是绝对地址
    const spans = [...it.querySelectorAll('span')]
      .filter(s => !a.contains(s) && s.childElementCount === 0);
    let content = '';
    let time = '';
    for (const s of spans) {
      const t = (s.innerText || '').trim();
      if (!t || t === '...' || t === '…') continue;
      if (TIME_RE.test(t)) { if (!time) time = t; continue; }        // 发布时间（含地区后缀，稍后切）
      if (/^[\d.]+(万|w|k)?$/i.test(t)) continue;                    // 点赞数
      if (['分享', '回复', '复制'].includes(t)) continue;
      if (/^展开?\d+条回复|^收起/.test(t)) continue;
      if (!content) content = t;
    }
    if (!content && !name) return;
    out.push({ name, profile, content, time });
  });
  return out;
}
"""

# ---------- 页面内执行：把最后一条评论滚进视野，触发懒加载 ----------
SCROLL_JS = r"""
() => {
  const items = document.querySelectorAll('[data-e2e="comment-item"]');
  if (items.length) items[items.length - 1].scrollIntoView({ block: 'center' });
  return items.length;
}
"""

# ---------- 页面内执行：是否已到底 ----------
NO_MORE_JS = r"""
() => /暂时没有更多评论|到底了|没有更多评论/.test(document.body.innerText)
"""

# ---------- 页面内执行：展开可见的「展开N条回复」按钮 ----------
EXPAND_JS = r"""
() => {
  let n = 0;
  document.querySelectorAll('[data-e2e="video-comment-more"]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.top > 0 && r.bottom < innerHeight) { b.click(); n++; }
  });
  return n;
}
"""

URL_RE = re.compile(r"https?://[^\s，,、]+", re.I)
FIELDS = ["视频ID", "昵称", "评论内容", "评论时间", "主页链接", "私信入口(主页点私信)"]
PROFILE_DIR = os.path.expanduser("~/.douyin_miner_profile")


# ---------- 通用工具 ----------
def extract_links(raw: str):
    """从任意文本（含抖音分享乱码）里抠出 http(s) 链接。"""
    return URL_RE.findall(raw)


def parse_video_id(url: str):
    m = re.search(r"/video/(\d+)", url) or re.search(r"modal_id=(\d+)", url)
    return m.group(1) if m else "unknown"


def launch_persistent(p, headless=False, log=None):
    """优先用系统 Chrome，退回 Edge，再退回 Playwright 自带内核。"""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    last_err = None
    for ch in ("chrome", "msedge", None):
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                channel=ch,
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
            )
            if log:
                log(f"已启动浏览器内核: {ch or 'bundled chromium'}")
            return ctx
        except Exception as e:  # 该内核不存在，试下一个
            last_err = e
            continue
    raise last_err


def wait_logged_in(page, timeout=240, log=None, stop_event=None):
    """等待评论区出现（= 已登录）。若需要登录则提示一次。"""
    deadline = time.time() + timeout
    prompted = False
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            if page.query_selector('[data-e2e="comment-list"]') is not None:
                return True
            if (page.query_selector('[data-e2e="login-card"]')
                    or page.query_selector('[data-e2e="login-button"]')
                    or "login" in page.url):
                if not prompted and log:
                    log("[需要登录] 请在弹出的浏览器窗口里扫码登录抖音，登录后会自动继续……")
                    prompted = True
        except Exception:
            pass
        time.sleep(2)
    return False


def harvest_comments(page, max_comments=3000, stable_limit=12, max_rounds=300,
                     with_replies=False, log=None, on_progress=None, stop_event=None,
                     on_new=None):
    """滚动 + 收集，返回 {key: {name, profile, content, time}} 去重字典。
    on_new(new_items) 每轮把【本轮新增】的评论回调出去，供上层边抓边显示。"""
    collected = {}
    last_count = -1
    stable = 0
    for _ in range(max_rounds):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            items = page.evaluate(GRAB_JS)
        except Exception:
            items = []
        new_items = []
        for c in items:
            key = c["profile"] + "|" + c["content"]
            if key not in collected:
                new_items.append(c)
            collected[key] = c
        if on_new and new_items:
            try:
                on_new(new_items)
            except Exception:
                pass
        if with_replies:
            try:
                page.evaluate(EXPAND_JS)
            except Exception:
                pass
        try:
            page.evaluate(SCROLL_JS)
        except Exception:
            pass
        n = len(collected)
        if n == last_count:
            stable += 1
        else:
            stable = 0
            last_count = n
        if on_progress:
            on_progress(n)
        if (stable >= stable_limit) or n >= max_comments:
            break
        try:
            if page.evaluate(NO_MORE_JS):
                break
        except Exception:
            pass
        time.sleep(random.uniform(0.8, 1.6))
    return collected


def match_keywords(comments, keywords):
    hits = []
    for c in comments.values():
        text = (c.get("content", "") or "") + "\n" + (c.get("name", "") or "")
        if any(k and k in text for k in keywords):
            hits.append(c)
    return hits


def parse_comment_time(text, now):
    """把抖音相对/绝对发布时间文本解析成 datetime（近似）。无法识别返回 None。"""
    if not text:
        return None
    t = text.split("·")[0].strip()          # 去掉「·地区」后缀
    if not t:
        return None
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "刚刚" in t or re.search(r"\d+\s*秒钟?前", t):
        return now
    m = re.search(r"(\d+)\s*分钟前", t)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*小时前", t)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*天前", t)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*周前", t)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    m = re.search(r"(\d+)\s*个月前", t)
    if m:
        return now - timedelta(days=int(m.group(1)) * 30)
    m = re.search(r"(\d+)\s*年前", t)
    if m:
        return now - timedelta(days=int(m.group(1)) * 365)
    if t.startswith("今天"):
        return day0
    if t.startswith("昨天"):
        return day0 - timedelta(days=1)
    if t.startswith("前天"):
        return day0 - timedelta(days=2)
    # 绝对日期
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})[-/](\d{1,2})", t)   # 「09-21」这种，按今年算
    if m:
        try:
            return datetime(now.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def filter_by_time(hits, since, until, now=None, log=None):
    """按发布时间过滤命中评论。since/until 为 datetime 或 None。
    过滤开启时，时间无法识别的评论会被排除（并计数提示）。"""
    if not since and not until:
        return hits, 0
    now = now or datetime.now()
    kept, unknown = [], 0
    for h in hits:
        dt = parse_comment_time(h.get("time", ""), now)
        if dt is None:
            unknown += 1
            continue
        if since and dt < since:
            continue
        if until and dt > until:
            continue
        kept.append(h)
    return kept, unknown


def parse_range_spec(spec, now=None):
    """把 '24h' / '7d' / '30d' 这类相对量转成 since(datetime)。"""
    now = now or datetime.now()
    m = re.match(r"^(\d+)\s*([hdw])$", (spec or "").strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return now - delta


# ---------- 对外：登录 ----------
def open_login_window(log=print, on_progress=None):
    """打开浏览器停在抖音首页，用户在窗口里扫码登录，
    登录完成后关闭浏览器窗口，本函数返回并把登录态留在 PROFILE_DIR。"""
    log("正在打开浏览器，请在窗口里扫码登录抖音……")
    log("（登录成功后，把那个浏览器窗口关掉，这里就会自动继续）")
    with sync_playwright() as p:
        ctx = launch_persistent(p, headless=False, log=log)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                      timeout=60000)
        except Exception:
            pass
        # 轮询直到浏览器被关闭（关闭后任何 playwright 调用会抛错）
        while True:
            time.sleep(2)
            if on_progress:
                on_progress()
            try:
                page.evaluate("1")
            except Exception:
                break
    log("登录态已保存，之后运行无需再扫码。")


LOGIN_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard"}


def ctx_is_logged_in(ctx):
    """通过抖音登录 Cookie 判断该浏览器上下文是否已登录（不依赖页面 DOM，最可靠）。"""
    try:
        cookies = ctx.cookies("https://www.douyin.com")
    except Exception:
        try:
            cookies = ctx.cookies()
        except Exception:
            return False
    names = {c.get("name") for c in cookies}
    return bool(names & LOGIN_COOKIE_NAMES)


def login_interactive(log=print, timeout=300):
    """打开浏览器让用户登录；一旦检测到登录 Cookie 出现就自动关闭浏览器并返回。
    若打开时已登录，会立即关闭。用户手动关掉浏览器也算完成（Cookie 已存到本地）。"""
    log("正在打开浏览器，请在窗口里扫码登录抖音…")
    with sync_playwright() as p:
        ctx = launch_persistent(p, headless=False, log=log)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        if ctx_is_logged_in(ctx):
            log("检测到已处于登录状态，自动关闭浏览器…")
        else:
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(2)
                try:
                    if ctx_is_logged_in(ctx):
                        log("登录成功，自动关闭浏览器…")
                        break
                except Exception:
                    log("浏览器已关闭，按已保存的登录态处理。")
                    break
        try:
            ctx.close()
        except Exception:
            pass
    return check_login()


def check_login(log=None):
    """无头快速校验当前本地登录态是否有效（不弹任何窗口）。"""
    with sync_playwright() as p:
        try:
            ctx = launch_persistent(p, headless=True, log=log)
        except Exception:
            return False
        try:
            return ctx_is_logged_in(ctx)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


# ---------- 登录态共享（供并发任务复用） ----------
STATE_PATH = os.path.expanduser("~/.douyin_miner_state.json")


def export_login_state(log=None):
    """把持久化档案里的登录态导出到 STATE_PATH，供并发任务用 storage_state 共享。
    返回当前是否已登录。"""
    with sync_playwright() as p:
        ctx = launch_persistent(p, headless=True, log=log)
        try:
            ok = ctx_is_logged_in(ctx)
            if ok:
                try:
                    ctx.storage_state(path=STATE_PATH)
                except Exception:
                    pass
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return ok


def _launch_state_ctx(p, headless=True, log=None):
    """优先用导出的 storage_state 开独立浏览器（可并发、不锁档案）；
    没有 state 文件时退回持久化档案。"""
    use_state = os.path.exists(STATE_PATH)
    last_err = None
    for ch in ("chrome", "msedge", None):
        try:
            if use_state:
                return _new_state_ctx(p, ch, headless)
            return p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR, channel=ch, headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
            )
        except Exception as e:
            last_err = e
            continue
    raise last_err


def _new_state_ctx(p, ch, headless):
    browser = p.chromium.launch(
        channel=ch, headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        storage_state=STATE_PATH,
        viewport={"width": 1280, "height": 900},
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    )
    ctx._browser = browser  # 便于关闭
    return ctx


def _close_ctx(ctx):
    try:
        ctx.close()
    except Exception:
        pass
    b = getattr(ctx, "_browser", None)
    if b:
        try:
            b.close()
        except Exception:
            pass


# ---------- 单视频抓取（供批量与并发任务共用） ----------
def _make_row(vid, c):
    profile = c.get("profile", "")
    return {
        "视频ID": vid,
        "昵称": c.get("name") or "(未取名/已重置)",
        "评论内容": c.get("content", ""),
        "评论时间": c.get("time", ""),
        "主页链接": profile,
        "私信入口(主页点私信)": profile,
    }


def _match_one(c, keywords, since, until, now):
    text = (c.get("content", "") or "") + "\n" + (c.get("name", "") or "")
    if not any(k and k in text for k in keywords):
        return False
    if since or until:
        dt = parse_comment_time(c.get("time", ""), now)
        if dt is None:
            return False
        if since and dt < since:
            return False
        if until and dt > until:
            return False
    return True


def scrape_one_page(page, url, keywords, since, until, with_replies, max_comments,
                    log=None, on_progress=None, stop_event=None, now=None, on_hit=None):
    """抓取一个视频评论并按关键词/时间过滤，返回 rows 列表；登录失效返回 None。
    on_hit(row) 会在滚动过程中【每发现一条新命中】即时回调，用于边抓边显示。"""
    now = now or datetime.now()
    vid = parse_video_id(url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        if log:
            log(f"  打开失败: {e}")
        return []
    time.sleep(random.uniform(1.5, 2.5))
    if not wait_logged_in(page, log=log, stop_event=stop_event):
        if log:
            log("  未检测到评论区（可能登录失效/被风控）")
        return None
    seen = set()

    def emit(new_items):
        if not on_hit:
            return
        for c in new_items:
            if not _match_one(c, keywords, since, until, now):
                continue
            key = c["profile"] + "|" + c["content"]
            if key in seen:
                continue
            seen.add(key)
            on_hit(_make_row(vid, c))

    comments = harvest_comments(page, max_comments=max_comments, with_replies=with_replies,
                                log=log, on_progress=on_progress, stop_event=stop_event,
                                on_new=emit)
    hits = match_keywords(comments, keywords)
    if since or until:
        hits, _ = filter_by_time(hits, since, until, now=now)
    return [_make_row(vid, c) for c in hits]


def write_outputs(rows, out, log=None):
    """写 CSV + xlsx，返回 (csv_path, xlsx_path or None)。"""
    if not os.path.isabs(out):
        out = os.path.join(os.getcwd(), out)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    xlsx_path = os.path.splitext(out)[0] + ".xlsx"
    wrote = write_xlsx(rows, xlsx_path, log=log)
    return out, (xlsx_path if wrote else None)


# ---------- 单个监控任务（并发用：独立浏览器 + 共享登录态） ----------
def run_task(url, keywords, out, with_replies=False, max_comments=3000,
             since=None, until=None, log=None, on_progress=None, stop_event=None,
             headless=True):
    """跑一个监控任务（一个视频链接），写自己的 CSV/xlsx，返回 (rows, csv, xlsx)。
    rows 为 None 表示登录失效。"""
    if not os.path.exists(STATE_PATH):
        export_login_state()
    rows = None
    with sync_playwright() as p:
        ctx = _launch_state_ctx(p, headless=headless, log=log)
        page = ctx.new_page()
        try:
            rows = scrape_one_page(page, url, keywords, since, until, with_replies,
                                   max_comments, log=log, on_progress=on_progress,
                                   stop_event=stop_event)
        finally:
            _close_ctx(ctx)
    if rows is None:
        return None, None, None          # 登录失效
    if not rows:
        return [], None, None            # 跑了但 0 命中
    csv_path, xlsx_path = write_outputs(rows, out, log=log)
    return rows, csv_path, xlsx_path


# ---------- 对外：批量抓取 + 导出（CLI / 单窗口顺序用） ----------
def run_batch(links, keywords, out="抖音评论名单.csv", with_replies=False,
              max_comments=3000, headless=False, log=print, on_progress=None,
              since=None, until=None, stop_event=None, require_login=True,
              on_login_required=None, on_hit=None):
    """抓取一批链接（同一浏览器顺序跑），导出 CSV + xlsx，返回 (rows, csv_path, xlsx_path)。"""
    rows = []
    now = datetime.now()
    with sync_playwright() as p:
        ctx = launch_persistent(p, headless=headless, log=log)
        if require_login and not ctx_is_logged_in(ctx):
            if log:
                log("未检测到抖音登录态，请先在「抖音账号」页面登录。")
            try:
                ctx.close()
            except Exception:
                pass
            if on_login_required:
                on_login_required()
            return rows, None, None
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for url in links:
            if stop_event is not None and stop_event.is_set():
                log("已手动停止。")
                break
            got = scrape_one_page(page, url, keywords, since, until, with_replies,
                                  max_comments, log=log, on_progress=on_progress,
                                  stop_event=stop_event, now=now, on_hit=on_hit)
            if got is None:
                continue
            rows.extend(got)
        ctx.close()

    if not rows:
        log("没有命中任何评论。")
        return rows, None, None
    out, xlsx_path = write_outputs(rows, out, log=log)
    uniq = len({r["主页链接"] for r in rows})
    log(f"完成：{len(rows)} 条命中 / {uniq} 个用户，已导出 → {out}")
    return rows, out, xlsx_path


def write_xlsx(rows, xlsx_path, log=print):
    """生成可点击超链接的 .xlsx；未装 openpyxl 则跳过并返回 False。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        if log:
            log("（未装 openpyxl，仅输出 CSV）")
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "命中名单"
    ws.append(FIELDS)
    link = Font(color="0563C1", underline="single")
    for i, r in enumerate(rows, start=1):
        ws.append([r["视频ID"], r["昵称"], r["评论内容"], r.get("评论时间", ""),
                   r["主页链接"], "私信 →"])
        for col in (5, 6):
            c = ws.cell(row=i + 1, column=col)
            c.hyperlink = r["主页链接"]
            c.font = link
    for col, width in zip("ABCDEF", [16, 18, 52, 14, 60, 20]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(xlsx_path)
    return True


# ---------- 命令行入口 ----------
def main():
    ap = argparse.ArgumentParser(description="抖音评论关键词名单挖掘 (导名单，不做私信)")
    ap.add_argument("links", nargs="*", help="视频链接，可多个；可直接粘整段分享文案")
    ap.add_argument("-k", "--keyword", action="append", default=[], dest="keywords")
    ap.add_argument("-o", "--out", default="抖音评论名单.csv")
    ap.add_argument("--login", action="store_true", help="只打开浏览器登录并保存登录态后退出")
    ap.add_argument("--with-replies", action="store_true")
    ap.add_argument("--max-comments", type=int, default=3000)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--last", help="只抓最近时间内的评论，如 24h / 7d / 30d")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（含当天 00:00）")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD（含当天 23:59）")
    args = ap.parse_args()

    def cli_progress(n):
        print(f"  ...已收集 {n} 条评论", end="\r", flush=True)

    if args.login:
        open_login_window(log=print)
        return

    now = datetime.now()
    since = until = None
    if args.last:
        since = parse_range_spec(args.last, now)
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            ap.error("--since 格式应为 YYYY-MM-DD")
    if args.until:
        try:
            until = datetime.strptime(args.until, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            ap.error("--until 格式应为 YYYY-MM-DD")

    links = []
    for raw in args.links:
        found = extract_links(raw)
        links.extend(found if found else [raw])
    links = [l for l in links if "douyin.com" in l]
    if not links:
        ap.error("没找到有效的抖音链接")
    if not args.keywords:
        ap.error("至少用 -k 传一个关键词")

    run_batch(links, args.keywords, out=args.out, with_replies=args.with_replies,
              max_comments=args.max_comments, headless=args.headless,
              log=print, on_progress=cli_progress, since=since, until=until)
    print()


if __name__ == "__main__":
    main()
