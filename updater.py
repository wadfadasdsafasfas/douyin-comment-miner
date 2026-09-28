#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立更新工具 —— PyInstaller 打 updater.exe / updater_mac 后使用。

用法：
    updater <action> <target_dir> <zip_path> [app_exe_relpath]

action:
    apply  - 把 zip_path 解压覆盖到 target_dir，然后启动新主程序
    check  - 仅检查 target_dir/DouyinCommentMiner.exe 是否还在运行（Windows 用）

target_dir:
    应用所在目录，例如 ./DouyinCommentMiner/  (Windows)
              或 ./DouyinCommentMiner.app/Contents/MacOS/DouyinCommentMiner.app 的父目录
              实际使用中，主程序把 zip 解到 <exe_dir>/_update/zip.zip，
              target_dir 传 <exe_dir>/_update/ 即可

app_exe_relpath:
    覆盖后要启动的可执行文件相对 target_dir 的路径，默认：
        Windows: ../DouyinCommentMiner.exe
        Mac:     ../DouyinCommentMiner.app
    （target_dir 通常是 _update/，exe/app 在它上一层）
"""

from __future__ import annotations

import os
import platform as _plat
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def _wait_process_exit(pid: int, timeout: float = 10.0) -> bool:
    """Windows: 等 pid 进程退出；跨平台用 taskkill 检查。"""
    if _plat.system() == "Windows":
        for _ in range(int(timeout * 10)):
            # 用 tasklist 检查 PID 是否存在
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True,
            )
            if str(pid) not in r.stdout:
                return True
            time.sleep(0.1)
        return False
    else:
        # Mac/Linux: kill -0 检查
        for _ in range(int(timeout * 10)):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return True  # 别的用户的进程，说明主进程还在
            time.sleep(0.1)
        return False


def _extract_zip(zip_path: Path, dest_dir: Path):
    """解压 zip 到 dest_dir，覆盖同名文件。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)


def _launch(app_path: Path):
    """启动新版本主程序。"""
    if _plat.system() == "Darwin":
        # .app 用 open 命令
        if app_path.suffix == ".app":
            subprocess.Popen(["open", "-a", str(app_path)])
            return
    if app_path.exists():
        if _plat.system() == "Windows":
            subprocess.Popen([str(app_path)], creationflags=0x00000008)  # DETACHED_PROCESS
        else:
            os.chmod(app_path, 0o755)
            subprocess.Popen([str(app_path)])
    else:
        print(f"[updater] 找不到新程序：{app_path}", file=sys.stderr)


def cmd_apply(target_dir: str, zip_path: str, app_exe_relpath: str | None = None,
              wait_pid: int | None = None):
    target = Path(target_dir).resolve()
    zpath = Path(zip_path).resolve()
    if not zpath.exists():
        print(f"[updater] 找不到 zip：{zpath}", file=sys.stderr)
        return 1

    # 等主进程退出（updater 由主程序启动时把主进程 pid 传进来）
    if wait_pid:
        print(f"[updater] 等 pid {wait_pid} 退出...", flush=True)
        if not _wait_process_exit(wait_pid, timeout=15):
            print(f"[updater] 主进程 {wait_pid} 还没退，强行继续", file=sys.stderr)

    # 解压到 target 的父目录（target 是 _update/，父目录才是真正应用目录）
    extract_root = target.parent
    print(f"[updater] 解压 {zpath.name} 到 {extract_root}", flush=True)
    _extract_zip(zpath, extract_root)

    # 清理 zip
    try:
        zpath.unlink()
    except Exception:
        pass

    # 启动新程序
    if app_exe_relpath:
        new_app = extract_root / app_exe_relpath
    else:
        if _plat.system() == "Windows":
            new_app = extract_root / "DouyinCommentMiner.exe"
        elif _plat.system() == "Darwin":
            new_app = extract_root / "DouyinCommentMiner.app"
        else:
            new_app = extract_root / "DouyinCommentMiner"

    print(f"[updater] 启动新程序：{new_app}", flush=True)
    time.sleep(0.5)  # 给文件系统一点时间
    _launch(new_app)
    return 0


def cmd_check(target_dir: str) -> int:
    """检查 target_dir/DouyinCommentMiner.exe 是否存在（解压前的 sanity check）。"""
    if _plat.system() == "Windows":
        exe = Path(target_dir) / "DouyinCommentMiner.exe"
    elif _plat.system() == "Darwin":
        exe = Path(target_dir) / "DouyinCommentMiner.app"
    else:
        exe = Path(target_dir) / "DouyinCommentMiner"
    return 0 if exe.exists() else 1


USAGE = """updater <action> <target_dir> <zip_path> [app_exe_relpath] [--wait-pid PID]

actions:
  apply  解压覆盖 & 重启
  check  检查主程序是否存在

example:
  updater apply ./_update ./update.zip ../DouyinCommentMiner.exe --wait-pid 12345
"""


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0

    wait_pid = None
    if "--wait-pid" in args:
        i = args.index("--wait-pid")
        wait_pid = int(args[i + 1])
        del args[i:i + 2]

    if len(args) < 3:
        print(USAGE, file=sys.stderr)
        return 2

    action, target_dir, zip_path = args[0], args[1], args[2]
    app_exe_relpath = args[3] if len(args) > 3 else None

    if action == "apply":
        return cmd_apply(target_dir, zip_path, app_exe_relpath, wait_pid)
    elif action == "check":
        return cmd_check(target_dir)
    else:
        print(f"未知 action：{action}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())