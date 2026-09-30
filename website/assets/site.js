/* 听潮官网交互：导航毛玻璃、入场动画、数字滚动、FAQ 手风琴互斥 */
(function () {
  'use strict';

  // 滚动后给导航加描边
  var nav = document.querySelector('.nav');
  var onScroll = function () { nav && nav.classList.toggle('stuck', window.scrollY > 8); };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  // 入场动画（尊重系统的"减弱动态效果"）
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var targets = document.querySelectorAll('.grid > .card, .steps > .step, .price-grid > .plan, .dl-grid > .dl, .faq details, .shot, .sec > .h2, .sec > .sub');
  if (reduce || !('IntersectionObserver' in window)) {
    targets.forEach(function (el) { el.classList.add('in'); });
  } else {
    targets.forEach(function (el) { el.classList.add('rv'); });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        var sibs = Array.prototype.indexOf.call(e.target.parentNode.children, e.target);
        e.target.style.transitionDelay = Math.min(sibs, 5) * 70 + 'ms';
        e.target.classList.add('in');
        io.unobserve(e.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    targets.forEach(function (el) { io.observe(el); });
  }

  // 数字滚动
  var nums = document.querySelectorAll('.num[data-to]');
  var animate = function (el) {
    var to = parseInt(el.getAttribute('data-to'), 10) || 0;
    if (reduce || to === 0) { el.textContent = String(to); return; }
    var t0 = performance.now(), dur = 1100;
    var step = function (now) {
      var p = Math.min(1, (now - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(to * eased));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  if ('IntersectionObserver' in window && !reduce) {
    var nio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { animate(e.target); nio.unobserve(e.target); } });
    }, { threshold: 0.6 });
    nums.forEach(function (n) { nio.observe(n); });
  } else {
    nums.forEach(animate);
  }

  // FAQ：一次只展开一个
  var list = document.querySelectorAll('.faq details');
  list.forEach(function (d) {
    d.addEventListener('toggle', function () {
      if (!d.open) return;
      list.forEach(function (o) { if (o !== d) o.removeAttribute('open'); });
    });
  });

  // ---------- macOS 芯片架构自适应 ----------
  // Apple Silicon 上 Chrome 的 UA 仍报 Intel，因此用 WebGL 渲染器兜底判断
  var isIntelMac = false;
  (function detectArch() {
    if (!/Macintosh|Mac OS X/i.test(navigator.userAgent)) { applyArch('arm'); return; }
    try {
      var c = document.createElement('canvas');
      var gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      var dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
      var r = dbg ? String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)) : '';
      if (/Apple M\d|Apple GPU/i.test(r)) isIntelMac = false;
      else if (/Intel|AMD|NVIDIA/i.test(r)) isIntelMac = true;
    } catch (e) { /* 保守按 Apple 芯片 */ }
    applyArch(isIntelMac ? 'intel' : 'arm');
  })();

  function applyArch(which) {
    document.querySelectorAll('.mac-dl').forEach(function (a) {
      var f = a.getAttribute(which === 'intel' ? 'data-intel' : 'data-arm');
      if (f) a.setAttribute('href', '/downloads/' + f);
    });
    var note = document.getElementById('arch-note');
    if (!note || !note.firstChild) return;
    note.firstChild.nodeValue = '已按 ' + (which === 'intel' ? 'Intel 芯片' : 'Apple 芯片') + ' 准备 macOS 安装包 · ';
    var sw = document.getElementById('arch-switch');
    if (sw) sw.textContent = which === 'intel' ? '我是 Apple 芯片' : '我是 Intel 芯片';
    checkDownloads();
  }
  var swBtn = document.getElementById('arch-switch');
  if (swBtn) swBtn.addEventListener('click', function (e) {
    e.preventDefault();
    isIntelMac = !isIntelMac;
    applyArch(isIntelMac ? 'intel' : 'arm');
  });

  // ---------- 下载可用性探测：缺失的包置灰并标「即将上线」 ----------
  function probe(url) {
    return fetch(url, { method: 'HEAD' }).then(function (r) { return r.ok; }).catch(function () { return false; });
  }
  function paintPending(a) {
    a.style.opacity = '.5';
    a.style.pointerEvents = 'none';
    if (a.querySelector('.dl-tip')) return;
    var tip = document.createElement('span');
    tip.className = 'dl-tip';
    tip.textContent = '即将上线';
    a.appendChild(tip);
  }
  function checkDownloads() {
    document.querySelectorAll('.dl').forEach(function (a) {
      var href = a.getAttribute('href');
      if (!href || href.charAt(0) !== '/') return;
      a.style.position = 'relative';
      a.style.opacity = '';
      a.style.pointerEvents = '';
      var t = a.querySelector('.dl-tip'); if (t) t.remove();
      probe(href).then(function (ok) { if (!ok) paintPending(a); });
    });
  }
  checkDownloads();
})();
