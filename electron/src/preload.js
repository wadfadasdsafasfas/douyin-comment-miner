'use strict';
/* 听潮 · preload：把主进程的原生能力用最小接口暴露给页面（contextIsolation 开启，页面拿不到 Node） */
const { contextBridge, ipcRenderer } = require('electron');

const ALLOWED_IN = new Set(['menu-config', 'menu-start', 'menu-stop', 'goto', 'auth-changed']);

contextBridge.exposeInMainWorld('tingchao', {
  /** 订阅主进程事件：menu-config / menu-start / menu-stop / goto */
  on(channel, cb) {
    if (!ALLOWED_IN.has(channel) || typeof cb !== 'function') return () => {};
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on(channel, handler);
    return () => ipcRenderer.removeListener(channel, handler);
  },
  /** 原生系统通知（新线索、任务完成） */
  notify: (title, body) => ipcRenderer.invoke('tc:notify', { title, body }),
  /** 用系统浏览器打开外链 */
  openExternal: url => ipcRenderer.invoke('tc:open-external', url),
  /** 运行环境信息 */
  platform: () => ipcRenderer.invoke('tc:platform'),
});
