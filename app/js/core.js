/* ============================================================
   core.js — window.App 骨架（冻结接口核心，所有模块依赖此契约）
   ------------------------------------------------------------
   职责：nav / screens / 事件总线 on·emit / state（localStorage 持久化）
        / photoFor / toast 全局反馈 / 状态栏时钟 / boot
   约定：
   · 所有模块只【附加】属性到 window.App，禁止整体重写
     （data.js 可能先执行并创建占位 App，本文件用合并方式补全骨架）
   · 屏幕模块注册：App.screens.home = { mount(el), show(), hide() }
     mount 只会被调用一次；show/hide 随导航切换
   · 事件：'speaking:start' | 'speaking:sentence' | 'speaking:end' | 'toast'
          （另发 'nav:change' {from,to} 与 'mic:tap'，均为附加约定）
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});

  var SCREEN_NAMES = ['home', 'convos', 'chat', 'cards', 'me'];
  /* 底栏高亮映射：进入某个二级屏幕时，高亮它所属的 tab（会话列表页与聊天页共用「聊天」tab） */
  var TAB_OF = { convos: 'convos', chat: 'convos', home: 'home', cards: 'cards', me: 'me' };
  App.screens = App.screens || {};

  /* ============ 事件总线（极简） ============ */
  var listeners = Object.create(null);

  /** 订阅事件；返回取消订阅函数 */
  App.on = function (evt, cb) {
    if (typeof evt !== 'string' || typeof cb !== 'function') return function () {};
    (listeners[evt] = listeners[evt] || []).push(cb);
    return function off() {
      var arr = listeners[evt];
      if (!arr) return;
      var i = arr.indexOf(cb);
      if (i > -1) arr.splice(i, 1);
    };
  };

  /** 发布事件（回调异常只告警，不中断其他订阅者） */
  App.emit = function (evt, data) {
    var arr = listeners[evt];
    if (!arr || !arr.length) return;
    arr.slice().forEach(function (cb) {
      try { cb(data); } catch (e) { console.warn('[App.emit] ' + evt + ' 回调出错', e); }
    });
  };

  /* ============ 照片路径 ============ */
  /** 爱豆头像/封面照片：'photos/<id>/01.jpg'（应用内相对路径，本地与网页版通用） */
  App.photoFor = function (id) {
    return 'photos/' + id + '/01.jpg';
  };

  /* ============ 状态：localStorage 持久化（键前缀 idoltalk-app-） ============ */
  var LS_KEY = 'idoltalk-app-state';
  var DEFAULT_STATE = { stars: 0, cards: [], prefs: {} };

  function loadState() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      if (raw) {
        var saved = JSON.parse(raw);
        var st = Object.assign({}, DEFAULT_STATE, saved);
        st.prefs = Object.assign({}, (saved && saved.prefs) || {});
        if (!Array.isArray(st.cards)) st.cards = [];
        if (typeof st.stars !== 'number') st.stars = 0;
        return st;
      }
    } catch (e) { console.warn('[App] 读取本地状态失败，使用默认值', e); }
    return { stars: 0, cards: [], prefs: {} };
  }

  var saveTimer = null;
  /** 保存状态（默认 250ms 防抖合并；传 true 立即落盘） */
  App.saveState = function (immediate) {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    var run = function () {
      try { localStorage.setItem(LS_KEY, JSON.stringify(App.state)); }
      catch (e) { console.warn('[App] 保存本地状态失败', e); }
    };
    if (immediate) run();
    else saveTimer = setTimeout(run, 250);
  };

  App.state = loadState();
  /* 页面隐藏/关闭前兜底落盘 */
  addEventListener('pagehide', function () { App.saveState(true); });
  addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') App.saveState(true);
  });

  /* ============ 导航 ============ */
  var phoneEl = document.getElementById('phone');

  var nav = {
    current: null,
    /** 切屏：'home' | 'chat' | 'cards' | 'me' */
    go: function (name) {
      if (SCREEN_NAMES.indexOf(name) < 0) {
        console.warn('[App.nav] 未知屏幕：', name);
        return;
      }
      var prev = nav.current;
      if (prev === name) return;

      SCREEN_NAMES.forEach(function (n) {
        var el = document.getElementById('screen-' + n);
        if (!el) return;
        var on = n === name;
        var mod = App.screens[n];
        try {
          if (on && mod && typeof mod.show === 'function') mod.show(el);
          if (!on && prev === n && mod && typeof mod.hide === 'function') mod.hide(el);
        } catch (e) { console.warn('[App.nav] 屏幕 ' + n + ' 切换钩子出错', e); }
        el.classList.toggle('on', on);
      });

      nav.prev = prev;
      nav.current = name;
      /* 驱动 CSS（如聊天屏隐藏底栏、沉浸输入坞） */
      if (phoneEl) phoneEl.dataset.nav = name;
      /* tab 高亮同步（chat/convos 共用「聊天」tab） */
      var activeTab = TAB_OF[name] || name;
      document.querySelectorAll('.tabbar .tab[data-nav]').forEach(function (t) {
        t.classList.toggle('on', t.getAttribute('data-nav') === activeTab);
      });
      App.emit('nav:change', { from: prev, to: name });
    }
  };
  App.nav = nav;

  /* tab 点击接线 */
  document.querySelectorAll('.tabbar .tab[data-nav]').forEach(function (t) {
    t.addEventListener('click', function () { nav.go(t.getAttribute('data-nav')); });
  });

  /* 中央麦克风 FAB：口语练习核心动作。
     已在聊天中 → 回聊天屏；否则 → 首页选人（后续模块可监听 'mic:tap' 接管） */
  var micBtn = document.getElementById('micFab');
  if (micBtn) {
    micBtn.addEventListener('click', function () {
      App.emit('mic:tap');
      nav.go(App.chat && App.chat.activeId ? 'chat' : 'home');
    });
  }

  /* ============ toast 全局反馈 ============ */
  var toastLayer = document.getElementById('toast-layer');

  /**
   * 弹出全局提示：App.toast('文案', 2400)
   * 也可经事件总线触发：App.emit('toast', '文案')
   */
  App.toast = function (msg, ms) {
    if (!toastLayer) return;
    var t = document.createElement('div');
    t.className = 'toast';
    var tick = document.createElement('span');
    tick.className = 'toast-tick';
    var label = document.createElement('span');
    label.textContent = String(msg == null ? '' : msg);
    t.appendChild(tick);
    t.appendChild(label);
    toastLayer.appendChild(t);
    /* 最多同时 3 条 */
    while (toastLayer.children.length > 3) toastLayer.firstElementChild.remove();
    requestAnimationFrame(function () { t.classList.add('show'); });
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () { t.remove(); }, 400);
    }, ms || 2400);
  };
  App.on('toast', function (msg) { App.toast(msg); });

  /* ============ 状态栏时钟（桌面预览装饰） ============ */
  var sbTime = document.getElementById('sb-time');
  function tickClock() {
    if (!sbTime) return;
    var d = new Date();
    var h = (d.getHours() < 10 ? '0' : '') + d.getHours();
    var m = (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
    sbTime.textContent = h + ':' + m;
  }
  tickClock();
  setInterval(tickClock, 15000);

  /* ============ boot：挂载已注册屏幕，默认进 home ============ */
  App.boot = function () {
    if (App._booted) return;
    App._booted = true;

    SCREEN_NAMES.forEach(function (n) {
      var el = document.getElementById('screen-' + n);
      var mod = App.screens[n];
      if (!el || !mod) {
        if (!mod) console.warn('[App.boot] 屏幕未注册（仅显示占位）：', n);
        return;
      }
      if (el.getAttribute('data-mounted')) return;
      try {
        if (typeof mod.mount === 'function') mod.mount(el);
        el.setAttribute('data-mounted', '1');
      } catch (e) {
        console.warn('[App.boot] 挂载屏幕失败：' + n, e);
      }
    });

    nav.go('home');
  };
})();
