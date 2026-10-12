/* ============================================================
   会话列表屏 · js/screens/convos.js
   契约：App.screens.convos = { mount(el), show(), hide() }
   el = index.html 的 #screen-convos（mount 时整体替换 .scaffold 占位）

   设计（杂志梦幻语言）：
   · 刊头：IdolTalk. + THE CONVERSATIONS · 会话 + 计数（N° 编号风格）
   · 衬线大标题「最近，和谁聊过?」+ 日期行
   · 列表行：头像（说话光环同款 .ring 结构）+ 衬线大写名字 + N°xx 编号
     + 最后一条消息摘要（英文一行省略 + 中文小字）+ 右侧时间（今天/昨天/N 天前）
     行间发丝线分隔；hover 有 sheen 扫光
   · 空态：杂志式排版（还没有聊过天 → 去首页选一位）+ CTA
   · 版尾：「发现新爱豆」发丝线链接 → 首页

   数据：App.state.chats = { [idolId]: { msgs:[{role:'them'|'you', en, zh, t}], meta } }
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});
  App.screens = App.screens || {};

  /* ---------- 工具 ---------- */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /** 时间 → 会话列表的时间标注（今天 hh:mm / 昨天 / N 天前 / 日期） */
  function timeago(t) {
    if (!t) return '';
    var d = new Date(t), now = new Date();
    var dk = function (x) { return x.getFullYear() * 10000 + (x.getMonth() + 1) * 100 + x.getDate(); };
    var today = dk(now), that = dk(d);
    if (that === today) return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    if (that === today - 1) return '昨天';
    var days = Math.max(2, Math.round((now - d) / 864e5));
    if (days < 30) return days + ' 天前';
    return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  function nowLine() {
    var d = new Date();
    var week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + week + ' · ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }

  /* ---------- 数据 ---------- */

  /** 取出聊过天的爱豆（按最后消息时间倒序）：[{idol, last, t, count}] */
  function listConvos() {
    var chats = (App.state && App.state.chats) || {};
    var out = [];
    Object.keys(chats).forEach(function (id) {
      var rec = chats[id];
      var msgs = (rec && rec.msgs) || [];
      if (!msgs.length) return;
      var idol = (App.idolById && App.idolById[id]) || null;
      if (!idol) {
        // data.js 未提供 idolById 时的兜底查找
        var all = App.idols || [];
        for (var i = 0; i < all.length; i++) { if (all[i].id === id) { idol = all[i]; break; } }
      }
      if (!idol) return;
      var last = msgs[msgs.length - 1];
      out.push({ idol: idol, last: last, t: last.t || 0, count: msgs.length });
    });
    out.sort(function (a, b) { return b.t - a.t; });
    return out;
  }

  /* ---------- 屏幕模块 ---------- */

  var rootEl = null;
  var listEl = null;
  var countEl = null;
  var emptyEl = null;

  var STYLE_ID = 'convos-style';

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      /* 注意：不能给 #screen-convos 写 display——ID 选择器会盖过框架的 .screen{display:none}，
         导致本屏在非激活时仍然可见、拦截其他屏的点击（已踩过） */
      '#screen-convos .cv-scroll{min-height:100%;}',
      '#screen-convos .cv-scroll{flex:1;overflow-y:auto;padding:0 0 18px;scrollbar-width:none;}',
      '#screen-convos .cv-scroll::-webkit-scrollbar{display:none;}',

      /* 刊头 */
      '#screen-convos .cv-mast{display:flex;align-items:baseline;justify-content:space-between;padding:14px 22px 0;}',
      '#screen-convos .cv-logo{font-family:var(--serif);font-style:italic;font-size:17px;letter-spacing:.04em;color:var(--paper);}',
      '#screen-convos .cv-vol{font-size:9px;font-weight:600;letter-spacing:var(--ls-wide);color:var(--p3);text-transform:uppercase;}',
      '#screen-convos .cv-rule{height:1px;margin:10px 22px 0;background:linear-gradient(90deg,var(--line-rose),var(--line-violet) 55%,transparent);}',

      /* 标题区 */
      '#screen-convos .cv-head{padding:18px 22px 0;}',
      '#screen-convos .cv-kicker{display:flex;align-items:center;gap:8px;font-size:9px;font-weight:600;letter-spacing:var(--ls-wide);text-transform:uppercase;color:var(--p3);}',
      '#screen-convos .cv-kicker::before{content:"";width:22px;height:2px;border-radius:2px;background:var(--grad);}',
      '#screen-convos h1{margin:10px 0 0;font-size:26px;line-height:1.22;font-weight:800;letter-spacing:.01em;color:var(--paper);}',
      '#screen-convos .cv-sub{margin-top:8px;font-family:var(--serif);font-style:italic;font-size:13px;color:var(--p2);}',
      '#screen-convos .cv-meta{margin-top:6px;font-size:9.5px;letter-spacing:.08em;color:var(--p4);}',

      /* 列表 */
      '#screen-convos .cv-list{margin-top:16px;padding:0 22px;}',
      '#screen-convos .cv-row{position:relative;display:flex;align-items:center;gap:12px;padding:13px 2px;cursor:pointer;overflow:hidden;}',
      '#screen-convos .cv-row + .cv-row::before{content:"";position:absolute;left:0;right:0;top:0;height:1px;background:var(--line);}',
      '#screen-convos .cv-row:active{opacity:.75;}',
      '#screen-convos .cv-row .cv-sheen{position:absolute;inset:0;pointer-events:none;opacity:0;background:linear-gradient(115deg,transparent 30%,rgba(255,255,255,.06) 45%,transparent 60%);transform:translateX(-30%);transition:opacity .3s;}',
      '#screen-convos .cv-row:hover .cv-sheen{opacity:1;animation:cvSheen 1.1s var(--ease-soft);}',
      '@keyframes cvSheen{from{transform:translateX(-40%);}to{transform:translateX(40%);}}',

      /* 头像（沿用说话光环结构） */
      '#screen-convos .cv-ava{position:relative;flex:none;width:46px;height:46px;}',
      '#screen-convos .cv-ava img{width:46px;height:46px;border-radius:50%;object-fit:cover;display:block;filter:saturate(.96);}',
      '#screen-convos .cv-ava::after{content:"";position:absolute;inset:-2px;border-radius:50%;border:1px solid var(--line-2);pointer-events:none;}',
      '#screen-convos .cv-ava .cv-dot{position:absolute;right:1px;bottom:1px;width:8px;height:8px;border-radius:50%;background:var(--grad);border:1.5px solid var(--paper);}',

      /* 中列 */
      '#screen-convos .cv-mid{flex:1;min-width:0;}',
      '#screen-convos .cv-name{display:flex;align-items:baseline;gap:7px;min-width:0;}',
      '#screen-convos .cv-name b{font-family:var(--serif);font-size:15px;letter-spacing:.05em;text-transform:uppercase;font-weight:600;color:var(--paper);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '#screen-convos .cv-name i{flex:none;font-family:var(--serif);font-style:italic;font-size:9px;letter-spacing:.08em;color:var(--p4);}',
      '#screen-convos .cv-prev{display:block;margin-top:3px;font-size:11.5px;line-height:1.45;color:var(--p2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '#screen-convos .cv-prev .cv-zh{margin-left:6px;font-size:10px;color:var(--p4);}',

      /* 右列 */
      '#screen-convos .cv-side{flex:none;display:flex;flex-direction:column;align-items:flex-end;gap:5px;}',
      '#screen-convos .cv-time{font-size:9.5px;letter-spacing:.06em;color:var(--p3);}',
      '#screen-convos .cv-count{font-size:8.5px;font-weight:600;letter-spacing:.1em;color:var(--p4);}',

      /* 空态 */
      '#screen-convos .cv-empty{padding:44px 22px;text-align:center;}',
      '#screen-convos .cv-empty .cv-en{font-family:var(--serif);font-style:italic;font-size:17px;color:var(--p2);}',
      '#screen-convos .cv-empty .cv-hint{margin-top:10px;font-size:11.5px;line-height:1.8;color:var(--p3);}',
      '#screen-convos .cv-empty .cv-cta{margin-top:20px;display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border-radius:999px;border:1px solid transparent;background:linear-gradient(var(--paper),var(--paper)) padding-box,var(--grad) border-box;color:var(--ink);font-size:12px;font-weight:600;letter-spacing:.06em;cursor:pointer;}',
      '#screen-convos .cv-empty .cv-cta:active{opacity:.8;}',

      /* 版尾 */
      '#screen-convos .cv-foot{display:flex;align-items:center;gap:10px;margin:22px 22px 0;color:var(--p3);font-size:10px;letter-spacing:.12em;text-transform:uppercase;cursor:pointer;}',
      '#screen-convos .cv-foot::before,#screen-convos .cv-foot::after{content:"";flex:1;height:1px;background:var(--line);}'
    ].join('\n');
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = css;
    document.head.appendChild(st);
  }

  function rowHTML(item) {
    var idol = item.idol;
    var last = item.last || {};
    var en = last.en || '';
    var zh = last.zh || '';
    return '' +
      '<div class="cv-row" data-id="' + esc(idol.id) + '" role="button" tabindex="0" aria-label="和 ' + esc(idol.zh) + ' 的会话">' +
      '  <span class="cv-sheen" aria-hidden="true"></span>' +
      '  <span class="cv-ava" style="--pos:' + (App.photoPos ? App.photoPos(idol.id) : '50% 20%') + ';--zoom:' + (App.photoZoom ? App.photoZoom(idol.id) : 1) + ';--fy:' + (App.photoY ? Math.round(App.photoY(idol.id) * 100) + '%' : '30%') + '"><img loading="lazy" src="' + esc(App.photoFor ? App.photoFor(idol.id) : '') + '" alt="" onerror="this.style.opacity=0"><span class="cv-dot" aria-hidden="true"></span></span>' +
      '  <span class="cv-mid">' +
      '    <span class="cv-name"><b>' + esc(idol.stage || idol.name) + '</b><i>' + esc(idol.group) + '</i></span>' +
      '    <span class="cv-prev">' + esc(en) + '</span>' +
      '  </span>' +
      '  <span class="cv-side"><span class="cv-time">' + esc(timeago(item.t)) + '</span><span class="cv-count">' + item.count + ' 条</span></span>' +
      '</div>';
  }

  function render() {
    if (!listEl) return;
    var items = listConvos();
    if (countEl) countEl.textContent = items.length + ' 段会话';
    if (!items.length) {
      listEl.innerHTML = '';
      if (emptyEl) emptyEl.style.display = '';
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    listEl.innerHTML = items.map(rowHTML).join('');
  }

  App.screens.convos = {
    mount: function (el) {
      injectStyle();
      rootEl = el;
      el.innerHTML = '' +
        '<div class="cv-scroll">' +
        '  <div class="cv-mast"><span class="cv-logo">IdolTalk.</span><span class="cv-vol">The Conversations · 会话</span></div>' +
        '  <div class="cv-rule" aria-hidden="true"></div>' +
        '  <div class="cv-head">' +
        '    <div class="cv-kicker">The Conversations <span id="cv-count"></span></div>' +
        '    <h1>最近，<br>和谁聊过？</h1>' +
        '    <div class="cv-sub">Your recent conversations.</div>' +
        '    <div class="cv-meta">' + nowLine() + ' · 往下翻是聊过的每一位</div>' +
        '  </div>' +
        '  <div class="cv-list" id="cv-list"></div>' +
        '  <div class="cv-empty" id="cv-empty" style="display:none">' +
        '    <div class="cv-en">No conversations yet.</div>' +
        '    <div class="cv-hint">还没有和任何爱豆聊过天。<br>去首页选一位，说第一句话吧。</div>' +
        '    <button class="cv-cta" id="cv-go-home" type="button">去首页选爱豆 →</button>' +
        '  </div>' +
        '  <div class="cv-foot" id="cv-foot" role="button" tabindex="0">发现新爱豆</div>' +
        '</div>';

      listEl = el.querySelector('#cv-list');
      countEl = el.querySelector('#cv-count');
      emptyEl = el.querySelector('#cv-empty');

      /* 行点击 / 键盘 → 打开会话 */
      listEl.addEventListener('click', function (e) {
        var row = e.target.closest('.cv-row');
        if (!row) return;
        var id = row.getAttribute('data-id');
        if (!id) return;
        if (App.chat && App.chat.open) App.chat.open(id);
        App.nav.go('chat');
      });
      listEl.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var row = e.target.closest('.cv-row');
        if (!row) return;
        e.preventDefault();
        if (App.chat && App.chat.open) App.chat.open(row.getAttribute('data-id'));
        App.nav.go('chat');
      });

      el.querySelector('#cv-go-home').addEventListener('click', function () { App.nav.go('home'); });
      var foot = el.querySelector('#cv-foot');
      foot.addEventListener('click', function () { App.nav.go('home'); });
      foot.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); App.nav.go('home'); }
      });

      render();
    },

    show: function () {
      render(); // 每次进入刷新（新聊的会话即时出现）
    },

    hide: function () {}
  };
})();
