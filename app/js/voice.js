/* ============================================================
   voice.js — 语音朗读（App.voice，冻结接口实现）
   ------------------------------------------------------------
   流程（句级并发 + 顺序播放）：
   · speak(text, cbs)：正则分句 → 并发预取前 2 句（不等全部）→
     new Audio(blob URL) 逐句顺序播放 → 全部完成后 cbs.onEnd
   · 回调：onStart（第一句出声）/ onSentence(i,total) / onEnd / onError
     同时 App.emit('speaking:start'|'speaking:sentence'|'speaking:end')
   · 令牌机制：每次 speak 生成新令牌；stop() 或新调用都会使旧令牌失效，
     旧播放链在每个 await 后自检退出（防“双声”复活的桌面版既有模式）
   · 请求失败：console.warn + 静默跳过该句（不阻断后续，不弹错）
   · 端点从 App.state.prefs.ttsEndpoint 读（默认 127.0.0.1:8780），
     setEndpoint 持久化；偏好不含 UI 噪音
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});

  var DEFAULT_ENDPOINT = 'http://127.0.0.1:8780/v1/audio/speech';

  /* 端点：懒读取（voice.js 先于 core.js 加载，App.state 当时尚未就绪，
     故不能在模块初始化时读 prefs；首次实际使用时再读） */
  function readEndpoint() {
    var saved = null;
    try { saved = App.state && App.state.prefs && App.state.prefs.ttsEndpoint; }
    catch (e) { /* 忽略 */ }
    return saved || DEFAULT_ENDPOINT;
  }
  var endpoint = null; /* null = 未初始化，用 getEndpointInternal() 懒取 */

  function getEndpointInternal() {
    if (!endpoint) endpoint = readEndpoint();
    return endpoint;
  }

  /* ============ 播放会话状态（令牌制） ============ */
  var token = 0;          // 会话令牌：自增；旧会话发现自己被顶掉即静默退出
  var current = null;     // 当前会话句柄 { token, audios:[{audio,url}], abort, cbs }

  function fire(cb, arg1, arg2) {
    try { if (typeof cb === 'function') cb(arg1, arg2); }
    catch (e) { console.warn('[App.voice] 回调执行出错', e); }
  }

  /** 释放一段音频资源（pause + 撤销 blob URL） */
  function releaseAudio(rec) {
    if (!rec) return;
    try { rec.audio.pause(); } catch (e) { /* 已停 */ }
    try { if (rec.url) URL.revokeObjectURL(rec.url); } catch (e) { /* 忽略 */ }
  }

  /* ============ 对外接口 ============ */
  App.voice = {
    /** 当前 TTS 端点（OpenAI 兼容 /v1/audio/speech） */
    getEndpoint: function () { return getEndpointInternal(); },

    /** 更新端点并持久化到 App.state.prefs */
    setEndpoint: function (url) {
      endpoint = String(url || DEFAULT_ENDPOINT);
      if (App.state) {
        App.state.prefs = App.state.prefs || {};
        App.state.prefs.ttsEndpoint = endpoint;
        if (App.saveState) App.saveState();
      }
    },

    /**
     * 朗读文本
     * @param {string} text    英文正文（按句拆分朗读）
     * @param {object} [cbs]   { onStart, onSentence(i,total), onEnd, onError }
     * @param {object} [opts]  { voice, speed }（voice 默认当前爱豆 id）
     */
    speak: function (text, cbs, opts) {
      cbs = cbs || {};
      opts = opts || {};

      /* 新调用天然顶掉旧会话（双声保险） */
      stopInternal();

      var raw = String(text == null ? '' : text);
      var sentences = splitSentences(raw);
      if (!sentences.length) {
        /* 没有可读内容：直接走一次完整的空流程，避免调用方等待 */
        fire(cbs.onEnd);
        return;
      }

      var my = ++token;
      var voice = opts.voice || (App.chat && App.chat.activeId) || 'default';
      var speed = typeof opts.speed === 'number' ? opts.speed : 1;

      var session = {
        token: my,
        cbs: cbs,
        abort: new AbortController(),
        audios: [],        /* 每句 { audio, url }（播放后释放） */
        started: false,    /* onStart 只发一次 */
        finished: false,   /* onEnd 只发一次 */
        failed: 0          /* 合成失败句数（全失败时补发一次 onError） */
      };
      current = session;

      /* —— 句级流水线：预取窗口 2，边取边播 —— */
      prefetchAndPlay(sentences, 0, session, voice, speed);
    },

    /** 停止当前朗读：中止预取 + 停当前音频 + 清队列 */
    stop: function () {
      stopInternal(false);
    }
  };

  /* ============ 内部实现 ============ */

  /** 停止当前会话（silent=true 表示被新 speak 顶掉，不发 onEnd） */
  function stopInternal(silent) {
    if (silent === undefined) silent = true;
    var session = current;
    current = null;
    token++; /* 使一切在途会话失效（stop 或新调用） */
    if (!session) return;

    if (session.abort) {
      try { session.abort.abort(); } catch (e) { /* 忽略 */ }
    }
    session.audios.forEach(releaseAudio);
    session.audios = [];
    if (!session.finished) {
      session.finished = true;
      if (!silent) fire(session.cbs.onEnd);
      /* 停止也视为会话收尾：对外发 speaking:end，让光环/声波 UI 复位 */
      App.emit('speaking:end');
    }
  }

  /** 分句：/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g（与规格一致） */
  function splitSentences(text) {
    var out = [];
    var re = /[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      var s = m[0].trim();
      if (s) out.push(s);
    }
    return out;
  }

  /** 请求单句合成 → mp3 blob（失败返回 null 并 console.warn） */
  function fetchSentence(sentence, session, voice, speed) {
    return fetch(getEndpointInternal(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: session.abort.signal,
      body: JSON.stringify({
        model: 'app',
        voice: voice,
        input: sentence,
        response_format: 'mp3',
        speed: speed
      })
    }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.blob();
    }).then(function (blob) {
      if (!blob || !blob.size) throw new Error('空音频');
      return blob;
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return null; /* stop/顶掉：静默 */
      console.warn('[App.voice] 合成失败，跳过此句：', sentence, err);
      if (session) session.failed++;
      return null; /* 静默降级：不阻断后续句子 */
    });
  }

  /**
   * 从第 idx 句开始：预取 ≤2 句 → 顺序播放 → 递归推进。
   * 每步 await 后核对 session.token，被顶掉立即退出。
   */
  function prefetchAndPlay(sentences, idx, session, voice, speed) {
    if (current !== session) return; /* 已被顶掉 */

    if (idx >= sentences.length) {
      finish(session);
      return;
    }

    var total = sentences.length;
    var windowEnd = Math.min(idx + 2, total); /* 并发预取前 2 句 */

    var jobs = [];
    for (var i = idx; i < windowEnd; i++) jobs.push(fetchSentence(sentences[i], session, voice, speed));

    Promise.all(jobs).then(function (blobs) {
      if (current !== session) return; /* 已被顶掉（令牌自检） */

      /* 播放窗口内逐句（失败句跳过，不占 onSentence 序号语义：
         仍按原句序号上报，保持“第 i/total 句”进度准确） */
      var playNext = function (k) {
        if (current !== session) return;

        if (k >= windowEnd) {
          prefetchAndPlay(sentences, windowEnd, session, voice, speed);
          return;
        }

        /* blobs 是窗口内相对数组（blobs[0] = 第 idx 句）；k 为全局句序 */
        var blob = blobs[k - idx];
        if (!blob) { playNext(k + 1); return; } /* 该句合成失败：静默跳过 */

        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        var rec = { audio: audio, url: url };
        session.audios.push(rec);

        /* k 本身就是全局句序（playNext 从窗口起点 idx 起） */
        onSentenceStart(session, k, total);

        var advance = function () {
          audio.onended = null;
          audio.onerror = null;
          /* 释放这句的资源并出队 */
          var at = session.audios.indexOf(rec);
          if (at > -1) session.audios.splice(at, 1);
          releaseAudio(rec);
          playNext(k + 1);
        };
        audio.onended = advance;
        audio.onerror = function () {
          console.warn('[App.voice] 播放出错，跳过此句');
          advance();
        };

        var p = audio.play();
        if (p && typeof p.catch === 'function') {
          p.catch(function (err) {
            console.warn('[App.voice] 自动播放被拦截，跳过此句', err);
            advance();
          });
        }
      };

      playNext(idx);
    }).catch(function (err) {
      console.warn('[App.voice] 预取失败（意外）', err);
      if (current === session) prefetchAndPlay(sentences, idx + 1, session, voice, speed);
    });
  }

  /** 某句开始播放：首发 onStart，逐句 onSentence + 事件总线 */
  function onSentenceStart(session, i, total) {
    if (!session.started) {
      session.started = true;
      fire(session.cbs.onStart, i, total);
      App.emit('speaking:start', { sentence: i, total: total });
    }
    fire(session.cbs.onSentence, i, total);
    App.emit('speaking:sentence', { index: i, total: total });
  }

  /** 全部句子播完：onEnd + 事件；若一句都没能播且全部合成失败，先补发 onError */
  function finish(session) {
    if (session.finished) return;
    session.finished = true;
    if (current === session) current = null;
    if (!session.started && session.failed > 0) {
      fire(session.cbs.onError, new Error('语音服务不可用（' + session.failed + ' 句全部失败）'));
    }
    fire(session.cbs.onEnd);
    App.emit('speaking:end');
  }
})();
