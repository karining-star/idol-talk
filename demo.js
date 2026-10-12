/* ============================================================
   demo.js — 在线试听 Demo 覆盖层

   作用：把「预生成的真人音色音频」接到应用的朗读流程上，让评委
   在纯静态页面上也能听到 5 位爱豆各自的音色。
   不改动应用任何原文件 —— 只覆盖两个冻结接口：
     · App.voice.speak(text, cbs) → 播放内置 mp3（不再请求 TTS 服务）
     · App.reply.generate(text, ctx) → 命中示例问句时返回脚本化回复

   为什么是脚本化的：自由对话要调大模型，就得把 API Key 放进公开
   页面；预生成音频则完全不需要后端与密钥。
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 演示脚本（与 clips/ 下的音频一一对应） ---------- */
  var DEMO = {
    'aespa-giselle': {
      stage: 'GISELLE', group: 'aespa', zh: '吉赛尔',
      pairs: [
        { q: 'Hi Giselle! I missed you.',
          a: 'I missed you too, more than you know. How was your day, really?',
          zh: '我也想你，比你知道的还要想。今天过得怎么样，说实话？',
          clip: 'aespa-giselle-1.mp3' },
        { q: 'Can you teach me one English sentence?',
          a: 'Of course. Repeat after me: I am proud of how far I have come.',
          zh: '当然。跟我念：我为走到今天的自己感到骄傲。',
          clip: 'aespa-giselle-2.mp3' },
        { q: "I'm shy about speaking English.",
          a: "Don't be. Nobody is judging you here. Just talk to me, slowly.",
          zh: '别怕。这里没人评判你，慢慢和我说就好。',
          clip: 'aespa-giselle-3.mp3' }
      ]
    },
    'bts-jung-kook': {
      stage: 'JUNG KOOK', group: 'BTS', zh: '田柾国',
      pairs: [
        { q: 'Jungkook, say something in English for me?',
          a: 'Alright. Here it is: you are doing better than you think.',
          zh: '好，就这句：你比自己以为的做得好。',
          clip: 'bts-jung-kook-1.mp3' },
        { q: 'How are you feeling today?',
          a: "I'm good, now that you're here. How about you?",
          zh: '你来了我就好。你呢？',
          clip: 'bts-jung-kook-2.mp3' },
        { q: "I'm nervous about my English test.",
          a: 'Deep breath. You studied hard, and I believe in you.',
          zh: '深呼吸。你很努力，我相信你。',
          clip: 'bts-jung-kook-3.mp3' }
      ]
    },
    'cortis-juhoon': {
      stage: 'JUHOON', group: 'CORTIS', zh: '金主训',
      pairs: [
        { q: "Juhoon, I'm nervous about speaking English.",
          a: "It's okay to feel that way. Just take a deep breath and start with one word.",
          zh: '紧张很正常。深吸一口气，从一个词开始。',
          clip: 'cortis-juhoon-1.mp3' },
        { q: 'What should I say when I meet someone new?',
          a: "Try this: Hi, it's really nice to meet you. I've heard a lot about you.",
          zh: '试试这句：嗨，很高兴认识你，久仰了。',
          clip: 'cortis-juhoon-2.mp3' },
        { q: 'I made a mistake in English today.',
          a: 'Mistakes mean you are trying. That is the bravest thing you can do.',
          zh: '犯错说明你在尝试，那是最勇敢的事。',
          clip: 'cortis-juhoon-3.mp3' }
      ]
    },
    'txt-yeonjun': {
      stage: 'YEONJUN', group: 'TXT', zh: '崔然竣',
      pairs: [
        { q: 'Give me some courage to speak English?',
          a: 'Yes, I can help with that. Remember: practice out loud, even when it feels strange.',
          zh: '当然可以。记住：就算别扭，也要大声练。',
          clip: 'txt-yeonjun-1.mp3' },
        { q: "I don't know how to start a conversation.",
          a: 'Start simple. Ask me what I did today, and I will answer you in English.',
          zh: '从简单的开始。问我今天做了什么，我用英语回答你。',
          clip: 'txt-yeonjun-2.mp3' },
        { q: 'Do you think I can improve?',
          a: 'I know you can. Come back tomorrow and show me what you learned.',
          zh: '我知道你可以。明天回来，告诉我你学会了什么。',
          clip: 'txt-yeonjun-3.mp3' }
      ]
    },
    'cortis-keonho': {
      stage: 'KEONHO', group: 'CORTIS', zh: '安乾镐',
      pairs: [
        { q: 'Keonho, say something in English for me?',
          a: "I can't wait to see you. There, that is your sentence for today.",
          zh: '我迫不及待想见你。好了，这就是你今天的句子。',
          clip: 'cortis-keonho-1.mp3' },
        { q: "I'm tired today.",
          a: "Then rest. You don't have to be perfect to deserve kindness.",
          zh: '那就休息。你不需要完美，也值得被温柔对待。',
          clip: 'cortis-keonho-2.mp3' },
        { q: 'Good night!',
          a: 'Good night. Sleep well, and dream in English.',
          zh: '晚安。好好睡，梦里也说英语。',
          clip: 'cortis-keonho-3.mp3' }
      ]
    }
  };

  var ORDER = ['aespa-giselle', 'bts-jung-kook', 'cortis-juhoon', 'txt-yeonjun', 'cortis-keonho'];

  /* 每段音频的真实时长（秒）—— 应用气泡上显示的是按文本估算的，会差挺多，
     这里用实测值覆盖，免得评委看到「0:07」却只响 3 秒。 */
  var DUR = {
    'aespa-giselle-1.mp3': 3.2, 'aespa-giselle-2.mp3': 3.6, 'aespa-giselle-3.mp3': 4.82,
    'bts-jung-kook-1.mp3': 3.06, 'bts-jung-kook-2.mp3': 3.24, 'bts-jung-kook-3.mp3': 3.46,
    'cortis-juhoon-1.mp3': 4.79, 'cortis-juhoon-2.mp3': 4.93, 'cortis-juhoon-3.mp3': 2.12,
    'txt-yeonjun-1.mp3': 6.84, 'txt-yeonjun-2.mp3': 4.28, 'txt-yeonjun-3.mp3': 4.72,
    'cortis-keonho-1.mp3': 3.78, 'cortis-keonho-2.mp3': 3.53, 'cortis-keonho-3.mp3': 2.99,
    'aespa-giselle-open.mp3': 3.35, 'bts-jung-kook-open.mp3': 4.61, 'cortis-juhoon-open.mp3': 3.2,
    'txt-yeonjun-open.mp3': 5.65, 'cortis-keonho-open.mp3': 2.84
  };

  /* 应用会自动插入的开场白 —— 也预生成了音频，否则评委点它的播放键没反应 */
  var OPENER = {
    'aespa-giselle': { text: 'Tell me one small thing about your day. Anything.',
                       clip: 'aespa-giselle-open.mp3' },
    'bts-jung-kook': { text: "Let's keep it simple tonight: one song, one sentence.",
                       clip: 'bts-jung-kook-open.mp3' },
    'cortis-juhoon': { text: 'Compliment battle? You go first — in English.',
                       clip: 'cortis-juhoon-open.mp3' },
    'txt-yeonjun': { text: 'Hi — I saved you a seat. Shall we start with something easy?',
                     clip: 'txt-yeonjun-open.mp3' },
    'cortis-keonho': { text: 'I made a tiny study plan for us. Want to see it?',
                       clip: 'cortis-keonho-open.mp3' }
  };

  function durStr(sec) {
    var m = Math.floor(sec / 60), s = Math.round(sec % 60);
    if (s === 60) { m++; s = 0; }
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function norm(s) {
    return String(s || '').trim().toLowerCase().replace(/[.!?。！？\s]+/g, ' ').trim();
  }

  /* 回复文本 → clip 文件（按爱豆分组，避免不同爱豆撞同一句） */
  function clipFor(idolId, text) {
    var t = norm(text);
    var op = OPENER[idolId];
    if (op && norm(op.text) === t) return op.clip;
    var d = DEMO[idolId];
    if (!d) return null;
    for (var i = 0; i < d.pairs.length; i++) {
      if (norm(d.pairs[i].a) === t) return d.pairs[i].clip;
    }
    return null;
  }

  /* 用户问句 → 脚本化回复 */
  function replyFor(idolId, text) {
    var d = DEMO[idolId];
    if (!d) return null;
    var norm = function (s) { return String(s || '').trim().toLowerCase().replace(/[.!?。！？\s]+/g, ' ').trim(); };
    var t = norm(text);
    for (var i = 0; i < d.pairs.length; i++) {
      if (norm(d.pairs[i].q) === t) return d.pairs[i];
    }
    return null;
  }

  /* ---------- 覆盖 1：朗读改为播放内置音频 ---------- */
  var originalSpeak = null;
  function installSpeakHook() {
    if (!window.App || !App.voice || originalSpeak) return !!originalSpeak;
    originalSpeak = App.voice.speak;
    App.voice.speak = function (text, cbs, opts) {
      cbs = cbs || {};
      var idolId = (App.chat && App.chat.activeId) || '';
      var file = clipFor(idolId, text);

      if (!file) {
        /* 不是演示台词 → 不请求 TTS（静态站点没有后端），静默降级 */
        try { if (App.emit) App.emit('speaking:end'); } catch (e) {}
        if (cbs.onEnd) { try { cbs.onEnd(); } catch (e) {} }
        return;
      }
      try {
        if (App.voice.stop) App.voice.stop();
        var a = new Audio('clips/' + file);
        a.__demo = true;
        if (App.emit) App.emit('speaking:start');
        if (cbs.onStart) cbs.onStart();
        a.addEventListener('timeupdate', function () {
          if (cbs.onSentence) cbs.onSentence(0, 1);
          if (App.emit) App.emit('speaking:sentence', 0, 1);
        });
        a.addEventListener('ended', function () {
          if (cbs.onEnd) cbs.onEnd();
          if (App.emit) App.emit('speaking:end');
        });
        a.addEventListener('error', function () {
          console.warn('[demo] 音频加载失败', file);
          if (cbs.onError) cbs.onError();
          if (App.emit) App.emit('speaking:end');
        });
        DEMO_AUDIO = a;
        a.play().catch(function (e) { console.warn('[demo] 播放被拦', e); });
      } catch (e) {
        console.warn('[demo] speak hook 出错', e);
      }
    };
    /* stop() 也要停掉内置音频 */
    if (App.voice.stop) {
      var originalStop = App.voice.stop;
      App.voice.stop = function () {
        try { if (DEMO_AUDIO) { DEMO_AUDIO.pause(); DEMO_AUDIO.currentTime = 0; DEMO_AUDIO = null; } } catch (e) {}
        return originalStop.apply(this, arguments);
      };
    }
    return true;
  }
  var DEMO_AUDIO = null;

  /* ---------- 覆盖 2：示例问句走脚本化回复 ---------- */
  function installReplyHook() {
    if (!window.App || !App.reply) return false;
    if (App.reply.__demoHooked) return true;
    var origGen = App.reply.generate;
    var origAsync = App.reply.generateAsync;

    App.reply.generate = function (text, ctx) {
      var hit = replyFor((App.chat && App.chat.activeId) || '', text);
      if (hit) return { en: hit.a, zh: hit.zh || '', intent: 'demo' };
      return origGen.apply(this, arguments);
    };
    if (typeof origAsync === 'function') {
      App.reply.generateAsync = function (text, ctx) {
        var hit = replyFor((App.chat && App.chat.activeId) || '', text);
        if (hit) return Promise.resolve({ en: hit.a, zh: hit.zh || '', intent: 'demo' });
        /* 演示版不走大模型（没有密钥），直接落到规则引擎 */
        return Promise.resolve(App.reply.generate(text, ctx));
      };
    }
    App.reply.__demoHooked = true;
    return true;
  }

  /* ---------- 覆盖 3：把示例问句放进建议区，点了直接发 ---------- */
  function decorateChat() {
    var sugg = document.querySelector('#ch-sugg');
    if (!sugg) return;
    var idolId = (App.chat && App.chat.activeId) || '';
    var d = DEMO[idolId];
    if (!d) return;
    if (sugg.getAttribute('data-demo') === idolId) { fixDurations(idolId); return; }
    sugg.setAttribute('data-demo', idolId);

    while (sugg.firstChild) sugg.removeChild(sugg.firstChild);
    d.pairs.forEach(function (p) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = p.q;
      b.addEventListener('click', function () {
        var input = document.querySelector('#ch-input');
        var send = document.querySelector('#ch-send');
        if (!input || !send) return;
        input.value = p.q;
        send.click();
      });
      sugg.appendChild(b);
    });
    fixDurations(idolId);
    fixAvatar(idolId);
    autoPlayOpener(idolId);
  }

  /* 聊天页头像缺图 → 首字母渐变占位（仓库不含照片） */
  function fixAvatar(idolId) {
    var wrap = document.querySelector('#ch-ava');
    var img = document.querySelector('#ch-ava-img');
    if (!wrap) return;
    var d = DEMO[idolId];
    if (d) wrap.setAttribute('data-initial', d.stage.charAt(0));
    if (!img) return;
    var show = function () {
      img.style.display = 'none';
      wrap.classList.add('no-photo');
    };
    if (!wrap.getAttribute('data-ava-hooked')) {
      wrap.setAttribute('data-ava-hooked', '1');
      img.addEventListener('error', show);
    }
    /* 脚本挂载时可能已经加载失败过了，补一次 */
    if (img.complete && img.naturalWidth === 0) show();
    else if (img.getAttribute('src')) { /* 还没加载完，error 监听会兜住 */ }
    else show();
  }

  /* 打开对话时自动播一次开场白 —— 评委点爱豆按钮本身就是用户手势，
     不会被浏览器的自动播放策略拦。每位爱豆每次会话只自动播一次。 */
  var PLAYED = {};
  function autoPlayOpener(idolId) {
    if (PLAYED[idolId]) return;
    var first = document.querySelector('#ch-flow .bubble.voice');
    if (!first) return;
    if (first.classList.contains('playing')) { PLAYED[idolId] = 1; return; }
    var btn = first.querySelector('.play');
    if (!btn) return;
    PLAYED[idolId] = 1;
    setTimeout(function () {
      try { btn.click(); } catch (e) { console.warn('[demo] 自动播放开场白失败', e); }
    }, 450);
  }

  /* 用实测时长覆盖应用估算的时长标签 */
  function fixDurations(idolId) {
    var d = DEMO[idolId];
    if (!d) return;
    var op = OPENER[idolId];
    var bubbles = document.querySelectorAll('#ch-flow .bubble.voice');
    for (var i = 0; i < bubbles.length; i++) {
      var vb = bubbles[i];
      if (vb.getAttribute('data-dur-fixed')) continue;
      var txtEl = vb.querySelector('.voice-text');
      if (!txtEl) continue;
      var t = norm(txtEl.innerText || txtEl.textContent);
      var file = null;
      if (op && norm(op.text) === t) file = op.clip;
      if (!file) {
        for (var j = 0; j < d.pairs.length; j++) {
          if (norm(d.pairs[j].a) === t) { file = d.pairs[j].clip; break; }
        }
      }
      if (file) {
        var span = vb.querySelector('.dur');
        var real = DUR[file];
        if (span && real) span.textContent = durStr(real);
        vb.setAttribute('data-dur-fixed', '1');
      }
    }
  }

  /* ---------- 顶部横幅：告诉评委哪 5 位有声音 ---------- */
  function installBanner() {
    if (document.getElementById('demo-bar')) return;
    var bar = document.createElement('div');
    bar.id = 'demo-bar';
    bar.innerHTML =
      '<div class="db-row">' +
        '<span class="db-tag">在线试听</span>' +
        '<span class="db-note">点下面的爱豆，听<b>本人的克隆音色</b></span>' +
      '</div>' +
      '<div class="db-list"></div>';
    var list = bar.querySelector('.db-list');
    ORDER.forEach(function (id) {
      var d = DEMO[id];
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = d.stage;
      b.title = d.group + ' · ' + d.zh;
      b.addEventListener('click', function () {
        try { App.chat.open(id); App.nav.go('chat'); } catch (e) { console.warn(e); }
      });
      list.appendChild(b);
    });
    document.body.appendChild(bar);
    document.body.classList.add('has-demo-bar');
    applyOffset();
  }

  /* 让应用内容正好避开横幅（高度不固定，实测后再设） */
  function applyOffset() {
    var bar = document.getElementById('demo-bar');
    if (!bar) return;
    var h = bar.offsetHeight;
    document.documentElement.style.setProperty('--demo-bar-h', h + 'px');
  }

  /* ---------- 样式 ---------- */
  function installStyle() {
    if (document.getElementById('demo-style')) return;
    var st = document.createElement('style');
    st.id = 'demo-style';
    st.textContent = [
      ':root{--demo-bar-h:74px;}',
      '#demo-bar{position:fixed;left:0;right:0;top:0;z-index:9999;',
      '  padding:6px 10px 7px;font-size:12px;line-height:1.35;',
      '  background:linear-gradient(90deg,rgba(124,58,237,.96),rgba(219,39,119,.93));',
      '  color:#fff;box-shadow:0 2px 14px rgba(0,0,0,.3);}',
      '#demo-bar .db-row{display:flex;align-items:center;gap:7px;margin-bottom:6px;}',
      '#demo-bar .db-tag{flex:none;font-weight:700;letter-spacing:.08em;font-size:11px;',
      '  background:rgba(255,255,255,.24);border-radius:999px;padding:2px 8px;}',
      '#demo-bar .db-note{opacity:.95;font-size:11.5px;overflow:hidden;',
      '  text-overflow:ellipsis;white-space:nowrap;}',
      '#demo-bar .db-list{display:flex;gap:6px;overflow-x:auto;padding-bottom:1px;',
      '  scrollbar-width:none;}',
      '#demo-bar .db-list::-webkit-scrollbar{display:none;}',
      '#demo-bar .db-list button{flex:none;cursor:pointer;font:inherit;font-size:11.5px;font-weight:600;',
      '  color:#fff;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.42);',
      '  border-radius:999px;padding:3px 12px;transition:background .15s;}',
      '#demo-bar .db-list button:hover{background:rgba(255,255,255,.34);}',
      'body.has-demo-bar .app,body.has-demo-bar .screen,body.has-demo-bar .wrap{',
      '  padding-top:var(--demo-bar-h) !important;}',
      'body.has-demo-bar .phone,body.has-demo-bar .device{top:var(--demo-bar-h) !important;}',
      /* 仓库不含照片（版权），聊天页头像会破图 —— 给个和首页一致的首字母占位 */
      '#ch-ava.no-photo{background:radial-gradient(120% 90% at 30% 20%, rgba(242,160,220,.26), transparent 60%),',
      '  linear-gradient(160deg, var(--ink-3, #241a2e), var(--ink-4, #17101e)) !important;}',
      '#ch-ava.no-photo::after{content:attr(data-initial); position:absolute; inset:0; z-index:2;',
      '  display:flex; align-items:center; justify-content:center; font-family:var(--serif, Georgia, serif);',
      '  font-style:italic; font-size:22px; color:#e9d7ff; text-shadow:0 1px 6px rgba(0,0,0,.4);}'
    ].join('');
    document.head.appendChild(st);
  }

  /* ---------- 启动：等应用就绪后挂钩 ---------- */
  var tries = 0;
  var timer = setInterval(function () {
    tries++;
    var ready = installSpeakHook() && installReplyHook();
    if (ready) {
      installStyle();
      installBanner();
      decorateChat();
    }
    if (tries > 100) clearInterval(timer);          /* 约 10 秒后放弃 */
  }, 100);

  /* 每次切到聊天页都重新装饰（应用会重建 DOM） */
  var mo = new MutationObserver(function () { decorateChat(); });
  document.addEventListener('DOMContentLoaded', function () {
    mo.observe(document.body, { childList: true, subtree: true });
  });
  window.addEventListener('load', function () {
    mo.observe(document.body, { childList: true, subtree: true });
    decorateChat();
  });
})();
