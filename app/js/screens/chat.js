/* ============================================================
   screens/chat.js — 聊天屏「杂志梦幻」（editorial.html 屏幕2 + 三方案精华）
   ------------------------------------------------------------
   契约：App.screens.chat = { mount(el), show(), hide() }
   el = index.html 的 #screen-chat（mount 时整体替换 .scaffold 占位）
   设计实现：
   · 头部：返回钮（App.nav.go('home')）+ .ava 说话光环头像
     （App.on('speaking:start'/'speaking:end') 驱动 .ring 旋转油墨环 +
      .speak-halo 呼吸光晕）+ 衬线大写名字 + N° 编号 + 状态文案切换
   · 消息流：.bubble.glass（爱豆）/ .bubble.solid（用户）；
     底部对齐（justify-content:flex-end）+ .mask-fade 上下渐隐
   · 每条爱豆消息 = 语音气泡：「听 Ta 说」.play 钮 + 24 根细声波
     （.p=已播渐变，播放进度由 voice.speak 的 onSentence 驱动）+
     时长 + 句子高亮（朗读到哪句哪句点亮）
   · 纠音批注（晨雾梦核精华）：用户语法错误时，在消息旁渲染
     「编辑批注」卡（Georgia 斜体 + 波浪下划线 + 修改建议）
   · 输入坞：快捷短语 chips + 胶囊输入 + 渐变发送钮；
     发送 → App.chat.send → App.reply.generate（本地引擎）→
     自动 App.voice.speak（光环/声波随 onStart/onSentence/onEnd 起落）
   · 语音失败静默降级（try/catch + console.warn，不弹错）
   · 屏幕样式以 <style> 注入并全部收在 #screen-chat 作用域内，
     不污染 app.css 设计系统与其他屏幕
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});
  App.screens = App.screens || {};

  /* ============ 屏幕私有样式（作用域 #screen-chat） ============ */
  var STYLE_ID = 'chat-screen-style';
  var STYLE_CSS = [
    '/* —— 头部 —— */',
    '#screen-chat .chat-head{ flex:none; padding:2px 20px 0; }',
    '#screen-chat .ch-row{ display:flex; align-items:center; gap:12px; padding:2px 0 12px; }',
    '#screen-chat .ch-id{ flex:1; min-width:0; }',
    '#screen-chat .ch-name{ display:flex; align-items:baseline; gap:8px; min-width:0; font-family:var(--serif); font-size:16px; letter-spacing:.05em; text-transform:uppercase; }',
    '#screen-chat .ch-name span{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }',
    '#screen-chat .ch-name i{ flex:none; font-family:var(--serif); font-style:italic; font-size:9.5px; letter-spacing:.1em; color:var(--p3); }',
    '#screen-chat .ch-sub{ margin-top:2px; font-size:8px; font-weight:600; letter-spacing:.2em; text-transform:uppercase; color:var(--p3); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }',
    '#screen-chat .ch-status{ display:flex; align-items:center; gap:6px; margin-top:4px; font-size:10px; letter-spacing:.08em; color:var(--p2); }',
    '#screen-chat #ch-status.live-on{ color:var(--rose); }',
    '#screen-chat .ch-rule{ height:1px; background:var(--line); position:relative; }',
    '#screen-chat .ch-rule::before{ content:""; position:absolute; left:0; top:0; width:64px; height:1px; background:var(--grad); }',
    '#screen-chat .icon-btn.off{ color:var(--p4); }',

    '/* —— 消息流：底部对齐 + 上下渐隐（.scroll/.mask-fade 来自 app.css） —— */',
    '#screen-chat .chat-body{ flex:1; min-height:0; overflow-y:auto; overscroll-behavior:contain; scrollbar-width:none; }',
    '#screen-chat .chat-body::-webkit-scrollbar{ display:none; }',
    '#screen-chat .chat-flow{ min-height:100%; display:flex; flex-direction:column; justify-content:flex-end; gap:11px; padding:16px 20px 12px; }',
    '#screen-chat .divider{ display:flex; align-items:center; gap:12px; margin:2px 0 5px; font-size:8.5px; font-weight:600; letter-spacing:.26em; color:var(--p3); text-transform:uppercase; }',
    '#screen-chat .divider::before, #screen-chat .divider::after{ content:""; flex:1; height:1px; background:var(--line); }',

    '/* —— 消息 —— */',
    '#screen-chat .msg{ display:flex; flex-direction:column; max-width:84%; }',
    '#screen-chat .msg.them{ align-self:flex-start; align-items:flex-start; }',
    '#screen-chat .msg.me{ align-self:flex-end; align-items:flex-end; }',
    '#screen-chat .msg-meta{ margin-top:6px; font-size:8px; font-weight:600; letter-spacing:.22em; color:var(--p3); text-transform:uppercase; }',
    '#screen-chat .msg-meta i{ font-style:normal; color:var(--p2); }',

    '/* —— 正在输入 —— */',
    '#screen-chat .typing{ display:flex; gap:5px; padding:15px 17px; }',
    '#screen-chat .typing i{ width:5px; height:5px; border-radius:50%; background:var(--grad); animation:ch-ty 1.3s var(--ease-soft) infinite; }',
    '#screen-chat .typing i:nth-child(2){ animation-delay:.16s; }',
    '#screen-chat .typing i:nth-child(3){ animation-delay:.32s; }',
    '@keyframes ch-ty{ 0%,60%,100%{ opacity:.25; transform:translateY(0); } 30%{ opacity:1; transform:translateY(-4px); } }',

    '/* —— 语音气泡：句子朗读高亮 —— */',
    '#screen-chat .voice-text .sent{ color:var(--p2); transition:color .35s var(--ease); }',
    '#screen-chat .voice-text .sent.on{ color:transparent; background:var(--grad); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; text-shadow:0 0 22px rgba(242,160,220,.35); }',
    '#screen-chat .voice-text .sent.done{ color:var(--paper); }',

    '/* —— 纠音批注卡（编辑批注 · 晨雾梦核精华） —— */',
    '#screen-chat .note-card{ align-self:stretch; margin:1px 0 4px; padding:13px 15px 12px; }',
    '#screen-chat .note-quote{ margin-top:9px; font-family:var(--serif); font-style:italic; font-size:13.5px; letter-spacing:.01em; color:var(--paper); line-height:1.55; }',
    '#screen-chat .note-quote b{ font-weight:600; }',
    '#screen-chat .note-tip{ margin-top:7px; font-size:11px; line-height:1.65; color:var(--p2); letter-spacing:.02em; }',

    '/* —— 输入坞（全沉浸，底栏已由 [data-nav="chat"] 隐藏） —— */',
    '#screen-chat .inputbar{ flex:none; border-top:1px solid var(--line); background:rgba(11,8,18,.8); backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px); padding:10px 20px 0; }',
    '#screen-chat .sugg{ display:flex; gap:8px; margin-bottom:11px; overflow-x:auto; scrollbar-width:none; }',
    '#screen-chat .sugg::-webkit-scrollbar{ display:none; }',
    '#screen-chat .sugg button{ flex:none; font-size:9.5px; letter-spacing:.08em; color:var(--p2); padding:6px 12px; border-radius:999px; border:1px solid var(--line); transition:color var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease); }',
    '#screen-chat .sugg button:hover{ color:var(--paper); border-color:var(--line-2); }',
    '#screen-chat .in-row{ display:flex; align-items:center; gap:10px; }',
    '#screen-chat .in-pill{ flex:1; display:flex; align-items:center; gap:9px; height:41px; padding:0 16px; border-radius:999px; border:1px solid var(--line); background:rgba(247,242,248,.05); transition:border-color .4s var(--ease); }',
    '#screen-chat .in-pill:focus-within{ border-color:rgba(242,160,220,.5); }',
    '#screen-chat .in-pill input{ flex:1; min-width:0; font-size:12px; letter-spacing:.02em; }',
    '#screen-chat .in-pill input::placeholder{ color:var(--p3); }',
    '#screen-chat .send{ flex:none; width:41px; height:41px; border-radius:50%; background:var(--grad-2); color:var(--on-grad); display:flex; align-items:center; justify-content:center; box-shadow:0 10px 26px -10px rgba(242,160,220,.6); transition:transform var(--t-fast) var(--ease); }',
    '#screen-chat .send:hover{ transform:scale(1.06); }',
    '#screen-chat .send:active{ transform:scale(.94); }',
    '#screen-chat .in-bar{ height:22px; position:relative; }',
    '#screen-chat .in-bar .home-bar{ bottom:6px; }',

    '/* —— 空态 / 最近对话 —— */',
    '#screen-chat .chat-empty{ flex:1; min-height:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; padding:0 40px; text-align:center; }',
    '#screen-chat .ce-title{ font-family:var(--serif); font-style:italic; font-size:26px; letter-spacing:.02em; margin-top:6px; }',
    '#screen-chat .ce-note{ font-size:9.5px; letter-spacing:.18em; text-transform:uppercase; color:var(--p3); line-height:2; white-space:pre-line; }',
    '#screen-chat .rc-list{ margin-top:26px; width:100%; display:flex; flex-direction:column; }',
    '#screen-chat .rc-item{ display:flex; align-items:center; gap:12px; width:100%; padding:9px 2px; text-align:left; transition:opacity var(--t-fast) var(--ease); }',
    '#screen-chat .rc-item + .rc-item{ border-top:1px solid var(--line); }',
    '#screen-chat .rc-item:hover{ opacity:.85; }',
    '#screen-chat .rc-item .ava{ --ava:38px; }',
    '#screen-chat .rc-main{ flex:1; min-width:0; }',
    '#screen-chat .rc-name{ font-family:var(--serif); font-size:13px; letter-spacing:.04em; text-transform:uppercase; }',
    '#screen-chat .rc-name small{ font-family:var(--sans); font-size:8px; letter-spacing:.2em; color:var(--p3); margin-left:7px; }',
    '#screen-chat .rc-preview{ margin-top:3px; font-size:10.5px; color:var(--p3); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }',
    '#screen-chat .rc-time{ flex:none; font-size:8.5px; letter-spacing:.12em; color:var(--p4); }',

    '/* —— 真机 PWA：输入坞让位底部安全区 —— */',
    '@media (max-width:520px){ #screen-chat .inputbar{ padding-bottom:calc(2px + env(safe-area-inset-bottom, 0px)); } }'
  ].join('\n');

  /* ============ 通用小工具 ============ */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function timeStr(t) {
    var d = new Date(Number(t) || Date.now());
    var h = d.getHours(), m = d.getMinutes();
    return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
  }

  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var WD = ['日', '一', '二', '三', '四', '五', '六'];
  function dayLabel() {
    var d = new Date();
    var dd = (d.getDate() < 10 ? '0' : '') + d.getDate();
    return MON[d.getMonth()] + ' ' + dd + ' · 周' + WD[d.getDay()] + '晚';
  }

  /** 英文分句（供朗读句级高亮） */
  function splitSentences(text) {
    var s = String(text || '').replace(/\s+/g, ' ').trim();
    if (!s) return [];
    var parts = s.match(/[^.!?…]+[.!?…]*/g) || [s];
    return parts.map(function (p) { return p.trim(); }).filter(Boolean);
  }

  /** 粗估朗读时长（秒）：约 0.42s/词 + 起始 1.1s */
  function estSeconds(en) {
    var words = String(en || '').split(/\s+/).filter(Boolean).length;
    return Math.max(2, Math.round(words * 0.42 + 1.1));
  }
  function durStr(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  /** 24 根声波高度图（取自概念稿固定样式，视觉稳定） */
  var WAVE_H = [7, 12, 18, 10, 22, 13, 8, 16, 24, 12, 9, 17, 14, 7, 19, 11, 23, 13, 8, 16, 10, 14, 7, 12];

  /* ============ 快捷短语池（桌面版 SUGGESTION_POOLS 精简移植） ============ */
  var SUGGEST = {
    opening: ['I had a long day.', 'I had a good day!', 'How was your day?'],
    open: ['I had a long day.', 'I had a good day!', 'How was your day?'],
    comfort: ['Can I tell you what happened?', 'Just stay with me a little.', 'Let’s talk about something light.'],
    tired: ['Let’s take it slow.', 'Just need some quiet company.', 'I’ll get some water.'],
    happy: ['The best part was…', 'What made you smile today?', 'I want to tell you the whole story.'],
    fandom: ['My bias is…', 'Their comeback made my week.', 'I became a fan because…'],
    'school-work': ['The hardest part is…', 'I finished something today!', 'I need a small break.'],
    plans: ['This weekend I want to…', 'I have a day off soon.', 'Tomorrow I’ll…'],
    'ask-idol': ['I haven’t eaten yet.', 'What do you recommend?', 'Tell me about practice.'],
    'food-question': ['I love spicy food.', 'What’s your favorite snack?', 'Something warm sounds perfect.'],
    food: ['I love spicy food.', 'Let’s get something warm.', 'What’s your favorite snack?'],
    music: ['I’ve been listening to…', 'Teach me one lyric.', 'What song should I try?'],
    care: ['I’ll take care of myself.', 'Let’s both get some rest.', 'I promise I’ll sleep early.'],
    affection: ['I need a little encouragement.', 'Make me laugh.', 'Just being here is enough.'],
    grammar: ['Let me try again…', 'How do I say it naturally?', 'Thanks for the tip!'],
    'english-help': ['I want to say…', 'Can you make it simpler?', 'Correct this sentence for me.'],
    'followup-answer': ['Let me explain a little…', 'It’s a long story.', 'I’ll tell you more later.'],
    'return-topic': ['A little better now.', 'Still the same, honestly.', 'I listened to it again!'],
    name: ['Can you call me that?', 'Let’s talk about today.', 'What snack do you like?'],
    resume: ['I thought about what you said.', 'I’m doing a little better.', 'Still a rough week, honestly.']
  };
  function poolFor(intent) {
    return SUGGEST[intent] || SUGGEST.open;
  }

  /* ============ 模块状态 ============ */

  var root, flow, body;
  var headEl, avaEl, avaImg, nameEl, noEl, subEl, statusEl, statusLive, voiceBtn;
  var input, suggBox;

  var openInfo = null;        /* App.chat.open 的返回（驱动分割线文案） */
  var lastRenderedId = null;
  var playingMsg = null;     /* 当前播放中的语音气泡 .msg */
  var speakSeq = 0;          /* 朗读令牌：新播放/停止令旧回调失效 */
  var hintOffset = 0;

  /* ============ 头部 ============ */

  function updateHeader() {
    var id = App.chat && App.chat.activeId;
    var idol = null, idx = -1;
    var list = App.idols || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) { idol = list[i]; idx = i; break; }
    }
    if (!idol) {
      nameEl.textContent = '—';
      noEl.textContent = 'N°—';
      subEl.textContent = 'The Conversation';
      avaImg.removeAttribute('src');
      return;
    }
    var n = idx + 1;
    var no = 'N°' + (n < 10 ? '0' : '') + n;
    avaImg.src = App.photoFor(id);
    avaImg.alt = idol.stage;
    nameEl.textContent = idol.stage;
    noEl.textContent = no;
    subEl.textContent = idol.group + ' · ' + idol.zh;
  }

  function setHeadSpeaking(on) {
    if (!headEl) return;
    headEl.classList.toggle('speaking', !!on);
    if (statusEl) {
      statusEl.textContent = on ? '正在说话…' : '正在陪你练英语';
      statusEl.classList.toggle('live-on', !!on);
    }
  }

  function autoSpeakOn() {
    return !!(App.state && App.state.prefs && App.state.prefs.autoSpeak !== false);
  }
  function refreshVoiceBtn() {
    if (!voiceBtn) return;
    var on = autoSpeakOn();
    voiceBtn.classList.toggle('off', !on);
    voiceBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  /* ============ 消息渲染 ============ */

  function renderMsg(m) {
    var msg = el('div', 'msg ' + (m.role === 'me' ? 'me' : 'them'));
    var idol = App.idols || [];
    var name = 'IDOL';
    var id = App.chat && App.chat.activeId;
    for (var i = 0; i < idol.length; i++) if (idol[i].id === id) { name = idol[i].stage.toUpperCase(); break; }

    if (m.role === 'me') {
      var b = el('div', 'bubble solid');
      /* 语法错误处加波浪下划线（编辑批注的呼应） */
      if (m.g && m.g.orig) {
        var raw = String(m.en || '');
        var at = raw.toLowerCase().indexOf(String(m.g.orig).toLowerCase());
        if (at >= 0) {
          b.appendChild(document.createTextNode(raw.slice(0, at)));
          b.appendChild(el('span', 'wavy', raw.slice(at, at + m.g.orig.length)));
          b.appendChild(document.createTextNode(raw.slice(at + m.g.orig.length)));
        } else {
          b.textContent = raw;
        }
      } else {
        b.textContent = m.en;
      }
      msg.appendChild(b);
      msg.appendChild(el('div', 'msg-meta', '你 · ' + timeStr(m.t)));
      return msg;
    }

    /* 爱豆消息 = 语音气泡 */
    var vb = el('div', 'bubble glass voice');
    var row = el('div', 'voice-row');
    var play = el('button', 'play');
    play.type = 'button';
    play.dataset.label = '听 ' + name + ' 说';
    play.setAttribute('aria-label', play.dataset.label);
    play.innerHTML =
      '<svg class="ic-play" width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z"/></svg>' +
      '<svg class="ic-pause" width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6.5" y="5" width="3.6" height="14" rx="1.4"/><rect x="13.9" y="5" width="3.6" height="14" rx="1.4"/></svg>';
    row.appendChild(play);

    var wave = el('div', 'wave');
    wave.setAttribute('aria-hidden', 'true');
    for (var w = 0; w < 24; w++) {
      var bar = el('i');
      bar.style.setProperty('--h', WAVE_H[w % WAVE_H.length] + 'px');
      bar.style.setProperty('--d', (w * 0.05).toFixed(2) + 's');
      wave.appendChild(bar);
    }
    row.appendChild(wave);
    row.appendChild(el('span', 'dur', durStr(estSeconds(m.en))));
    vb.appendChild(row);

    var vt = el('div', 'voice-text');
    var sents = splitSentences(m.en);
    if (!sents.length) sents = [String(m.en || '')];
    for (var s = 0; s < sents.length; s++) {
      var sp = el('span', 'sent', sents[s]);
      sp.dataset.si = String(s);
      vt.appendChild(sp);
      /* 空格在句后插入（最后一句除外）：保证句间有正常空格分隔 */
      if (s < sents.length - 1) vt.appendChild(document.createTextNode(' '));
    }
    vb.appendChild(vt);

    if (m.zh) vb.appendChild(el('div', 'cn', m.zh));
    msg.appendChild(vb);
    var meta = el('div', 'msg-meta');
    meta.appendChild(el('i', null, name));
    meta.appendChild(document.createTextNode(' · ' + timeStr(m.t) + (m.open ? ' · 开场' : ' · 语音')));
    msg.appendChild(meta);
    msg.dataset.speak = m.en;      /* 「听 Ta 说」朗读文本 */
    msg.dataset.sentTotal = String(sents.length);
    return msg;
  }

  function renderNote(g) {
    var card = el('div', 'glass glass--soft note-card rise');
    card.style.setProperty('--i', '1');
    var kick = el('div', 'kicker');
    kick.appendChild(el('span', 'tick'));
    kick.appendChild(document.createTextNode('编辑批注 · 纠音'));
    card.appendChild(kick);

    var quote = el('p', 'note-quote');
    var orig = el('span', 'wavy', g.orig);
    quote.appendChild(document.createTextNode('“'));
    quote.appendChild(orig);
    quote.appendChild(document.createTextNode('” → 应为 “'));
    var fix = el('b', 'grad-text', g.fix);
    quote.appendChild(fix);
    quote.appendChild(document.createTextNode('”'));
    card.appendChild(quote);

    card.appendChild(el('p', 'note-tip',
      '小提示：把 “' + g.orig + '” 换成 “' + g.fix + '” 再说一遍。' +
      '其他部分都很棒，要不要把整句再试一次？'));
    return card;
  }

  function renderTyping() {
    var msg = el('div', 'msg them');
    var b = el('div', 'bubble glass typing');
    b.appendChild(el('i'));
    b.appendChild(el('i'));
    b.appendChild(el('i'));
    msg.appendChild(b);
    flow.appendChild(msg);
    return msg;
  }

  function renderDivider(text) {
    return el('div', 'divider', text);
  }

  /* ============ 历史整屏渲染 ============ */

  function renderHistory() {
    if (!flow) return;
    while (flow.firstChild) flow.removeChild(flow.firstChild);
    var id = App.chat && App.chat.activeId;
    updateHeader();
    refreshVoiceBtn();
    lastRenderedId = id;

    if (!id) { renderEmpty(); return; }

    var rec = App.chat.history[id];
    var msgs = (rec && rec.msgs) || [];

    /* 分割线：新会话 = 日期；续聊 = 回归提示 */
    if (openInfo && openInfo.id === id && !openInfo.isNew) {
      flow.appendChild(renderDivider(openInfo.gap > 0
        ? (openInfo.gap + ' 天后回来 · 继续上次的对话')
        : '今天 · 继续上次的对话'));
    } else {
      flow.appendChild(renderDivider(dayLabel()));
    }

    msgs.forEach(function (m) {
      flow.appendChild(renderMsg(m));
      if (m.role === 'me' && m.g) flow.appendChild(renderNote(m.g));
    });
    updateSuggestions();
    scrollBottom();
  }

  function renderEmpty() {
    var box = el('div', 'chat-empty');
    var kick = el('div', 'kicker');
    kick.appendChild(el('span', 'tick'));
    kick.appendChild(document.createTextNode('The Conversation · 聊天'));
    box.appendChild(kick);
    box.appendChild(el('p', 'ce-title', 'Tonight, in conversation.'));
    box.appendChild(el('p', 'ce-note', '还没有开始对话\n去首页选一位今晚的爱豆吧'));

    /* 最近对话（跨会话恢复入口） */
    var hist = App.chat.history || {};
    var ids = Object.keys(hist).filter(function (k) {
      return hist[k] && hist[k].msgs && hist[k].msgs.length;
    });
    if (ids.length) {
      ids.sort(function (a, b) {
        return (hist[b].meta && hist[b].meta.lastSeen || 0) - (hist[a].meta && hist[a].meta.lastSeen || 0);
      });
      var list = el('div', 'rc-list');
      var listHead = el('div', 'divider', 'Recent · 最近对话');
      box.appendChild(listHead);
      ids.slice(0, 6).forEach(function (rid) {
        var rec = hist[rid];
        var idol = null, all = App.idols || [];
        for (var i = 0; i < all.length; i++) if (all[i].id === rid) { idol = all[i]; break; }
        if (!idol) return;
        var item = el('button', 'rc-item');
        item.type = 'button';
        var ava = el('div', 'ava');
        ava.appendChild(el('span', 'speak-halo'));
        ava.appendChild(el('span', 'ring'));
        var img = el('img');
        img.src = App.photoFor(rid);
        img.alt = idol.stage;
        ava.appendChild(img);
        item.appendChild(ava);
        var main = el('div', 'rc-main');
        var nm = el('div', 'rc-name', idol.stage);
        var sm = el('small', null, idol.group);
        nm.appendChild(sm);
        main.appendChild(nm);
        var lastThem = null;
        for (var j = rec.msgs.length - 1; j >= 0; j--) {
          if (rec.msgs[j].role === 'them') { lastThem = rec.msgs[j]; break; }
        }
        main.appendChild(el('div', 'rc-preview', lastThem ? String(lastThem.en) : ''));
        item.appendChild(main);
        item.appendChild(el('span', 'rc-time', timeStr(rec.meta && rec.meta.lastSeen)));
        item.addEventListener('click', function () { App.chat.open(rid); });
        list.appendChild(item);
      });
      if (list.firstChild) box.appendChild(list);
    }
    flow.appendChild(box);
  }

  function scrollBottom() {
    if (body) body.scrollTop = body.scrollHeight;
  }

  function updateSuggestions() {
    if (!suggBox) return;
    while (suggBox.firstChild) suggBox.removeChild(suggBox.firstChild);
    var id = App.chat && App.chat.activeId;
    var rec = id ? App.chat.history[id] : null;
    var intent = (rec && rec.meta && rec.meta.lastIntent) || 'opening';
    poolFor(intent).slice(0, 2).forEach(function (text) {
      var b = el('button', null, text);
      b.type = 'button';
      b.addEventListener('click', function () {
        input.value = text;
        input.focus();
      });
      suggBox.appendChild(b);
    });
  }

  /* ============ 语音朗读：光环 + 声波进度（静默降级） ============ */

  function setBubblePlaying(msgEl, on) {
    if (!msgEl) return;
    var vb = msgEl.querySelector('.bubble.voice');
    if (vb) vb.classList.toggle('playing', !!on);
    var play = msgEl.querySelector('.play');
    if (play) play.setAttribute('aria-label', on ? '暂停朗读' : (play.dataset.label || '听 Ta 说'));
  }

  function markBars(msgEl, count) {
    if (!msgEl) return;
    var bars = msgEl.querySelectorAll('.wave i');
    for (var i = 0; i < bars.length; i++) bars[i].classList.toggle('p', i < count);
  }

  function markSentences(msgEl, active) {
    if (!msgEl) return;
    var sents = msgEl.querySelectorAll('.voice-text .sent');
    for (var i = 0; i < sents.length; i++) {
      sents[i].classList.toggle('on', i === active);
      sents[i].classList.toggle('done', active < 0 || i < active);
    }
  }

  /** 清除全部句子高亮（回到未读态；朗读失败/重播前复位用） */
  function resetSentences(msgEl) {
    if (!msgEl) return;
    var sents = msgEl.querySelectorAll('.voice-text .sent');
    for (var i = 0; i < sents.length; i++) sents[i].classList.remove('on', 'done');
  }

  function finishPlayback(msgEl, played) {
    setHeadSpeaking(false);
    setBubblePlaying(msgEl, false);
    if (played) {
      markBars(msgEl, 24);
      markSentences(msgEl, -1);   /* 全部已读：回落纸色 */
    } else {
      markBars(msgEl, 0);         /* 失败：无假进度 UI，回到未读态 */
      resetSentences(msgEl);
    }
    if (playingMsg === msgEl) playingMsg = null;
  }

  function stopPlayback() {
    speakSeq++;
    try { if (App.voice) App.voice.stop(); } catch (e) { console.warn('[chat] 停止朗读失败（静默）', e); }
    var m = playingMsg;
    playingMsg = null;
    setHeadSpeaking(false);
    if (m) setBubblePlaying(m, false);
  }

  /** 朗读一条爱豆消息：光环/呼吸、声波逐句点亮、句子高亮
      （播放态只在收到 onStart 后进入——语音不可用时静默、无假播放 UI） */
  function speakMsg(msgEl) {
    if (!msgEl) return;
    var text = msgEl.dataset.speak || '';
    if (!text) return;
    stopPlayback();
    var my = ++speakSeq;
    var started = false;   /* 本次会话是否真正出声过（防“全失败 → onError+onEnd”被误标为已播） */
    playingMsg = msgEl;
    markBars(msgEl, 0);
    resetSentences(msgEl);   /* 高亮随 onStart/onSentence 真实推进（防假播放态） */

    var cbs = {
      onStart: function () {
        if (speakSeq !== my) return;
        started = true;
        setHeadSpeaking(true);
        setBubblePlaying(msgEl, true);
      },
      onSentence: function (i, total) {
        if (speakSeq !== my) return;
        var t = Math.max(1, Number(total) || 1);
        var idx = Math.max(0, Number(i) || 0);
        markBars(msgEl, Math.max(1, Math.round(24 * (idx + 1) / t)));
        markSentences(msgEl, idx);
      },
      onEnd: function () {
        if (speakSeq !== my) return;
        finishPlayback(msgEl, started);
      },
      onError: function () {
        if (speakSeq !== my) return;
        finishPlayback(msgEl, false);
      }
    };
    try {
      App.voice.speak(text, cbs);   /* 失败静默降级：不弹错、不 toast */
    } catch (e) {
      console.warn('[chat] 语音朗读不可用（静默降级）', e);
      if (speakSeq === my) finishPlayback(msgEl, false);
    }
  }

  /* ============ 亲吻音效（回复里出现亲亲时播放） ============ */
  var KISS_RE = /😘|💋|💏|mwah|smooch|muah|\bkiss|\bpecks?\b|亲亲|亲一口|亲一下|亲一个|啵/i;
  var kissAudio = null;
  function playKissSfx() {
    try {
      if (!kissAudio) { kissAudio = new Audio('assets/kiss.mp3'); kissAudio.volume = 0.95; }
      kissAudio.currentTime = 0;
      var pr = kissAudio.play();
      if (pr && pr.catch) pr.catch(function () {});
    } catch (e) { /* 静默降级：不影响对话 */ }
  }

  /* ============ 发送流程（打字气泡 → 回复 → 自动朗读） ============ */

  function handleSend() {
    if (!App.chat || !App.chat.activeId) {
      App.emit('toast', '先去首页选一位今晚的爱豆吧');
      return;
    }
    var text = input.value.trim();
    if (!text) { input.focus(); return; }
    input.value = '';

    stopPlayback();

    /* 用户气泡先上屏（乐观渲染），爱豆回复走大模型异步返回 */
    var userEl = flow.appendChild(renderMsg({ role: 'me', en: text, t: Date.now() }));
    var typing = renderTyping();
    scrollBottom();

    var p;
    try {
      p = (App.chat && typeof App.chat.sendAsync === 'function')
        ? App.chat.sendAsync(text)
        : Promise.resolve(App.chat.send(text));
    } catch (e) {
      console.warn('[chat] 发送失败', e);
      if (typing.parentNode) typing.remove();
      return;
    }

    Promise.resolve(p).then(function (res) {
      if (typing.parentNode) typing.remove();
      if (!res || !res.ok) return;
      /* 语法批注紧贴用户气泡下方 */
      if (res.user && res.user.g) {
        var noteEl = renderNote(res.user.g);
        if (userEl && userEl.parentNode) userEl.parentNode.insertBefore(noteEl, userEl.nextSibling);
        else flow.appendChild(noteEl);
      }
      var replyEl = flow.appendChild(renderMsg(res.reply));
      scrollBottom();
      updateSuggestions();
      if (autoSpeakOn()) speakMsg(replyEl);
      /* 回复里出现亲亲 → 补一声"啵" */
      if (KISS_RE.test(String(res.reply.en || '')) || KISS_RE.test(String(res.reply.zh || ''))) {
        setTimeout(playKissSfx, 700);
      }
    }).catch(function (e) {
      console.warn('[chat] 回复失败', e);
      if (typing.parentNode) typing.remove();
      App.emit('toast', '爱豆走神了，再发一次试试～');
    });
  }

  /* ============ 挂载 ============ */

  var ICONS = {
    back: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 5 8 12l6.5 7"/></svg>',
    voice: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9.5v5h3.2L12 19V5L7.2 9.5z"/><path d="M15.5 9.2a4 4 0 0 1 0 5.6"/><path d="M18 6.8a7.4 7.4 0 0 1 0 10.4"/></svg>',
    hint: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5l1.9 4.7 4.7 1.9-4.7 1.9L12 16.7l-1.9-4.7-4.7-1.9 4.7-1.9z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/></svg>',
    send: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V6M6 11.5 12 5.5l6 6"/></svg>'
  };

  App.screens.chat = {

    mount: function (el) {
      root = el;

      /* 注入屏幕样式（一次性） */
      if (!document.getElementById(STYLE_ID)) {
        var style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = STYLE_CSS;
        document.head.appendChild(style);
      }

      /* 整体替换占位脚手架 */
      while (root.firstChild) root.removeChild(root.firstChild);
      root.innerHTML =
        '<header class="chat-head">' +
          '<div class="ch-row">' +
            '<button class="icon-btn" id="ch-back" type="button" aria-label="返回首页">' + ICONS.back + '</button>' +
            '<div class="ava" id="ch-ava"><span class="speak-halo"></span><span class="ring"></span><img id="ch-ava-img" alt=""></div>' +
            '<div class="ch-id">' +
              '<div class="ch-name"><span id="ch-name">—</span><i id="ch-no">N°—</i></div>' +
              '<div class="ch-sub" id="ch-sub">The Conversation</div>' +
              '<div class="ch-status" id="ch-status-row"><span class="live" id="ch-live"></span><span id="ch-status">正在陪你练英语</span></div>' +
            '</div>' +
            '<button class="icon-btn" id="ch-voice" type="button" aria-label="自动朗读开关" aria-pressed="true">' + ICONS.voice + '</button>' +
          '</div>' +
          '<div class="ch-rule"></div>' +
        '</header>' +
        '<div class="chat-body scroll mask-fade" id="ch-body"><div class="chat-flow" id="ch-flow"></div></div>' +
        '<div class="inputbar">' +
          '<div class="sugg" id="ch-sugg"></div>' +
          '<div class="in-row">' +
            '<button class="icon-btn" id="ch-hint" type="button" aria-label="给一句提示">' + ICONS.hint + '</button>' +
            '<div class="in-pill"><input id="ch-input" type="text" placeholder="说点什么…（中文也可以）" aria-label="输入消息" autocomplete="off"></div>' +
            '<button class="send" id="ch-send" type="button" aria-label="发送">' + ICONS.send + '</button>' +
          '</div>' +
          '<div class="in-bar"><div class="home-bar"></div></div>' +
        '</div>';

      headEl = root.querySelector('.chat-head');
      avaEl = root.querySelector('#ch-ava');
      avaImg = root.querySelector('#ch-ava-img');
      nameEl = root.querySelector('#ch-name');
      noEl = root.querySelector('#ch-no');
      subEl = root.querySelector('#ch-sub');
      statusEl = root.querySelector('#ch-status');
      statusLive = root.querySelector('#ch-live');
      voiceBtn = root.querySelector('#ch-voice');
      body = root.querySelector('#ch-body');
      flow = root.querySelector('#ch-flow');
      input = root.querySelector('#ch-input');
      suggBox = root.querySelector('#ch-sugg');

      /* —— 头部交互 —— */
      root.querySelector('#ch-back').addEventListener('click', function () {
        /* 从首页点卡片进来的 → 回首页；从会话列表进来的 → 回列表 */
        App.nav.go(App.nav.prev === 'home' ? 'home' : 'convos');
      });
      voiceBtn.addEventListener('click', function () {
        var on = !autoSpeakOn();
        if (App.state) {
          App.state.prefs = App.state.prefs || {};
          App.state.prefs.autoSpeak = on;
          if (App.saveState) App.saveState();
        }
        refreshVoiceBtn();
        App.emit('toast', on ? '已开启自动朗读' : '已关闭自动朗读');
        if (!on) stopPlayback();
      });

      /* —— 消息流交互（事件委托：「听 Ta 说」播放/暂停） —— */
      flow.addEventListener('click', function (e) {
        var btn = e.target.closest('.play');
        if (!btn) return;
        var msgEl = btn.closest('.msg');
        if (!msgEl || !msgEl.dataset.speak) return;
        var vb = msgEl.querySelector('.bubble.voice');
        if (vb && vb.classList.contains('playing')) stopPlayback();
        else speakMsg(msgEl);
      });

      /* —— 输入坞 —— */
      root.querySelector('#ch-send').addEventListener('click', handleSend);
      input.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        /* 中文输入法组词中：Enter 用于确认候选词，不触发发送（keyCode 229 为旧浏览器兜底） */
        if (e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        handleSend();
      });
      root.querySelector('#ch-hint').addEventListener('click', function () {
        var id = App.chat && App.chat.activeId;
        var rec = id ? App.chat.history[id] : null;
        var intent = (rec && rec.meta && rec.meta.lastIntent) || 'opening';
        var pool = poolFor(intent);
        var text = pool[(hintOffset++) % pool.length];
        input.value = text;
        input.focus();
      });

      /* —— 事件总线：说话时刻驱动头像光环（全息偶像精华）——
         voice.stop() / 外部模块停止也会发 speaking:end：同步复位气泡播放态 */
      App.on('speaking:start', function () { setHeadSpeaking(true); });
      App.on('speaking:end', function () {
        setHeadSpeaking(false);
        if (playingMsg) { setBubblePlaying(playingMsg, false); playingMsg = null; }
      });
      App.on('chat:open', function (info) {
        openInfo = info || null;
        if (App.nav && App.nav.current === 'chat') renderHistory();
      });
      App.on('chat:cleared', function () {
        if (App.nav && App.nav.current === 'chat') renderHistory();
      });

      /* 中央麦克风 FAB：聊天屏内 = 重听上一句 / 播放中 = 停止 */
      App.on('mic:tap', function () {
        if (!App.nav || App.nav.current !== 'chat') return;
        if (playingMsg) { stopPlayback(); return; }
        var last = null, kids = flow.children;
        for (var i = kids.length - 1; i >= 0; i--) {
          if (kids[i].classList && kids[i].classList.contains('them') && kids[i].dataset && kids[i].dataset.speak) {
            last = kids[i];
            break;
          }
        }
        if (last) speakMsg(last);
      });

      renderHistory();
    },

    /** 每次切入：自动恢复上次对话 → 刷新头部与消息流，滚到底部 */
    show: function () {
      if (!App.chat.activeId && App.state && App.state.prefs && App.state.prefs.lastChatId) {
        try { App.chat.open(App.state.prefs.lastChatId); }
        catch (e) { console.warn('[chat] 恢复上次对话失败', e); }
      }
      renderHistory();
    },

    /** 切出：停止朗读并复位光环（静默） */
    hide: function () {
      stopPlayback();
    }
  };
})();
