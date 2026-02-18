/**
 * Messenger Widget - минимальный production-safe JS клиент для Widget API
 * 
 * Использование:
 * <script src="/static/messenger/widget.js" data-widget-token="YOUR_TOKEN"></script>
 */
(function() {
  'use strict';

  // Конфигурация
  const CONFIG = {
    POLL_INTERVAL: 3000, // 3 секунды
    API_BASE_URL: '', // Относительный путь (текущий домен)
    MAX_MESSAGE_LENGTH: 2000,
    STORAGE_PREFIX: 'messenger_widget::',
  };

  /**
   * Генерация UUIDv4
   */
  function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  /**
   * Класс MessengerWidget
   */
  class MessengerWidget {
    constructor(widgetToken) {
      if (!widgetToken) {
        console.error('[MessengerWidget] widget_token is required');
        return;
      }

      this.widgetToken = widgetToken;
      this.sessionToken = null;
      this.sinceId = null;
      this.contactId = null;
      this.pollInterval = null;
      this.eventSource = null;
      this.isOpen = false;
      this.isSending = false;
      this.receivedMessageIds = new Set(); // Anti-duplicate: Set для отслеживания полученных сообщений
      this.typingSendTimer = null;
      this.offlineMode = false;
      this.offlineMessage = '';
      this.initialMessages = [];
      this.ratingRequested = false;
      this.ratingType = 'stars';
      this.ratingMaxScore = 5;
      this.title = 'Чат с поддержкой';
      this.greeting = '';
      this.color = '#01948E';
      this.unreadCount = 0;
      this.privacyUrl = '';
      this.privacyText = '';
      this.captchaRequired = false;
      this.captchaToken = '';
      this.captchaQuestion = '';
      this.sseEnabled = true;
      this.attachmentsEnabled = true;
      this.maxFileSizeBytes = 5 * 1024 * 1024;
      this.allowedContentTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'];
      this.pendingFiles = [];
      this.fileInput = null;
      this.pendingFilesEl = null;
      this.markReadTimer = null;

      // DOM элементы (будут созданы в render)
      this.button = null;
      this.offlineBanner = null;
      this.ratingBlock = null;
      this.ratingForm = null;
      this.popup = null;
      this.messagesContainer = null;
      this.typingIndicator = null;
      this.input = null;
      this.sendButton = null;
      this.closeButton = null;
      this.captchaRow = null;
      this.captchaInput = null;
    }

    /**
     * Экранирование HTML
     */
    escapeHtml(str) {
      return String(str || '').replace(/[&<>"']/g, function(ch) {
        switch (ch) {
          case '&': return '&amp;';
          case '<': return '&lt;';
          case '>': return '&gt;';
          case '"': return '&quot;';
          case "'": return '&#39;';
          default: return ch;
        }
      });
    }

    /**
     * Рендер простого Markdown-подобного форматирования:
     * - **жирный**;
     * - ссылки http(s)://...;
     * - переводы строк.
     * HTML всегда предварительно экранируется.
     */
    renderFormattedBody(text) {
      let html = this.escapeHtml(text || '');
      // **bold**
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      // ссылки
      html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
      // переводы строк
      html = html.replace(/\r\n/g, '\n').replace(/\n/g, '<br>');
      return html;
    }

    /**
     * Получить ключ для localStorage с namespace по widget_token
     */
    _storageKey(key) {
      return CONFIG.STORAGE_PREFIX + this.widgetToken + '::' + key;
    }

    /**
     * Загрузить данные из localStorage
     */
    loadFromStorage() {
      try {
        this.sessionToken = localStorage.getItem(this._storageKey('session_token'));
        const sinceIdStr = localStorage.getItem(this._storageKey('since_id'));
        this.sinceId = sinceIdStr ? parseInt(sinceIdStr, 10) : null;
        this.contactId = localStorage.getItem(this._storageKey('contact_id'));

        // Если нет contact_id - генерируем и сохраняем
        if (!this.contactId) {
          this.contactId = generateUUID();
          localStorage.setItem(this._storageKey('contact_id'), this.contactId);
        }
      } catch (e) {
        console.error('[MessengerWidget] Error loading from storage:', e);
      }
    }

    /**
     * Сохранить данные в localStorage
     */
    saveToStorage() {
      try {
        if (this.sessionToken) {
          localStorage.setItem(this._storageKey('session_token'), this.sessionToken);
        }
        if (this.sinceId !== null) {
          localStorage.setItem(this._storageKey('since_id'), String(this.sinceId));
        }
        if (this.contactId) {
          localStorage.setItem(this._storageKey('contact_id'), this.contactId);
        }
      } catch (e) {
        console.error('[MessengerWidget] Error saving to storage:', e);
      }
    }

    /**
     * Очистить данные из localStorage
     */
    clearStorage() {
      try {
        localStorage.removeItem(this._storageKey('session_token'));
        localStorage.removeItem(this._storageKey('since_id'));
        // contact_id НЕ удаляем - он должен быть стабильным
      } catch (e) {
        console.error('[MessengerWidget] Error clearing storage:', e);
      }
    }

    /**
     * Инициализация виджета
     */
    async init() {
      // Загрузить сохранённые данные
      this.loadFromStorage();

      // Если нет сессии - bootstrap
      if (!this.sessionToken) {
        const success = await this.bootstrap();
        if (!success) {
          // Bootstrap не удался (404) - виджет не активируется
          return;
        }
      }

      // Рендерить UI
      this.render();

      // Реалтайм (SSE) с fallback на poll
      if (!this.sseEnabled || !this.startRealtime()) {
        this.startPolling();
      }
    }

    /**
     * Bootstrap: создание/получение сессии виджета
     */
    async bootstrap() {
      try {
        const response = await fetch(CONFIG.API_BASE_URL + '/api/widget/bootstrap/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            widget_token: this.widgetToken,
            contact_external_id: this.contactId,
          }),
        });

        if (response.status === 404) {
          // Invalid/inactive inbox - виджет не активируется
          console.warn('[MessengerWidget] Bootstrap failed: invalid widget_token or inactive inbox');
          return false;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Bootstrap failed:', response.status, response.statusText);
          return false;
        }

        const data = await response.json();
        this.sessionToken = data.widget_session_token;
        this.sinceId = null; // Сбросить since_id при bootstrap
        this.offlineMode = data.offline_mode === true;
        this.offlineMessage = data.offline_message || '';
        this.title = data.title || 'Чат с поддержкой';
        this.greeting = data.greeting || '';
        this.color = data.color || '#01948E';
        this.privacyUrl = data.privacy_url || '';
        this.privacyText = data.privacy_text || '';
        this.captchaRequired = data.captcha_required === true;
        this.captchaToken = data.captcha_token || '';
        this.captchaQuestion = data.captcha_question || '';
        if (data.sse_enabled !== undefined) this.sseEnabled = !!data.sse_enabled;
        if (data.attachments_enabled !== undefined) this.attachmentsEnabled = !!data.attachments_enabled;
        if (typeof data.max_file_size_bytes === 'number') this.maxFileSizeBytes = data.max_file_size_bytes;
        if (Array.isArray(data.allowed_content_types)) this.allowedContentTypes = data.allowed_content_types;

        // Обработать initial_messages
        if (data.initial_messages && Array.isArray(data.initial_messages)) {
          // Найти максимальный ID для since_id
          let maxId = null;
          for (const msg of data.initial_messages) {
            if (msg.id && (maxId === null || msg.id > maxId)) {
              maxId = msg.id;
            }
            // Добавить в Set для anti-duplicate
            if (msg.id) {
              this.receivedMessageIds.add(msg.id);
            }
          }
          this.sinceId = maxId;
          this.initialMessages = data.initial_messages;
        } else {
          this.initialMessages = [];
        }

        // Сохранить в localStorage
        this.saveToStorage();

        return true;
      } catch (error) {
        console.error('[MessengerWidget] Bootstrap error:', error);
        return false;
      }
    }

    /**
     * Проверка допустимости файла по размеру и типу
     */
    isFileAllowed(file) {
      if (file.size > this.maxFileSizeBytes) return false;
      const ct = (file.type || '').toLowerCase();
      if (!ct) return true;
      for (const allowed of this.allowedContentTypes) {
        const a = allowed.toLowerCase();
        if (a === ct) return true;
        if (a === 'image/*' && ct.indexOf('image/') === 0) return true;
      }
      return false;
    }

    /**
     * Отправка сообщения (текст и/или файлы)
     */
    async sendMessage(body, files) {
      const trimmedBody = (body || '').trim();
      const fileList = files && files.length ? Array.from(files) : [];
      if (!trimmedBody && !fileList.length) {
        return false;
      }
      if (trimmedBody.length > CONFIG.MAX_MESSAGE_LENGTH) {
        console.warn('[MessengerWidget] Message too long, max length:', CONFIG.MAX_MESSAGE_LENGTH);
        return false;
      }

      if (this.isSending) {
        return false;
      }

      if (!this.sessionToken) {
        const success = await this.bootstrap();
        if (!success) return false;
      }

      this.isSending = true;
      this.updateSendButton();

      try {
        let response;
        if (fileList.length > 0 && this.attachmentsEnabled) {
          const formData = new FormData();
          formData.append('widget_token', this.widgetToken);
          formData.append('widget_session_token', this.sessionToken);
          formData.append('body', trimmedBody);
          if (this.captchaRequired && this.captchaToken && this.captchaInput && this.captchaInput.value) {
            formData.append('captcha_token', this.captchaToken);
            formData.append('captcha_answer', this.captchaInput.value.trim());
          }
          fileList.forEach((f, i) => {
            formData.append('files', f);
          });
          response = await fetch(CONFIG.API_BASE_URL + '/api/widget/send/', {
            method: 'POST',
            body: formData,
          });
        } else {
          response = await fetch(CONFIG.API_BASE_URL + '/api/widget/send/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              widget_token: this.widgetToken,
              widget_session_token: this.sessionToken,
              body: trimmedBody,
              captcha_token: (this.captchaRequired ? this.captchaToken : ''),
              captcha_answer: (this.captchaRequired && this.captchaInput ? this.captchaInput.value.trim() : ''),
            }),
          });
        }

        if (response.status === 401) {
          this.clearStorage();
          this.sessionToken = null;
          await this.bootstrap();
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        if (response.status === 403) {
          this.clearStorage();
          this.sessionToken = null;
          this.stopPolling();
          this.stopRealtime();
          await this.bootstrap();
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Send failed:', response.status, response.statusText);
          // Если сервер требует капчу — покажем строку ввода
          try {
            const errData = await response.json();
            if (errData && errData.captcha_required === true) {
              this.captchaRequired = true;
              this.renderCaptchaRow();
            }
          } catch (e) {}
          this.isSending = false;
          this.updateSendButton();
          return false;
        }

        const data = await response.json();
        if (this.captchaInput) this.captchaInput.value = '';
        const attachmentsPayload = Array.isArray(data.attachments) ? data.attachments : [];
        this.addMessageToUI({
          id: data.id,
          body: trimmedBody,
          direction: 'in',
          created_at: data.created_at,
          attachments: attachmentsPayload,
        });

        if (this.input) this.input.value = '';
        this.pendingFiles = [];
        this.renderPendingFiles();
        this.isSending = false;
        this.updateSendButton();
        return true;
      } catch (error) {
        console.error('[MessengerWidget] Send error:', error);
        this.isSending = false;
        this.updateSendButton();
        return false;
      }
    }

    renderCaptchaRow() {
      if (!this.captchaRequired) return;
      if (!this.captchaRow) return;
      this.captchaRow.classList.remove('hidden');
      if (this.captchaQuestion && this.captchaRow.querySelector('.messenger-widget-captcha-q')) {
        this.captchaRow.querySelector('.messenger-widget-captcha-q').textContent = this.captchaQuestion;
      }
    }

    /**
     * Poll: получение новых сообщений
     */
    async poll() {
      if (!this.sessionToken) {
        return;
      }

      try {
        const params = new URLSearchParams({
          widget_token: this.widgetToken,
          widget_session_token: this.sessionToken,
        });
        if (this.sinceId !== null) {
          params.append('since_id', String(this.sinceId));
        }

        const response = await fetch(CONFIG.API_BASE_URL + '/api/widget/poll/?' + params.toString(), {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
          },
        });

        if (response.status === 401) {
          // Expired session - re-bootstrap и повторить poll
          this.clearStorage();
          this.sessionToken = null;
          const success = await this.bootstrap();
          if (success) {
            // Повторить poll после re-bootstrap
            setTimeout(() => this.poll(), 100);
          }
          return;
        }

        if (response.status === 403) {
          // Mismatch inbox - re-bootstrap и stop
          this.clearStorage();
          this.sessionToken = null;
          this.stopPolling();
          this.stopRealtime();
          await this.bootstrap();
          return;
        }

        if (!response.ok) {
          console.error('[MessengerWidget] Poll failed:', response.status, response.statusText);
          return;
        }

        const data = await response.json();
        if (data.operator_typing !== undefined) {
          this.setOperatorTypingVisible(data.operator_typing === true);
        }
        if (data.rating_requested === true) {
          this.ratingRequested = true;
          this.ratingType = data.rating_type || 'stars';
          this.ratingMaxScore = typeof data.rating_max_score === 'number' ? data.rating_max_score : 5;
          this.showRatingBlock();
        }
        if (data.messages && Array.isArray(data.messages)) {
          // Фильтруем дубликаты через Set
          const newMessages = data.messages.filter(msg => {
            if (!msg.id) return false;
            if (this.receivedMessageIds.has(msg.id)) {
              return false; // Уже получено
            }
            this.receivedMessageIds.add(msg.id);
            return true;
          });

          // Обновить since_id
          for (const msg of newMessages) {
            if (msg.id && (this.sinceId === null || msg.id > this.sinceId)) {
              this.sinceId = msg.id;
            }
          }

          // Сохранить since_id
          if (this.sinceId !== null) {
            localStorage.setItem(this._storageKey('since_id'), String(this.sinceId));
          }

          // Добавить сообщения в UI
          for (const msg of newMessages) {
            this.addMessageToUI(msg);
          }

          this.scheduleMarkOutgoingRead();
        }
      } catch (error) {
        console.error('[MessengerWidget] Poll error:', error);
      }
    }

    /**
     * Начать polling
     */
    startPolling() {
      if (this.pollInterval) {
        return; // Уже запущен
      }
      if (!this.sessionToken) {
        return; // Нет сессии
      }
      // Первый poll сразу
      this.poll();
      // Затем каждые 3 секунды
      this.pollInterval = setInterval(() => {
        this.poll();
      }, CONFIG.POLL_INTERVAL);
    }

    /**
     * Остановить polling
     */
    stopPolling() {
      if (this.pollInterval) {
        clearInterval(this.pollInterval);
        this.pollInterval = null;
      }
    }

    startRealtime() {
      if (this.eventSource) return true;
      if (!this.sessionToken) return false;
      if (typeof EventSource === 'undefined') return false;

      const params = new URLSearchParams({
        widget_token: this.widgetToken,
        widget_session_token: this.sessionToken,
      });
      if (this.sinceId !== null) {
        params.append('since_id', String(this.sinceId));
      }

      try {
        const es = new EventSource(CONFIG.API_BASE_URL + '/api/widget/stream/?' + params.toString());
        this.eventSource = es;

        es.addEventListener('update', (e) => {
          try {
            const data = JSON.parse(e.data || '{}');
            if (data.operator_typing !== undefined) {
              this.setOperatorTypingVisible(data.operator_typing === true);
            }
            if (data.rating_requested === true) {
              this.ratingRequested = true;
              this.ratingType = data.rating_type || 'stars';
              this.ratingMaxScore = typeof data.rating_max_score === 'number' ? data.rating_max_score : 5;
              this.showRatingBlock();
            }
            if (Array.isArray(data.messages)) {
              const newMessages = data.messages.filter(msg => {
                if (!msg.id) return false;
                if (this.receivedMessageIds.has(msg.id)) return false;
                this.receivedMessageIds.add(msg.id);
                return true;
              });
              for (const msg of newMessages) {
                if (msg.id && (this.sinceId === null || msg.id > this.sinceId)) {
                  this.sinceId = msg.id;
                }
                this.addMessageToUI(msg);
              }
              if (this.sinceId !== null) {
                localStorage.setItem(this._storageKey('since_id'), String(this.sinceId));
              }
              if (newMessages.length > 0) {
                this.scheduleMarkOutgoingRead();
              }
            }
          } catch (err) {
            // ignore
          }
        });

        es.onerror = () => {
          // SSE недоступен/оборван — fallback на poll
          this.stopRealtime();
          this.startPolling();
        };
        return true;
      } catch (e) {
        this.eventSource = null;
        return false;
      }
    }

    stopRealtime() {
      if (this.eventSource) {
        try { this.eventSource.close(); } catch (e) {}
        this.eventSource = null;
      }
    }

    /**
     * Открыть popup
     */
    open() {
      if (!this.popup) {
        return;
      }
      this.isOpen = true;
      this.unreadCount = 0;
      this.updateBadge && this.updateBadge();
      this.popup.classList.add('messenger-widget-popup-open');
      // Фокус на поле ввода
      if (this.input) {
        setTimeout(() => this.input.focus(), 100);
      }
      // Автоскролл вниз
      this.scrollToBottom();
      this.scheduleMarkOutgoingRead();
    }

    /**
     * Закрыть popup
     */
    close() {
      if (!this.popup) {
        return;
      }
      this.isOpen = false;
      this.popup.classList.remove('messenger-widget-popup-open');
    }

    /**
     * Показать/скрыть кнопку запуска виджета (launcher)
     */
    showLauncher() {
      if (this.button) {
        this.button.style.display = '';
      }
    }

    hideLauncher() {
      if (this.button) {
        this.button.style.display = 'none';
      }
    }

    toggle() {
      if (this.isOpen) this.close();
      else this.open();
    }

    /**
     * Скролл вниз ленты сообщений
     */
    scrollToBottom() {
      if (this.messagesContainer) {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
      }
    }

    updateBadge() {
      if (!this.badgeEl) return;
      if (this.unreadCount > 0 && !this.isOpen) {
        this.badgeEl.textContent = this.unreadCount > 9 ? '9+' : String(this.unreadCount);
        this.badgeEl.style.display = 'flex';
      } else {
        this.badgeEl.style.display = 'none';
      }
    }

    /**
     * Обновить состояние кнопки отправки
     */
    updateSendButton() {
      if (!this.sendButton) {
        return;
      }
      if (this.isSending) {
        this.sendButton.disabled = true;
        this.sendButton.textContent = 'Отправка...';
      } else {
        this.sendButton.disabled = false;
        this.sendButton.textContent = 'Отправить';
      }
    }

    /**
     * Добавить сообщение в UI
     */
    addMessageToUI(message) {
      if (!this.messagesContainer) {
        return;
      }

      const messageEl = document.createElement('div');
      messageEl.className = 'messenger-widget-message';
      messageEl.classList.add('messenger-widget-message-' + message.direction);
      if (typeof message.id === 'number') {
        messageEl.setAttribute('data-message-id', String(message.id));
      }

      const bodyEl = document.createElement('div');
      bodyEl.className = 'messenger-widget-message-body';
      bodyEl.innerHTML = this.renderFormattedBody(message.body || '');
      messageEl.appendChild(bodyEl);

      const attachments = message.attachments || [];
      if (attachments.length > 0) {
        const attWrap = document.createElement('div');
        attWrap.className = 'messenger-widget-message-attachments';
        attachments.forEach(att => {
          const isImage = (att.content_type || '').indexOf('image/') === 0;
          const link = document.createElement('a');
          link.href = att.url || '#';
          link.target = '_blank';
          link.rel = 'noopener';
          link.className = 'messenger-widget-attachment';
          if (isImage && att.url) {
            const img = document.createElement('img');
            img.src = att.url;
            img.alt = att.original_name || '';
            img.className = 'messenger-widget-attachment-img';
            link.appendChild(img);
          } else {
            link.textContent = att.original_name || 'Файл';
          }
          attWrap.appendChild(link);
        });
        messageEl.appendChild(attWrap);
      }

      const metaEl = document.createElement('div');
      metaEl.className = 'messenger-widget-message-meta';

      const timeEl = document.createElement('span');
      timeEl.className = 'messenger-widget-message-time';
      if (message.created_at) {
        const date = new Date(message.created_at);
        timeEl.textContent = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      }
      metaEl.appendChild(timeEl);

      if (message.direction === 'out') {
        const statusEl = document.createElement('span');
        statusEl.className = 'messenger-widget-message-status';
        if (message.read_at) {
          statusEl.textContent = '✓✓';
          statusEl.title = 'Прочитано';
        } else {
          statusEl.textContent = '✓';
          statusEl.title = 'Доставлено';
        }
        metaEl.appendChild(statusEl);
      }

      messageEl.appendChild(metaEl);
      this.messagesContainer.appendChild(messageEl);

      this.scrollToBottom();

      // Непрочитанные: считаем только исходящие сообщения, когда виджет закрыт
      if (message.direction === 'out' && !this.isOpen) {
        this.unreadCount = (this.unreadCount || 0) + 1;
        this.updateBadge();
      }
    }

    markOutgoingMessagesRead() {
      if (!this.sessionToken || !this.messagesContainer) return;
      const outNodes = this.messagesContainer.querySelectorAll('.messenger-widget-message-out[data-message-id]');
      let maxId = null;
      outNodes.forEach(el => {
        const raw = el.getAttribute('data-message-id');
        const id = raw ? parseInt(raw, 10) : NaN;
        if (!isNaN(id) && (maxId === null || id > maxId)) {
          maxId = id;
        }
      });
      if (maxId === null) return;

      fetch(CONFIG.API_BASE_URL + '/api/widget/mark_read/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          widget_token: this.widgetToken,
          widget_session_token: this.sessionToken,
          last_message_id: maxId,
        }),
      }).catch(() => {});
    }

    scheduleMarkOutgoingRead() {
      if (!this.sessionToken) return;
      if (!this.isOpen) return;
      if (this.markReadTimer) {
        clearTimeout(this.markReadTimer);
      }
      this.markReadTimer = setTimeout(() => {
        this.markOutgoingMessagesRead();
      }, 500);
    }

    setOperatorTypingVisible(visible) {
      if (!this.typingIndicator) return;
      if (visible) {
        this.typingIndicator.classList.remove('messenger-widget-typing-hidden');
      } else {
        this.typingIndicator.classList.add('messenger-widget-typing-hidden');
      }
    }

    sendContactTyping() {
      if (!this.sessionToken) return;
      fetch(CONFIG.API_BASE_URL + '/api/widget/typing/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          widget_token: this.widgetToken,
          widget_session_token: this.sessionToken,
        }),
      }).catch(() => {});
    }

    renderPendingFiles() {
      if (!this.pendingFilesEl) return;
      this.pendingFilesEl.innerHTML = '';
      this.pendingFiles.forEach((file, index) => {
        const span = document.createElement('span');
        span.className = 'messenger-widget-pending-file';
        span.textContent = file.name || 'Файл';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'messenger-widget-pending-file-remove';
        remove.textContent = '×';
        remove.addEventListener('click', () => {
          this.pendingFiles.splice(index, 1);
          this.renderPendingFiles();
        });
        span.appendChild(remove);
        this.pendingFilesEl.appendChild(span);
      });
    }

    showRatingBlock() {
      if (!this.ratingBlock) return;
      this.ratingBlock.classList.remove('messenger-widget-rating-hidden');
      if (this.ratingForm) this.ratingForm.classList.add('messenger-widget-rating-hidden');
      this.buildRatingButtons();
    }

    hideRatingBlock() {
      if (this.ratingBlock) this.ratingBlock.classList.add('messenger-widget-rating-hidden');
      if (this.ratingForm) this.ratingForm.classList.remove('messenger-widget-rating-hidden');
      this.ratingRequested = false;
    }

    buildRatingButtons() {
      const wrap = this.ratingBlock.querySelector('.messenger-widget-rating-buttons');
      if (!wrap) return;
      wrap.innerHTML = '';
      const minScore = this.ratingType === 'nps' ? 0 : 1;
      for (let i = minScore; i <= this.ratingMaxScore; i++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'messenger-widget-rating-btn';
        btn.textContent = this.ratingType === 'stars' ? '★' : i;
        btn.dataset.score = String(i);
        btn.addEventListener('click', () => this.submitRating(i));
        wrap.appendChild(btn);
      }
    }

    async submitRating(score) {
      if (!this.sessionToken) return;
      try {
        const body = { widget_token: this.widgetToken, widget_session_token: this.sessionToken, score: score };
        const commentEl = this.ratingBlock && this.ratingBlock.querySelector('.messenger-widget-rating-comment');
        if (commentEl && commentEl.value && commentEl.value.trim()) {
          body.comment = commentEl.value.trim().slice(0, 2000);
        }
        const r = await fetch(CONFIG.API_BASE_URL + '/api/widget/rate/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (r.ok) this.hideRatingBlock();
      } catch (e) { /* ignore */ }
    }

    /**
     * Рендеринг UI
     */
    render() {
      // Проверить, не создан ли уже виджет
      if (document.getElementById('messenger-widget-container')) {
        return;
      }

      // Создать контейнер
      const container = document.createElement('div');
      container.id = 'messenger-widget-container';

      // Кнопка чата
      this.button = document.createElement('button');
      this.button.className = 'messenger-widget-button';
      this.button.setAttribute('aria-label', 'Открыть чат');
      this.button.innerHTML = `
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
      `;
      if (this.color) {
        this.button.style.backgroundColor = this.color;
      }
      const badge = document.createElement('span');
      badge.className = 'messenger-widget-badge';
      badge.style.display = 'none';
      this.button.appendChild(badge);
      this.badgeEl = badge;
      this.button.addEventListener('click', () => {
        if (this.isOpen) {
          this.close();
        } else {
          this.open();
        }
      });

      // Popup окно
      this.popup = document.createElement('div');
      this.popup.className = 'messenger-widget-popup';

      // Заголовок
      const header = document.createElement('div');
      header.className = 'messenger-widget-header';
      if (this.color) {
        header.style.backgroundColor = this.color;
      }
      const headerText = document.createElement('div');
      headerText.style.display = 'flex';
      headerText.style.flexDirection = 'column';

      const titleSpan = document.createElement('span');
      titleSpan.textContent = this.title || 'Чат с поддержкой';
      headerText.appendChild(titleSpan);

      if (this.greeting) {
        const subtitle = document.createElement('div');
        subtitle.className = 'messenger-widget-header-subtitle';
        subtitle.textContent = this.greeting;
        headerText.appendChild(subtitle);
      }

      header.appendChild(headerText);

      this.closeButton = document.createElement('button');
      this.closeButton.className = 'messenger-widget-close';
      this.closeButton.setAttribute('aria-label', 'Закрыть');
      this.closeButton.innerHTML = '×';
      this.closeButton.addEventListener('click', () => this.close());

      header.appendChild(this.closeButton);
      this.popup.appendChild(header);

      // Лента сообщений
      this.messagesContainer = document.createElement('div');
      this.messagesContainer.className = 'messenger-widget-messages';
      this.popup.appendChild(this.messagesContainer);

      // Офлайн-баннер (настраиваемое сообщение)
      this.offlineBanner = document.createElement('div');
      this.offlineBanner.className = 'messenger-widget-offline';
      this.offlineBanner.textContent = this.offlineMessage || 'Сейчас никого нет. Оставьте заявку — мы ответим в рабочее время.';
      if (this.offlineMode) {
        this.offlineBanner.classList.remove('messenger-widget-offline-hidden');
      } else {
        this.offlineBanner.classList.add('messenger-widget-offline-hidden');
      }
      this.popup.appendChild(this.offlineBanner);

      // Предзагруженные сообщения после bootstrap
      if (this.initialMessages && this.initialMessages.length) {
        for (const msg of this.initialMessages) {
          this.addMessageToUI(msg);
        }
      }
      this.scheduleMarkOutgoingRead();

      // Индикатор «Оператор печатает»
      this.typingIndicator = document.createElement('div');
      this.typingIndicator.className = 'messenger-widget-typing messenger-widget-typing-hidden';
      this.typingIndicator.innerHTML = 'Оператор печатает<span class="messenger-widget-typing-dots"><span class="messenger-widget-typing-dot"></span><span class="messenger-widget-typing-dot"></span><span class="messenger-widget-typing-dot"></span></span>';
      this.popup.appendChild(this.typingIndicator);

      // Форма отправки
      const form = document.createElement('div');
      form.className = 'messenger-widget-form';

      // CAPTCHA (показываем только если требуется)
      this.captchaRow = document.createElement('div');
      this.captchaRow.className = 'messenger-widget-captcha hidden';
      this.captchaRow.innerHTML = '<div class="messenger-widget-captcha-q"></div><input class="messenger-widget-captcha-input" placeholder="Ответ" inputmode="numeric">';
      this.captchaInput = this.captchaRow.querySelector('.messenger-widget-captcha-input');
      form.appendChild(this.captchaRow);

      this.pendingFilesEl = document.createElement('div');
      this.pendingFilesEl.className = 'messenger-widget-pending-files';
      form.appendChild(this.pendingFilesEl);

      const inputRow = document.createElement('div');
      inputRow.className = 'messenger-widget-form-row';

      this.input = document.createElement('textarea');
      this.input.className = 'messenger-widget-input';
      this.input.placeholder = 'Введите сообщение... (Enter — отправить, Shift+Enter — перенос строки)';
      this.input.rows = 3;
      this.input.maxLength = CONFIG.MAX_MESSAGE_LENGTH;
      this.input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          const body = this.input.value.trim();
          if (body || this.pendingFiles.length > 0) {
            this.sendMessage(body, this.pendingFiles);
          }
        }
      });
      this.input.addEventListener('input', () => {
        clearTimeout(this.typingSendTimer);
        this.typingSendTimer = setTimeout(() => this.sendContactTyping(), 400);
      });
      this.input.addEventListener('paste', (e) => {
        if (!this.attachmentsEnabled) return;
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        for (let i = 0; i < items.length; i++) {
          if (items[i].kind === 'file') {
            const file = items[i].getAsFile();
            if (file && this.isFileAllowed(file) && this.pendingFiles.length < 5) {
              this.pendingFiles.push(file);
              this.renderPendingFiles();
              e.preventDefault();
            }
            break;
          }
        }
      });

      if (this.attachmentsEnabled) {
        this.fileInput = document.createElement('input');
        this.fileInput.type = 'file';
        this.fileInput.multiple = true;
        this.fileInput.accept = this.allowedContentTypes.join(',');
        this.fileInput.style.display = 'none';
        this.fileInput.addEventListener('change', () => {
          const files = this.fileInput.files;
          if (!files) return;
          for (let i = 0; i < files.length && this.pendingFiles.length < 5; i++) {
            if (this.isFileAllowed(files[i])) {
              this.pendingFiles.push(files[i]);
            }
          }
          this.renderPendingFiles();
          this.fileInput.value = '';
        });
        form.appendChild(this.fileInput);
      }

      const attachBtn = this.attachmentsEnabled ? document.createElement('button') : null;
      if (attachBtn) {
        attachBtn.type = 'button';
        attachBtn.className = 'messenger-widget-attach';
        attachBtn.setAttribute('aria-label', 'Прикрепить файл');
        attachBtn.innerHTML = '📎';
        attachBtn.addEventListener('click', () => {
          if (this.fileInput) this.fileInput.click();
        });
        inputRow.appendChild(attachBtn);
      }

      inputRow.appendChild(this.input);

      this.sendButton = document.createElement('button');
      this.sendButton.className = 'messenger-widget-send';
      this.sendButton.textContent = 'Отправить';
      this.sendButton.addEventListener('click', () => {
        const body = this.input.value.trim();
        if (body || this.pendingFiles.length > 0) {
          this.sendMessage(body, this.pendingFiles);
        }
      });
      inputRow.appendChild(this.sendButton);

      form.appendChild(inputRow);
      this.ratingForm = form;
      this.popup.appendChild(form);

      // Блок оценки (после закрытия диалога)
      this.ratingBlock = document.createElement('div');
      this.ratingBlock.className = 'messenger-widget-rating messenger-widget-rating-hidden';
      this.ratingBlock.innerHTML = '<div class="messenger-widget-rating-title">Оцените, пожалуйста, диалог</div><div class="messenger-widget-rating-buttons"></div><textarea class="messenger-widget-rating-comment" placeholder="Комментарий (необязательно)" rows="2"></textarea>';
      this.popup.appendChild(this.ratingBlock);

      // Privacy notice
      if (this.privacyText) {
        const privacy = document.createElement('div');
        privacy.className = 'messenger-widget-privacy';
        const textSpan = document.createElement('span');
        textSpan.textContent = this.privacyText;
        privacy.appendChild(textSpan);
        if (this.privacyUrl) {
          const link = document.createElement('a');
          link.href = this.privacyUrl;
          link.target = '_blank';
          link.rel = 'noopener';
          link.className = 'messenger-widget-privacy-link';
          link.textContent = 'Политика конфиденциальности';
          privacy.appendChild(document.createTextNode(' '));
          privacy.appendChild(link);
        }
        this.popup.appendChild(privacy);
      }

      container.appendChild(this.button);
      container.appendChild(this.popup);
      document.body.appendChild(container);

      // Если капча нужна — покажем строку
      this.renderCaptchaRow();
    }
  }

  // Автоинициализация при загрузке скрипта + публичный JS API
  let widgetInstance = null;

  const scriptTag = document.currentScript;
  if (scriptTag) {
    const widgetToken = scriptTag.getAttribute('data-widget-token');
    if (widgetToken) {
      const widget = new MessengerWidget(widgetToken);
      widgetInstance = widget;
      widget.init();
    } else {
      console.warn('[MessengerWidget] data-widget-token attribute is required');
    }
  }

  if (typeof window !== 'undefined') {
    window.ProfiMessenger = {
      open() {
        if (widgetInstance) widgetInstance.open();
      },
      close() {
        if (widgetInstance) widgetInstance.close();
      },
      toggle() {
        if (widgetInstance) widgetInstance.toggle();
      },
      showLauncher() {
        if (widgetInstance) widgetInstance.showLauncher();
      },
      hideLauncher() {
        if (widgetInstance) widgetInstance.hideLauncher();
      },
      isOpen() {
        return !!(widgetInstance && widgetInstance.isOpen);
      },
    };
  }
})();
