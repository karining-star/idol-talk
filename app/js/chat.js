/* ============================================================
   chat.js — 聊天状态与对外 API（冻结接口实现）
   ------------------------------------------------------------
   冻结接口：App.chat = { activeId, open(id), send(text), history }
   · history 结构：{ [爱豆id]: { msgs:[{role,en,zh,t,g?,open?}], meta:{…} } }
     role = 'them'（爱豆）| 'me'（用户）；g = 纠音批注 {orig, fix}
   · open(id)：首次打开写入「每爱豆定制开场白」；已有历史则恢复，
     隔天回来补一句欢迎语（参考桌面版 startConversation 逻辑）
   · send(text)：用户消息入列 → App.reply.generate(text, ctx) 取回复 →
     回复入列（朗读由 screens/chat.js 经 App.voice.speak 驱动）；
     会话记忆（姓名/默契/话题等）归属 reply.js（idoltalk-app-memory），
     send 后读回同步到本文件 meta（引擎无记忆 API 时本地兜底推进）
   · 持久化：聊天历史挂在 App.state.chats，经 App.saveState() 落盘
     （键 idoltalk-app-state，符合 idoltalk-app- 前缀约定）
   · 事件：'chat:open' {id,isNew,count,gap} / 'chat:send' {id,user,reply}
   注意：本文件在 core.js 之前加载（index.html 冻结顺序），
   因此 App.state 只能在运行期（open/send 被调用时）懒绑定。
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});

  /* ============ 小工具（与桌面版行为对齐） ============ */

  /** FNV-1a 32 位哈希：为每位爱豆稳定挑选开场白 */
  function hash32(str) {
    var h = 2166136261, s = String(str || ''), i;
    for (i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  /** 距上次对话过了几天（0 = 今天） */
  function gapDays(ts) {
    var n = Number(ts);
    if (!n || n <= 0) return 0;
    return Math.max(0, Math.round((Date.now() - n) / 864e5));
  }

  /** 从用户输入里记住 TA 的名字（桌面版 extractName 逻辑） */
  function extractName(text) {
    var raw = String(text || '');
    var en = raw.match(/\b(?:my name is|call me|you can call me)\s+([a-z][a-z'-]{0,18})/i);
    if (en) return en[1][0].toUpperCase() + en[1].slice(1);
    var zh = raw.match(/(?:我叫|叫我|可以称呼我)\s*([一-龥]{1,4})/);
    return zh ? zh[1] : '';
  }

  /** 用户提到的食物（供回复引擎上下文） */
  var FOOD_RE = /tteokbokki|ramen|noodles|pizza|sushi|chicken|pasta|coffee|tea|chocolate|炒年糕|泡面|拉面|面条|披萨|寿司|炸鸡|意面|咖啡|奶茶|巧克力/i;

  /** 语法速查（桌面版 GRAMMAR_RULES）：reply.generate 未给出细目时的兜底 */
  var GRAMMAR_RULES = [
    [/\b(i)\s+(is|are)\b/i, function (m) { return m[1] + ' am'; }],
    [/\b(he|she|it)\s+are\b/i, function (m) { return m[1] + ' is'; }],
    [/\b(he|she|it)\s+don't\b/i, function (m) { return m[1] + ' doesn’t'; }],
    [/\b(we|you|they)\s+is\b/i, function (m) { return m[1] + ' are'; }],
    [/\b(he|she|it)\s+have\b/i, function (m) { return m[1] + ' has'; }]
  ];
  function grammarMatch(s) {
    for (var i = 0; i < GRAMMAR_RULES.length; i++) {
      var m = String(s).match(GRAMMAR_RULES[i][0]);
      if (m) return { orig: m[0], fix: GRAMMAR_RULES[i][1](m) };
    }
    return null;
  }

  function idolById(id) {
    var list = App.idols || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  /* ============ 开场白（概念稿 PARTNERS 文案） ============
     11 位主打爱豆与 concepts/editorial.html 的 PARTNERS 一一对应；
     其余爱豆按 id 哈希从同一池中稳定挑选（同一位爱豆每次都是同一句） */
  var OPENERS = [
    /* 0 · Karina */ { en: "Hey — you're back. How was your day? I've been waiting to hear about it.",
      zh: "嘿，你回来啦。我一直在等你说说今天。" },
    /* 1 · Winter */ { en: "It's cold tonight — perfect weather for talking. What are you up to?",
      zh: "今晚有点凉，正适合聊聊天。你在忙什么？" },
    /* 2 · Jisoo */ { en: "Hi — I saved you a seat. Shall we start with something easy?",
      zh: "嗨，我给你留了位置。先从简单的聊起？" },
    /* 3 · Lisa */ { en: "You're here! Quick — teach me one new word today.",
      zh: "你来啦！快，今天教我一个新单词。" },
    /* 4 · Jung Kook */ { en: "Let's keep it simple tonight: one song, one sentence.",
      zh: "今晚简单点：一首歌，一句话。" },
    /* 5 · V */ { en: "I wrote something today. Want to hear it in English?",
      zh: "我今天写了点东西，想听我用英语念吗？" },
    /* 6 · Wonyoung */ { en: "Compliment battle? You go first — in English.",
      zh: "互夸大赛？你先来——用英语。" },
    /* 7 · Sakura */ { en: "I made a tiny study plan for us. Want to see it?",
      zh: "我给咱们做了个小学习计划，要看吗？" },
    /* 8 · Tzuyu */ { en: "Today's word is 'cozy'. Use it in a sentence for me?",
      zh: "今天的词是 cozy。用它造个句给我？" },
    /* 9 · Felix */ { en: "Deep breath — English sounds better when you're relaxed.",
      zh: "深呼吸——放松的时候，英语会更好听。" },
    /* 10 · Soobin */ { en: "Tell me one small thing about your day. Anything.",
      zh: "跟我说一件今天的小事，什么都行。" }
  ];

  /* 主打爱豆 → 概念稿指定的开场白索引；其余爱豆走 hash 稳定挑选 */
  var FEATURED_OPENERS = {
    'aespa-karina': 0,
    'aespa-winter': 1,
    'blackpink-jisoo': 2,
    'blackpink-lisa': 3,
    'bts-jung-kook': 4,
    'bts-v': 5,
    'ive-wonyoung': 6,
    'le-sserafim-sakura': 7,
    'twice-tzuyu': 8,
    'stray-kids-felix': 9,
    'txt-soobin': 10
  };

  /** 取某位爱豆的开场白：主打爱豆一一对应，其余按 id 哈希稳定挑选 */
  function openerFor(id) {
    var fi = FEATURED_OPENERS[id];
    if (typeof fi === 'number' && OPENERS[fi]) return OPENERS[fi];
    return OPENERS[hash32(id) % OPENERS.length];
  }

  /* ============ 持久化（挂 App.state.chats，懒绑定） ============ */

  var historyStore = null;

  function defaultMeta() {
    return {
      name: '', favoriteFood: '', lastIntent: '', turn: 0,
      closeness: 0, mood: '', topics: [], awaitingAnswer: false, lastSeen: 0
    };
  }

  function sanitizeRec(rec) {
    var r = (rec && typeof rec === 'object') ? rec : {};
    if (!Array.isArray(r.msgs)) r.msgs = [];
    var m = (r.meta && typeof r.meta === 'object') ? r.meta : {};
    var meta = defaultMeta();
    Object.keys(meta).forEach(function (k) {
      if (m[k] !== undefined && m[k] !== null) meta[k] = m[k];
    });
    if (!Array.isArray(meta.topics)) meta.topics = [];
    meta.turn = Math.max(0, Number(meta.turn) || 0);
    meta.closeness = Math.max(0, Math.min(12, Number(meta.closeness) || 0));
    r.meta = meta;
    return r;
  }

  /** 取（并按需水合）历史仓库；绑定到 App.state.chats 以持久化 */
  function store() {
    if (historyStore) return historyStore;
    var saved = (App.state && App.state.chats);
    var st = {};
    if (saved && typeof saved === 'object') {
      Object.keys(saved).forEach(function (id) { st[id] = sanitizeRec(saved[id]); });
    }
    historyStore = st;
    if (App.state) App.state.chats = st;
    return st;
  }

  function persist() {
    try { if (App.saveState) App.saveState(); } catch (e) { console.warn('[chat] 保存历史失败', e); }
  }

  /* ============ 对外 API ============ */

  var chat = {

    /** 当前聊天的爱豆 id（null = 未开始对话） */
    activeId: null,

    /** 打开与某位爱豆的对话：首次写入开场白，已有历史则恢复 */
    open: function (id) {
      var idol = idolById(id);
      if (!idol) {
        console.warn('[chat] 未知爱豆 id：', id);
        return { ok: false };
      }
      chat.activeId = id;
      /* 记住最近对话的爱豆：聊天屏 show() 时自动恢复（跨刷新/重开） */
      try {
        if (App.state) {
          App.state.prefs = App.state.prefs || {};
          App.state.prefs.lastChatId = id;
        }
      } catch (e) { /* 忽略：不影响对话本身 */ }

      var st = store();
      var rec = st[id];
      var isNew = !rec || !rec.msgs.length;
      var gap = 0;

      if (isNew) {
        rec = st[id] = { msgs: [], meta: defaultMeta() };
        var opener = openerFor(id);
        rec.msgs.push({
          role: 'them',
          en: opener.en,
          zh: opener.zh,
          t: Date.now(),
          open: true
        });
        rec.meta.lastIntent = 'opening';
      } else {
        /* 回来续聊：隔天补一句欢迎语（桌面版 gap 逻辑） */
        gap = gapDays(rec.meta.lastSeen);
        if (gap >= 1) {
          var at = rec.meta.name ? ', ' + rec.meta.name : '';
          var zhAt = rec.meta.name ? '，' + rec.meta.name : '';
          var back = gap >= 3
            ? { en: 'It’s really been a while' + at + '. I’m so glad you found your way back. How is your heart doing lately?',
                zh: '好久不见' + zhAt + '。你回来了，我真的很开心。最近心里感觉怎么样？' }
            : { en: 'It’s been a couple of days' + at + '. I kept our seat by the window warm. How have you been?',
                zh: '有几天没见了' + zhAt + '。靠窗的位置我一直给你留着。这几天过得怎么样？' };
          rec.msgs.push({ role: 'them', en: back.en, zh: back.zh, t: Date.now() });
          /* 控制体量：每爱豆最多保留 60 条 */
          if (rec.msgs.length > 60) rec.msgs = rec.msgs.slice(-60);
        }
      }
      rec.meta.lastSeen = Date.now();
      persist();

      var info = { ok: true, id: id, isNew: isNew, count: rec.msgs.length, gap: gap };
      App.emit('chat:open', info);
      return info;
    },

    /** 发送一条用户消息并生成爱豆回复（本地引擎，行为与桌面版一致） */
    send: function (text) {
      var raw = String(text == null ? '' : text).trim();
      if (!raw) return { ok: false };
      var id = chat.activeId;
      if (!id || !idolById(id)) return { ok: false };

      var st = store();
      var rec = st[id];
      if (!rec || !rec.msgs.length) { chat.open(id); rec = st[id]; }
      var meta = rec.meta;

      /* —— 上下文：记忆归属 reply.js（自持 idoltalk-app-memory 存档），
         此处只传身份与近期消息，避免双份记忆漂移 —— */
      var ctx = {
        idolId: id,
        idol: idolById(id),
        raw: raw,
        recent: rec.msgs.slice(-10)
      };

      var reply;
      try {
        reply = App.reply.generate(raw, ctx) || {};
      } catch (e) {
        console.warn('[chat] 回复引擎异常，使用兜底回复', e);
        reply = {};
      }
      reply.en = String(reply.en || "I'm listening — tell me more about your day.");
      reply.zh = String(reply.zh || '我在听——再和我多讲讲你今天的事。');
      reply.intent = String(reply.intent || 'open');

      /* —— 用户消息 + 纠音批注（reply.needsCorrection 优先，本地兜底） —— */
      var userMsg = { role: 'me', en: raw, t: Date.now() };
      if (reply.intent === 'grammar') {
        var g = null;
        if (reply.needsCorrection && reply.needsCorrection.orig && reply.needsCorrection.fix) {
          g = reply.needsCorrection;
        } else if (reply.grammar && reply.grammar.orig && reply.grammar.fix) {
          g = reply.grammar;
        } else {
          g = grammarMatch(raw);
        }
        if (g) userMsg.g = { orig: g.orig, fix: g.fix };
      }

      var replyMsg = { role: 'them', en: reply.en, zh: reply.zh, t: Date.now() };

      /* —— 记忆同步：以回复引擎存档为准（读回）；引擎无记忆 API 时本地推进 —— */
      var m = null;
      try {
        if (App.reply && typeof App.reply.memory === 'function') m = App.reply.memory(id);
      } catch (e) { m = null; }

      if (m && typeof m === 'object') {
        if (m.name) meta.name = m.name;
        if (m.favoriteFood) meta.favoriteFood = m.favoriteFood;
        meta.closeness = Math.max(0, Math.min(12, Number(m.closeness) || 0));
        if (m.mood) meta.mood = m.mood;
        if (Array.isArray(m.topics)) meta.topics = m.topics.slice(-8);
        meta.turn = Math.max(0, Number(m.turn) || 0);
        meta.lastIntent = m.lastIntent || reply.intent;
        meta.awaitingAnswer = !!m.awaitingAnswer;
      } else {
        meta.turn += 1;
        meta.closeness = Math.min(12, meta.closeness + 1);
        var name = extractName(raw);
        if (name) meta.name = name;
        var foodMatch = raw.match(FOOD_RE);
        if (foodMatch) meta.favoriteFood = foodMatch[0];
        meta.lastIntent = reply.intent;
        meta.awaitingAnswer = /\?\s*$/.test(reply.en);
        if (['open', 'grammar', 'followup-answer', 'return-topic'].indexOf(reply.intent) < 0) {
          if (meta.topics[meta.topics.length - 1] !== reply.intent) meta.topics.push(reply.intent);
          meta.topics = meta.topics.slice(-8);
        }
      }

      rec.msgs.push(userMsg, replyMsg);
      if (rec.msgs.length > 60) rec.msgs = rec.msgs.slice(-60);
      rec.meta.lastSeen = Date.now();
      persist();

      App.emit('chat:send', { id: id, user: userMsg, reply: replyMsg });
      return { ok: true, user: userMsg, reply: replyMsg, intent: reply.intent };
    },

    /** 异步发送：走大模型（App.reply.generateAsync），失败自动回退规则引擎。
     *  返回 Promise<{ok,user,reply,intent,source}>；用户消息在回复就绪后一并入列。 */
    sendAsync: function (text) {
      var raw = String(text == null ? '' : text).trim();
      if (!raw) return Promise.resolve({ ok: false });
      var id = chat.activeId;
      if (!id || !idolById(id)) return Promise.resolve({ ok: false });

      var st = store();
      var rec = st[id];
      if (!rec || !rec.msgs.length) { chat.open(id); rec = st[id]; }
      var meta = rec.meta;

      var ctx = {
        idolId: id,
        idol: idolById(id),
        raw: raw,
        recent: rec.msgs.slice(-10)
      };

      var p;
      try {
        p = (App.reply && typeof App.reply.generateAsync === 'function')
          ? App.reply.generateAsync(raw, ctx)
          : Promise.resolve(App.reply.generate(raw, ctx));
      } catch (e) {
        console.warn('[chat] 大模型调用异常，回退', e);
        p = Promise.resolve(App.reply.generate(raw, ctx));
      }

      return Promise.resolve(p).catch(function (e) {
        console.warn('[chat] 回复异常，使用兜底', e);
        return {};
      }).then(function (reply) {
        reply = reply || {};
        reply.en = String(reply.en || "I'm listening — tell me more about your day.");
        reply.zh = String(reply.zh || '我在听——再和我多讲讲你今天的事。');
        reply.intent = String(reply.intent || 'open');

        var userMsg = { role: 'me', en: raw, t: Date.now() };
        if (reply.needsCorrection && reply.needsCorrection.orig && reply.needsCorrection.fix) {
          userMsg.g = { orig: reply.needsCorrection.orig, fix: reply.needsCorrection.fix };
        } else if (reply.intent === 'grammar') {
          var g = reply.needsCorrection || grammarMatch(raw);
          if (g && g.orig && g.fix) userMsg.g = { orig: g.orig, fix: g.fix };
        }
        var replyMsg = { role: 'them', en: reply.en, zh: reply.zh, t: Date.now() };

        /* 记忆同步（与 send 同逻辑，以回复引擎存档为准） */
        var m = null;
        try { if (App.reply && typeof App.reply.memory === 'function') m = App.reply.memory(id); } catch (e) { m = null; }
        if (m && typeof m === 'object') {
          if (m.name) meta.name = m.name;
          if (m.favoriteFood) meta.favoriteFood = m.favoriteFood;
          meta.closeness = Math.max(0, Math.min(12, Number(m.closeness) || 0));
          if (m.mood) meta.mood = m.mood;
          if (Array.isArray(m.topics)) meta.topics = m.topics.slice(-8);
          meta.turn = Math.max(0, Number(m.turn) || 0);
          meta.lastIntent = m.lastIntent || reply.intent;
          meta.awaitingAnswer = !!m.awaitingAnswer;
        } else {
          meta.turn += 1;
          meta.closeness = Math.min(12, meta.closeness + 1);
          var nm = extractName(raw);
          if (nm) meta.name = nm;
          var foodMatch = raw.match(FOOD_RE);
          if (foodMatch) meta.favoriteFood = foodMatch[0];
          meta.lastIntent = reply.intent;
          meta.awaitingAnswer = /\?\s*$/.test(reply.en);
          if (['open', 'grammar', 'followup-answer', 'return-topic'].indexOf(reply.intent) < 0) {
            if (meta.topics[meta.topics.length - 1] !== reply.intent) meta.topics.push(reply.intent);
            meta.topics = meta.topics.slice(-8);
          }
        }

        rec.msgs.push(userMsg, replyMsg);
        if (rec.msgs.length > 60) rec.msgs = rec.msgs.slice(-60);
        rec.meta.lastSeen = Date.now();
        persist();

        App.emit('chat:send', { id: id, user: userMsg, reply: replyMsg, source: reply.source });
        return { ok: true, user: userMsg, reply: replyMsg, intent: reply.intent, source: reply.source };
      });
    },

    /** 清空某位爱豆的对话记忆（连同回复引擎存档，重新生成开场白） */
    clear: function (id) {
      var target = id || chat.activeId;
      if (!target) return;
      var st = store();
      delete st[target];
      persist();
      try {
        if (App.reply && typeof App.reply.clearMemory === 'function') App.reply.clearMemory(target);
      } catch (e) { console.warn('[chat] 清除回复引擎记忆失败', e); }
      if (target === chat.activeId) chat.open(target);
      App.emit('chat:cleared', { id: target });
    }
  };

  /* history 以 getter 暴露：保证读取时才水合（core.js 在本文件之后加载） */
  Object.defineProperty(chat, 'history', {
    get: function () { return store(); },
    enumerable: true,
    configurable: true
  });

  App.chat = chat;
})();
