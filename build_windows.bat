@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ============================================================
rem  抖音评论关键词名单挖掘 —— Windows 一键打包脚本
rem  在【装了 Python 的 Windows】上双击本文件，生成免环境的 exe。
rem  产物：dist\DouyinCommentMiner\  整个文件夹拷到目标电脑即可运行。
rem ============================================================

where python >nul 2>&1
if errorlevel 1 (
  echo [!] 这台电脑没检测到 Python。
  echo     请到  https://www.python.org/downloads/  下载安装 Python 3.11，
  echo     安装第一屏务必勾选  "Add python.exe to PATH"，
  echo     装好后重新双击本脚本。
  pause
  exit /b 1
)

echo [1/3] 安装依赖 playwright / openpyxl / customtkinter / pyinstaller（用阿里云镜像）...
python -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ playwright openpyxl customtkinter pyinstaller
if errorlevel 1 (
  echo [!] 依赖安装失败，多半是网络问题，稍后重试。
  pause
  exit /b 1
)

echo.
echo [2/3] 正在打包（约 1-3 分钟，请等待）...
python -m PyInstaller --noconfirm --clean ^
  --name DouyinCommentMiner ^
  --windowed ^
  --onedir ^
  --collect-all playwright ^
  --collect-all openpyxl ^
  --collect-all customtkinter ^
  --hidden-import openpyxl ^
  douyin_miner_gui.py
if errorlevel 1 (
  echo [!] 打包失败，把上面的报错发给我。
  pause
  exit /b 1
)

echo.
echo [3/3] 打包完成！
echo        可执行文件： %cd%\dist\DouyinCommentMiner\DouyinCommentMiner.exe
echo        把整个「DouyinCommentMiner」文件夹拷到目标电脑，
echo        双击里面的 DouyinCommentMiner.exe 即可运行。
echo        （目标电脑需装有 Chrome 或 Edge；不需要装 Python）
echo.
pause
