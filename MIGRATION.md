# 听潮 · 架构迁移说明（Electron + Python sidecar）

> 迁移日期：2026-09-30 ｜ 迁移前快照：git tag `pre-refactor-20260930-1422`
> 回退方式：`git checkout pre-refactor-20260930-1422`，或用仓库外备份
> `../backups/tingchao-src-20260930-1422.zip` / `../backups/src-20260930-1422/`

## 一、这次做了什么

把「CustomTkinter 单体 GUI」拆成 **Electron 壳 + Python 本地服务 + Web UI** 三层，
**没有删除任何原有文件**，老客户端 `douyin_miner_gui.py` 仍可照常运行，可双跑对比。

```
douyin-comment-miner/
├─ douyin_miner.py          引擎（未改动）
├─ douyin_miner_gui.py      旧 CustomTkinter 客户端（保留，作回退）
├─ license.py               授权（未改动，被 sidecar 复用）
├─ tingchao/                ★ 新增：本地服务层
│   ├─ leads_db.py          本地 SQLite 线索池（跨任务去重、状态流转、导出）
│   ├─ crawler.py           采集任务管理 + 事件总线（包 engine.run_batch）
│   ├─ local_api.py         FastAPI sidecar，仅监听 127.0.0.1
│   └─ web/                 ★ 前端 UI（多彩数据版设计系统）
│       ├─ index.html  app.js  app.css
│       ├─ tokens.css  tingchao-ui.css
│       └─ icons/  logo/
├─ electron/                ★ 新增：桌面壳
│   ├─ package.json         electron 33 + electron-builder 25
│   └─ src/main.js  preload.js
├─ admin/                   后台（已修路径 bug，见第四节）
└─ server/                  云端授权服务（未改动）
```

**边界原则**：Electron 主进程不碰业务，只负责拉起/守护 sidecar、托盘、原生通知、外链；
所有业务在 sidecar，UI 只做展示与调用。采集**始终在用户本机执行**（本机 IP + 本机登录态），
不把爬虫搬到云端——那是这类产品的风控生命线。

## 二、开发环境启动

```bash
# 1) 装依赖（sidecar 新增了 fastapi/uvicorn）
./.venv/bin/python -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2) 装 Electron（国内必须走镜像，否则下载 electron 二进制会 ECONNRESET）
cd electron
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ \
  npm install --registry=https://registry.npmmirror.com

# 3) 起桌面端（主进程会自动拉起 sidecar 并随机选端口）
npm run dev
```

不想开 Electron 也可以直接 `./run_desktop.sh --browser`，用浏览器调试同一套 UI。

## 三、打包发布

```bash
# 1) 先把 sidecar 打成单目录二进制
./build_sidecar.sh            # 产物：dist-sidecar/tingchao-sidecar[.exe]

# 2) 再打桌面安装包（electron-builder 会把 dist-sidecar 拷进 Resources/sidecar）
cd electron && npm run dist:mac     # macOS dmg
cd electron && npm run dist:win     # Windows nsis（需在 Windows 或 CI 上跑）
```

CI 里在 `build-release.yml` 增加两步即可：PyInstaller 打 sidecar → electron-builder 打包。

## 四、过程中修掉的三个真实缺陷

1. **CSS 特异性压垮组件样式**（影响客户端 + 后台 + 切图包）
   `tingchao-ui.css` 里 reset 写成 `.tc-app button{background:none}`，特异性 0-1-1，
   压过了组件类 `.tc-btn-primary`（0-1-0），导致所有主按钮渐变失效、ghost 按钮无边框。
   已改为 `.tc-app :where(button){...}`（零特异性）。三处副本均已同步：
   `tingchao/web/`、`admin/static/`、切图交付包。

2. **后台静态资源与路由 404**（线上同样受影响）
   模板统一引用 `/admin/static/...`、链接写 `/admin/users`，但 `admin.py` 挂载的是 `/static`、
   路由注册不带前缀，nginx 又不改写路径 → 后台页面**无样式且点导航 404**。
   已加 `StripPrefixMiddleware` 在 ASGI 层剥离 `/admin` 前缀，直连与经 nginx 两种方式都可用。
   注意：**不要**在中间件里设 `scope["root_path"]`，Starlette 1.6 的 `get_route_path()` 会再剥一次，
   导致 `StaticFiles` 挂载匹配不上（实测会 404）。

3. **sidecar 依赖缺失**：`requirements.txt` 补了 `fastapi / uvicorn[standard] / pydantic`。

## 五、新旧能力对照

| 能力 | 旧版 | 新架构 |
|---|---|---|
| 界面 | CustomTkinter 自绘（2054 行单文件） | Web UI，复用已定稿设计系统 |
| 采集结果 | 每次导出 CSV/Excel，无沉淀 | 本地线索池，跨任务去重、状态流转（待联系/已联系/已回复/已成交/无效） |
| 实时进度 | 线程 + 队列轮询 UI | SSE 事件流（`/api/crawl/events`） |
| 托盘/通知 | pystray + 自研 notifier | Electron Tray / Notification（旧模块保留给老 GUI） |
| 图表 | 无 | 趋势折线 + 命中构成环形（纯 SVG，零依赖） |
| 多平台 | 只有抖音 | `platforms` 注册表 + `ready` 标记，小红书接口位已留（`crawler` 里按平台分派） |

## 六、下一步建议（按性价比排序）

1. **拆 `douyin_miner_gui.py`**：新 UI 已承接全部界面后，把旧 GUI 降级为「命令行/应急模式」，
   或彻底退役，避免两套 UI 长期并存。
2. **接入小红书采集器**：在 `tingchao/crawler.py` 里按 `platform` 分派到新的 `xhs_miner.py`，
   前端已能自动列出未就绪平台。
3. **升级包签名**：`updater.py` 目前是「下载 zip 覆盖自身」，无校验。Electron 侧改用
   `electron-updater`（minisign 签名 + 灰度），sidecar 作为资源随包更新。
4. **HTTPS**：`server_url.py` 仍是 `http://117.72.28.123`，token 明文传输，上线前必须补证书。
5. **`deploy/douyin-auth-admin.service` 仍写着 Streamlit**，而 admin 早就是 FastAPI，
   按该 unit 部署会起错进程，需改成 uvicorn 启动。

## Windows 制品接力（国内服务器 → GitHub 限速的解法）

CI 出包后，制品要从 GitHub 拉回本地再传到服务器。单条 curl 只能跑到 ~56KB/s（297MB 要一个多小时）。
Actions 制品的重定向地址指向 Azure Blob，支持 `Range`（返回 206），所以切成 6 段并行下载即可：

```bash
bash tools/parallel-artifact-get.sh <runId> <artifactId> <sizeInBytes> [段数]
# 例：bash tools/parallel-artifact-get.sh 36813956256 11140308096 297423330 6
```

实测 297MB / 16 分钟（约 310KB/s），合并后 `unzip -tq` 校验通过再上传。
签名地址每次重新解析，避免 SAS 过期；分片断点续传靠 `-C -` 加 `-r 起点-终点`。
上传前务必核对绿色包里的 `resources/sidecar/tingchao-sidecar/_internal/tingchao/web/app.js`
是否含本次改动，别把上一版制品当新版传上线。

## 安装包分发走腾讯云 COS（2026-10 起）

服务器出口只有 ~4Mbps（实测 481KB/s 顶格），官网直发安装包用户要下十几分钟。
现在包放在腾讯云 COS：桶 `tingchao-downloads-1315442697`（ap-beijing，标准存储，公开读）。

- 发布：`python3 tools/publish-to-cos.py`（密钥在 /tmp/.cos_keys，600）
- 后台 `/api/latest` 的 `downloads.windows/macos` 与官网按钮都指向 COS；
  服务器 `/opt/app/downloads/` 留一份备份
- 成本：存储 ~0.1 元/GB/月，外网流出 ~0.5 元/GB
- 腾讯云的「主账号 ID」是 UIN（100028693851），建桶要用的 APPID 是另一个数
  （1315442697），用 CAM `GetUserAppId` 查，别拿 UIN 拼桶名（会 AccessDenied）

## macOS「已损坏」问题与签名路线

未签名（或签名与 CodeResources 不一致）的 App 一旦带上浏览器下载的
`com.apple.quarantine`，macOS 直接判「已损坏，无法打开」，右键打开无效。
已做两件事缓解：① electron-builder `afterSign` 钩子（`scripts/adhoc-sign.js`）
做一致的 ad-hoc 深签名；② 官网帮助区给出 `xattr -rd com.apple.quarantine`
命令。彻底解决 = 加入 Apple Developer Program（$99/年），CI 里用
Developer ID Application 证书签名 + `xcrun notarytool submit` 公证 +
`stapler staple` 装订，之后用户双击即开、不再弹任何提示。

## v1.0.1（2026-10-09）：新服务器切换 + 到期秒级管控

- 客户端默认授权地址改为 https://tingchao.cengfengkeji.cn；版本 1.0.1
- 到期判定精确到秒（字符串比较 ISO 即字典序）；服务端过期文案带完整年月日时分秒（auth_db._fmt_exp）
- 客户端每 3 分钟调 /api/auth/me（pollAuthOnce），过期/停用/被顶号 → 清 token、踢回登录页并 toast 精确原因
- 后台改到期升级 datetime-local（step=1 可输秒），快捷选项按当天 23:59:59；列表时间到秒（admin.fmt_date）
- 老京东云服务器 nginx 的 /api/ 已桥接代理到新域名（旧 IP 客户端无感，可继续收到 1.0.1 升级），
  客户端全部升级后可撤桥、退订老服务器
