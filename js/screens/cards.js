/* ============================================================
   screens/cards.js — 词卡屏「今夜词卡 / TONIGHT'S CARDS」
   ------------------------------------------------------------
   契约：App.screens.cards = { mount(el), show(), hide() }
   el = index.html 的 #screen-cards（mount 时整体替换 .scaffold 占位）

   设计（延续「杂志梦幻」语言）：
   · 报头 IdolTalk. + Vol.132 + 发丝线；kicker「The Card Desk · 今夜词卡」
     + 超大衬线 TONIGHT'S CARDS（grad 高亮 Cards）+ 衬线斜体中文副题
   · 词卡 = 3D 翻转玻璃卡：正面 = 衬线斜体例句（关键词 .wavy 波浪下划线）
     + 中文 + 图版注释（图版 N°01 — KARINA / AESPA）+「听一听」；
     背面 = 词条（grad-text）+ 音标 + 释义 + 标记已掌握 / 删除
   · 「听一听」→ App.voice.speak（静默降级，不弹错）
   · 空态：杂志排版引语 + CTA 去找爱豆聊聊
   · 入场 .rise + --i stagger；卡片 .sheen 扫光；操作走 .toast 反馈
   ------------------------------------------------------------
   词卡数据（App.state.cards，localStorage 持久化）：
   { id, word, phon, def, en, zh, from, t, mastered }
   本模块同时提供 App.cards 附加接口（供聊天屏等调用）：
   App.cards = { list(), add(rec), remove(id), toggleMaster(id) }
   事件：'cards:change'（增删/掌握态变化后广播，便于统计刷新）
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});
  App.screens = App.screens || {};

  /* ---------- 小工具 ---------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /* 爱豆索引（含 N° 期号），懒构建一次 */
  var _idx = null;
  function idolIndex() {
    if (!_idx) {
      _idx = {};
      (App.idols || []).forEach(function (p, i) { _idx[p.id] = { p: p, no: i + 1 }; });
    }
    return _idx;
  }
  function idolInfo(id) { return id ? idolIndex()[id] : null; }

  /* 例句中给词条加波浪下划线（大小写不敏感，只高亮第一处） */
  function markWord(en, word) {
    en = String(en || '');
    if (!word) return esc(en);
    var i = en.toLowerCase().indexOf(String(word).toLowerCase());
    if (i < 0) return esc(en);
    return esc(en.slice(0, i)) +
      '<em class="wavy kw">' + esc(en.slice(i, i + word.length)) + '</em>' +
      esc(en.slice(i + word.length));
  }

  /* ============================================================
     App.cards —— 词卡数据操作（附加命名空间，仅追加属性）
     ============================================================ */
  if (!App.cards) {
    App.cards = {
      /** 词卡列表（即 App.state.cards 的引用） */
      list: function () { return App.state.cards; },

      /**
       * 加入词卡（去重按 word 忽略大小写；缺省字段自动补全）
       * @returns {object|null} 新卡片；已存在时返回 null
       */
      add: function (rec) {
        if (!App.state || !Array.isArray(App.state.cards)) return null;
        var r = rec || {};
        var word = String(r.word || '').trim();
        if (!word) return null;
        var dup = App.state.cards.some(function (c) {
          return String(c.word || '').toLowerCase() === word.toLowerCase();
        });
        if (dup) {
          App.toast('这个词已经在词卡里啦');
          App.emit('cards:change');
          return null;
        }
        var card = {
          id: 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
          word: word,
          phon: String(r.phon || ''),
          def: String(r.def || ''),
          en: String(r.en || ''),
          zh: String(r.zh || ''),
          from: String(r.from || ''),
          t: typeof r.t === 'number' ? r.t : Date.now(),
          mastered: !!r.mastered
        };
        App.state.cards.push(card);
        /* 星数经济：收藏即得一颗星 */
        App.state.stars = (App.state.stars || 0) + 1;
        if (App.saveState) App.saveState();
        App.toast('已加入词卡 · 星数 +1');
        App.emit('cards:change');
        return card;
      },

      /** 删除词卡 */
      remove: function (id) {
        var arr = App.state.cards;
        if (!Array.isArray(arr)) return false;
        var i = arr.findIndex(function (c) { return c.id === id; });
        if (i < 0) return false;
        arr.splice(i, 1);
        if (App.saveState) App.saveState();
        App.toast('词卡已移除');
        App.emit('cards:change');
        return true;
      },

      /**
       * 切换「已掌握」；首次置为已掌握时星数 +1（复习得星）
       * @returns {boolean} 切换后的掌握态
       */
      toggleMaster: function (id) {
        var card = (App.state.cards || []).filter(function (c) { return c.id === id; })[0];
        if (!card) return false;
        card.mastered = !card.mastered;
        if (card.mastered) {
          App.state.stars = (App.state.stars || 0) + 1;
          App.toast('已掌握 · 星数 +1');
        }
        if (App.saveState) App.saveState();
        App.emit('cards:change');
        return !!card.mastered;
      }
    };
  }

  /* ============================================================
     屏幕样式（本屏私有，cd- / wc- 前缀；注入一次）
     ============================================================ */
  var STYLE_ID = 'style-screen-cards';
  var STYLE_CSS = [
    '/* —— 词卡屏 · 今夜词卡 —— */',
    '.cd-scroll{ flex:1; min-height:0; }',

    /* 报头 */
    '.cd-masthead{ padding:4px var(--gutter) 0; }',
    '.cd-mh-row{ display:flex; align-items:baseline; justify-content:space-between; padding-bottom:9px; }',
    '.cd-logo{ font-family:var(--serif); font-style:italic; font-size:var(--fs-title); letter-spacing:.01em; }',
    '.cd-logo i{ font-style:italic; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
    '.cd-mh-meta{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.26em; color:var(--p3); text-transform:uppercase; }',

    /* 刊首区：kicker + 超大衬线题 + 副题 + 计数行 */
    '.cd-hero{ padding:14px var(--gutter) 4px; }',
    '.cd-h1{ margin-top:10px; font-family:var(--serif); font-weight:400; font-size:34px; line-height:.99; letter-spacing:.005em; text-transform:uppercase; text-shadow:0 2px 26px rgba(0,0,0,.35); }',
    '.cd-sub{ margin-top:10px; font-family:var(--serif); font-style:italic; font-size:12.5px; color:var(--p2); letter-spacing:.03em; }',
    '.cd-meta{ margin-top:8px; font-size:var(--fs-note); letter-spacing:.14em; color:var(--p3); text-transform:uppercase; }',
    '.cd-meta b{ font-weight:600; color:var(--p2); }',
    '.cd-meta i{ font-style:normal; opacity:.5; padding:0 3px; }',

    /* 分节头：词卡架 */
    '.cd-index-head{ margin:16px var(--gutter) 0; padding-top:12px; border-top:1px solid var(--line); position:relative; display:flex; align-items:baseline; justify-content:space-between; }',
    '.cd-index-head::before{ content:""; position:absolute; left:0; top:-1px; width:46px; height:1px; background:var(--grad); }',
    '.cd-index-title{ font-family:var(--serif); font-style:italic; font-size:var(--fs-h3); letter-spacing:.02em; }',
    '.cd-index-count{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p3); text-transform:uppercase; }',

    /* 词卡列表 */
    '.cd-list{ padding:14px var(--gutter) 0; }',
    '.scroll-pad{ height:30px; }',

    /* —— 3D 翻转词卡 —— */
    '.wc{ position:relative; perspective:1100px; overflow:hidden; cursor:pointer; -webkit-tap-highlight-color:transparent; }',
    '.wc + .wc{ margin-top:16px; }',
    /* 双面用 grid 同格堆叠：容器高度 = 较高的一面（背面不用 absolute，
       否则不参与尺寸计算，内容会被压扁） */
    '.wc-inner{ position:relative; display:grid; transform-style:preserve-3d; transition:transform .75s var(--ease); }',
    '.wc.flip .wc-inner{ transform:rotateY(180deg); }',
    /* 注意：翻转面不能用 backdrop-filter（Chromium 下会无视 backface-visibility，
       两面同显）——故 .wc-face 用无模糊的玻璃渐变底，视觉与 .glass 一致 */
    '.wc-face{ grid-area:1/1; display:flex; flex-direction:column; padding:15px 16px 13px; border-radius:18px;',
    '  background:var(--glass-bg-soft); border:1px solid transparent;',
    '  box-shadow:0 14px 34px -22px rgba(0,0,0,.85);',
    '  backface-visibility:hidden; -webkit-backface-visibility:hidden; }',
    '.wc-front{ position:relative; }',
    '.wc-back{ transform:rotateY(180deg); }',
    /* 弹性间隔：把页脚钉在卡底，同时保证内容与分隔线之间的最小间距 */
    '.wc-sp{ flex:1; min-height:0; }',
    /* Chromium 的 3D 命中测试不认祖先的 backface-visibility ——
       隐藏面的叶子节点仍会拦截点击。用 pointer-events 切换双保险：
       未翻面时背面不可点，翻面后正面不可点 */
    '.wc:not(.flip) .wc-back{ pointer-events:none; }',
    '.wc.flip .wc-front{ pointer-events:none; }',
    '.wc:focus-visible{ outline:1px solid var(--line-rose); outline-offset:3px; border-radius:18px; }',

    /* 卡内排版 */
    '.wc-kick{ display:flex; align-items:center; gap:8px; font-size:var(--fs-micro); font-weight:600; letter-spacing:.26em; color:var(--p3); text-transform:uppercase; }',
    '.wc-kick .tick{ width:14px; }',
    '.wc-en{ margin-top:11px; font-family:var(--serif); font-style:italic; font-size:15.5px; line-height:1.62; letter-spacing:.01em; color:var(--paper); }',
    '.wc-en .kw{ font-style:normal; color:var(--paper); text-shadow:0 0 22px rgba(242,160,220,.25); }',
    '.wc-zh{ margin-top:9px; font-size:var(--fs-meta); line-height:1.7; color:var(--p2); letter-spacing:.02em; }',
    '.wc-foot{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:13px; padding-top:11px; border-top:1px solid var(--line); }',
    '.wc-hint{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.2em; color:var(--p4); text-transform:uppercase; }',
    '.wc-word{ margin-top:10px; font-family:var(--serif); font-style:italic; font-size:22px; letter-spacing:.01em; }',
    '.wc-phon{ margin-top:5px; font-size:9.5px; letter-spacing:.08em; color:var(--p2); }',
    '.wc-def{ margin-top:9px; font-size:12.5px; line-height:1.65; color:var(--p2); }',
    '.wc-def b{ color:var(--paper); font-weight:600; }',

    /* 听一听（.cta 变体，播放态换图标） */
    '.wc-play .ic-pause{ display:none; }',
    '.wc-play.playing .ic-pause{ display:block; }',
    '.wc-play.playing .ic-play{ display:none; }',
    '.wc-play.playing{ background:var(--grad) padding-box, var(--grad) border-box; color:#180D20; }',

    /* 已掌握印章（正面右上角，印刷图章感） */
    '.wc-stamp{ position:absolute; top:13px; right:13px; transform:rotate(-7deg);',
    '  font-family:var(--serif); font-style:italic; font-size:10px; letter-spacing:.2em; text-transform:uppercase;',
    '  color:var(--rose); border:1px solid rgba(242,160,220,.55); border-radius:5px; padding:3px 9px;',
    '  background:rgba(11,8,18,.35); pointer-events:none; }',
    '.wc.mastered .wc-front{ opacity:.88; }',

    /* 背面操作：掌握 / 删除 */
    '.wc-master{ display:inline-flex; align-items:center; gap:7px; flex:none;',
    '  font-size:9.5px; font-weight:600; letter-spacing:var(--ls-wide); padding:7px 13px; border-radius:var(--r-pill);',
    '  background:linear-gradient(var(--ink), var(--ink)) padding-box, var(--grad) border-box;',
    '  border:1px solid transparent; color:var(--paper);',
    '  transition:background var(--t-mid) var(--ease), color var(--t-mid) var(--ease); }',
    '.wc-master:hover{ background:var(--grad) padding-box, var(--grad) border-box; color:#180D20; }',
    '.wc-master.on{ background:var(--grad) padding-box, var(--grad) border-box; color:#180D20; }',
    '.wc-del{ flex:none; width:30px; height:30px; border-radius:50%; display:flex; align-items:center; justify-content:center;',
    '  border:1px solid var(--line); color:var(--p3);',
    '  transition:color var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease); }',
    '.wc-del:hover{ color:var(--rose); border-color:var(--line-rose); }',

    /* —— 空态（杂志排版） —— */
    '.cd-empty{ padding:54px 32px 30px; text-align:center; }',
    '.cd-empty .q{ font-family:var(--serif); font-style:italic; font-size:16.5px; line-height:1.72; color:var(--p2); }',
    '.cd-empty .q em{ font-style:italic; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
    '.cd-empty .zh{ margin-top:12px; font-size:var(--fs-meta); line-height:1.8; color:var(--p3); }',
    '.cd-empty .grad-rule{ margin:24px auto; }',
    '.cd-empty .note{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p4); text-transform:uppercase; }',
    '.cd-empty .cta{ margin-top:20px; }'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = STYLE_CSS;
    document.head.appendChild(st);
  }

  /* ============================================================
     屏幕模块
     ============================================================ */
  var els = {};               /* DOM 引用 */
  var lastSig = '';           /* 上次渲染签名（结构变化才重建列表） */
  var lastMasteredSig = '';   /* 上次渲染的掌握数签名 */
  var suppressRebuild = false;/* 本屏就地更新时抑制重建 */

  /* 通用图标 */
  var ICONS = {
    arrow: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 5.5 19.5 12 13 18.5"/></svg>',
    speaker: '<svg class="ic-play" width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4.2V4.8L8 9H4z" fill="currentColor"/><path d="M16.2 8.6a4.8 4.8 0 0 1 0 6.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>' +
             '<svg class="ic-pause" width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6.5" y="5" width="3.6" height="14" rx="1.4"/><rect x="13.9" y="5" width="3.6" height="14" rx="1.4"/></svg>',
    check: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 12.5 9.5 17.5 19.5 6.5"/></svg>'
  };

  /* ---------- 渲染 ---------- */

  function counts() {
    var cards = App.state.cards || [];
    var mastered = cards.filter(function (c) { return c.mastered; }).length;
    return { total: cards.length, mastered: mastered, stars: App.state.stars || 0 };
  }

  function updateHero() {
    var c = counts();
    if (els.meta) {
      els.meta.innerHTML = '共 <b>' + c.total + '</b> 张<i>·</i>已掌握 <b>' + c.mastered + '</b><i>·</i>星数 <b>' + c.stars + '</b>';
    }
    if (els.count) {
      var left = c.total - c.mastered;
      els.count.textContent = c.total === 0 ? '今夜待收藏'
        : (left === 0 ? '全部已掌握' : left + ' 张待温习');
    }
  }

  /* 列表结构签名：只看「张数 + 最新收藏时间」——
     掌握态/星数变化走就地更新（toggleMaster），不触发重建，
     否则每次标记已掌握都会重排列表、丢掉翻面状态 */
  function signature() {
    var cards = App.state.cards || [];
    var last = cards.length ? cards[cards.length - 1].t : 0;
    return cards.length + '|' + last;
  }
  /* 掌握态签名：外部模块（或本屏翻面后）改变掌握分布时重建列表；
     本屏就地更新时同步签名并抑制重建，保住翻面状态 */
  function masteredSig() {
    return (App.state.cards || []).filter(function (c) { return c.mastered; }).length + '';
  }

  /** 重建词卡列表（结构变化时才调用） */
  function renderList() {
    lastSig = signature();
    lastMasteredSig = masteredSig();
    updateHero();

    var cards = App.state.cards || [];
    var list = els.list;
    var empty = els.empty;
    if (!list || !empty) return;

    list.textContent = '';
    empty.style.display = cards.length ? 'none' : '';
    if (!cards.length) return;

    cards.forEach(function (card, i) {
      list.appendChild(buildCard(card, i));
    });
  }

  /** 构建单张词卡 DOM */
  function buildCard(card, i) {
    var info = idolInfo(card.from);
    var src = info ? (info.p.stage + ' / ' + info.p.group) : (card.from ? card.from : '词卡编辑部');
    var no = pad2(i + 1);

    var el = document.createElement('article');
    el.className = 'wc sheen rise' + (card.mastered ? ' mastered' : '');
    el.style.setProperty('--i', Math.min(i, 9));
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    el.setAttribute('aria-label', '词卡 ' + (card.word || '') + '，回车翻面');

    el.innerHTML =
      '<div class="wc-inner">' +
        /* —— 正面：例句 —— */
        '<div class="wc-face wc-front">' +
          '<div class="wc-kick"><span class="tick"></span>图版 N°' + no + ' — ' + esc(src.toUpperCase()) + '</div>' +
          (card.mastered ? '<span class="wc-stamp">已掌握</span>' : '') +
          '<p class="wc-en">&ldquo;' + markWord(card.en, card.word) + '&rdquo;</p>' +
          (card.zh ? '<p class="wc-zh">' + esc(card.zh) + '</p>' : '') +
          '<span class="wc-sp"></span>' +
          '<div class="wc-foot">' +
            '<span class="wc-hint">点击翻面 · 背面是词义</span>' +
            '<button class="cta wc-play" type="button" data-act="play" aria-label="听一听例句">' + ICONS.speaker + '<span>听一听</span></button>' +
          '</div>' +
        '</div>' +
        /* —— 背面：词义 —— */
        '<div class="wc-face wc-back">' +
          '<div class="wc-kick"><span class="tick"></span>词义 · The Meaning</div>' +
          '<div class="wc-word grad-text">' + esc(card.word || '') + '</div>' +
          (card.phon ? '<div class="wc-phon">' + esc(card.phon) + '</div>' : '') +
          '<p class="wc-def">' + (card.def ? '<b>' + esc(card.def) + '</b>' : '把这个词留在今晚的例句里。') + '</p>' +
          '<span class="wc-sp"></span>' +
          '<div class="wc-foot">' +
            '<button class="wc-master' + (card.mastered ? ' on' : '') + '" type="button" data-act="master" aria-label="标记已掌握">' +
              ICONS.check + '<span>' + (card.mastered ? '已掌握' : '标记已掌握') + '</span>' +
            '</button>' +
            '<button class="wc-del" type="button" data-act="del" aria-label="删除这张词卡">' +
              '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>' +
            '</button>' +
          '</div>' +
        '</div>' +
      '</div>';

    el.dataset.id = card.id;
    return el;
  }

  function findCard(id) {
    return (App.state.cards || []).filter(function (c) { return c.id === id; })[0];
  }

  /* ---------- 交互 ---------- */

  function clearPlaying() {
    (els.list ? els.list.querySelectorAll('.wc-play.playing') : []).forEach(function (b) {
      b.classList.remove('playing');
    });
  }

  function toggleSpeak(id, btn) {
    var card = findCard(id);
    if (!card || !App.voice) return;
    if (btn.classList.contains('playing')) {
      App.voice.stop();
      btn.classList.remove('playing');
      return;
    }
    clearPlaying();
    btn.classList.add('playing');
    /* 静默降级：失败不弹错；播放结束经回调与总线双向清理 */
    var done = function () { btn.classList.remove('playing'); };
    /* 朗读纯文本（防止例句里带标记）；音色用词卡来源爱豆（persona-id 即 voice），
       语速取「编辑室 · 配音设置」里存的偏好 */
    var plain = String(card.en || card.word || '').replace(/<[^>]+>/g, '');
    var sp = App.state.prefs.ttsSpeed;
    var opts = {
      voice: card.from || (App.chat && App.chat.activeId) || 'default',
      speed: typeof sp === 'number' ? sp : 1
    };
    try {
      App.voice.speak(plain, {
        onEnd: done, onError: done, onStart: function () {}
      }, opts);
    } catch (e) { console.warn('[cards] 朗读调用出错（静默降级）', e); done(); }
  }

  function toggleMaster(id, cardEl) {
    suppressRebuild = true;                 /* 事件在本调用内同步发出，先抑制 */
    var now = App.cards.toggleMaster(id);
    suppressRebuild = false;
    var card = findCard(id);
    if (cardEl) {
      cardEl.classList.toggle('mastered', now);
      var btn = cardEl.querySelector('.wc-master');
      if (btn) {
        btn.classList.toggle('on', now);
        var label = btn.querySelector('span');
        if (label) label.textContent = now ? '已掌握' : '标记已掌握';
      }
      var stamp = cardEl.querySelector('.wc-stamp');
      if (now && !stamp) {
        var s = document.createElement('span');
        s.className = 'wc-stamp';
        s.textContent = '已掌握';
        cardEl.querySelector('.wc-front').appendChild(s);
      } else if (!now && stamp) {
        stamp.remove();
      }
    }
    lastMasteredSig = masteredSig();        /* 就地更新已同步签名 */
    updateHero();
    return card ? card.mastered : false;
  }

  function removeCard(id, cardEl) {
    if (cardEl) {
      /* 先收起（透明 + 缩落），随后删数据 → cards:change 事件驱动重建 */
      cardEl.style.transition = 'opacity .18s var(--ease), transform .18s var(--ease)';
      cardEl.style.opacity = '0';
      cardEl.style.transform = 'translateY(-8px) scale(.97)';
    }
    setTimeout(function () { App.cards.remove(id); }, 170);
  }

  function bindList() {
    var list = els.list;

    list.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      var cardEl = e.target.closest('.wc');
      if (!cardEl) return;
      var id = cardEl.dataset.id;

      if (btn) {
        var act = btn.getAttribute('data-act');
        if (act === 'play') { e.stopPropagation(); toggleSpeak(id, btn); return; }
        if (act === 'master') { e.stopPropagation(); toggleMaster(id, cardEl); return; }
        if (act === 'del') { e.stopPropagation(); removeCard(id, cardEl); return; }
      }
      cardEl.classList.toggle('flip');
    });

    /* 键盘翻面（Enter / 空格） */
    list.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var cardEl = e.target.closest('.wc');
      if (!cardEl || e.target.closest('button')) return;
      e.preventDefault();
      cardEl.classList.toggle('flip');
    });
  }

  /* ---------- 空态 CTA：去聊天收集 ---------- */
  function goCollect() {
    App.nav.go(App.chat && App.chat.activeId ? 'chat' : 'home');
  }

  /* ============================================================
     挂载 / 显示 / 隐藏
     ============================================================ */
  App.screens.cards = {
    mount: function (el) {
      injectStyle();

      el.textContent = '';
      var root = document.createElement('div');
      root.className = 'cd-scroll scroll mask-fade';
      root.innerHTML =
        /* 报头 */
        '<header class="cd-masthead rise" style="--i:0">' +
          '<div class="cd-mh-row"><span class="cd-logo">IdolTalk<i>.</i></span><span class="cd-mh-meta">Vol.132 — Oct.2026</span></div>' +
          '<div class="grad-rule"></div>' +
        '</header>' +
        /* 刊首 */
        '<section class="cd-hero rise" style="--i:1">' +
          '<div class="kicker"><span class="tick"></span>The Card Desk · 今夜词卡</div>' +
          '<h1 class="cd-h1">Tonight&rsquo;s<br><span class="grad-text">Cards</span></h1>' +
          '<p class="cd-sub">每晚翻一翻，把聊过的词留下来。</p>' +
          '<p class="cd-meta" id="cdMeta"></p>' +
        '</section>' +
        /* 分节头 */
        '<div class="cd-index-head rise" style="--i:2">' +
          '<span class="cd-index-title">The Deck · 词卡架</span>' +
          '<span class="cd-index-count" id="cdCount"></span>' +
        '</div>' +
        /* 列表 / 空态 */
        '<div class="cd-list" id="cdList"></div>' +
        '<div class="cd-empty" id="cdEmpty" style="display:none">' +
          '<p class="q">The best words are the ones you learned from <em>someone you like</em>.</p>' +
          '<p class="zh">最好的词，是从喜欢的人那里听来的。</p>' +
          '<div class="grad-rule center" style="--rule-w:64px"></div>' +
          '<p class="note">聊天时点「+ 加入词卡」 · 把今晚的词留下来</p>' +
          '<button class="cta" id="cdGoChat" type="button">去找爱豆聊聊 ' + ICONS.arrow + '</button>' +
        '</div>' +
        '<div class="scroll-pad"></div>';

      el.appendChild(root);
      els = {
        root: root,
        meta: root.querySelector('#cdMeta'),
        count: root.querySelector('#cdCount'),
        list: root.querySelector('#cdList'),
        empty: root.querySelector('#cdEmpty')
      };

      bindList();
      root.querySelector('#cdGoChat').addEventListener('click', goCollect);

      /* 其他模块改动词卡后刷新：计数永远即时更新；
         列表在「结构或掌握分布」变化且非本屏就地更新时重建 */
      App.on('cards:change', function () {
        updateHero();
        if (suppressRebuild) return;
        if (signature() !== lastSig || masteredSig() !== lastMasteredSig) renderList();
      });

      renderList();
    },

    show: function () {
      updateHero();
      if (signature() !== lastSig || masteredSig() !== lastMasteredSig) renderList();
      if (els.root) els.root.scrollTop = 0;
    },

    hide: function () {
      clearPlaying();
      try { if (App.voice) App.voice.stop(); } catch (e) { console.warn('[cards] 停止朗读出错', e); }
    }
  };
})();
