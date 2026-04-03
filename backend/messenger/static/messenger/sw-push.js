/**
 * Service Worker для browser push-уведомлений мессенджера.
 * Аналог Chatwoot Service Worker.
 *
 * Обрабатывает push-события и показывает нативные уведомления ОС,
 * даже когда вкладка CRM закрыта.
 */

self.addEventListener('push', function(event) {
  if (!event.data) return;

  let data;
  try {
    data = event.data.json();
  } catch (e) {
    data = { title: 'Новое сообщение', body: event.data.text() };
  }

  const title = data.title || 'Мессенджер';
  const options = {
    body: data.body || '',
    icon: data.icon || '/static/messenger/icon-chat.png',
    badge: '/static/messenger/icon-badge.png',
    tag: data.tag || 'messenger',
    renotify: true,
    requireInteraction: false,
    data: {
      url: data.url || '/messenger/',
    },
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();

  const url = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : '/messenger/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      // Если уже есть открытая вкладка — фокусируемся на ней
      for (var i = 0; i < clientList.length; i++) {
        var client = clientList[i];
        if (client.url.includes('/messenger') && 'focus' in client) {
          client.focus();
          client.navigate(url);
          return;
        }
      }
      // Иначе открываем новую вкладку
      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});
