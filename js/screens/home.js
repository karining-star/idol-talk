/* ============================================================
   screens/home.js — 首页（选择爱豆）·「杂志梦幻」
   ------------------------------------------------------------
   契约：App.screens.home = { mount(el), show(), hide() }
   el = index.html 的 #screen-home（mount 时整体替换 .scaffold 占位）

   版面（对照 concepts/editorial.html · 屏幕 01）：
     报头      IdolTalk. + VOL.132 — OCT.2026 + 刻度发丝线
     问候区    The Evening Edit · 晚间特辑（随时段切换晨间/午后/深夜特辑）
               衬线大标题「今晚，和谁聊聊？」+ 日期/时段行（按当前时间生成）
     今晚之选  当日随机 1 位爱豆 hero（跨零点自动换人，当天恒定）：
               N°01 图版角标 + TONIGHT'S PICK 超大衬线题 + 图版注释
               + 图版刻度行 + 「开始对话」CTA
     The Index 搜索（实时过滤 + 清除钮 + 空态排版）+ 团体 chips
               （App.groups 生成，含「全部」）+ 错落双列爱豆网格
     版尾      「查看全部 132 位」发丝线链接 + 刊尾 colophon

   交互与工程：
     · 卡片 hover 上浮 + .sheen 扫光；入场 .rise + --i 错峰（封顶防长尾）
     · 选择性在线：固定 4 位标在线（呼吸绿点），其余不标
     · 132 位全量渲染；图片 IntersectionObserver 懒加载（rootMargin 预取），
       缺图自动换渐变衬线首字母占位（onerror 捕获阶段委托）
     · 点击卡片 / hero 图 / CTA → App.chat.open(id) → App.nav.go('chat')
     · show/hide 维护 30 秒时钟（问候行实时刷新），hide 时清理定时器
     · 样式经 <style id="home-style"> 注入，选择器全部锁定 #screen-home；
       基元（.kicker/.tick/.n-tag/.chip/.cta/.live/.sheen/.rise/.scroll）
       复用 app.css，本文件不重定义全局类
   ============================================================ */
(function () {
  'use strict';

  var App = (window.App = window.App || {});
  App.screens = App.screens || {};

  /* ============ 常量 ============ */

  /** 选择性在线：固定 4 位（评审补充——只标 3-4 位，不做全员假在线） */
  var ONLINE_IDS = ['aespa-karina', 'blackpink-lisa', 'bts-v', 'twice-tzuyu'];

  /** 入场 stagger 封顶（132 张卡片若不封顶，错峰会拖到 8 秒开外） */
  var STAGGER_CAP = 12;
  /** 卡片在区块编排中的起始序（报头 0 / 问候 1 / hero 2 / 目录头 3 / 搜索 4 / chips 5） */
  var CARD_BASE = 6;

  /** 月份缩写与星期（刊头 + 问候行） */
  var MONS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  var WEEK = ['日', '一', '二', '三', '四', '五', '六'];

  /** 时段文案（5-11 晨间 / 11-17 午后 / 17-22 晚间 / 其余 深夜） */
  var PERIODS = {
    morning:   { edit: 'The Morning Edit',    zh: '晨间', l1: '今天，', l2: '和谁聊聊？', sub: 'A new day, in conversation.',      tip: '适合热身闲聊' },
    afternoon: { edit: 'The Afternoon Edit',   zh: '午后', l1: '今天，', l2: '和谁聊聊？', sub: 'Afternoon, in conversation.',     tip: '适合轻松陪练' },
    evening:   { edit: 'The Evening Edit',    zh: '晚间', l1: '今晚，', l2: '和谁聊聊？', sub: 'Tonight, in conversation.',       tip: '适合闲聊' },
    night:     { edit: 'The Late-night Edit', zh: '深夜', l1: '夜深了，', l2: '还聊聊？', sub: 'Late night, in conversation.',   tip: '适合安静练习' }
  };

  /** 卡片右下角箭头（hover 时滑入，与概念稿一致） */
  var ARROW = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 12h15M13 5.5 19.5 12 13 18.5"/></svg>';

  /** CTA 箭头 */
  var CTA_ARROW = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 12h15M13 5.5 19.5 12 13 18.5"/></svg>';

  /** 搜索放大镜 */
  var SEARCH_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" aria-hidden="true">' +
    '<circle cx="11" cy="11" r="6.2"/><path d="m15.6 15.6 4.2 4.2"/></svg>';

  /* ============ 小工具 ============ */

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /** 图版号：N°01 … N°99 / N°100（两位零填充，三位不填） */
  function noStr(i) { return 'N°' + (i < 10 ? '0' + i : i); }

  /** HTML 转义（数据虽来自固化 data.js，仍按防御式处理属性插值） */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** 当前时段键 */
  function periodOf(d) {
    var h = d.getHours();
    if (h >= 5 && h < 11) return 'morning';
    if (h >= 11 && h < 17) return 'afternoon';
    if (h >= 17 && h < 22) return 'evening';
    return 'night';
  }

  /** 日期字符串（YYYY-MM-DD）——「今晚之选」的换人粒度 */
  function dateKey(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  /** 今晚之选固定为柳智敏（与设计稿一致）；置空则退回按日随机 */
  var PINNED_HERO = 'aespa-karina';

  /** djb2 字符串散列 → 当日恒定的伪随机挑选（不依赖 Math.random，刷新不变） */
  function dailyPick(list, key) {
    if (PINNED_HERO) {
      for (var j = 0; j < list.length; j++) {
        if (list[j].id === PINNED_HERO) return list[j];
      }
    }
    var h = 5381;
    for (var i = 0; i < key.length; i++) {
      h = (((h << 5) + h) + key.charCodeAt(i)) >>> 0;
    }
    return list[h % list.length];
  }

  /* ============ 模块状态 ============ */

  var idols = [];        // data.js 注入的爱豆总目
  var hero = null;       // 今晚之选
  var heroKey = '';      // 当前 hero 对应的日期
  var timeTimer = null;  // show 期间的 30s 时钟
  var filterState = { group: 'all', q: '' };

  /* ============ 模块级 DOM 引用（mount 时赋值） ============ */

  var scrollEl, mhMetaEl, greetEditEl, greetH1El, greetSubEl, greetMetaEl;
  var heroCardEl, heroImgEl, heroCapEl, heroNameEl, heroSubEl, heroCtaEl;
  var idxCountEl, searchInputEl, searchClearEl, searchTotalEl, chipsEl;
  var gridEl, gridEmptyEl, emptyNoteEl, emptyResetEl, allLinkEl;

  /* ============================================================
     屏幕样式（注入一次；全部锁定 #screen-home，不污染全局）
     ============================================================ */
  function styleText() {
    return [
      /* ---------- 滚动区：仅底部渐隐（顶部报头保持清晰） ---------- */
      '#screen-home .home-scroll{ flex:1; min-height:0; ',
      '  -webkit-mask-image:linear-gradient(180deg,#000 0,#000 calc(100% - 34px),transparent 100%);',
      '          mask-image:linear-gradient(180deg,#000 0,#000 calc(100% - 34px),transparent 100%); }',

      /* ---------- 报头 ---------- */
      '#screen-home .masthead{ padding:4px 22px 0; }',
      '#screen-home .mh-row{ display:flex; align-items:baseline; justify-content:space-between; padding-bottom:9px; }',
      '#screen-home .logo{ font-family:var(--serif); font-style:italic; font-size:var(--fs-title); letter-spacing:.01em; }',
      '#screen-home .logo i{ font-style:italic; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
      '#screen-home .mh-meta{ font-size:var(--fs-micro); font-weight:600; letter-spacing:.26em; color:var(--p3); text-transform:uppercase; }',
      '#screen-home .mh-rule{ height:1px; background:var(--line); position:relative; }',
      '#screen-home .mh-rule::before{ content:""; position:absolute; left:0; top:0; width:46px; height:1px; background:var(--grad); }',

      /* ---------- 首页透明：舞台光（射灯/光球/星点）在 .phone 层，必须能透出来 ---------- */
      '#screen-home{ background:transparent; }',

      /* ---------- 问候区 ---------- */
      '#screen-home .greet{ padding:12px 22px 0; }',
      '#screen-home .h1{ margin-top:10px; font-family:var(--serif); font-weight:400; font-size:var(--fs-h1); line-height:1.28; letter-spacing:.02em; }',
      '#screen-home .h1 .l2{ position:relative; }',
      '#screen-home .h1 .l2::after{ content:""; position:absolute; left:2px; bottom:-5px; width:58px; height:3px; border-radius:3px; background:var(--grad); opacity:.9; }',
      '#screen-home .sub{ margin-top:9px; font-family:var(--serif); font-style:italic; font-size:12.5px; color:var(--p2); letter-spacing:.03em; }',
      '#screen-home .greet-meta{ margin-top:7px; font-size:var(--fs-note); letter-spacing:.14em; color:var(--p3); display:flex; gap:8px; flex-wrap:wrap; }',
      '#screen-home .greet-meta i{ font-style:normal; opacity:.55; }',

      /* ---------- 今晚之选 · hero ---------- */
      '#screen-home .hero{ padding:14px 22px 0; }',
      '#screen-home .hero-card{ position:relative; height:216px; border-radius:var(--r-lg); overflow:hidden; cursor:pointer;',
      '  border:1px solid rgba(247,242,248,.10); background:linear-gradient(160deg,var(--ink-3),var(--ink-4));',
      '  transition:border-color var(--t-mid) var(--ease); }',
      '#screen-home .hero-card img{ width:100%; height:100%; object-fit:cover; object-position:var(--pos,50% 8%);',
      '  transform:scale(var(--zoom,1)) translateZ(0); transform-origin:50% var(--fy,30%);',
      '  transition:transform 1.4s var(--ease-soft); }',
      '#screen-home .hero-card:hover img{ transform:scale(calc(var(--zoom,1) * 1.04)) translateZ(0); }',
      '#screen-home .hero-card:hover{ border-color:var(--line-rose); }',
      '#screen-home .hero-veil{ position:absolute; inset:0;',
      '  background:linear-gradient(180deg, rgba(11,8,18,.66) 0%, rgba(11,8,18,.26) 26%, rgba(11,8,18,0) 48%, rgba(11,8,18,0) 72%, rgba(11,8,18,.38) 100%); }',
      '#screen-home .hero-no{ position:absolute; top:14px; right:14px; z-index:3; }',
      '#screen-home .hero-title{ position:absolute; left:17px; top:13px; right:17px; z-index:3;',
      '  font-family:var(--serif); font-weight:400; font-size:var(--fs-hero); line-height:.97; letter-spacing:.005em; text-transform:uppercase;',
      '  text-shadow:0 2px 26px rgba(0,0,0,.45); }',
      '#screen-home .hero-title .pick{ background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
      '#screen-home .hero-cap{ display:flex; align-items:center; gap:8px; margin-top:9px;',
      '  font-size:var(--fs-micro); letter-spacing:.2em; color:var(--p3); text-transform:uppercase; }',
      '#screen-home .hero-cap .tick{ width:16px; }',
      '#screen-home .hero-row{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:6px; }',
      '#screen-home .hero-name{ font-family:var(--serif); font-size:var(--fs-h2); letter-spacing:.05em; text-transform:uppercase; min-width:0; }',
      '#screen-home .hero-name small{ display:flex; align-items:center; gap:6px; margin-top:3px;',
      '  font-family:var(--sans); font-size:var(--fs-tag); font-weight:600; letter-spacing:.22em; color:var(--p2); text-transform:uppercase; }',

      /* ---------- The Index · 目录头 / 搜索 / chips ---------- */
      '#screen-home .index-head{ margin:16px 22px 0; padding-top:12px; border-top:1px solid var(--line);',
      '  position:relative; display:flex; align-items:baseline; justify-content:space-between; gap:10px; }',
      '#screen-home .index-head::before{ content:""; position:absolute; left:0; top:-1px; width:46px; height:1px; background:var(--grad); }',
      '#screen-home .index-title{ font-family:var(--serif); font-style:italic; font-size:var(--fs-h3); letter-spacing:.02em; }',
      '#screen-home .index-count{ flex:none; font-size:var(--fs-micro); font-weight:600; letter-spacing:.24em; color:var(--p3); text-transform:uppercase; }',
      '#screen-home .search{ display:flex; align-items:center; gap:10px; margin:12px 22px 0; padding-bottom:9px; border-bottom:1px solid var(--line-2); }',
      '#screen-home .search svg{ flex:none; opacity:.6; }',
      '#screen-home .search input{ flex:1; min-width:0; font-size:var(--fs-body); letter-spacing:.02em; }',
      '#screen-home .search input::placeholder{ color:var(--p3); }',
      '#screen-home .s-esc{ flex:none; font-size:8px; font-weight:600; letter-spacing:.2em; color:var(--p3); }',
      '#screen-home .s-clr{ flex:none; font-size:var(--fs-micro); font-weight:600; letter-spacing:.2em; text-transform:uppercase; color:var(--p2); padding:2px 7px; border-radius:6px; }',
      '#screen-home .s-clr:hover{ color:var(--paper); background:rgba(247,242,248,.06); }',
      '#screen-home .chips{ display:flex; gap:8px; overflow-x:auto; scrollbar-width:none; padding:11px 22px 3px; }',
      '#screen-home .chips::-webkit-scrollbar{ display:none; }',

      /* ---------- 爱豆网格 · 错落双列（全息偶像 Holo Idol：深紫黑 + 镭射描边 + 舞台光） ---------- */
      /* 注意 align-items:start —— 错落用的 :nth-child(even){margin-top} 会把行撑高，
         默认 stretch 会把奇数位卡片一起拉长，底部多出一截空卡面（黑框） */
      '#screen-home .grid{ display:grid; grid-template-columns:1fr 1fr; align-items:start; gap:26px 13px; padding:13px 22px 0; }',
      /* 镭射描边：改由独立伪元素承载，这样能单独做「色相流动」而不影响照片 */
      '#screen-home .card{ position:relative; border-radius:22px; overflow:hidden; cursor:pointer;',
      '  border:1px solid transparent;',
      '  background:#160d28;',
      '  box-shadow:0 16px 36px -22px rgba(110,255,238,.42), 0 0 26px -10px rgba(170,120,255,.5);',
      '  transition:transform var(--t-mid) var(--ease), box-shadow var(--t-mid) var(--ease); }',
      /* 全息描边层：conic 色环 + 遮罩成细边。平时微亮(.38)，hover 时全亮（设计稿如此） */
      '#screen-home .card::after{ content:""; position:absolute; inset:0; z-index:6; pointer-events:none;',
      '  border-radius:inherit; padding:1.5px; opacity:.38; transition:opacity .45s var(--ease);',
      '  background:conic-gradient(from 200deg, #7ee8fa, #a78bfa, #f0abfc, #fda4af, #7ee8fa);',
      '  -webkit-mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);',
      '  -webkit-mask-composite:xor;',
      '  mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);',
      '  mask-composite:exclude; }',
      '#screen-home .card:hover::after, #screen-home .card.flash-on::after{ opacity:1; }',
      /* 内圈发丝线 */
      '#screen-home .card .ph::after{ content:""; position:absolute; inset:0; z-index:5; pointer-events:none;',
      '  border-radius:inherit; box-shadow:inset 0 0 0 1px rgba(255,255,255,.09), inset 0 1px 0 rgba(255,255,255,.16); }',
      '#screen-home .card:nth-child(even){ margin-top:30px; }',
      /* 舞台光：卡片背后一圈辉光，缓慢呼吸 */
      '#screen-home .card::before{ content:""; position:absolute; inset:-40% -40% auto -40%; height:150%; z-index:0;',
      '  background:radial-gradient(50% 60% at 50% 0%, rgba(160,130,255,.34), transparent 72%);',
      '  opacity:.7; pointer-events:none;',
      '  animation:stage-breathe 6.5s var(--ease-soft) infinite alternate; }',
      '@keyframes stage-breathe{',
      '  from{ opacity:.55; transform:translate3d(-3%,0,0) scale(1); }',
      '  to{ opacity:.95; transform:translate3d(3%,4%,0) scale(1.12); } }',
      '#screen-home .card .ph{ position:relative; z-index:1; aspect-ratio:3/4; overflow:hidden; background:transparent; }',
      '#screen-home .card .ph img{ width:100%; height:100%; object-fit:cover; object-position:var(--pos,50% 20%);',
      '  filter:saturate(1.06) contrast(1.03);',
      '  transform:scale(var(--zoom,1)) translateZ(0); transform-origin:50% var(--fy,39%);',
      '  transition:transform 1.2s var(--ease-soft); }',
      '#screen-home .card:hover .ph img{ transform:scale(calc(var(--zoom,1) * 1.05)) translateZ(0); }',
      /* （原来的 .ph::after 扫光已移除：与上面的内圈发丝线冲突；
         「一道光」由点击时的 .flash 承担） */
      '#screen-home .card .veil{ position:absolute; inset:0; z-index:2;',
      '  background:linear-gradient(180deg, rgba(9,5,18,.2) 0%, rgba(9,5,18,.02) 32%, rgba(9,5,18,.62) 68%, rgba(9,5,18,.95) 100%); }',
      '#screen-home .card .no{ position:absolute; top:11px; left:12px; z-index:2;',
      '  font-family:var(--serif); font-style:italic; font-size:11px; letter-spacing:.08em; text-shadow:0 1px 10px rgba(0,0,0,.65); }',
      '#screen-home .card .no.is-pick{ background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; text-shadow:none; }',
      '#screen-home .card .meta{ position:absolute; left:13px; right:44px; bottom:11px; z-index:2; }',
      '#screen-home .card .name{ font-family:var(--sans); font-weight:700; font-size:14.5px; letter-spacing:.01em;',
      '  text-transform:none; text-shadow:0 2px 14px rgba(0,0,0,.6); display:flex; align-items:center; gap:6px; }',
      '#screen-home .card .name .live{ width:5px; height:5px; border-radius:50%; background:#7ee8fa;',
      '  box-shadow:0 0 8px #7ee8fa; flex:none; }',
      /* 圆形玻璃箭头按钮（右下角） */
      '#screen-home .card .go{ position:absolute; right:10px; bottom:10px; z-index:5; width:32px; height:32px;',
      '  border-radius:50%; display:grid; place-items:center; color:#fff;',
      '  background:rgba(255,255,255,.10); backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);',
      '  box-shadow:inset 0 0 0 1px rgba(255,255,255,.16); opacity:.6; transform:translateY(2px);',
      '  transition:all .35s var(--ease-soft); }',
      '#screen-home .card:hover .go{ opacity:1; transform:none;',
      '  background:linear-gradient(135deg,#8b5cf6 0%,#c05ce8 55%,#ec4899 100%);',
      '  box-shadow:0 8px 22px -6px rgba(192,92,232,.85), inset 0 1px 0 rgba(255,255,255,.4); }',
      /* ---------- 全息偏光（设计稿 .tint）：soft-light 叠一层镭射色 ---------- */
      '#screen-home .card .tint{ position:absolute; inset:0; z-index:3; pointer-events:none;',
      '  mix-blend-mode:soft-light;',
      '  background:linear-gradient(150deg, rgba(167,139,250,.32), transparent 45%,',
      '    rgba(126,232,250,.24) 80%, rgba(240,171,252,.3)); }',
      /* 扫光（设计稿 .sheen）：hover 时一道斜高光扫过 */
      '#screen-home .card .sheen{ position:absolute; top:-40%; bottom:-40%; left:-70%; width:60%; z-index:3;',
      '  opacity:0; pointer-events:none; filter:blur(2px);',
      '  background:linear-gradient(100deg, transparent, rgba(255,255,255,.3), rgba(126,232,250,.42), transparent); }',
      '#screen-home .card:hover .sheen{ animation:card-sheen .95s cubic-bezier(.3,.6,.3,1) forwards; }',
      '@keyframes card-sheen{',
      '  0%{ transform:translateX(0) rotate(8deg); opacity:0; }',
      '  25%{ opacity:1; }',
      '  100%{ transform:translateX(400px) rotate(8deg); opacity:0; } }',
      /* hover 时展开的一句台词（设计稿 .line） */
      '#screen-home .card .line{ margin-top:0; font-size:10px; color:var(--p2); opacity:0; max-height:0;',
      '  transform:translateY(6px); overflow:hidden; transition:all .45s var(--ease-soft); }',
      '#screen-home .card:hover .line{ opacity:.95; max-height:16px; margin-top:7px; transform:none; }',
      '#screen-home .card .grp{ display:flex; align-items:center; margin-top:4px; }',
      '#screen-home .card .grp>span{ font-size:9px; font-weight:700; letter-spacing:.22em; color:var(--p2); text-transform:uppercase; opacity:.85; }',
      '#screen-home .card:hover{ transform:translateY(-5px);',
      '  box-shadow:0 0 0 1px rgba(110,255,238,.30), 0 24px 50px -24px rgba(110,255,238,.6),',
      '    0 0 38px -8px rgba(170,120,255,.75); }',
      '#screen-home .card:hover::before{ opacity:1; }',
      /* 注意：.card:hover .ph img 的规则在上面（要带上 --zoom），这里不要再写一条覆盖它 */

      /* ---------- 点卡片时闪过的「一道光」 ---------- */
      '#screen-home .card .flash{ position:absolute; inset:0; z-index:3; pointer-events:none; opacity:0;',
      '  background:linear-gradient(104deg,',
      '    rgba(255,255,255,0) 0%, rgba(180,255,246,.07) 38%,',
      '    rgba(210,255,250,.72) 47.5%, rgba(255,255,255,.90) 51%, rgba(190,225,255,.60) 55%,',
      '    rgba(150,120,255,.14) 63%, rgba(255,255,255,0) 74%);',
      '  mix-blend-mode:screen; transform:translateX(-125%) skewX(-8deg); }',
      '#screen-home .card.flash-on .flash{ animation:card-flash .58s var(--ease-soft) both; }',
      '@keyframes card-flash{',
      '  0%{ opacity:0; transform:translateX(-130%) skewX(-8deg); }',
      '  15%{ opacity:1; }',      /* 光带进画面前就点亮，保证扫过卡片时是亮的 */
      '  85%{ opacity:1; }',
      '  100%{ opacity:0; transform:translateX(130%) skewX(-8deg); } }',
      '#screen-home .card.flash-on{ box-shadow:0 0 0 1px rgba(210,255,250,.55),',
      '  0 0 44px -6px rgba(180,255,246,.75), 0 0 60px -10px rgba(170,120,255,.6); }',

      /* ---------- 缺图占位（渐变 + 衬线首字母） ---------- */
      '#screen-home .ph.no-photo, #screen-home .hero-card.no-photo{ background:',
      '  radial-gradient(120% 90% at 30% 20%, rgba(242,160,220,.26), transparent 60%),',
      '  radial-gradient(120% 90% at 75% 85%, rgba(167,139,250,.28), transparent 58%),',
      '  linear-gradient(160deg, var(--ink-3), var(--ink-4)); }',
      '#screen-home .ph.no-photo::after, #screen-home .hero-card.no-photo::after{',
      '  content:attr(data-initial); position:absolute; inset:0; z-index:2;',
      '  display:flex; align-items:center; justify-content:center;',
      '  font-family:var(--serif); font-style:italic; font-size:56px;',
      '  background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
      '#screen-home .hero-card.no-photo::after{ font-size:96px; }',

      /* ---------- 搜索空态 ---------- */
      '#screen-home .empty{ grid-column:1 / -1; text-align:center; padding:34px 0 26px; }',
      '#screen-home .empty .e-rule{ margin:0 auto; width:min(64px, 40%); }',
      '#screen-home .empty .e-title{ margin-top:14px; font-family:var(--serif); font-style:italic; font-size:var(--fs-h3); letter-spacing:.02em; color:var(--paper); }',
      '#screen-home .empty .e-note{ margin-top:9px; font-size:var(--fs-meta); letter-spacing:.05em; line-height:1.75; color:var(--p3); }',
      '#screen-home .empty .e-note b{ font-weight:600; color:var(--p2); }',
      '#screen-home .empty .cta{ margin-top:18px; }',

      /* ---------- 版尾 ---------- */
      '#screen-home .all-link{ display:flex; align-items:center; gap:14px; margin:30px 22px 0; width:calc(100% - 44px);',
      '  justify-content:center; font-size:var(--fs-note); font-weight:600; letter-spacing:.24em; color:var(--p2); text-transform:uppercase; }',
      '#screen-home .all-link::before, #screen-home .all-link::after{ content:""; flex:1; height:1px; background:var(--line); }',
      '#screen-home .all-link b{ font-weight:600; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; -webkit-text-fill-color:transparent; }',
      '#screen-home .all-link.filtered span{ color:var(--paper); }',
      '#screen-home .colophon{ margin:24px 22px 0; text-align:center; font-size:var(--fs-micro); letter-spacing:.26em; color:var(--p4); text-transform:uppercase; line-height:2.1; }',
      '#screen-home .scroll-pad{ height:32px; }'
    ].join('\n');
  }

  function injectStyle() {
    if (document.getElementById('home-style')) return;
    var s = document.createElement('style');
    s.id = 'home-style';
    s.textContent = styleText();
    document.head.appendChild(s);
  }

  /* ============================================================
     静态骨架（一次性构建）
     ============================================================ */
  function buildSkeleton(el) {
    el.innerHTML =
      '<div class="home-scroll scroll" id="homeScroll">' +

        /* —— 报头 —— */
        '<header class="masthead rise" style="--i:0">' +
          '<div class="mh-row">' +
            '<span class="logo">IdolTalk<i>.</i></span>' +
            '<span class="mh-meta" id="mhMeta">VOL.132</span>' +
          '</div>' +
          '<div class="mh-rule"></div>' +
        '</header>' +

        /* —— 问候区（文案随时段实时生成） —— */
        '<section class="greet rise" style="--i:1">' +
          '<div class="kicker"><span class="tick"></span><span id="greetEdit">The Evening Edit · 晚间特辑</span></div>' +
          '<h1 class="h1" id="greetH1">今晚，<br><span class="l2">和谁聊聊？</span></h1>' +
          '<p class="sub" id="greetSub">Tonight, in conversation.</p>' +
          '<p class="greet-meta" id="greetMeta"></p>' +
        '</section>' +

        /* —— 今晚之选 —— */
        '<section class="hero rise" style="--i:2">' +
          '<div class="hero-card sheen" id="heroCard" role="button" tabindex="0" aria-label="与今晚特辑爱豆开始对话">' +
            '<img id="heroImg" data-photo="1" alt="">' +
            '<div class="hero-veil"></div>' +
            '<span class="hero-no n-tag">N°01</span>' +
            '<div class="hero-title">Tonight&rsquo;s<br><span class="pick">Pick</span></div>' +
          '</div>' +
          '<div class="hero-cap"><span class="tick"></span><span id="heroCap"></span></div>' +
          '<div class="hero-row">' +
            '<div class="hero-name"><span id="heroName"></span><small id="heroSub"></small></div>' +
            '<button class="cta" type="button" id="heroCta">开始对话 ' + CTA_ARROW + '</button>' +
          '</div>' +
        '</section>' +

        /* —— The Index 目录头 —— */
        '<div class="index-head rise" style="--i:3">' +
          '<span class="index-title">The Index</span>' +
          '<span class="index-count" id="idxCount"></span>' +
        '</div>' +

        /* —— 搜索 —— */
        '<div class="search rise" style="--i:4">' +
          SEARCH_ICON +
          '<input id="homeSearch" type="text" placeholder="搜索爱豆、团体…" aria-label="搜索爱豆" autocomplete="off" spellcheck="false">' +
          '<button class="s-clr" type="button" id="searchClear" hidden>清除</button>' +
          '<span class="s-esc" id="searchTotal">132</span>' +
        '</div>' +

        /* —— 团体 chips（mount 时按 App.groups 填充） —— */
        '<div class="chips rise" style="--i:5" id="homeChips"></div>' +

        /* —— 爱豆网格（mount 时全量填充）+ 空态 —— */
        '<div class="grid" id="homeGrid" aria-label="爱豆列表"></div>' +

        /* —— 版尾 —— */
        '<button class="all-link" type="button" id="allLink"><span id="allLinkText">查看全部 <b>132</b> 位</span></button>' +
        '<footer class="colophon">IdolTalk · VOL.132<br>本刊由 132 位爱豆联合出镜 · 每晚更新「今晚之选」</footer>' +
        '<div class="scroll-pad"></div>' +
      '</div>';

    /* 模块级引用 */
    scrollEl       = el.querySelector('#homeScroll');
    mhMetaEl       = el.querySelector('#mhMeta');
    greetEditEl    = el.querySelector('#greetEdit');
    greetH1El      = el.querySelector('#greetH1');
    greetSubEl     = el.querySelector('#greetSub');
    greetMetaEl    = el.querySelector('#greetMeta');
    heroCardEl     = el.querySelector('#heroCard');
    heroImgEl      = el.querySelector('#heroImg');
    heroCapEl      = el.querySelector('#heroCap');
    heroNameEl     = el.querySelector('#heroName');
    heroSubEl      = el.querySelector('#heroSub');
    heroCtaEl      = el.querySelector('#heroCta');
    idxCountEl     = el.querySelector('#idxCount');
    searchInputEl  = el.querySelector('#homeSearch');
    searchClearEl  = el.querySelector('#searchClear');
    searchTotalEl  = el.querySelector('#searchTotal');
    chipsEl        = el.querySelector('#homeChips');
    gridEl         = el.querySelector('#homeGrid');
    allLinkEl      = el.querySelector('#allLink');
  }

  /* ============================================================
     动态文案：时段 / 日期 / 刊号 / hero（跨零点换人）
     ============================================================ */

  /** 刷新问候区与刊头（mount / show / 每 30 秒） */
  function refreshDynamic() {
    var d = new Date();
    var p = PERIODS[periodOf(d)];

    /* 刊号：VOL.<人数> — <月份缩写>.<年> */
    if (mhMetaEl) {
      mhMetaEl.textContent = 'VOL.' + (idols.length || 132) + ' — ' + MONS[d.getMonth()] + '.' + d.getFullYear();
    }

    if (greetEditEl) {
      greetEditEl.textContent = p.edit + ' · ' + p.zh + '特辑';
    }
    if (greetH1El) {
      greetH1El.innerHTML = esc(p.l1) + '<br><span class="l2">' + esc(p.l2) + '</span>';
    }
    if (greetSubEl) greetSubEl.textContent = p.sub;
    if (greetMetaEl) {
      greetMetaEl.innerHTML =
        esc((d.getMonth() + 1) + '月' + d.getDate() + '日 周' + WEEK[d.getDay()]) +
        ' <i>·</i> ' + esc(p.zh + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes())) +
        ' <i>·</i> ' + esc(p.tip);
    }

    /* 今晚之选：同一天恒定，跨零点自动换人 */
    var key = dateKey(d);
    if (key !== heroKey) {
      heroKey = key;
      renderHero(dailyPick(idols, key), p);
    } else if (hero) {
      /* 时段变了（如深夜→晨间），只更新图版注释的档期措辞 */
      updateHeroCap(p);
    }
  }

  /** 渲染 hero（今晚之选） */
  function renderHero(idol, p) {
    hero = idol;
    if (!idol) return;

    heroImgEl.src = App.photoFor(idol.id);
    heroImgEl.alt = idol.stage + ', ' + idol.group;
    heroCardEl.style.setProperty('--pos', App.photoPos(idol.id));
    heroCardEl.style.setProperty('--fy', Math.round(App.photoY(idol.id) * 100) + '%');
    /* 英雄卡是宽幅横条，缩放收敛一些，别把脸怼满 */
    heroCardEl.style.setProperty('--zoom', Math.min(1.25, App.photoZoom(idol.id)).toFixed(2));
    heroCardEl.dataset.initial = (idol.stage || '?').charAt(0).toUpperCase();
    heroCardEl.setAttribute('aria-label', '与 ' + idol.stage + ' 开始对话');
    /* 若上一张缺图占位生效，换人后复位重试 */
    heroCardEl.classList.remove('no-photo');
    heroImgEl.style.display = '';

    updateHeroCap(p);

    heroNameEl.textContent = idol.stage;
    var online = ONLINE_IDS.indexOf(idol.id) > -1;
    heroSubEl.innerHTML = (online ? '<span class="live" aria-hidden="true"></span>在线 · ' : '') +
      esc(idol.group) + ' · ' + esc(idol.zh);

    markPick(idol.id);
  }

  /** 图版注释行：图版 N°01 — NAME / GROUP · 档期已上线 */
  function updateHeroCap(p) {
    if (!heroCapEl || !hero) return;
    var pp = p || PERIODS[periodOf(new Date())];
    heroCapEl.textContent = '图版 N°01 — ' +
      hero.stage.toUpperCase() + ' / ' + hero.group.toUpperCase() +
      ' · ' + pp.zh + '档已上线';
  }

  /** 网格中标记/取消「今晚之选」的渐变图版号 */
  function markPick(id) {
    var prev = gridEl.querySelector('.card .no.is-pick');
    if (prev) prev.classList.remove('is-pick');
    if (!id) return;
    var card = gridEl.querySelector('.card[data-id="' + id + '"]');
    if (card) {
      var no = card.querySelector('.no');
      if (no) no.classList.add('is-pick');
    }
  }

  /* ============================================================
     The Index：chips + 网格 + 过滤 + 空态
     ============================================================ */

  /** 团体 chips：全部 + App.groups（目录序） */
  function buildChips() {
    var html = '<button class="chip on" type="button" data-filter="all">全部</button>';
    (App.groups || []).forEach(function (g) {
      html += '<button class="chip" type="button" data-filter="' + esc(g) + '">' + esc(g) + '</button>';
    });
    chipsEl.innerHTML = html;
  }

  /** 132 位全量网格（照片懒加载 + 错落双列 + 编号 N°xx） */
  function buildGrid() {
    var html = '';
    idols.forEach(function (idol, i) {
      var online = ONLINE_IDS.indexOf(idol.id) > -1;
      var initial = esc((idol.stage || '?').charAt(0).toUpperCase());
      html +=
        '<article class="card rise" role="button" tabindex="0" data-id="' + esc(idol.id) + '" ' +
          'style="--i:' + (CARD_BASE + Math.min(i, STAGGER_CAP)) + '" ' +
          'aria-label="与 ' + esc(idol.stage) + ' 开始对话">' +
          '<div class="ph" data-initial="' + initial + '" style="--pos:' + App.photoPos(idol.id) +
            ';--zoom:' + App.photoZoom(idol.id) + ';--fy:' + Math.round(App.photoY(idol.id) * 100) + '%">' +
            '<img data-photo="1" data-src="' + esc(App.photoFor(idol.id)) + '" alt="' + esc(idol.stage) + ', ' + esc(idol.group) + '" loading="lazy">' +
            '<div class="veil"></div>' +
            '<span class="tint" aria-hidden="true"></span>' +
            '<span class="sheen" aria-hidden="true"></span>' +
            '<span class="flash" aria-hidden="true"></span>' +
            '<span class="no">' + noStr(i + 1) + '</span>' +
            '<div class="meta">' +
              '<div class="name">' + esc(idol.stage) +
                (online ? '<span class="live" aria-hidden="true"></span>' : '') + '</div>' +
              '<div class="grp"><span>' + esc(idol.group) + '</span></div>' +
              '<div class="line">Let&rsquo;s talk in English</div>' +
            '</div>' +
            '<span class="go" aria-hidden="true">' + ARROW + '</span>' +
          '</div>' +
        '</article>';
    });

    /* 空态（搜索无结果时展示） */
    html +=
      '<div class="empty" id="gridEmpty" hidden>' +
        '<hr class="grad-rule center e-rule" style="--rule-w:64px">' +
        '<p class="e-title">No one by that name.</p>' +
        '<p class="e-note" id="emptyNote"></p>' +
        '<button class="cta" type="button" id="emptyReset">查看全部 132 位</button>' +
      '</div>';

    gridEl.innerHTML = html;
    gridEmptyEl = gridEl.querySelector('#gridEmpty');
    emptyNoteEl = gridEl.querySelector('#emptyNote');
    emptyResetEl = gridEl.querySelector('#emptyReset');
  }

  /** 应用过滤（团体 + 搜索词，空格分词按 AND 命中） */
  function applyFilter() {
    var q = filterState.q.trim().toLowerCase();
    var terms = q ? q.split(/\s+/).filter(Boolean) : [];
    var shown = 0;

    var cards = gridEl.querySelectorAll('.card');
    cards.forEach(function (card, idx) {
      var idol = idols[idx];
      var ok = true;
      if (filterState.group !== 'all' && idol.group !== filterState.group) ok = false;
      if (ok && terms.length) {
        var hay = (idol.stage + ' ' + idol.zh + ' ' + idol.name + ' ' + idol.group + ' ' + idol.korean).toLowerCase();
        for (var t = 0; t < terms.length; t++) {
          if (hay.indexOf(terms[t]) < 0) { ok = false; break; }
        }
      }
      /* display 切换会让重新出现的卡片自动重播 .rise 入场（错峰波纹） */
      card.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });

    /* 计数与空态 */
    if (idxCountEl) idxCountEl.textContent = '此刻可约 · ' + shown + ' 位';
    if (gridEmptyEl) {
      gridEmptyEl.hidden = shown !== 0;
      if (shown === 0 && emptyNoteEl) {
        emptyNoteEl.innerHTML = q
          ? '查无「<b>' + esc(filterState.q.trim()) + '</b>」——试试英文名（Karina）、团体（aespa）或中文名。'
          : '该团体暂无在册爱豆。';
      }
    }
    if (searchClearEl) searchClearEl.hidden = !q;
    if (searchTotalEl) searchTotalEl.hidden = !!q;
    if (allLinkEl) {
      allLinkEl.querySelector('b').textContent = idols.length || 132;
      allLinkEl.classList.toggle('filtered', shown !== idols.length);
    }
  }

  /** 复位：搜索词 + 团体（空态按钮 / 版尾链接共用） */
  function resetFilter(toast) {
    var changed = filterState.group !== 'all' || filterState.q.trim() !== '';
    filterState.group = 'all';
    filterState.q = '';
    if (searchInputEl) searchInputEl.value = '';
    chipsEl.querySelectorAll('.chip').forEach(function (c) {
      c.classList.toggle('on', c.getAttribute('data-filter') === 'all');
    });
    applyFilter();
    if (changed) {
      if (toast) App.emit('toast', '已显示全部 ' + (idols.length || 132) + ' 位爱豆');
      scrollToGrid();
    }
  }

  /** 平滑滚到网格顶部（尊重 prefers-reduced-motion） */
  function scrollToGrid() {
    if (!gridEl) return;
    var smooth = !window.matchMedia || !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    try { gridEl.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' }); }
    catch (e) { /* 老浏览器退化为跳转 */ }
  }

  /* ============================================================
     照片：IntersectionObserver 懒加载 + 缺图占位
     ============================================================ */
  function lazyLoadImages() {
    var imgs = gridEl.querySelectorAll('img[data-src]');
    if (!('IntersectionObserver' in window)) {
      /* 无 IO 支持：直接全量加载 */
      imgs.forEach(function (img) {
        img.src = img.getAttribute('data-src');
        img.removeAttribute('data-src');
      });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var img = en.target;
        var src = img.getAttribute('data-src');
        if (src) {
          img.src = src;
          img.removeAttribute('data-src');
        }
        io.unobserve(img);
      });
    }, { root: scrollEl, rootMargin: '600px 0px' });   /* 提前约一屏预取 */
    imgs.forEach(function (img) { io.observe(img); });
  }

  /* ============================================================
     进聊天：App.chat.open(id) → nav 切屏
     ============================================================ */
  function openIdol(id) {
    if (!id) return;
    try {
      if (App.chat && typeof App.chat.open === 'function') App.chat.open(id);
    } catch (e) {
      console.warn('[home] App.chat.open 出错，仍然前往聊天屏', e);
    }
    if (App.nav && typeof App.nav.go === 'function') App.nav.go('chat');
  }

  /* ============================================================
     事件接线
     ============================================================ */
  function wireEvents(el) {
    /* 点卡片时闪过的「一道光」：pointerdown 立即反馈，连点也能重播 */
    gridEl.addEventListener('pointerdown', function (e) {
      var card = e.target && e.target.closest ? e.target.closest('.card') : null;
      if (!card) return;
      card.classList.remove('flash-on');
      void card.offsetWidth;            /* 强制重排，让动画能重新触发 */
      card.classList.add('flash-on');
      setTimeout(function () { card.classList.remove('flash-on'); }, 640);
    });
    /* 卡片点击（事件委托，132 张只挂一个监听） */
    gridEl.addEventListener('click', function (e) {
      var card = e.target && e.target.closest ? e.target.closest('.card') : null;
      if (card) openIdol(card.getAttribute('data-id'));
    });
    /* 键盘可达（Enter / Space） */
    gridEl.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var card = e.target && e.target.closest ? e.target.closest('.card') : null;
      if (card) { e.preventDefault(); openIdol(card.getAttribute('data-id')); }
    });

    /* 空态复位 */
    if (emptyResetEl) {
      emptyResetEl.addEventListener('click', function () { resetFilter(true); });
    }

    /* 团体 chips（委托） */
    chipsEl.addEventListener('click', function (e) {
      var chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
      if (!chip) return;
      chipsEl.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('on'); });
      chip.classList.add('on');
      filterState.group = chip.getAttribute('data-filter') || 'all';
      applyFilter();
    });

    /* 搜索：rAF 合帧过滤 */
    var raf = 0;
    searchInputEl.addEventListener('input', function () {
      filterState.q = searchInputEl.value;
      if (!raf) {
        raf = requestAnimationFrame(function () { raf = 0; applyFilter(); });
      }
    });
    searchClearEl.addEventListener('click', function () {
      searchInputEl.value = '';
      filterState.q = '';
      applyFilter();
      searchInputEl.focus();
    });

    /* 版尾链接：过滤态下复位并回滚到网格顶部 */
    allLinkEl.addEventListener('click', function () { resetFilter(true); });

    /* hero：整卡可点（含键盘），CTA 同效 */
    heroCardEl.addEventListener('click', function () { openIdol(hero && hero.id); });
    heroCardEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openIdol(hero && hero.id);
      }
    });
    heroCtaEl.addEventListener('click', function () { openIdol(hero && hero.id); });

    /* 缺图占位：error 不冒泡，用捕获阶段委托（含 hero 图） */
    scrollEl.addEventListener('error', function (e) {
      var t = e.target;
      if (!t || t.tagName !== 'IMG' || !t.hasAttribute('data-photo')) return;
      var holder = t.closest('.ph') || t.closest('.hero-card');
      t.style.display = 'none';
      t.removeAttribute('src');
      if (holder) holder.classList.add('no-photo');
    }, true);

    /* 入场动画只播一轮：.rise 播完即摘除。
       原因：.screen 以 display 切换，display:none 会取消并【重启动画】，
       若保留 .rise，从聊天屏返回首页时 132 张卡片会重新错峰——
       滚动位置较深时内容会空白 1 秒以上。摘除后：
       首开完整播放入场，返回/过滤均为即时呈现。 */
    scrollEl.addEventListener('animationend', function (e) {
      if (e.animationName !== 'rise-in') return;
      var t = e.target;
      if (t && t.classList && t.classList.contains('rise')) {
        t.classList.remove('rise');
        t.style.removeProperty('--i');
      }
    });
  }

  /* ============================================================
     屏幕模块（冻结契约）
     ============================================================ */
  App.screens.home = {

    /** 挂载（只调用一次）：构建整屏 DOM 并接线 */
    mount: function (el) {
      idols = App.idols || [];

      injectStyle();
      buildSkeleton(el);
      buildChips();
      buildGrid();

      /* 数据未就绪的防御：仍渲染骨架，目录区显示空态 */
      if (!idols.length) {
        console.warn('[home] App.idols 为空－请检查 js/data.js 是否加载');
      }

      wireEvents(el);
      lazyLoadImages();
      refreshDynamic();
      applyFilter();
    },

    /** 每次切入：刷新时段文案并启动 30s 时钟 */
    show: function () {
      refreshDynamic();
      if (timeTimer) clearInterval(timeTimer);
      timeTimer = setInterval(refreshDynamic, 30000);
    },

    /** 切出：停时钟（跨屏不空转） */
    hide: function () {
      if (timeTimer) { clearInterval(timeTimer); timeTimer = null; }
    }
  };
})();
