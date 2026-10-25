// 포트핏 서비스 워커: 앱처럼 설치되게 하고, 인터넷이 끊겨도 마지막에 본 화면을 열 수 있게 함
// - 화면(HTML)과 데이터(JSON)는 항상 인터넷에서 새로 받고, 실패할 때만 저장해 둔 것을 씀 (항상 최신 유지)
// - 아이콘 같은 고정 파일은 저장해 둔 것을 먼저 씀
// - 로그인·AI 서버(Supabase) 같은 다른 사이트 요청은 건드리지 않음
const CACHE = "portfit-v12";
const CORE = ["./", "./index.html", "./privacy.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  const fresh = req.mode === "navigate" || /\.(html|json)$/.test(url.pathname) || url.pathname.endsWith("/");
  if (fresh) {
    e.respondWith(fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: true }).then(r => r || caches.match("./index.html"))));
  } else {
    e.respondWith(caches.match(req).then(r => r || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    })));
  }
});

// 규칙 알림: 서버가 보낸 알림을 화면에 띄우고, 누르면 포트핏 레버리지 탭을 엶
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "포트핏 알림", {
    body: d.body || "", icon: "icon-192.png", badge: "icon-192.png", tag: d.tag || "portfit", renotify: true, data: { url: d.url || "./#lev" },
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "./#lev";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(ws => {
    for (const w of ws) if ("focus" in w) { if (w.navigate) w.navigate(url).catch(() => {}); return w.focus(); }
    return self.clients.openWindow(url);
  }));
});
