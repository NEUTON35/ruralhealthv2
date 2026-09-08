/**
 * Service Worker de RuralHealth Connect.
 *
 * PRINCIPIO: ninguna respuesta autenticada se guarda en disco.
 *
 * La versión anterior cacheaba toda respuesta HTML con estado 200, incluidos
 * chats clínicos, órdenes médicas e historias de pacientes. La aplicación envía
 * `Cache-Control: no-store` en las respuestas autenticadas y este archivo lo
 * ignoraba deliberadamente.
 *
 * En un puesto de salud rural el dispositivo es compartido. Eso significaba que
 * la siguiente persona que abriera el navegador podía recuperar sin conexión -y
 * sin haber iniciado sesión- las páginas del paciente anterior, porque
 * CacheStorage sobrevive al cierre de sesión y al cierre del navegador.
 *
 * Qué se conserva del modo sin conexión: la aplicación sigue instalable y sigue
 * mostrando una pantalla útil sin red. Lo que ya no ocurre es que los datos
 * clínicos queden en el disco del equipo.
 */

const CACHE_VERSION = 'v7';
const SHELL_CACHE = `ruralhealth-shell-${CACHE_VERSION}`;

/**
 * Solo recursos públicos, sin datos de ningún paciente.
 * `/login` y `/offline` no requieren sesión, así que cachearlos es inocuo.
 *
 * Desde v7 se precachean también la hoja de estilos y las librerías, que antes
 * venían de CDN externos. Ese era el motivo de que la aplicación apareciera sin
 * ningún estilo cuando el CDN no era alcanzable, que en zona rural no es un
 * caso raro. Ahora todo se sirve desde este servidor y queda disponible sin
 * conexión.
 */
const APP_SHELL = [
  '/login',
  '/offline',
  '/manifest.json',
  '/static/icon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/pwa.js',
  '/static/location_picker.js',
  '/static/css/app.css',
  '/static/css/fonts.css',
  '/static/vendor/lucide.min.js',
  '/static/vendor/leaflet.css',
  '/static/vendor/leaflet.js',
  '/static/vendor/fonts/inter-400-latin.woff2',
  '/static/vendor/fonts/inter-500-latin.woff2',
  '/static/vendor/fonts/inter-600-latin.woff2',
  '/static/vendor/fonts/inter-700-latin.woff2',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      // `allSettled` evita que un solo recurso ausente impida la instalación
      // completa del Service Worker.
      .then((cache) => Promise.allSettled(APP_SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

/**
 * Purga total. La invoca la página al cerrar sesión.
 * Es la garantía de que en un equipo compartido no queda rastro de la sesión
 * anterior en el almacenamiento del navegador.
 */
async function purgeAllCaches() {
  const keys = await caches.keys();
  await Promise.all(keys.map((key) => caches.delete(key)));
  const cache = await caches.open(SHELL_CACHE);
  await Promise.allSettled(APP_SHELL.map((url) => cache.add(url)));
}

self.addEventListener('message', (event) => {
  if (!event.data) return;

  if (event.data.type === 'PURGE_CACHES') {
    event.waitUntil(purgeAllCaches());
  }
  if (event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

/** Rutas públicas: nada de lo que sirven depende de quién esté autenticado. */
const PUBLIC_PATHS = new Set([
  '/login', '/register', '/offline', '/manifest.json',
  '/terminos', '/privacidad', '/transparencia', '/socio', '/recuperar',
]);

function isPublicPath(pathname) {
  return PUBLIC_PATHS.has(pathname) || pathname.startsWith('/static/');
}

/**
 * Una respuesta se considera privada si el servidor lo indica.
 * Se respeta `Cache-Control`, en lugar de decidirlo aquí: el servidor es quien
 * sabe si la respuesta llevaba datos de un paciente.
 */
function isPrivateResponse(response) {
  const cacheControl = response.headers.get('Cache-Control') || '';
  return /no-store|private/i.test(cacheControl);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;

  // Solo GET: un POST cacheado podría reenviar una orden médica o una entrega.
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== location.origin) return;

  // Los archivos subidos son documentos clínicos y comprobantes: nunca se cachean.
  if (url.pathname.startsWith('/uploads/')) return;

  // Estáticos públicos: primero la caché, que es lo que hace utilizable la
  // aplicación con conectividad intermitente.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => cached))
    );
    return;
  }

  // Páginas públicas: red primero, con la copia cacheada como respaldo.
  if (isPublicPath(url.pathname)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok && !isPrivateResponse(response)) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match('/offline')))
    );
    return;
  }

  // Todo lo demás está autenticado: se pide siempre a la red y **no se guarda**.
  // Sin conexión se muestra la pantalla de modo sin conexión, que no revela
  // ningún dato. Es la diferencia entre una aplicación que funciona mal sin red
  // y una que filtra historias clínicas en un equipo compartido.
  event.respondWith(
    fetch(request).catch(() => caches.match('/offline').then((offline) => (
      offline || new Response(
        '<!doctype html><meta charset="utf-8">'
        + '<title>Sin conexion</title>'
        + '<body style="font-family:system-ui;padding:2rem;text-align:center">'
        + '<h1>Sin conexion</h1>'
        + '<p>Esta pagina contiene informacion clinica y no se guarda en el '
        + 'dispositivo por seguridad. Reconectate para verla.</p></body>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
      )
    )))
  );
});
