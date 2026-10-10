/* ============================================================
   boot.js — 最终启动（所有脚本按冻结顺序加载完毕后执行）
   ------------------------------------------------------------
   挂载全部已注册屏幕 → 默认进入 home。
   由 index.html 最后一个 <script> 引入（经典脚本顺序执行，
   此时 data/reply/voice/chat/core/screens 均已就绪）。
   ============================================================ */
(function () {
  'use strict';

  function start() {
    if (window.App && typeof window.App.boot === 'function') {
      window.App.boot();
    } else {
      console.warn('[boot] window.App.boot 不可用：core.js 未加载或出错');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
