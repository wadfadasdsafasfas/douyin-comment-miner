# Windows 打包 & 使用说明（给没装环境的电脑）

目标：做出一台**没装 Python 也能双击运行**的图形工具。整个工具只需要**一次打包**，
之后把生成的文件夹拷给别人即可，收的人**不用装任何东西**（只要电脑有 Chrome 或 Edge）。

---

## 你需要两类电脑

| 电脑 | 用途 | 要求 |
|---|---|---|
| 打包机（自己有一台就行） | 跑一次打包，生成 exe | 装了 Python 3.11 的 Windows |
| 目标机（没环境的电脑） | 日常使用 | 有 Chrome 或 Edge，**不需要 Python** |

打包机只用来“生产” exe，用完就可以不管了；真正用的是拷过去的 exe 文件夹。

---

## 第一步：在打包机上生成 exe

1. 把整个 `douyin-comment-miner` 文件夹拷到装了 Python 的 Windows 上。
2. 双击 **`build_windows.bat`**。
   - 没装 Python 会提示你去官网装（安装时勾 **Add python.exe to PATH**）。
   - 脚本会自动装依赖 + 打包，约 1-3 分钟。
3. 完成后打开 `dist\DouyinCommentMiner\`，里面有 **`DouyinCommentMiner.exe`**。

> 若被 Windows  SmartScreen 拦：“更多信息 → 仍要运行”。个别杀毒软件会误报未签名 exe，加白名单即可。

---

## 第二步：拷到没环境的电脑

把 **`dist\DouyinCommentMiner\` 整个文件夹**（不是单个 exe）用 U 盘 / 网盘拷到目标机，
双击里面的 `DouyinCommentMiner.exe` 就能用。**不要**只拷 exe，会缺依赖文件。

---

## 第三步：使用（图形界面，全中文）

界面从上到下：

1. **视频链接**：一行一个，直接把抖音分享文案（带一堆乱码前缀那种）整段粘进去也行，会自动抠出链接。
2. **关键词**：一行一个，命中任意一个就入选。
3. **连子回复一起抓**（可选）：勾上更全，但更慢、更易触发风控，默认关。
4. 右上角能改**保存位置**（默认在 `文档\抖音评论名单`）。

按钮：

- **① 首次登录（扫码）**：弹出浏览器 → 扫码登录抖音 → **把浏览器窗口关掉**，工具就记下了，以后不用再扫。
- **② 开始抓取**：自动开视频、滚评论区、抓命中评论；下方实时显示进度和日志。
- **打开表格**：跑完后点它，直接打开结果（`.xlsx`，链接可点击）。

抓完在保存位置会同时生成 `…….csv` 和 `…….xlsx`，列是：
`视频ID / 昵称 / 评论内容 / 主页链接 / 私信入口(主页点私信)`。

> “私信入口”是**对方主页**链接——抖音对陌生人没有直达会话的链接，进主页点“私信”才是正规入口；
> 对方若设了“仅朋友可发”，会发不出去，属正常。**别拿它批量私信**，容易被封号也不合规。

---

## 常见问题

- **双击 exe 报错“找不到 xxxx.dll / python3xx.dll”**：说明只拷了 exe，没拷整个文件夹。重新拷 `DouyinCommentMiner` 整个目录。
- **浏览器没弹出来 / 抓不到评论**：目标机要装 Chrome 或 Edge（Win10/11 自带 Edge）。先点“① 首次登录”走一遍。
- **想换保存位置**：点“保存位置”按钮选目录。
- **打包失败**：把 `build_windows.bat` 窗口里的红字报错发我。

---

## （可选）用 GitHub Actions 云端打包

如果你本地没有 Windows，或者不想在本地装 PyInstaller，可以推到 GitHub 后让 Actions
在云端 Windows runner 上构建，自动发到 **Releases**。

### 用法

```bash
git init && git add . && git commit -m "init"
# 在 GitHub 上创建一个空仓库，比如  douyin-comment-miner
git remote add origin git@github.com:你的用户名/douyin-comment-miner.git
git push -u origin main

# 打 tag 并 push，触发 Action
git tag v1.0.0
git push origin v1.0.0
```

然后打开仓库页面 → 右侧 **Releases** → 找到 `v1.0.0` → 下载
`DouyinCommentMiner-v1.0.0.zip` → 解压 → 双击 `DouyinCommentMiner.exe` 就能用。

工作流定义在 [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml)，
与本地 `build_windows.bat` 等价（同样的 PyInstaller 参数）。

> 目标电脑需要装了 Chrome 或 Edge（Win10/11 自带 Edge 即可）。如果要给**完全裸机**
> 用，把 workflow 里 `playwright install chromium` 那段注释打开，让产物自带浏览器内核
> （zip 会从 ~50MB 涨到 ~200MB）。

---

## （可选）Mac 上直接跑

你自己在 Mac 上不想打包，也可以直接：

```bash
cd douyin-comment-miner
python3 -m pip install -i https://mirrors.aliyun.com/pypi/simple/ playwright openpyxl
python3 douyin_miner_gui.py
```

同样弹出这个图形界面。

> 若你的 Mac 上 `python3 douyin_miner_gui.py` 报 tkinter 相关错（个别 Homebrew/pyenv 的 Python 会有），
> 说明那个 Python 的图形库有问题，改用命令行版即可（功能一样，只是没有窗口）：
>
> ```bash
> python3 douyin_miner.py --login
> python3 douyin_miner.py "视频链接" -k 郝美丽
> ```
