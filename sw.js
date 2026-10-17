/* ============================================================
   sw.js — Service Worker：应用壳离线缓存（PWA）
   ------------------------------------------------------------
   · install  ：预缓存应用壳（index.html / css / js / icons / manifest）
   · fetch    ：
       - scope 内同源 GET 静态资源 → cache-first（命中直返；未命中取网络并回填）
       - 导航请求断网     → 回退到缓存的 index.html（离线可打开）
       - TTS（127.0.0.1:8780）→ network-only：不拦截、不缓存（语音永远实时）
       - 跨源 / 非 GET / scope 外 → 放行（浏览器默认行为）
   · activate ：清理旧版本缓存 + clients.claim 立即接管
   · 更新方式 ：内容变更时递增 VERSION 常量即可（旧缓存自动清除）
   注 1：file:// 直开时本文件不注册（SW 仅限 https / localhost 安全上下文），
         两条使用方式互不干扰。
   注 2：照片（../IdolTalk-photos/）在 scope 之外。实测个别 Chromium 会把
         这类相对路径请求也派发到 fetch 事件，故 fetch 中显式放行照片，
         永不缓存（照片保持直读：file:// 本地文件 / http 网络实时）。
   ============================================================ */

const VERSION = '1.0.1';                    // 版本号：部署更新时递增
const CACHE_NAME = 'idoltalk-shell-' + VERSION;
const TTS_PORT = '8780';                    // 本机 tts_server 端口，永不缓存
const SCOPE_PATH = new URL(self.registration.scope).pathname; // 如 /IdolTalk-App/

/* 预缓存清单（相对 sw.js 所在目录）——应用壳全部本地资源 */
const SHELL = [
  './',
  'index.html',
  'manifest.webmanifest',
  'css/app.css',
  'js/data.js',
  'js/reply.js',
  'js/voice.js',
  'js/chat.js',
  'js/core.js',
  'js/boot.js',
  'js/screens/home.js',
  'js/screens/chat.js',
  'js/screens/cards.js',
  'js/screens/convos.js',
  'js/screens/me.js',
  'assets/icon.svg',
  'icons/icon-192.png',
  'icons/icon-512.png',
];

/* ---------- install：预缓存（逐项容错：单个文件失败不拖垮整个安装） ---------- */
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      await Promise.all(
        SHELL.map(async (url) => {
          try {
            const res = await fetch(new Request(url, { cache: 'reload' }));
            if (res && res.ok) await cache.put(url, res);
          } catch (e) {
            console.warn('[sw] 预缓存失败：', url, e);
          }
        })
      );
      await self.skipWaiting(); // 新版本立即进入待命
    })()
  );
});

/* ---------- activate：清理旧版本缓存 + 立即接管已开页面 ---------- */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

/* ---------- fetch：按资源类型分流 ---------- */
self.addEventListener('fetch', (event) => {
  const req = event.request;

  if (req.method !== 'GET') return; // POST（语音合成等）一律放行

  let url;
  try {
    url = new URL(req.url);
  } catch (e) {
    return;
  }

  // TTS 语音请求 → network-only：不 respondWith、不缓存（失败由调用方静默降级）
  if (url.port === TTS_PORT) return;

  // 跨源资源 → 放行
  if (url.origin !== self.location.origin) return;

  // 只接管本 SW scope 内的应用壳资源。
  // 注意：页面用 ../IdolTalk-photos/... 相对路径请求照片时，个别 Chromium
  // 版本会把 scope 外的这类请求也派发到 fetch 事件 —— 这里显式放行，
  // 照片永不进缓存（保持直读：file:// 本地文件 / http 网络实时）。
  if (!url.pathname.startsWith(SCOPE_PATH)) return;
  if (url.pathname.indexOf('/IdolTalk-photos/') !== -1) return;

  event.respondWith(cacheFirst(req));
});

/** cache-first：缓存命中即返回；未命中取网络并回填；断网时导航回退应用壳 */
async function cacheFirst(req) {
  const cached = await caches.match(req, { ignoreSearch: true });
  if (cached) return cached;

  try {
    const res = await fetch(req);
    if (res && res.ok && res.type === 'basic') {
      // 回填缓存（失败不影响本次返回）
      try {
        const cache = await caches.open(CACHE_NAME);
        await cache.put(req, res.clone());
      } catch (e) {
        console.warn('[sw] 回填缓存失败：', req.url, e);
      }
    }
    return res;
  } catch (err) {
    // 离线：导航请求回退到缓存的应用壳（保证离线可打开）
    if (req.mode === 'navigate') {
      const shell =
        (await caches.match('index.html')) || (await caches.match('./'));
      if (shell) return shell;
    }
    console.warn('[sw] 离线且未缓存：', req.url);
    return new Response('offline', { status: 504, statusText: 'Offline' });
  }
}
