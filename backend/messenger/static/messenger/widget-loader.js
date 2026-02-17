/**
 * Ленивая загрузка виджета мессенджера.
 * Подключает widget.css и widget.js после первого скролла или через N секунд.
 *
 * Использование:
 *   Обычная загрузка (сразу):
 *   <script src="/static/messenger/widget-loader.js" data-widget-token="TOKEN"></script>
 *
 *   Через 5 секунд:
 *   <script src="/static/messenger/widget-loader.js" data-widget-token="TOKEN" data-load-after="5"></script>
 *
 *   При первом скролле:
 *   <script src="/static/messenger/widget-loader.js" data-widget-token="TOKEN" data-load-on-scroll="1"></script>
 *
 *   Скролл ИЛИ через 10 сек (что раньше):
 *   <script src="/static/messenger/widget-loader.js" data-widget-token="TOKEN" data-load-on-scroll="1" data-load-after="10"></script>
 */
(function() {
  'use strict';
  var scriptTag = document.currentScript;
  if (!scriptTag) return;
  var token = scriptTag.getAttribute('data-widget-token');
  if (!token) {
    console.warn('[MessengerWidgetLoader] data-widget-token is required');
    return;
  }
  var loadAfter = parseInt(scriptTag.getAttribute('data-load-after'), 10);
  var loadOnScroll = scriptTag.getAttribute('data-load-on-scroll');
  var loadOnScrollEnabled = loadOnScroll === '1' || loadOnScroll === 'true' || loadOnScroll === 'yes';
  var baseUrl = (scriptTag.src || '').replace(/\/[^/]*$/, '');
  var loaded = false;

  function loadWidget() {
    if (loaded) return;
    loaded = true;
    window.removeEventListener('scroll', onScroll, { passive: true });
    window.removeEventListener('touchmove', onScroll, { passive: true });

    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = baseUrl + '/widget.css';
    document.head.appendChild(link);

    var s = document.createElement('script');
    s.src = baseUrl + '/widget.js';
    s.setAttribute('data-widget-token', token);
    s.async = true;
    document.body.appendChild(s);
  }

  function onScroll() {
    loadWidget();
  }

  if (!loadOnScrollEnabled && (typeof loadAfter !== 'number' || loadAfter < 0)) {
    loadWidget();
    return;
  }
  if (loadOnScrollEnabled) {
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('touchmove', onScroll, { passive: true });
  }
  if (typeof loadAfter === 'number' && loadAfter > 0) {
    setTimeout(loadWidget, loadAfter * 1000);
  }
})();
