#!/usr/bin/env python3
"""从切图包 SVG 一键生成桌面应用所需的 PNG/ICO/ICNS 资源。

输入（外部资源，不在仓里）：
    /Library/知识库/听潮-切图-多彩数据版/logo/app-icon.svg   # 渐变主标，512×512
    /Library/知识库/听潮-切图-多彩数据版/logo/logo-mono.svg   # 单色标（托盘用）

输出（项目内）：
    desktop/assets/tray-idle.png     # 托盘 idle 多尺寸 PNG
    desktop/assets/tray-busy.png     # 托盘抓取中（idle + 红点）
    desktop/assets/notify-icon.png   # 系统通知图标
    desktop/assets/app.ico           # Windows 多尺寸 ICO
    desktop/assets/app.icns          # macOS 多尺寸 ICNS

用法：
    python tools/build_assets.py            # 用本地切图包
    python tools/build_assets.py --src DIR  # 指定切图包目录

依赖：Pillow（>=10）、可选 cairosvg（更清晰） / 退化到 rsvg-convert / 退化到 resvg
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Windows CI 默认 cp1252 编码会让中文 print 直接抛 UnicodeEncodeError
# reconfigure stdout 到 utf-8 后安全
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "desktop" / "assets"
# 默认从仓内 desktop/assets-src/ 读 SVG（CI 友好）
# 本地开发时可用 --src 指向切图包原路径覆盖：
#   python tools/build_assets.py --src "/Library/知识库/听潮-切图-多彩数据版/logo"
DEFAULT_SRC = ROOT / "desktop" / "assets-src"
LOCAL_FALLBACK_SRC = Path("/Library/知识库/听潮-切图-多彩数据版/logo")

TRAY_SIZES = [16, 22, 32, 44, 64, 128, 256]      # macOS 22/44 是菜单栏标准
NOTIFY_SIZES = [64, 128, 256]
APP_ICON_SIZES = [16, 32, 48, 64, 128, 256, 512]


# ---------- SVG → PNG 渲染（多 fallback） ----------
def render_svg(svg_path: Path, size: int) -> Image.Image:
    """优先级：cairosvg > rsvg-convert > resvg；都没有就报错。"""
    # 1. cairosvg（纯 Python，最方便）
    try:
        import cairosvg  # type: ignore
        png_bytes = cairosvg.svg2png(url=str(svg_path), output_width=size, output_height=size)
        return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except ImportError:
        pass
    except Exception:
        pass

    # 2. rsvg-convert（macOS 装了 librsvg 一般都有）
    if shutil.which("rsvg-convert"):
        try:
            out = subprocess.check_output(
                ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path)],
                timeout=15,
            )
            return Image.open(io.BytesIO(out)).convert("RGBA")
        except Exception as e:
            print(f"  ! rsvg-convert 失败: {e}")

    # 3. resvg（Rust 写的）
    if shutil.which("resvg"):
        try:
            tmp = ASSETS / f"_tmp_{size}.png"
            subprocess.check_call(
                ["resvg", "-w", str(size), "-h", str(size), str(svg_path), str(tmp)],
                timeout=15,
            )
            img = Image.open(tmp).convert("RGBA")
            tmp.unlink()
            return img
        except Exception as e:
            print(f"  ! resvg 失败: {e}")

    raise RuntimeError(
        "无法渲染 SVG：请安装 cairosvg (pip install cairosvg)\n"
        "或 macOS 自带 rsvg-convert，或 brew install librsvg"
    )


# ---------- 生成器 ----------
def gen_tray_idle(mono_svg: Path) -> None:
    """托盘 idle：单色 logo 多尺寸。"""
    print("[1/4] tray-idle.png ...")
    # 主图用 256×256（pystray 拿到会自动缩到 22pt 菜单栏，矢量感保留）
    base = render_svg(mono_svg, 256)
    out = ASSETS / "tray-idle.png"
    base.save(out, format="PNG")
    print(f"  → {out} ({out.stat().st_size} B, 主尺寸 {base.size[0]}×{base.size[1]})")


def gen_tray_busy(mono_svg: Path) -> None:
    """托盘 busy：idle + 右上角红色圆点。"""
    print("[2/4] tray-busy.png ...")
    img = render_svg(mono_svg, 128).convert("RGBA")
    draw = ImageDraw.Draw(img)
    # 红点（BRAND 红 #FF4D4D）
    radius = max(14, img.size[0] // 8)
    cx, cy = img.size[0] - radius - 6, radius + 6
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(255, 77, 77, 255),
        outline=(255, 255, 255, 230),
        width=4,
    )
    out = ASSETS / "tray-busy.png"
    img.save(out, format="PNG")
    print(f"  → {out} ({out.stat().st_size} B)")


def gen_notify_icon(mono_svg: Path) -> None:
    """通知图标：单色 logo 128×256 双尺寸。"""
    print("[3/4] notify-icon.png ...")
    img = render_svg(mono_svg, 256)
    out = ASSETS / "notify-icon.png"
    img.save(out, format="PNG")
    print(f"  → {out} ({out.stat().st_size} B)")


def gen_app_ico(app_svg: Path) -> None:
    """Windows ICO：多尺寸。"""
    print("[4a/4] app.ico ...")
    images = [render_svg(app_svg, s) for s in APP_ICON_SIZES]
    out = ASSETS / "app.ico"
    images[-1].save(out, format="ICO", sizes=[(s, s) for s in APP_ICON_SIZES])
    print(f"  → {out} ({out.stat().st_size} B, {len(APP_ICON_SIZES)} sizes)")


def gen_app_icns(app_svg: Path) -> None:
    """macOS ICNS：优先 Pillow 自带，fallback 到 iconutil。"""
    print("[4b/4] app.icns ...")
    out = ASSETS / "app.icns"
    try:
        # Pillow >= 10.1 支持 ICNS 写入
        images = [render_svg(app_svg, s) for s in [16, 32, 64, 128, 256, 512]]
        images[-1].save(out, format="ICNS", append_images=images[:-1])
        print(f"  → {out} ({out.stat().st_size} B, Pillow ICNS)")
        return
    except Exception as e:
        print(f"  ! Pillow ICNS 失败: {e}")

    # fallback: macOS iconutil（需要 iconset 目录）
    if sys.platform == "darwin" and shutil.which("iconutil"):
        try:
            iconset = ASSETS / "_iconset"
            iconset.mkdir(exist_ok=True)
            mapping = {
                16: "icon_16x16.png",
                32: "icon_16x16@2x.png",
                64: "icon_32x32@2x.png",
                128: "icon_128x128.png",
                256: "icon_128x128@2x.png",
                512: "icon_256x256@2x.png",
            }
            for sz, name in mapping.items():
                render_svg(app_svg, sz).save(iconset / name, format="PNG")
            subprocess.check_call(["iconutil", "-c", "icns", str(iconset), "-o", str(out)])
            shutil.rmtree(iconset)
            print(f"  → {out} ({out.stat().st_size} B, iconutil)")
            return
        except Exception as e:
            print(f"  ! iconutil 失败: {e}")

    print(f"  ✗ ICNS 未能生成，请安装 Pillow>=10.1 或 macOS iconutil")


# ---------- main ----------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=None,
                        help=f"切图包 logo 目录（默认优先 {DEFAULT_SRC}，"
                             f"fallback 到 {LOCAL_FALLBACK_SRC}）")
    args = parser.parse_args()

    # 优先级：--src 指定 > 仓内 assets-src > 本地切图包
    candidates = []
    if args.src:
        candidates.append(Path(args.src))
    candidates.append(DEFAULT_SRC)
    if LOCAL_FALLBACK_SRC != DEFAULT_SRC:
        candidates.append(LOCAL_FALLBACK_SRC)

    src = None
    for c in candidates:
        if (c / "app-icon.svg").exists() and (c / "logo-mono.svg").exists():
            src = c
            break

    if src is None:
        print("错误：找不到 SVG 资源")
        for c in candidates:
            print(f"  试过: {c}  "
                  f"(app-icon.svg={ (c/'app-icon.svg').exists() },"
                  f" logo-mono.svg={ (c/'logo-mono.svg').exists() })")
        return 1

    app_svg = src / "app-icon.svg"
    mono_svg = src / "logo-mono.svg"

    ASSETS.mkdir(parents=True, exist_ok=True)

    print(f"源目录：{src}")
    print(f"输出目录：{ASSETS}\n")

    gen_tray_idle(mono_svg)
    gen_tray_busy(mono_svg)
    gen_notify_icon(mono_svg)
    gen_app_ico(app_svg)
    gen_app_icns(app_svg)

    print("\n✓ 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
