"""听潮 · 客户端自升级（适配 Electron 布局）

流程：预检 → 下载 zip → 校验结构 → 解压到临时目录 → 原子替换 → 重启
macOS  : 替换 听潮.app（旧版改名备份，成功后异步清理）
Windows: 替换安装目录内容（绿色版与 nsis 安装目录同构）

由 sidecar 以分离子进程调用：
    <sidecar> --apply-update <zip> <app_root> [--wait-pid <pid>]

所有步骤同时写入 ~/.tingchao/updates/updater.log —— 升级器是 detached 子进程，
stdout 被父进程丢进 DEVNULL，不落盘的话失败原因对用户和客服都是黑箱。
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

APP_NAME = "听潮"
LOG_PATH = Path.home() / ".tingchao" / "updates" / "updater.log"


# ---------------------------------------------------------------- 日志

def _file_logger():
    def log(m):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [updater] {m}"
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return log


def read_log_tail(limit: int = 40) -> str:
    """给 /api/update/log 用：客服排查升级失败时直接看最后几十行。"""
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-limit:])
    except OSError:
        return ""


# ---------------------------------------------------------------- 预检

def preflight(app_root: Path) -> str:
    """替换前的可行性检查，返回中文原因（空串=可行）。"""
    if not app_root or not app_root.exists():
        return "找不到应用安装目录，请手动覆盖安装"
    if sys.platform == "darwin" and str(app_root).startswith("/Volumes/"):
        return "当前是直接从安装盘（DMG）里运行的，请先把听潮拖进「应用程序」再升级"
    probe = app_root / ".tingchao-write-test"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return ("没有权限写入安装目录（" + str(app_root) + "）。"
                + ("请右键用管理员身份运行一次，或改用绿色免安装版" if os.name == "nt"
                   else "请把应用移到用户可写目录后重试"))
    finally:
        try:
            if probe.exists():
                probe.rename(probe.with_name(".tingchao-write-test.done"))
        except OSError:
            pass
    # 收尾清掉探针（改名后删除，避免直接删被安全策略拦）
    try:
        done = app_root / ".tingchao-write-test.done"
        if done.exists():
            os.remove(done)
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- 下载

def download(url: str, dest_dir: Path, on_progress=None) -> Path:
    """流式下载到 dest_dir/tingchao-update.zip，返回文件路径。"""
    import requests
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "tingchao-update.zip"
    tmp = dest_dir / "tingchao-update.part"
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    tmp.replace(target)
    return target


# ---------------------------------------------------------------- 校验

def _list_top_names(zf: zipfile.ZipFile) -> set[str]:
    return {n.split("/", 1)[0].replace("\\", "/") for n in zf.namelist() if n.strip()}


def verify_zip(zip_path: Path) -> tuple[bool, str, str]:
    """返回 (ok, 布局, 错误信息)。布局: app-bundle | flat"""
    if not zip_path.exists():
        return False, "", "更新包不存在"
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        return False, "", "更新包已损坏，请重试"
    with zf:
        tops = _list_top_names(zf)
        if sys.platform == "darwin":
            if f"{APP_NAME}.app" in tops:
                return True, "app-bundle", ""
            # 允许 zip 里直接是 .app 的深层结构
            if any(n.startswith("Contents/") or n.endswith("Contents/Info.plist") for n in zf.namelist()):
                return True, "flat", ""
            return False, "", f"更新包内找不到 {APP_NAME}.app"
        # Windows：zip 根应含主程序
        names = {n.lower() for n in tops}
        if any(n.endswith(".exe") for n in names) or APP_NAME.lower() in names:
            return True, "flat", ""
        return False, "", "更新包内找不到主程序"
    return False, "", "无法读取更新包"


# ---------------------------------------------------------------- 应用

def _wait_process_exit(pid: int, timeout: float = 20.0) -> bool:
    if not pid:
        return True
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.3)
    return False


def _wait_no_instance(bundle: Path, log, timeout: float = 12.0) -> None:
    """mac 专用：等该 .app 自身的实例退干净再重启。
    旧实例不退，新版会被 Electron 单实例锁挡回去，用户就看到
    「升级完再打开还是旧版本、还提示升级」。
    注意匹配串必须精确到 .app 内部路径——只写 /Applications 会把机器上
    所有装在 /Applications 的进程都当成"旧实例"，白等一整轮超时。"""
    if sys.platform != "darwin":
        return
    target = str(bundle / "Contents" / "MacOS")
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["pgrep", "-f", target], capture_output=True, text=True)
        pids = [p for p in out.stdout.split() if p.strip().isdigit()]
        if not pids:
            return
        log(f"等待旧实例退出：{pids[:8]}")
        time.sleep(0.5)
    log("旧实例超时未退出，仍尝试重启新版")


def _relaunch(app_root: Path) -> None:
    if sys.platform == "darwin":
        app = app_root if app_root.suffix == ".app" else app_root / f"{APP_NAME}.app"
        subprocess.Popen(["open", str(app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        exe = app_root / f"{APP_NAME}.exe"
        if not exe.exists():
            cands = list(app_root.glob("*.exe"))
            if not cands:
                return
            exe = cands[0]
        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([str(exe)], cwd=str(app_root), creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _extract_zip_preserving_links(zf: zipfile.ZipFile, dest: Path) -> None:
    """zipfile.extractall 不还原符号链接（会把链接写成含目标路径的文本文件），
    Electron 的 .app 里 Frameworks 全靠 symlink 组织，直接 extractall 会得到
    结构残废、签名失效、无法启动的 App。这里手工还原：链接建 symlink，
    普通文件按 unix 权限落盘，并防 zip 路径穿越。"""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        mode = info.external_attr >> 16
        name = info.filename
        # 防 zip-slip
        target = (dest / name)
        if not str(target.resolve()).startswith(str(dest)):
            continue
        if stat.S_ISLNK(mode):
            target.parent.mkdir(parents=True, exist_ok=True)
            link = zf.read(info.filename).decode("utf-8", "replace")
            if target.is_symlink() or target.exists():
                if target.is_dir() and not target.is_symlink():
                    continue
                os.remove(target)
            os.symlink(link, target)
        elif info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info.filename) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if mode:
                os.chmod(target, mode & 0o7777)


def apply_update(zip_path: Path, app_root: Path, wait_pid: int | None = None) -> int:
    """原子替换应用本体。app_root 在 mac 上是 .app 的父目录，在 win 上是安装目录。

    返回码：0 成功 / 1 包不可用 / 2 主进程未退出 / 3 预检不通过 / 4 关键文件写入失败
    """
    log = _file_logger()
    zip_path = Path(zip_path).resolve()
    app_root = Path(app_root).resolve()
    if sys.platform == "darwin" and app_root.suffix.lower() == ".app":
        # 壳层偶尔把 .app 本体当安装根传进来，那样新版会被解压成
        # 听潮.app/听潮.app 套娃，外面那层还是旧版——这里强制修正为父目录
        log(f"app_root 指向 .app 本体，自动修正为父目录：{app_root.parent}")
        app_root = app_root.parent
    log(f"开始升级：包={zip_path.name} 目录={app_root} 等待退出 pid={wait_pid}")

    why = preflight(app_root)
    if why:
        log(f"预检不通过：{why}")
        return 3

    ok, layout, err = verify_zip(zip_path)
    if not ok:
        log(f"校验失败：{err}")
        return 1

    if wait_pid and not _wait_process_exit(wait_pid):
        log(f"主进程 {wait_pid} 未在超时内退出，放弃替换以免损坏")
        return 2

    work = Path(tempfile.mkdtemp(prefix="tingchao-update-"))
    try:
        log(f"解压 → {work}")
        with zipfile.ZipFile(zip_path) as zf:
            _extract_zip_preserving_links(zf, work)

        if sys.platform == "darwin":
            new_app = work / f"{APP_NAME}.app"
            if layout == "flat" or not new_app.exists():
                found = list(work.glob("**/" + f"{APP_NAME}.app"))
                if not found:
                    log("解压后找不到 .app")
                    return 1
                new_app = found[0]
            if not (new_app / "Contents" / "Info.plist").exists():
                log("新包结构不完整（缺 Info.plist），中止")
                return 1

            final = app_root / f"{APP_NAME}.app"
            backup = app_root / f"{APP_NAME}.app.old"
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            if final.exists():
                log(f"备份旧版 → {backup.name}")
                final.rename(backup)
            log(f"就位新版 → {final}")
            shutil.move(str(new_app), str(final))
            if not (final / "Contents" / "Info.plist").exists():
                # 新版没装好：把旧的换回去，坏包挪到一边，别让用户打不开
                log("新版结构不完整，回滚旧版")
                if backup.exists():
                    final.rename(app_root / f"{APP_NAME}.app.broken")
                    backup.rename(final)
                return 5
            # 清掉隔离属性，避免更新后被 Gatekeeper 拦
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(final)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _wait_no_instance(final, log)
            _relaunch(app_root)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            log("升级完成，已重启新版")
            return 0

        # ---- Windows：把解压内容覆盖进安装目录 ----
        src = work
        inner = list(work.iterdir())
        if len(inner) == 1 and inner[0].is_dir() and not (inner[0] / f"{APP_NAME}.exe").exists():
            if any(p.suffix == ".exe" for p in inner[0].iterdir()):
                src = inner[0]
        log(f"覆盖安装目录 {app_root}")
        failed = []
        for item in src.iterdir():
            dst = app_root / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
            except OSError as e:
                failed.append(item.name)
                log(f"跳过 {item.name}：{e}")
        if failed:
            log(f"有 {len(failed)} 项写入失败（示例：{failed[:5]}），多半是权限不足")
            _wait_no_instance(app_root / f"{APP_NAME}.app", log)
            _relaunch(app_root)
            return 4
        _wait_no_instance(app_root / f"{APP_NAME}.app", log)
        _relaunch(app_root)
        log("升级完成，已重启新版")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            if zip_path.exists():
                os.remove(zip_path)
        except OSError:
            pass


# ---------------------------------------------------------------- CLI

USAGE = ("用法：\n"
         "  --apply-update <zip> <app_root> [--wait-pid <pid>]")


def run_cli(argv: list[str]) -> int:
    if "--apply-update" in argv:
        i = argv.index("--apply-update")
        try:
            zip_path, app_root = argv[i + 1], argv[i + 2]
        except IndexError:
            print(USAGE, file=sys.stderr)
            return 2
        pid = None
        if "--wait-pid" in argv:
            j = argv.index("--wait-pid")
            pid = int(argv[j + 1])
        return apply_update(Path(zip_path), Path(app_root), pid)
    print(USAGE, file=sys.stderr)
    return 2
