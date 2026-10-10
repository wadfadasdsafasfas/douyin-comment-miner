'use strict';
/* 听潮 TideSignal · Electron 主进程
   职责：拉起并守护 Python sidecar → 打开窗口加载它 → 提供托盘/通知/外链等原生能力。
   原则：主进程不碰业务数据，业务全在 sidecar（Python）与 renderer（web/）。 */

const { app, BrowserWindow, Menu, Tray, Notification, ipcMain, shell, dialog, nativeImage } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');

const IS_DEV = !app.isPackaged;
const ELECTRON_DIR = path.resolve(__dirname, '..');
const REPO_DIR = path.resolve(ELECTRON_DIR, '..');

const state = {
  win: null,
  tray: null,
  sidecar: null,
  port: 0,
  restarting: false,
  quitting: false,
};

/* ---------------------------------------------------------------- 日志 */
const log = (...a) => console.log('[听潮]', ...a);
const warn = (...a) => console.warn('[听潮]', ...a);

/* 应用安装根目录：mac 为 .app 的父目录，win 为 exe 所在目录。
   exe 形如 <root>/听潮.app/Contents/MacOS/听潮，按 .app 边界回溯，
   不要写死层数——之前少退一层，升级包被装进 听潮.app/听潮.app 里了。 */
function appInstallRoot() {
  const exe = app.getPath('exe');
  if (process.platform !== 'darwin') return path.dirname(exe);
  let dir = path.dirname(exe);                 // .../听潮.app/Contents/MacOS
  for (let i = 0; i < 6 && dir !== path.dirname(dir); i++) {
    if (path.basename(dir).toLowerCase().endsWith('.app')) return path.dirname(dir);
    dir = path.dirname(dir);
  }
  return path.resolve(path.dirname(exe), '..', '..', '..');  // 兜底：Contents 上三级
}

/* ---------------------------------------------------------------- sidecar */
function sidecarSpec() {
  if (IS_DEV) {
    const venvPy = path.join(REPO_DIR, '.venv', 'bin', 'python');
    const venvWin = path.join(REPO_DIR, '.venv', 'Scripts', 'python.exe');
    const cmd = fs.existsSync(venvPy) ? venvPy : (fs.existsSync(venvWin) ? venvWin : 'python3');
    return { cmd, args: ['-m', 'tingchao.local_api', '--port', '0'], cwd: REPO_DIR };
  }
  const exe = process.platform === 'win32' ? 'tingchao-sidecar.exe' : 'tingchao-sidecar';
  const res = path.join(process.resourcesPath, 'sidecar');
  // PyInstaller --onedir：可执行文件在 sidecar/tingchao-sidecar/ 目录内
  // PyInstaller --onefile：可执行文件直接是 sidecar/tingchao-sidecar(.exe)
  const candidates = [path.join(res, 'tingchao-sidecar', exe), path.join(res, exe)];
  const bin = candidates.find(p => fs.existsSync(p));
  if (!bin) return { cmd: candidates[0], args: ['--port', '0'], cwd: app.getPath('documents'), missing: true };
  return { cmd: bin, args: ['--port', '0'], cwd: app.getPath('documents') };
}

function startSidecar() {
  return new Promise((resolve, reject) => {
    const { cmd, args, cwd } = sidecarSpec();
    if (!fs.existsSync(cmd) && path.isAbsolute(cmd)) {
      return reject(new Error(`找不到本地服务程序：${cmd}\n请先执行 PyInstaller 打包 sidecar（见 MIGRATION.md）`));
    }
    log('启动 sidecar:', cmd, args.join(' '), 'cwd=', cwd);
    let buf = '';
    let child;
    try {
      child = spawn(cmd, args, {
        cwd,
        env: Object.assign({}, process.env, {
          PYTHONUNBUFFERED: '1',
          PYTHONIOENCODING: 'utf-8',
          // 供 sidecar 的自升级逻辑定位"要替换哪个目录"，并知道宿主 PID 以便等它退出
          TC_APP_ROOT: appInstallRoot(),
          TC_HOST_PID: String(process.pid),
        }),
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      });
    } catch (e) { return reject(e); }

    state.sidecar = child;
    child.stdout.on('data', d => {
      const s = d.toString();
      buf += s;
      const m = buf.match(/SIDECAR_PORT=(\d+)/);
      if (m && !state.port) { state.port = Number(m[1]); log('sidecar 端口', state.port); resolve(state.port); }
    });
    child.stderr.on('data', d => warn('sidecar:', String(d).trim()));
    child.on('error', e => { state.port = 0; reject(e); });
    child.on('exit', code => {
      const wasUp = state.port;
      state.sidecar = null; state.port = 0;
      if (!state.quitting && wasUp) {
        warn(`sidecar 退出（code=${code}），尝试重启…`);
        recoverAfterSidecarExit();
      }
    });
    setTimeout(() => { if (!state.port) reject(new Error('sidecar 15 秒内未就绪')); }, 15000);
  });
}

function waitHealth(port, tries = 40) {
  return new Promise((resolve, reject) => {
    let n = 0;
    const tick = () => {
      const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 1500 }, res => {
        res.resume(); resolve(true);
      });
      req.on('error', () => { if (++n >= tries) return reject(new Error('本地服务健康检查失败')); setTimeout(tick, 400); });
      req.on('timeout', () => { req.destroy(); if (++n >= tries) return reject(new Error('本地服务健康检查超时')); setTimeout(tick, 400); });
    };
    tick();
  });
}

async function recoverAfterSidecarExit() {
  if (state.restarting) return;
  state.restarting = true;
  try {
    const p = await startSidecar();
    await waitHealth(p);
    if (state.win && !state.win.isDestroyed()) await state.win.loadURL(`http://127.0.0.1:${p}/`);
    log('sidecar 已恢复');
  } catch (e) {
    warn('恢复失败：', e.message);
    dialog.showErrorBox('本地服务异常', `${e.message}\n\n可以重试：退出后重新打开「听潮」。`);
  } finally {
    state.restarting = false;
  }
}

function stopSidecar() {
  if (!state.sidecar) return;
  try { state.sidecar.kill(process.platform === 'win32' ? 'taskkill' : 'SIGTERM'); } catch (e) { /* 已退出 */ }
  state.sidecar = null;
}

/* ---------------------------------------------------------------- 窗口 */
function createWindow(port) {
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 1040,
    minHeight: 680,
    show: false,
    title: '听潮 · TideSignal',
    backgroundColor: '#F5F5FA',
    autoHideMenuBar: true,
    // 内容顶到窗口边缘：mac 隐藏标题栏只留红绿灯，Windows 用无边框默认样式
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: process.platform === 'darwin' ? { x: 16, y: 14 } : undefined,
    fullscreenable: true,
    icon: path.join(ELECTRON_DIR, 'build', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      devTools: IS_DEV,
    },
  });
  state.win = win;

  win.once('ready-to-show', () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };                       // 站内跳转交给页面，外部一律系统浏览器
  });
  win.webContents.on('did-fail-load', (e, code, desc) => {
    if (code === -3) return;                          // 主动取消，忽略
    warn('页面加载失败', code, desc);
    setTimeout(() => { if (state.port) win.loadURL(`http://127.0.0.1:${state.port}/`); }, 1200);
  });
  // 输入框右键菜单：没有它，鼠标用户依然无法粘贴
  win.webContents.on('context-menu', (e, params) => {
    const items = params.isEditable
      ? [
          { role: 'cut', label: '剪切' }, { role: 'copy', label: '复制' },
          { role: 'paste', label: '粘贴' }, { type: 'separator' },
          { role: 'selectAll', label: '全选' },
        ]
      : (params.selectionText
          ? [{ role: 'copy', label: '复制' }]
          : []);
    if (!items.length) return;
    const menu = Menu.buildFromTemplate(items);
    menu.popup({ window: win });
  });
  win.on('close', e => {
    if (!state.quitting && state.tray) {             // 有托盘时关窗=收进托盘
      e.preventDefault();
      win.hide();
    }
  });
  win.on('closed', () => { state.win = null; });

  win.loadURL(`http://127.0.0.1:${port}/`);
  // 不再自动弹出 DevTools（试用时很干扰）；需要时用「窗口 → 开发者工具」或 TC_DEVTOOLS=1
  if (IS_DEV && process.env.TC_DEVTOOLS === '1') win.webContents.openDevTools({ mode: 'detach' });
  return win;
}

/* ---------------------------------------------------------------- 托盘与通知 */
function trayIcon() {
  const p = path.join(ELECTRON_DIR, 'build', process.platform === 'win32' ? 'icon.ico' : 'trayTemplate.png');
  if (!fs.existsSync(p)) return nativeImage.createEmpty();
  const img = nativeImage.createFromPath(p);
  if (process.platform === 'darwin') img.setTemplateImage(true);
  return img;
}

function createTray() {
  if (state.tray) return;
  const tray = new Tray(trayIcon());
  tray.setToolTip('听潮 · 评论监控');
  const menu = Menu.buildFromTemplate([
    { label: '显示主窗口', click: () => { state.win ? state.win.show() : null; } },
    { type: 'separator' },
    { label: '开始抓取', click: () => send('menu-start') },
    { label: '停止抓取', click: () => send('menu-stop') },
    { label: '抓取配置…', click: () => send('menu-config') },
    { type: 'separator' },
    { label: '退出 听潮', click: () => { state.quitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
  tray.on('click', () => { if (state.win) { state.win.show(); state.win.focus(); } });
  state.tray = tray;
}

function send(channel, payload) {
  if (state.win && !state.win.isDestroyed()) state.win.webContents.send(channel, payload);
}

function notify(title, body) {
  if (!Notification.isSupported()) return;
  new Notification({ title, body, silent: true }).show();
}

/* ---------------------------------------------------------------- 应用菜单 */
function buildAppMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{ label: '听潮', submenu: [
      { role: 'about', label: '关于 听潮' }, { type: 'separator' },
      { role: 'hide', label: '隐藏' }, { role: 'hideOthers' }, { role: 'unhide' },
      { type: 'separator' }, { role: 'quit', label: '退出' } ] }] : []),
    // 「编辑」菜单必须存在：macOS 下 ⌘C/⌘V 是靠菜单 role 的 accelerator 生效的，
    // 少了这一项，输入框就无法粘贴剪贴板内容（用户反馈的「只能打字」问题）
    { label: '编辑', submenu: [
      { role: 'undo', label: '撤销' }, { role: 'redo', label: '重做' }, { type: 'separator' },
      { role: 'cut', label: '剪切' }, { role: 'copy', label: '复制' },
      { role: 'paste', label: '粘贴' }, { role: 'pasteAndMatchStyle', label: '粘贴并匹配样式' },
      { role: 'delete', label: '删除' }, { type: 'separator' },
      { role: 'selectAll', label: '全选' } ] },
    { label: '窗口', submenu: [
      { label: '打开抓取配置', accelerator: 'CmdOrCtrl+,', click: () => send('menu-config') },
      { label: '开始抓取', accelerator: 'CmdOrCtrl+R', click: () => send('menu-start') },
      { type: 'separator' },
      { role: 'reload', visible: IS_DEV }, { role: 'toggleDevTools', visible: IS_DEV },
      { type: 'separator' }, { role: 'minimize' }, { role: 'zoom' },
      ...(isMac ? [{ role: 'close' }] : [{ role: 'quit' }]) ] },
    { label: '帮助', submenu: [
      { label: '使用说明', click: () => { if (state.win) state.win.webContents.send('goto', 'help'); } },
      { label: '官网/客服', click: () => shell.openExternal('https://tingsignal.cn') } ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/* ---------------------------------------------------------------- IPC */
ipcMain.handle('tc:notify', (e, { title, body }) => { notify(String(title || '听潮'), String(body || '')); return true; });
ipcMain.handle('tc:open-external', (e, url) => { if (/^https?:\/\//.test(String(url))) shell.openExternal(url); return true; });
ipcMain.handle('tc:platform', () => ({
  platform: process.platform, version: app.getVersion(), dev: IS_DEV,
  appRoot: appInstallRoot(), hostPid: process.pid,
}));
// 自升级：更新器在等这个进程退出后才会替换文件
ipcMain.handle('tc:quit-for-update', () => {
  log('为安装更新而退出');
  state.quitting = true;
  setTimeout(() => app.quit(), 150);
  return true;
});

/* ---------------------------------------------------------------- 生命周期 */
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (state.win) { if (state.win.isMinimized()) state.win.restore(); state.win.show(); state.win.focus(); }
  });

  app.whenReady().then(async () => {
    if (process.platform === 'darwin') app.dock && app.dock.setIcon(trayIcon());
    buildAppMenu();
    try {
      const port = await startSidecar();
      await waitHealth(port);
      createWindow(port);
      createTray();
    } catch (e) {
      dialog.showErrorBox('听潮 启动失败', e.message);
      app.quit();
    }
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0 && state.port) createWindow(state.port);
      else if (state.win) state.win.show();
    });
  });

  app.on('before-quit', () => { state.quitting = true; stopSidecar(); });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') { stopSidecar(); app.quit(); } });
}
