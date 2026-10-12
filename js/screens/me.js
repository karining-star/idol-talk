/* ============================================================
   screens/me.js — 我的屏「编辑室 / THE STUDY」
   ------------------------------------------------------------
   契约：App.screens.me = { mount(el), show(), hide() }
   el = index.html 的 #screen-me（mount 时整体替换 .scaffold 占位）

   设计（延续「杂志梦幻」语言，杂志「编辑室/版权页」气质）：
   · 报头 IdolTalk. + Vol.132 + 发丝线；kicker「The Study · 编辑室」
     + 超大衬线 THE STUDY（grad 高亮 Study）+ 衬线斜体中文副题
   · 学习统计：连续天数 / 词卡数 / 聊过的爱豆数 / 星数 ——
     大衬线渐变数字 + 发丝线分格（数据全部读 App.state）
   · 语音设置：配音接口地址（默认本机 tts_server）+ 语速滑杆
     +「试听一句 Hello」+ 保存（存 App.state.prefs，改动即存）
   · 爱豆收藏 / 最近聊过：.ava 头像行，点击直达对话
   · 关于区：杂志版权页（Colophon）——版权行 / 版本号 / 期号 / 声明
     + 双击确认式「清空本地数据」
   ------------------------------------------------------------
   本模块写入 App.state.prefs 的键（供其他模块对齐）：
   · studyDays  { 'YYYY-MM-DD':1 }   学习日历（连续天数）
   · ttsSpeed   number (0.5–1.5)     语速（voice.js 合成时读取）
   · favIds     [idolId]             收藏的爱豆
   · recent     [{id, t}]            最近聊过（去重、最新在前、上限 8）
   附加接口：App.favs = { has(id), toggle(id) }（供聊天屏收藏按钮调用）
   事件：'favs:change'；订阅 'nav:change' 自动记录最近聊过的爱豆
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});
  App.screens = App.screens || {};

  var VERSION = '1.0.0';
  var ISSUE = 'Vol.132 · 2026年10月';
  var DEFAULT_ENDPOINT = 'http://127.0.0.1:8780/v1/audio/speech';

  /* ---------- 小工具 ---------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function clampSpeed(v) {
    v = parseFloat(v);
    if (!isFinite(v)) return 1;
    return Math.min(1.5, Math.max(0.5, v));
  }
  function dayKey(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }
  function timeago(t) {
    var diff = Date.now() - (t || 0);
    if (diff < 60e3) return '刚刚';
    if (diff < 3600e3) return Math.floor(diff / 60e3) + ' 分钟前';
    if (diff < 86400e3) return Math.floor(diff / 3600e3) + ' 小时前';
    if (diff < 7 * 86400e3) return Math.floor(diff / 86400e3) + ' 天前';
    var d = new Date(t);
    return (d.getMonth() + 1) + '月' + d.getDate() + '日';
  }

  /* 爱豆索引（含 N° 期号），懒构建一次 */
  var _idx = null;
  function idolIndex() {
    if (!_idx) {
      _idx = {};
      (App.idols || []).forEach(function (p, i) { _idx[p.id] = { p: p, no: i + 1 }; });
    }
    return _idx;
  }

  /* ============================================================
     学习日历（连续天数）——打开 App 即算一天
     ============================================================ */
  function normDays() {
    var prefs = App.state.prefs;
    var d = prefs.studyDays;
    if (Array.isArray(d)) {          /* 兼容旧数组形态 → 对象 */
      var o = {};
      d.forEach(function (k) { o[k] = 1; });
      prefs.studyDays = o;
      return o;
    }
    if (d && typeof d === 'object') return d;
    return (prefs.studyDays = {});
  }
  function recordToday() {
    var days = normDays();
    var k = dayKey(new Date());
    if (!days[k]) {
      days[k] = 1;
      if (App.saveState) App.saveState();
    }
  }
  /** 连续天数（今天或昨天起往回连续）；total = 累计学习天数 */
  function streakInfo() {
    var days = normDays();
    var keys = Object.keys(days).sort().reverse();   /* 字典序 = 时间序 */
    var cur = 0;
    if (keys.length) {
      var today = dayKey(new Date());
      var yd = new Date();
      yd.setDate(yd.getDate() - 1);
      var yesterday = dayKey(yd);
      if (keys[0] === today || keys[0] === yesterday) {
        var cursor = keys[0] === today ? new Date() : yd;
        for (var guard = 0; guard < 400; guard++) {  /* guard 防御死循环 */
          if (days[dayKey(cursor)]) {
            cur++;
            cursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() - 1);
          } else break;
        }
      }
    }
    return { cur: cur, total: keys.length };
  }

  /* ============================================================
     最近聊过 / 收藏 —— 附加约定（订阅导航事件自动记录）
     ============================================================ */
  function pushRecent(id) {
    if (!id) return;
    var prefs = App.state.prefs;
    var arr = prefs.recent || (prefs.recent = []);
    arr = arr.filter(function (r) { return r && r.id !== id; });
    arr.unshift({ id: id, t: Date.now() });
    if (arr.length > 8) arr.length = 8;
    prefs.recent = arr;
    if (App.saveState) App.saveState();
  }
  /* 切到聊天屏且有活跃爱豆 → 记一笔「最近聊过」 */
  App.on('nav:change', function (d) {
    if (d && d.to === 'chat' && App.chat && App.chat.activeId) {
      pushRecent(App.chat.activeId);
    }
  });

  /* App.favs —— 收藏爱豆（供聊天屏调用；本屏渲染收藏列表） */
  if (!App.favs) {
    App.favs = {
      has: function (id) {
        return (App.state.prefs.favIds || []).indexOf(id) > -1;
      },
      toggle: function (id) {
        if (!id) return false;
        var arr = App.state.prefs.favIds || (App.state.prefs.favIds = []);
        var i = arr.indexOf(id);
        if (i > -1) arr.splice(i, 1); else arr.push(id);
        if (App.saveState) App.saveState();
        App.toast(i > -1 ? '已取消收藏' : '已收藏 · 收进编辑室');
        App.emit('favs:change');
        return i < 0;
      }
    };
  }

  /** 聊过的爱豆（最近记录 ∪ 聊天历史键） */
  function chattedIds() {
    var set = {};
    (App.state.prefs.recent || []).forEach(function (r) { if (r && r.id) set[r.id] = 1; });
    var h = (App.chat && App.chat.history) || {};
    if (typeof h === 'object') Object.keys(h).forEach(function (k) { set[k] = 1; });
    return Object.keys(set);
  }

  /* ============================================================
     屏幕样式（本屏私有，me- 前缀；注入一次）
     ============================================================ */
  var STYLE_ID = 'style-screen-me';
  var STYLE_CSS = [
    '/* —— 我的屏 · 编辑室 —— */',
    '.me-scroll{ flex:1; min-height:0; }',

    /* 报头 */
    '.me-masthead{ padding:4px var(--gutter) 0; }',
    '.me-mh-row{ display:flex; align-items:baseline; justify-content:space-between; padding-bottom:9px; }',
    '.me-logo{ font-family:var(--serif); font-style:italic; font-size:var(--fs-title); letter-spacing:.01em; }',
    '.me-logo i{ font-style:italic; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
    '.me-mh-meta{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.26em; color:var(--p3); text-transform:uppercase; }',

    /* 刊首 */
    '.me-hero{ padding:14px var(--gutter) 2px; }',
    '.me-h1{ margin-top:10px; font-family:var(--serif); font-weight:400; font-size:34px; line-height:.99; letter-spacing:.005em; text-transform:uppercase; text-shadow:0 2px 26px rgba(0,0,0,.35); }',
    '.me-sub{ margin-top:10px; font-family:var(--serif); font-style:italic; font-size:12.5px; color:var(--p2); letter-spacing:.03em; }',
    '.me-meta{ margin-top:8px; font-size:var(--fs-note); letter-spacing:.14em; color:var(--p3); text-transform:uppercase; }',
    '.me-meta i{ font-style:normal; opacity:.5; padding:0 3px; }',

    /* 学习统计 —— 杂志数字版式 */
    '.me-stats{ display:grid; grid-template-columns:1fr 1fr; gap:11px; padding:0 var(--gutter); margin-top:16px; }',
    '.me-stat{ padding:14px 15px 12px; }',
    '.st-k{ display:flex; align-items:center; gap:7px; font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p3); text-transform:uppercase; }',
    '.st-k .tick{ width:14px; }',
    '.st-num{ margin-top:10px; display:flex; align-items:baseline; gap:5px; }',
    '.st-num b{ font-family:var(--serif); font-style:italic; font-weight:400; font-size:27px; line-height:1; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
    '.st-num span{ font-size:9.5px; letter-spacing:.14em; color:var(--p3); }',
    '.st-sub{ margin-top:8px; font-size:9px; letter-spacing:.12em; color:var(--p4); text-transform:uppercase; }',

    /* 语音设置 */
    '.me-voice{ margin:20px var(--gutter) 0; padding:16px 16px 15px; }',
    '.me-label{ margin-top:15px; font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p3); text-transform:uppercase; }',
    '.me-voice .me-label:first-of-type{ margin-top:16px; }',
    '.me-input{ margin-top:8px; width:100%; padding:10px 13px; border-radius:10px;',
    '  border:1px solid var(--line); background:rgba(247,242,248,.05);',
    '  font-family:ui-monospace, Consolas, "Courier New", monospace; font-size:11px; letter-spacing:.02em; color:var(--paper);',
    '  transition:border-color var(--t-fast) var(--ease); }',
    '.me-input:focus{ border-color:rgba(242,160,220,.5); }',
    '.me-input::placeholder{ color:var(--p4); font-family:inherit; }',
    /* 语速滑杆：渐变轨道 + 渐变圆钮 */
    '.me-speed{ display:flex; align-items:center; gap:10px; margin-top:9px; }',
    '.me-speed .sp-cap{ font-family:var(--serif); font-style:italic; font-size:10px; color:var(--p4); }',
    '.me-range{ -webkit-appearance:none; appearance:none; flex:1; height:18px; background:transparent; cursor:pointer; }',
    '.me-range::-webkit-slider-runnable-track{ height:2px; border-radius:2px;',
    '  background:linear-gradient(90deg, var(--rose), var(--lilac) var(--fill,50%), var(--line) var(--fill,50%)); }',
    '.me-range::-webkit-slider-thumb{ -webkit-appearance:none; width:15px; height:15px; border-radius:50%; margin-top:-6.5px;',
    '  background:var(--grad-2); border:2px solid var(--ink); box-shadow:0 3px 10px -3px rgba(242,160,220,.7); }',
    '.me-range::-moz-range-track{ height:2px; border-radius:2px; background:var(--line); }',
    '.me-range::-moz-range-progress{ height:2px; border-radius:2px; background:var(--grad-2); }',
    '.me-range::-moz-range-thumb{ width:12px; height:12px; border-radius:50%; background:var(--grad-2); border:2px solid var(--ink); }',
    '.me-speed-val{ flex:none; font-size:9.5px; letter-spacing:.08em; color:var(--p2); }',
    '.me-speed-val b{ font-family:var(--serif); font-style:italic; font-weight:400; font-size:12px; color:var(--paper); }',
    '.me-actions{ display:flex; gap:10px; margin-top:16px; }',
    '.me-note{ margin-top:13px; font-size:9.5px; line-height:1.75; color:var(--p4); letter-spacing:.04em; }',

    /* 爱豆行 —— 收藏 / 最近 */
    '.me-sec{ margin-top:22px; }',
    '.me-sec-head{ display:flex; align-items:baseline; justify-content:space-between; padding-bottom:3px; }',
    '.me-sec-title{ font-family:var(--serif); font-style:italic; font-size:var(--fs-h3); letter-spacing:.02em; }',
    '.me-sec-count{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p3); text-transform:uppercase; }',
    '.me-rows{ padding:0 2px; }',
    '.me-row{ display:flex; align-items:center; gap:12px; width:100%; padding:11px 2px;',
    '  border-bottom:1px solid var(--line); text-align:left; }',
    '.me-row:last-child{ border-bottom:0; }',
    '.me-ava{ --ava:34px; }',
    /* 缺图占位：与 home.js 同款（渐变 + 衬线首字母），头像行缺图时不显示浏览器破图标记 */
    '.me-ava.no-photo{ background:',
    '  radial-gradient(120% 90% at 30% 20%, rgba(242,160,220,.26), transparent 60%),',
    '  linear-gradient(160deg, var(--ink-3), var(--ink-4)); }',
    '.me-ava.no-photo::after{',
    '  content:attr(data-initial); position:absolute; inset:0; z-index:2;',
    '  display:flex; align-items:center; justify-content:center;',
    '  font-family:var(--serif); font-style:italic; font-size:16px;',
    '  background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
    '.mr-id{ flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; }',
    '.mr-name{ font-family:var(--serif); font-size:14.5px; letter-spacing:.05em; text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }',
    '.mr-grp{ font-size:8.5px; font-weight:600; letter-spacing:.2em; color:var(--p3); text-transform:uppercase; }',
    '.mr-when{ flex:none; font-size:9.5px; color:var(--p4); letter-spacing:.06em; }',
    '.mr-arrow{ flex:none; opacity:.4; transform:translateX(-4px);',
    '  transition:opacity var(--t-fast) var(--ease), transform var(--t-fast) var(--ease); }',
    '.me-row:hover .mr-arrow{ opacity:1; transform:translateX(0); }',
    '.me-empty{ padding:13px 2px 3px; font-family:var(--serif); font-style:italic; font-size:12px; color:var(--p4); letter-spacing:.03em; }',

    /* 版权页（Colophon） */
    '.me-colo{ margin-top:26px; padding:16px var(--gutter) 0; border-top:1px solid var(--line); position:relative; }',
    '.me-colo::before{ content:""; position:absolute; left:var(--gutter); top:-1px; width:46px; height:1px; background:var(--grad); }',
    '.colo-row{ display:flex; align-items:baseline; justify-content:space-between; gap:14px; padding:10px 2px; border-bottom:1px solid var(--line); }',
    '.colo-row dt{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p3); text-transform:uppercase; }',
    '.colo-row dd{ font-family:var(--serif); font-style:italic; font-size:11.5px; color:var(--p2); letter-spacing:.03em; text-align:right; }',
    '.me-reset{ margin-top:18px; display:inline-flex; padding:7px 14px; border-radius:var(--r-pill);',
    '  border:1px dashed var(--line-2); color:var(--p3); font-size:9px; font-weight:600; letter-spacing:.2em; text-transform:uppercase;',
    '  transition:color var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease); }',
    '.me-reset:hover, .me-reset.armed{ color:var(--rose); border-color:var(--line-rose); }',
    '.me-copy{ margin-top:20px; text-align:center; font-family:var(--serif); font-style:italic; font-size:10px; color:var(--p4); letter-spacing:.05em; }',
    '.scroll-pad{ height:32px; }'
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
  var els = {};
  var ARROW = '<svg class="mr-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 5.5 19.5 12 13 18.5"/></svg>';

  /* ---------- 统计 ---------- */
  function refreshStats() {
    var s = streakInfo();
    var cards = App.state.cards || [];
    var mastered = cards.filter(function (c) { return c.mastered; }).length;

    if (els.stNum) els.stNum.textContent = s.cur;
    if (els.stSub) els.stSub.textContent = '累计学习 ' + s.total + ' 天';
    if (els.cdNum) els.cdNum.textContent = cards.length;
    if (els.cdSub) els.cdSub.textContent = '已掌握 ' + mastered + ' 张';
    if (els.idNum) els.idNum.textContent = chattedIds().length;
    if (els.idSub) els.idSub.textContent = '目录共 ' + (App.idols || []).length + ' 位';
    if (els.srNum) els.srNum.textContent = (App.state.stars || 0);
    if (els.srSub) els.srSub.textContent = '收藏与复习可得星';
  }

  /* ---------- 爱豆行（收藏 / 最近） ---------- */
  function personaRowHtml(id, when) {
    var info = idolIndex()[id];
    if (!info) return '';                       /* 数据里没有 → 静默跳过 */
    var p = info.p;
    return '<button class="me-row" type="button" data-id="' + esc(p.id) + '" aria-label="与 ' + esc(p.stage) + ' 对话">' +
      '<span class="ava me-ava" data-initial="' + esc(p.stage.charAt(0)) + '"><span class="speak-halo"></span><span class="ring"></span>' +
      '<img data-photo src="' + esc(App.photoFor(p.id)) + '" alt="' + esc(p.stage) + '" loading="lazy"></span>' +
      '<span class="mr-id"><span class="mr-name">' + esc(p.stage) + '</span>' +
      '<span class="mr-grp">' + esc(p.group) + ' · N°' + pad2(info.no) + '</span></span>' +
      (when ? '<span class="mr-when">' + esc(when) + '</span>' : '') +
      ARROW +
      '</button>';
  }

  function refreshPeople() {
    /* 收藏 */
    var favIds = (App.state.prefs.favIds || []).filter(function (id) { return idolIndex()[id]; });
    var favHtml = favIds.map(function (id) { return personaRowHtml(id, ''); }).join('');
    if (els.favRows) els.favRows.innerHTML = favHtml;
    if (els.favEmpty) els.favEmpty.style.display = favIds.length ? 'none' : '';
    if (els.favCount) els.favCount.textContent = favIds.length ? favIds.length + ' 位' : 'The Favorites';

    /* 最近聊过 */
    var recent = (App.state.prefs.recent || []).filter(function (r) { return r && idolIndex()[r.id]; });
    var recHtml = recent.map(function (r) { return personaRowHtml(r.id, timeago(r.t)); }).join('');
    if (els.recRows) els.recRows.innerHTML = recHtml;
    if (els.recEmpty) els.recEmpty.style.display = recent.length ? 'none' : '';
    if (els.recCount) els.recCount.textContent = recent.length ? recent.length + ' 位' : 'The Recent';
  }

  /* ---------- 语音设置 ---------- */
  function currentSpeed() { return clampSpeed(els.speed ? els.speed.value : 1); }

  function updateSpeedUI() {
    if (!els.speed) return;
    var v = currentSpeed();
    if (els.speedVal) els.speedVal.innerHTML = '语速 <b>' + v.toFixed(2) + '</b>×';
    /* 渐变轨道填充比例（0.5–1.5 → 0–100%） */
    els.speed.style.setProperty('--fill', ((v - 0.5) / 1 * 100).toFixed(1) + '%');
  }

  function syncVoiceInputs(force) {
    if (!els.url || !els.speed) return;
    if (force || !els.urlDirty) {
      els.url.value = (App.voice && App.voice.getEndpoint) ? App.voice.getEndpoint() : DEFAULT_ENDPOINT;
      els.urlDirty = false;
    }
    if (force || !els.speedDirty) {
      var sp = App.state.prefs.ttsSpeed;
      els.speed.value = clampSpeed(typeof sp === 'number' ? sp : 1);
      els.speedDirty = false;
    }
    /* 对话后端（大模型） */
    if (els.chatUrl && (force || !els.chatUrlDirty)) {
      els.chatUrl.value = App.state.prefs.chatEndpoint || 'https://open.bigmodel.cn/api/paas/v4/chat/completions';
      els.chatUrlDirty = false;
    }
    if (els.chatModel && (force || !els.chatModelDirty)) {
      els.chatModel.value = App.state.prefs.chatModel || 'glm-4-flash';
      els.chatModelDirty = false;
    }
    if (els.chatKey && (force || !els.chatKeyDirty)) {
      els.chatKey.value = App.state.prefs.chatApiKey || '';
      els.chatKeyDirty = false;
    }
    updateSpeedUI();
  }

  /** 应用当前界面上的端点与语速（持久化） */
  function applyVoicePrefs() {
    var url = (els.url.value || '').trim();
    if (url && !/^https?:\/\//i.test(url)) {
      App.toast('地址要以 http:// 或 https:// 开头');
      return false;
    }
    try { App.voice.setEndpoint(url || DEFAULT_ENDPOINT); }
    catch (e) { console.warn('[me] setEndpoint 出错（静默降级）', e); }
    App.state.prefs.ttsSpeed = currentSpeed();
    /* 对话后端（大模型）：本机 Ollama 或云端 API，OpenAI 兼容 */
    var chatUrl = (els.chatUrl && els.chatUrl.value || '').trim();
    var chatModel = (els.chatModel && els.chatModel.value || '').trim();
    if (chatUrl && !/^https?:\/\//i.test(chatUrl)) {
      App.toast('对话接口地址要以 http:// 或 https:// 开头');
      return false;
    }
    App.state.prefs.chatEndpoint = chatUrl || 'https://open.bigmodel.cn/api/paas/v4/chat/completions';
    App.state.prefs.chatModel = chatModel || 'glm-4-flash';
    App.state.prefs.chatApiKey = (els.chatKey && els.chatKey.value || '').trim();
    if (App.saveState) App.saveState();
    els.urlDirty = els.speedDirty = false;
    els.chatUrlDirty = els.chatModelDirty = els.chatKeyDirty = false;
    return true;
  }

  function onTest() {
    if (!applyVoicePrefs()) return;
    /* 静默降级：服务不在也不弹错，只给出已发送的反馈；
       音色用当前聊天中的爱豆（persona-id 即 voice），语速用界面当前值 */
    var sp = currentSpeed();
    var opts = {
      voice: (App.chat && App.chat.activeId) || 'default',
      speed: sp
    };
    try {
      App.voice.speak('Hello! This is a quick voice test.', {}, opts);
    } catch (e) { console.warn('[me] 试听调用出错（静默降级）', e); }
    App.toast('试听已发送 · Hello');
  }

  function onSave() {
    if (!applyVoicePrefs()) return;
    App.toast('语音设置已保存');
  }

  /* ---------- 清空本地数据（双击确认） ---------- */
  var resetArmed = false, resetTimer = null;

  function disarmReset() {
    resetArmed = false;
    if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
    if (els.reset) {
      els.reset.classList.remove('armed');
      els.reset.textContent = '清空本地数据';
    }
  }
  function onReset() {
    if (!resetArmed) {
      resetArmed = true;
      if (els.reset) {
        els.reset.classList.add('armed');
        els.reset.textContent = '再点一次确认清空';
      }
      App.toast('将清空星数 / 词卡 / 设置 —— 再点一次确认');
      resetTimer = setTimeout(disarmReset, 4200);
      return;
    }
    disarmReset();

    App.state.stars = 0;
    App.state.cards = [];
    App.state.prefs = {};
    try { App.voice.setEndpoint(DEFAULT_ENDPOINT); } catch (e) { /* 静默 */ }
    if (App.saveState) App.saveState(true);
    recordToday();                       /* 连续天数从今晚重新起算 */

    refreshStats();
    refreshPeople();
    syncVoiceInputs(true);
    App.emit('cards:change');
    App.toast('已清空 · 从今晚重新开始');
  }

  /* ============================================================
     挂载 / 显示 / 隐藏
     ============================================================ */
  App.screens.me = {
    mount: function (el) {
      injectStyle();

      el.textContent = '';
      var root = document.createElement('div');
      root.className = 'me-scroll scroll mask-fade';
      root.innerHTML =
        /* 报头 */
        '<header class="me-masthead rise" style="--i:0">' +
          '<div class="me-mh-row"><span class="me-logo">IdolTalk<i>.</i></span><span class="me-mh-meta">Vol.132 — Oct.2026</span></div>' +
          '<div class="grad-rule"></div>' +
        '</header>' +
        /* 刊首 */
        '<section class="me-hero rise" style="--i:1">' +
          '<div class="kicker"><span class="tick"></span>The Study · 编辑室</div>' +
          '<h1 class="me-h1">The <span class="grad-text">Study</span></h1>' +
          '<p class="me-sub">你的学习手账，每晚印一页。</p>' +
          '<p class="me-meta">数据仅存本机<i>·</i>改动即存<i>·</i>离线可用</p>' +
        '</section>' +
        /* 学习统计 */
        '<section class="me-stats">' +
          '<div class="me-stat glass--soft rise" style="--i:2">' +
            '<div class="st-k"><span class="tick"></span>连续学习 · Streak</div>' +
            '<div class="st-num"><b id="meStNum">0</b><span>天</span></div>' +
            '<div class="st-sub" id="meStSub"></div>' +
          '</div>' +
          '<div class="me-stat glass--soft rise" style="--i:3">' +
            '<div class="st-k"><span class="tick"></span>收藏词卡 · Cards</div>' +
            '<div class="st-num"><b id="meCdNum">0</b><span>张</span></div>' +
            '<div class="st-sub" id="meCdSub"></div>' +
          '</div>' +
          '<div class="me-stat glass--soft rise" style="--i:4">' +
            '<div class="st-k"><span class="tick"></span>聊过爱豆 · Idols</div>' +
            '<div class="st-num"><b id="meIdNum">0</b><span>位</span></div>' +
            '<div class="st-sub" id="meIdSub"></div>' +
          '</div>' +
          '<div class="me-stat glass--soft rise" style="--i:5">' +
            '<div class="st-k"><span class="tick"></span>星数 · Stars</div>' +
            '<div class="st-num"><b id="meSrNum">0</b><span>颗</span></div>' +
            '<div class="st-sub" id="meSrSub"></div>' +
          '</div>' +
        '</section>' +
        /* 语音设置 */
        '<section class="me-voice glass--soft rise" style="--i:6">' +
          '<div class="kicker"><span class="tick"></span>Voice Desk · 配音设置</div>' +
          '<div class="me-label">配音接口 · Endpoint</div>' +
          '<input class="me-input" id="meTtsUrl" type="text" spellcheck="false" autocomplete="off" placeholder="' + DEFAULT_ENDPOINT + '" aria-label="配音接口地址">' +
          '<div class="me-label">语速 · Speed</div>' +
          '<div class="me-speed">' +
            '<span class="sp-cap">0.5</span>' +
            '<input class="me-range" id="meSpeed" type="range" min="0.5" max="1.5" step="0.05" value="1" aria-label="语速">' +
            '<span class="sp-cap">1.5</span>' +
            '<span class="me-speed-val" id="meSpeedVal">语速 <b>1.00</b>×</span>' +
          '</div>' +
          '<div class="me-actions">' +
            '<button class="cta" id="meTest" type="button">试听一句 Hello' +
              '<svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4.2V4.8L8 9H4z" fill="currentColor"/><path d="M16.2 8.6a4.8 4.8 0 0 1 0 6.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>' +
            '</button>' +
            '<button class="cta" id="meSave" type="button">保存设置' +
              '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 12.5 9.5 17.5 19.5 6.5"/></svg>' +
            '</button>' +
          '</div>' +
          '<p class="me-note">语音服务在本机运行时才有声音（默认 http://127.0.0.1:8780）。断线时自动静默降级，不打扰练习。</p>' +
          '<div class="me-label" style="margin-top:16px">对话接口 · Chat Endpoint</div>' +
          '<input class="me-input" id="meChatUrl" type="text" spellcheck="false" autocomplete="off" placeholder="https://open.bigmodel.cn/api/paas/v4/chat/completions" aria-label="对话模型接口地址">' +
          '<div class="me-label">模型 · Model</div>' +
          '<input class="me-input" id="meChatModel" type="text" spellcheck="false" autocomplete="off" placeholder="glm-4-flash" aria-label="模型名称">' +
          '<div class="me-label">密钥 · API Key</div>' +
          '<input class="me-input" id="meChatKey" type="password" spellcheck="false" autocomplete="off" placeholder="智谱 bigmodel.cn 的 API Key（免费）" aria-label="对话模型密钥">' +
          '<p class="me-note">对话由大模型生成（OpenAI 兼容接口、支持跨域）。默认智谱 <b>GLM-4-Flash（免费）</b>——去 bigmodel.cn 注册拿 API Key 填这里；也可改成本机 Ollama。断线自动回退本地规则引擎。</p>' +
        '</section>' +
        /* 爱豆收藏 / 最近聊过 */
        '<section class="me-sec rise" style="--i:7">' +
          '<div class="me-sec-head"><span class="me-sec-title">收藏 · Favorites</span><span class="me-sec-count" id="meFavCount"></span></div>' +
          '<div class="me-rows" id="meFavRows"></div>' +
          '<p class="me-empty" id="meFavEmpty">还没有收藏——聊天页可以收藏爱豆。</p>' +
        '</section>' +
        '<section class="me-sec rise" style="--i:8">' +
          '<div class="me-sec-head"><span class="me-sec-title">最近聊过 · Recent</span><span class="me-sec-count" id="meRecCount"></span></div>' +
          '<div class="me-rows" id="meRecRows"></div>' +
          '<p class="me-empty" id="meRecEmpty">今晚还没和谁聊过。</p>' +
        '</section>' +
        /* 版权页 */
        '<section class="me-colo rise" style="--i:9">' +
          '<div class="kicker"><span class="tick"></span>Colophon · 版权页</div>' +
          '<dl>' +
            '<div class="colo-row"><dt>版本 Version</dt><dd>App Edition · v' + VERSION + '</dd></div>' +
            '<div class="colo-row"><dt>期号 Issue</dt><dd>' + ISSUE + '</dd></div>' +
            '<div class="colo-row"><dt>印制 Process</dt><dd>本地离线 · 纯静态 PWA</dd></div>' +
            '<div class="colo-row"><dt>语音 Voice</dt><dd>本机 tts_server · 静默降级</dd></div>' +
            '<div class="colo-row"><dt>肖像 Notice</dt><dd>爱豆照片仅供个人学习</dd></div>' +
          '</dl>' +
          '<button class="me-reset" id="meReset" type="button">清空本地数据</button>' +
          '<p class="me-copy">&copy; 2026 IdolTalk — The Evening Study · Printed in rose ink.</p>' +
        '</section>' +
        '<div class="scroll-pad"></div>';

      el.appendChild(root);
      els = {
        root: root,
        stNum: root.querySelector('#meStNum'), stSub: root.querySelector('#meStSub'),
        cdNum: root.querySelector('#meCdNum'), cdSub: root.querySelector('#meCdSub'),
        idNum: root.querySelector('#meIdNum'), idSub: root.querySelector('#meIdSub'),
        srNum: root.querySelector('#meSrNum'), srSub: root.querySelector('#meSrSub'),
        url: root.querySelector('#meTtsUrl'), urlDirty: false,
        speed: root.querySelector('#meSpeed'), speedDirty: false,
        chatUrl: root.querySelector('#meChatUrl'), chatUrlDirty: false,
        chatModel: root.querySelector('#meChatModel'), chatModelDirty: false,
        chatKey: root.querySelector('#meChatKey'), chatKeyDirty: false,
        speedVal: root.querySelector('#meSpeedVal'),
        favRows: root.querySelector('#meFavRows'), favEmpty: root.querySelector('#meFavEmpty'), favCount: root.querySelector('#meFavCount'),
        recRows: root.querySelector('#meRecRows'), recEmpty: root.querySelector('#meRecEmpty'), recCount: root.querySelector('#meRecCount'),
        reset: root.querySelector('#meReset')
      };

      /* —— 事件接线 —— */

      /* 缺图占位：error 不冒泡，用捕获阶段委托 */
      root.addEventListener('error', function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'IMG' || !t.hasAttribute('data-photo')) return;
        var holder = t.closest('.me-ava');
        t.style.display = 'none';
        t.removeAttribute('src');
        if (holder) holder.classList.add('no-photo');
      }, true);

      /* 爱豆行 → 直达对话（收藏与最近共用一个委托） */
      root.addEventListener('click', function (e) {
        var row = e.target.closest('.me-row');
        if (!row) return;
        var id = row.getAttribute('data-id');
        if (!id || !App.chat) return;
        try { App.chat.open(id); } catch (err) { console.warn('[me] chat.open 出错', err); }
        App.nav.go('chat');
      });

      /* 语音输入 */
      els.url.addEventListener('input', function () { els.urlDirty = true; });
      if (els.chatUrl) els.chatUrl.addEventListener('input', function () { els.chatUrlDirty = true; });
      if (els.chatModel) els.chatModel.addEventListener('input', function () { els.chatModelDirty = true; });
      if (els.chatKey) els.chatKey.addEventListener('input', function () { els.chatKeyDirty = true; });
      els.speed.addEventListener('input', function () {
        els.speedDirty = true;
        updateSpeedUI();
      });
      els.speed.addEventListener('change', function () {
        /* 松手即存（改动即存，不打扰） */
        App.state.prefs.ttsSpeed = currentSpeed();
        if (App.saveState) App.saveState();
        els.speedDirty = false;
      });
      root.querySelector('#meTest').addEventListener('click', onTest);
      root.querySelector('#meSave').addEventListener('click', onSave);
      els.reset.addEventListener('click', onReset);

      /* 其他模块改动词卡/收藏后，切入本屏时刷新（show 内总会刷新，这里兜底） */
      App.on('favs:change', refreshPeople);

      recordToday();
      refreshStats();
      refreshPeople();
      syncVoiceInputs(true);
    },

    show: function () {
      recordToday();          /* 打开即学习：记一笔今天 */
      refreshStats();
      refreshPeople();
      syncVoiceInputs();      /* 未编辑时回填最新持久化值 */
    },

    hide: function () {
      try { if (App.voice) App.voice.stop(); } catch (e) { console.warn('[me] 停止朗读出错', e); }
    }
  };
})();
