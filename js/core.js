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

  /* 人脸裁切位置与缩放：在【当前照片】上实测（OpenCV 人脸检测）。
     ⚠️ covers_meta.json 里的尺寸/face_y 已过期（照片后来重裁过），别再拿它算。
     卡片用 object-position:50% <y*100>% + transform:scale(zoom)，
     缩放中心取人脸位置，放大时脸不会跑。 */
  var PHOTO_POS = {'aespa-giselle':0.33,'aespa-karina':0.34,'aespa-ningning':0.47,'aespa-winter':0.35,'ateez-hongjoong':0.8,'ateez-jongho':0.2,'ateez-san':0.45,'ateez-seonghwa':0.29,'ateez-wooyoung':0.43,'ateez-yeosang':0.45,'ateez-yunho':0.3,'babymonster-ahyeon':0.33,'babymonster-asa':0.4,'babymonster-chiquita':0.37,'babymonster-pharita':0.31,'babymonster-rami':0.44,'babymonster-rora':0.4,'babymonster-ruka':0.38,'blackpink-jennie':0.45,'blackpink-jisoo':0.48,'blackpink-lisa':0.21,'blackpink-ros-':0.47,'bts-j-hope':0.39,'bts-jimin':0.15,'bts-jin':0.44,'bts-jung-kook':0.41,'bts-rm':0.24,'bts-suga':0.41,'bts-v':0.43,'cortis-james':0.45,'cortis-juhoon':0.37,'cortis-keonho':0.44,'cortis-martin':0.39,'cortis-seonghyeon':0.35,'enhypen-heeseung':0.43,'enhypen-jake':0.58,'enhypen-jay':0.79,'enhypen-jungwon':0.45,'enhypen-ni-ki':0.47,'illit-iroha':0.43,'illit-moka':0.43,'illit-wonhee':0.33,'illit-yunah':0.36,'itzy-chaeryeong':0.37,'itzy-lia':0.36,'itzy-ryujin':0.31,'itzy-yeji':0.73,'itzy-yuna':0.29,'ive-gaeul':0.43,'ive-leeseo':0.33,'ive-rei':0.4,'ive-wonyoung':0.37,'ive-yujin':0.35,'kiss-of-life-belle':0.38,'kiss-of-life-haneul':0.34,'kiss-of-life-julie':0.25,'kiss-of-life-natty':0.55,'le-sserafim-chaewon':0.3,'le-sserafim-eunchae':0.32,'le-sserafim-kazuha':0.42,'le-sserafim-sakura':0.46,'le-sserafim-yunjin':0.38,'nct-dream-haechan':0.24,'nct-dream-jaemin':0.24,'nct-dream-jeno':0.35,'nct-dream-jisung':0.47,'nct-dream-mark':0.29,'nct-dream-renjun':0.45,'riize-anton':0.4,'riize-eunseok':0.52,'riize-shotaro':0.43,'riize-sohee':0.39,'riize-sungchan':0.3,'riize-wonbin':0.42,'seventeen-dino':0.52,'seventeen-dk':0.35,'seventeen-hoshi':0.25,'seventeen-jeonghan':0.49,'seventeen-joshua':0.37,'seventeen-jun':0.34,'seventeen-mingyu':0.4,'seventeen-s-coups':0.53,'seventeen-seungkwan':0.29,'seventeen-the8':0.28,'seventeen-vernon':0.35,'seventeen-woozi':0.43,'stray-kids-bang-chan':0.21,'stray-kids-changbin':0.39,'stray-kids-felix':0.49,'stray-kids-han':0.36,'stray-kids-hyunjin':0.22,'stray-kids-i-n':0.58,'stray-kids-lee-know':0.41,'stray-kids-seungmin':0.23,'the-boyz-eric':0.26,'the-boyz-haknyeon':0.33,'the-boyz-jacob':0.39,'the-boyz-juyeon':0.27,'the-boyz-kevin':0.4,'the-boyz-new':0.24,'the-boyz-q':0.4,'the-boyz-sangyeon':0.8,'the-boyz-sunwoo':0.39,'the-boyz-younghoon':0.31,'twice-chaeyoung':0.35,'twice-dahyun':0.28,'twice-jeongyeon':0.42,'twice-jihyo':0.33,'twice-mina':0.38,'twice-momo':0.36,'twice-nayeon':0.36,'twice-sana':0.4,'twice-tzuyu':0.39,'tws-dohoon':0.4,'tws-hanjin':0.45,'tws-jihoon':0.42,'tws-kyungmin':0.45,'tws-shinyu':0.27,'tws-youngjae':0.28,'txt-beomgyu':0.26,'txt-huening-kai':0.49,'txt-soobin':0.41,'txt-taehyun':0.36,'txt-yeonjun':0.42};
  var PHOTO_ZOOM = {'aespa-giselle':1.01,'aespa-karina':1.43,'aespa-ningning':1.0,'aespa-winter':1.03,'ateez-hongjoong':1.65,'ateez-jongho':1.65,'ateez-san':1.65,'ateez-seonghwa':1.65,'ateez-wooyoung':1.65,'ateez-yeosang':1.0,'ateez-yunho':1.52,'babymonster-ahyeon':1.0,'babymonster-asa':1.0,'babymonster-chiquita':1.65,'babymonster-pharita':1.65,'babymonster-rami':1.31,'babymonster-rora':1.65,'babymonster-ruka':1.65,'blackpink-jennie':1.0,'blackpink-jisoo':1.0,'blackpink-lisa':1.65,'blackpink-ros-':1.65,'bts-j-hope':1.49,'bts-jimin':1.65,'bts-jin':1.0,'bts-jung-kook':1.0,'bts-rm':1.65,'bts-suga':1.0,'bts-v':1.0,'cortis-james':1.0,'cortis-juhoon':1.0,'cortis-keonho':1.0,'cortis-martin':1.45,'cortis-seonghyeon':1.33,'enhypen-heeseung':1.6,'enhypen-jake':1.0,'enhypen-jay':1.65,'enhypen-jungwon':1.08,'enhypen-ni-ki':1.65,'illit-iroha':1.01,'illit-moka':1.36,'illit-wonhee':1.65,'illit-yunah':1.15,'itzy-chaeryeong':1.0,'itzy-lia':1.34,'itzy-ryujin':1.0,'itzy-yeji':1.65,'itzy-yuna':1.29,'ive-gaeul':1.04,'ive-leeseo':1.65,'ive-rei':1.26,'ive-wonyoung':1.3,'ive-yujin':1.65,'kiss-of-life-belle':1.65,'kiss-of-life-haneul':1.53,'kiss-of-life-julie':1.65,'kiss-of-life-natty':1.65,'le-sserafim-chaewon':1.65,'le-sserafim-eunchae':1.65,'le-sserafim-kazuha':1.24,'le-sserafim-sakura':1.36,'le-sserafim-yunjin':1.0,'nct-dream-haechan':1.65,'nct-dream-jaemin':1.22,'nct-dream-jeno':1.65,'nct-dream-jisung':1.06,'nct-dream-mark':1.65,'nct-dream-renjun':1.0,'riize-anton':1.11,'riize-eunseok':1.0,'riize-shotaro':1.01,'riize-sohee':1.06,'riize-sungchan':1.17,'riize-wonbin':1.0,'seventeen-dino':1.65,'seventeen-dk':1.65,'seventeen-hoshi':1.33,'seventeen-jeonghan':1.0,'seventeen-joshua':1.5,'seventeen-jun':1.62,'seventeen-mingyu':1.65,'seventeen-s-coups':1.15,'seventeen-seungkwan':1.65,'seventeen-the8':1.65,'seventeen-vernon':1.65,'seventeen-woozi':1.32,'stray-kids-bang-chan':1.65,'stray-kids-changbin':1.65,'stray-kids-felix':1.48,'stray-kids-han':1.65,'stray-kids-hyunjin':1.65,'stray-kids-i-n':1.38,'stray-kids-lee-know':1.36,'stray-kids-seungmin':1.65,'the-boyz-eric':1.01,'the-boyz-haknyeon':1.16,'the-boyz-jacob':1.0,'the-boyz-juyeon':1.65,'the-boyz-kevin':1.58,'the-boyz-new':1.46,'the-boyz-q':1.65,'the-boyz-sangyeon':1.65,'the-boyz-sunwoo':1.34,'the-boyz-younghoon':1.29,'twice-chaeyoung':1.65,'twice-dahyun':1.45,'twice-jeongyeon':1.03,'twice-jihyo':1.28,'twice-mina':1.33,'twice-momo':1.0,'twice-nayeon':1.21,'twice-sana':1.2,'twice-tzuyu':1.08,'tws-dohoon':1.65,'tws-hanjin':1.06,'tws-jihoon':1.65,'tws-kyungmin':1.39,'tws-shinyu':1.65,'tws-youngjae':1.65,'txt-beomgyu':1.65,'txt-huening-kai':1.03,'txt-soobin':1.02,'txt-taehyun':1.65,'txt-yeonjun':1.29};
  App.photoPos = function (id) {
    var y = PHOTO_POS[id];
    if (typeof y !== 'number') y = 0.39;
    return '50% ' + Math.round(y * 100) + '%';
  };
  App.photoZoom = function (id) {
    var z = PHOTO_ZOOM[id];
    return (typeof z === 'number') ? z : 1;
  };
  /** 人脸垂直位置（0~1），供 transform-origin 用 */
  App.photoY = function (id) {
    var y = PHOTO_POS[id];
    return (typeof y === 'number') ? y : 0.39;
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
