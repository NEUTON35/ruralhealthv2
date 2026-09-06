(function () {
  const queueKey = 'ruralhealth.offlineMessages.v1';
  const agendaKey = 'ruralhealth.offlineAgenda.v1';
  let deferredInstallPrompt = null;

  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  }

  function isMobile() {
    return window.matchMedia('(max-width: 768px)').matches || /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  }

  function getQueue() {
    try {
      return JSON.parse(localStorage.getItem(queueKey) || '[]');
    } catch (error) {
      return [];
    }
  }

  function setQueue(messages) {
    localStorage.setItem(queueKey, JSON.stringify(messages));
  }

  function toast(message, tone) {
    const box = document.createElement('div');
    box.className = `fixed left-3 right-3 bottom-20 z-50 rounded-xl px-4 py-3 text-sm font-bold shadow-lg ${tone === 'warn' ? 'bg-yellow-100 text-yellow-900 border border-yellow-300' : 'bg-emerald-100 text-emerald-900 border border-emerald-300'}`;
    box.textContent = message;
    document.body.appendChild(box);
    window.setTimeout(() => box.remove(), 4500);
  }

  function queueMessage(form) {
    const input = form.querySelector('input[name="content"]');
    const file = form.querySelector('input[type="file"]');
    const content = (input && input.value.trim()) || '';
    if (!content) return false;
    if (file && file.files && file.files.length) {
      toast('El texto se puede guardar offline. Los archivos necesitan señal para enviarse.', 'warn');
      return false;
    }

    const messages = getQueue();
    messages.push({
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      chatId: form.dataset.chatId,
      action: form.getAttribute('action') || window.location.pathname,
      content,
      createdAt: new Date().toISOString()
    });
    setQueue(messages);
    input.value = '';
    toast('Mensaje guardado en este celular. Se enviará cuando vuelva la señal.', 'warn');
    return true;
  }

  async function syncQueuedMessages() {
    const messages = getQueue();
    if (!messages.length || !navigator.onLine) return;
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const remaining = [];

    for (const item of messages) {
      const body = new FormData();
      body.append('content', item.content);
      body.append('_csrf_token', csrf);
      try {
        const response = await fetch(item.action, {
          method: 'POST',
          body,
          headers: { 'X-CSRFToken': csrf },
          credentials: 'same-origin'
        });
        const redirectedToLogin = response.redirected && new URL(response.url).pathname.includes('/login');
        if (!response.ok || redirectedToLogin) remaining.push(item);
      } catch (error) {
        remaining.push(item);
      }
    }

    setQueue(remaining);
    if (messages.length !== remaining.length) {
      toast('Mensajes offline sincronizados.');
      if (window.location.pathname.startsWith('/patient/chat/')) {
        window.setTimeout(() => window.location.reload(), 700);
      }
    }
  }

  function currentRole() {
    const meta = document.querySelector('meta[name="user-role"]');
    return meta ? meta.content : '';
  }

  async function refreshOfflineAgenda() {
    if (!navigator.onLine) return;
    // La agenda sin conexión solo existe para pacientes. Antes se pedía desde
    // cualquier rol y en cada página, así que el servidor respondía 403 en cada
    // carga: peticiones desperdiciadas y una consola llena de errores que
    // ocultaban los problemas reales.
    if (currentRole() !== 'patient') return;

    try {
      const response = await fetch('/patient/api/offline_agenda', { credentials: 'same-origin' });
      if (!response.ok || (response.redirected && new URL(response.url).pathname.includes('/login'))) return;
      localStorage.setItem(agendaKey, JSON.stringify(await response.json()));
    } catch (error) {
      return;
    }
  }

  function showOfflineAgenda() {
    if (navigator.onLine || !window.location.pathname.startsWith('/patient/dashboard')) return;
    let data = null;
    try {
      data = JSON.parse(localStorage.getItem(agendaKey) || 'null');
    } catch (error) {
      data = null;
    }
    if (!data) return;
    const main = document.querySelector('main');
    if (!main || document.getElementById('offline-agenda-panel')) return;
    const panel = document.createElement('section');
    panel.id = 'offline-agenda-panel';
    panel.className = 'mb-4 bg-yellow-50 border-2 border-yellow-200 rounded-2xl p-4 text-yellow-950';

    const heading = document.createElement('h2');
    heading.className = 'font-black flex items-center gap-2';
    heading.textContent = 'Agenda offline';
    panel.appendChild(heading);

    const syncInfo = document.createElement('p');
    syncInfo.className = 'text-xs mb-2';
    const dateStr = new Date(data.synced_at).toLocaleString('es-CO');
    syncInfo.textContent = `Última sincronización: ${dateStr}`;
    panel.appendChild(syncInfo);

    const ul = document.createElement('ul');
    ul.className = 'space-y-2 text-sm';

    const appointmentsList = data.appointments || [];
    if (appointmentsList.length === 0) {
      const li = document.createElement('li');
      li.className = 'text-sm';
      li.textContent = 'No hay citas guardadas.';
      ul.appendChild(li);
    } else {
      appointmentsList.slice(0, 5).forEach((item) => {
        const li = document.createElement('li');
        li.className = 'bg-white/70 rounded-lg p-2';

        const b = document.createElement('b');
        b.textContent = `${item.date || ''} ${item.time || ''}`;
        li.appendChild(b);

        const textNode = document.createTextNode(` · ${item.doctor || ''} · ${item.status || ''}`);
        li.appendChild(textNode);

        ul.appendChild(li);
      });
    }
    panel.appendChild(ul);
    main.prepend(panel);
  }

  async function sendFormOrQueue(form) {
    const file = form.querySelector('input[type="file"]');
    if (file && file.files && file.files.length) return false;
    const input = form.querySelector('input[name="content"]');
    if (!input || !input.value.trim()) return false;

    const body = new FormData(form);
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    if (csrf && !body.has('_csrf_token')) body.append('_csrf_token', csrf);

    try {
      const response = await fetch(form.getAttribute('action') || window.location.pathname, {
        method: 'POST',
        body,
        headers: { 'X-CSRFToken': csrf },
        credentials: 'same-origin'
      });
      const redirectedToLogin = response.redirected && new URL(response.url).pathname.includes('/login');
      if (!response.ok || redirectedToLogin) throw new Error('sync failed');
      input.value = '';
      window.location.reload();
    } catch (error) {
      queueMessage(form);
    }
    return true;
  }

  function setupInstallButton() {
    const button = document.getElementById('install-home-screen');
    if (!button || isStandalone() || !isMobile()) return;

    const showButton = () => {
      button.classList.remove('hidden');
      button.classList.add('flex');
      if (window.lucide) window.lucide.createIcons();
    };

    window.addEventListener('beforeinstallprompt', (event) => {
      event.preventDefault();
      deferredInstallPrompt = event;
      showButton();
    });

    if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) {
      showButton();
    }

    button.addEventListener('click', async () => {
      if (deferredInstallPrompt) {
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice.catch(() => null);
        deferredInstallPrompt = null;
        button.classList.add('hidden');
        button.classList.remove('flex');
        return;
      }
      toast('En iPhone: toca Compartir y luego “Agregar a pantalla de inicio”.', 'warn');
    });
  }

  function setupOfflineForms() {
    document.querySelectorAll('[data-offline-chat-form]').forEach((form) => {
      form.addEventListener('submit', (event) => {
        if (!navigator.onLine) {
          if (queueMessage(form)) event.preventDefault();
          return;
        }
        event.preventDefault();
        sendFormOrQueue(form).then((handled) => {
          if (!handled) form.submit();
        });
      });
    });
    window.addEventListener('online', syncQueuedMessages);
    window.addEventListener('online', refreshOfflineAgenda);
    syncQueuedMessages();
    refreshOfflineAgenda();
    showOfflineAgenda();
  }

  /**
   * Registro del Service Worker.
   *
   * No estaba: el archivo existía y se servía, pero ninguna página lo registraba,
   * así que el modo sin conexión anunciado nunca llegaba a activarse.
   */
  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
        .then((registration) => {
          // Si hay una versión nueva esperando, se activa de inmediato: una
          // corrección de privacidad en el Service Worker no debe quedar
          // retenida hasta que el usuario cierre todas las pestañas.
          if (registration.waiting) {
            registration.waiting.postMessage({ type: 'SKIP_WAITING' });
          }
        })
        .catch(() => { /* sin Service Worker la aplicación sigue funcionando con red */ });
    });
  }

  /**
   * Purga de los datos clínicos guardados en este dispositivo.
   *
   * El equipo de un puesto de salud lo usan varias personas. La cola de mensajes
   * sin enviar y la agenda sin conexión contienen datos del paciente anterior, y
   * ni una ni otra se borraban al cerrar sesión: quedaban en `localStorage`
   * indefinidamente, legibles por quien usara el navegador después.
   *
   * Se ejecuta al llegar a `/login?purge=1`, que es a donde redirige el cierre
   * de sesión.
   */
  function purgeLocalClinicalData() {
    try {
      localStorage.removeItem(queueKey);
      localStorage.removeItem(agendaKey);
    } catch (error) {
      /* almacenamiento no disponible: no hay nada que purgar */
    }
    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: 'PURGE_CACHES' });
    }
  }

  function maybePurge() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('purge') === '1' || window.location.pathname === '/login') {
      // Se purga también al llegar al login por cualquier vía —sesión caducada,
      // acceso directo—: si no hay sesión activa, no debe quedar dato clínico
      // en este dispositivo.
      purgeLocalClinicalData();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    maybePurge();
    setupInstallButton();
    setupOfflineForms();
  });

  registerServiceWorker();
})();
