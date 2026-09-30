"""听潮 · 客户端自升级（适配 Electron 布局）

流程：下载 zip → 校验结构 → 解压到临时目录 → 原子替换 → 重启
macOS  : 替换 听潮.app（旧版改名备份，成功后异步清理）
Windows: 替换安装目录内容（绿色版与 nsis 安装目录同构）

由 sidecar 以分离子进程调用：
    <sidecar> --apply-update <zip> <app_root> [--wait-pid <pid>]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

APP_NAME = "听潮"


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


def apply_update(zip_path: Path, app_root: Path, wait_pid: int | None = None) -> int:
    """原子替换应用本体。app_root 在 mac 上是 .app 的父目录，在 win 上是安装目录。"""
    log = lambda m: print(f"[updater] {m}", flush=True)
    zip_path = Path(zip_path).resolve()
    app_root = Path(app_root).resolve()

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
            zf.extractall(work)

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
            # 清掉隔离属性，避免更新后被 Gatekeeper 拦
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(final)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _relaunch(app_root)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            return 0

        # ---- Windows：把解压内容覆盖进安装目录 ----
        src = work
        inner = list(work.iterdir())
        if len(inner) == 1 and inner[0].is_dir() and not (inner[0] / f"{APP_NAME}.exe").exists():
            if any(p.suffix == ".exe" for p in inner[0].iterdir()):
                src = inner[0]
        log(f"覆盖安装目录 {app_root}")
        for item in src.iterdir():
            dst = app_root / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
            except OSError as e:
                log(f"跳过 {item.name}：{e}")
        _relaunch(app_root)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            zip_path.unlink(missing_ok=True)
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
