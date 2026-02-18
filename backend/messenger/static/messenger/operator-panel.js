/**
 * Messenger Operator Panel - Unified Chatwoot-style interface
 * Управление трёхколоночной панелью мессенджера
 */

class MessengerOperatorPanel {
  constructor() {
    this.currentConversationId = null;
    this.pollingIntervals = {};
    this.lastMessageTimestamp = null;
    this.selectedConversationId = null;
  }

  /**
   * Открыть диалог (загрузить через AJAX)
   */
  async openConversation(conversationId, opts = {}) {
    const force = Boolean(opts && opts.force);
    if (!force && this.currentConversationId === conversationId) {
      return; // Уже открыт
    }

    this.currentConversationId = conversationId;
    this.selectedConversationId = conversationId;
    
    // Обновить URL hash (без перезагрузки страницы)
    if (window.location.hash !== `#conversation/${conversationId}`) {
      window.history.replaceState(null, '', `#conversation/${conversationId}`);
    }
    
    // Обновить активную карточку в списке
    document.querySelectorAll('.conversation-card').forEach(card => {
      if (parseInt(card.dataset.conversationId) === conversationId) {
        card.classList.add('active');
      } else {
        card.classList.remove('active');
      }
    });

    // Показать индикатор загрузки
    const contentArea = document.getElementById('conversationContent');
    const infoArea = document.getElementById('conversationInfo');
    
    if (contentArea) {
      contentArea.innerHTML = '<div class="p-4 text-center text-brand-dark/60"><p>Загрузка диалога...</p></div>';
    }
    if (infoArea) {
      infoArea.innerHTML = '<div class="text-center text-brand-dark/60 p-4"><p class="text-sm">Загрузка информации...</p></div>';
    }

    try {
      // Загрузить диалог через API
      const response = await fetch(`/api/messenger/conversations/${conversationId}/`, {
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
        }
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const conversation = await response.json();
      
      // Загрузить сообщения
      const messagesResponse = await fetch(`/api/messenger/conversations/${conversationId}/messages/`, {
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
        }
      });

      if (!messagesResponse.ok) {
        throw new Error(`HTTP ${messagesResponse.status}`);
      }

      const messages = await messagesResponse.json();
      
      // Рендерить диалог (пока простой вариант, потом можно улучшить)
      this.renderConversation(conversation, messages);
      this.renderConversationInfo(conversation);
      
      // Начать polling для новых сообщений
      this.startPolling(conversationId);
      
      // Сохранить timestamp последнего сообщения
      if (messages.length > 0) {
        this.lastMessageTimestamp = messages[messages.length - 1].created_at;
      }
      
    } catch (error) {
      console.error('Failed to load conversation:', error);
      if (contentArea) {
        contentArea.innerHTML = `
          <div class="p-4 text-center text-red-600">
            <p>Ошибка загрузки диалога</p>
            <p class="text-sm mt-2">${error.message}</p>
          </div>
        `;
      }
    }
  }

  /**
   * Рендерить диалог в центральной колонке
   */
  renderConversation(conversation, messages) {
    const contentArea = document.getElementById('conversationContent');
    if (!contentArea) return;

    // Заголовок диалога
    const contactName = conversation.contact?.name || conversation.contact?.email || conversation.contact?.phone || 'Без имени';
    
    let html = `
      <div class="flex flex-col h-full overflow-hidden">
      <div class="border-b border-brand-soft/60 p-4 bg-white flex-shrink-0">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-lg font-semibold">${this.escapeHtml(contactName)}</h3>
            <p class="text-xs text-brand-dark/60">Диалог #${conversation.id}</p>
          </div>
          <div class="flex items-center gap-2">
            ${conversation.status === 'open' ? '<span class="badge badge-new">Открыт</span>' : ''}
            ${conversation.status === 'pending' ? '<span class="badge badge-progress">В ожидании</span>' : ''}
            ${conversation.status === 'resolved' ? '<span class="badge badge-done">Решён</span>' : ''}
            ${conversation.status === 'closed' ? '<span class="badge badge-cancel">Закрыт</span>' : ''}
          </div>
        </div>
      </div>
      
      <div class="flex-1 min-h-0 overflow-y-auto p-4" id="messagesList">
    `;

    // Сообщения
    if (messages.length === 0) {
      html += '<div class="text-center py-8 text-brand-dark/60"><p>Сообщений пока нет</p></div>';
    } else {
      let currentDate = null;
      messages.forEach(message => {
        const messageDate = new Date(message.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
        if (currentDate !== messageDate) {
          currentDate = messageDate;
          html += `
            <div class="text-center my-4">
              <span class="inline-block px-3 py-1 rounded-full bg-brand-soft/40 text-xs text-brand-dark/70">
                ${messageDate}
              </span>
            </div>
          `;
        }

        const isOutgoing = message.direction === 'OUT' || message.direction === 'INTERNAL';
        const senderName = isOutgoing 
          ? (message.sender_user?.first_name || message.sender_user?.username || 'Оператор')
          : (message.sender_contact?.name || 'Клиент');
        const avatarInitial = isOutgoing
          ? (message.sender_user?.first_name?.[0] || message.sender_user?.username?.[0] || 'О').toUpperCase()
          : (message.sender_contact?.name?.[0] || 'К').toUpperCase();
        
        html += `
          <div class="flex gap-3 mb-4 ${isOutgoing ? 'flex-row-reverse' : 'flex-row'}">
            <div class="flex-shrink-0">
              <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                isOutgoing ? 'bg-brand-orange/20 text-brand-orange' : 'bg-brand-teal/20 text-brand-teal'
              }">
                ${avatarInitial}
              </div>
            </div>
            <div class="flex-1 ${isOutgoing ? 'text-right' : 'text-left'}">
              <div class="inline-block max-w-[80%] rounded-lg px-4 py-2 ${
                isOutgoing ? 'bg-brand-teal/10' : 'bg-brand-soft/40'
              }">
                <div class="text-sm font-medium mb-1">${this.escapeHtml(senderName)}</div>
                <div class="text-sm text-brand-dark whitespace-pre-wrap">${this.escapeHtml(message.body || '')}</div>
                <div class="text-xs text-brand-dark/50 mt-1">
                  ${new Date(message.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
                </div>
              </div>
            </div>
          </div>
        `;
      });
    }

    html += '</div>';

    // Форма отправки сообщения
    html += `
      <div class="border-t border-brand-soft/60 p-3 bg-white flex-shrink-0">
        <form id="messageForm" onsubmit="window.MessengerPanel.sendMessage(event)" enctype="multipart/form-data">
          <input type="hidden" name="conversation_id" value="${conversation.id}">
          <div class="flex items-end gap-2">
            <div class="flex-1 min-w-0">
              <textarea name="body" id="messageBody" class="textarea w-full resize-none" rows="2" placeholder="Введите сообщение..."></textarea>
              <div id="messageAttachmentsNames" class="text-xs text-brand-dark/60 mt-1 px-1"></div>
            </div>
            <div class="flex items-end gap-1.5 flex-shrink-0">
              <input type="file" name="attachments" id="messageAttachments" class="hidden" multiple accept="image/*,.pdf">
              <button type="button" onclick="document.getElementById('messageAttachments').click()" class="btn btn-outline btn-sm p-2" title="Прикрепить файл">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/>
                </svg>
              </button>
              <button type="submit" class="btn btn-primary p-2.5" title="Отправить (Ctrl+Enter)">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
                </svg>
              </button>
            </div>
          </div>
          <p class="text-[10px] text-brand-dark/40 mt-1.5 px-1">Ctrl+Enter — отправить • Макс. 5 МБ</p>
        </form>
      </div>
    `;

    html += `</div>`;
    contentArea.innerHTML = html;
    
    // Автоскролл к последнему сообщению
    setTimeout(() => {
      const messagesList = document.getElementById('messagesList');
      if (messagesList) {
        messagesList.scrollTop = messagesList.scrollHeight;
      }
    }, 100);

    // Обработчик файлов
    const fileInput = document.getElementById('messageAttachments');
    if (fileInput) {
      fileInput.addEventListener('change', function() {
        const names = Array.from(this.files).map(f => f.name).join(', ');
        const namesEl = document.getElementById('messageAttachmentsNames');
        if (namesEl) {
          namesEl.textContent = names || '';
        }
      });
    }

    // Ctrl+Enter для отправки
    const messageBody = document.getElementById('messageBody');
    if (messageBody) {
      messageBody.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          const form = document.getElementById('messageForm');
          if (form) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
        }
      });
    }
  }

  /**
   * Рендерить информацию о диалоге в правой колонке
   */
  renderConversationInfo(conversation) {
    const infoArea = document.getElementById('conversationInfo');
    if (!infoArea) return;

    const contactName = conversation.contact?.name || conversation.contact?.email || conversation.contact?.phone || 'Без имени';
    const contactEmail = conversation.contact?.email || '';
    const contactPhone = conversation.contact?.phone || '';
    
    let html = `
      <div class="space-y-4">
        <div>
          <h3 class="text-sm font-semibold mb-3">Информация</h3>
          <dl class="space-y-2 text-sm">
            <div>
              <dt class="text-brand-dark/60">Контакт</dt>
              <dd class="font-medium">${this.escapeHtml(contactName)}</dd>
              ${contactEmail ? `<dd class="text-xs text-brand-dark/60">${this.escapeHtml(contactEmail)}</dd>` : ''}
              ${contactPhone ? `<dd class="text-xs text-brand-dark/60">${this.escapeHtml(contactPhone)}</dd>` : ''}
            </div>
            <div>
              <dt class="text-brand-dark/60">Филиал</dt>
              <dd class="font-medium">${this.escapeHtml(conversation.branch?.name || '—')}</dd>
            </div>
            <div>
              <dt class="text-brand-dark/60">Создан</dt>
              <dd class="font-medium">${new Date(conversation.created_at).toLocaleString('ru-RU')}</dd>
            </div>
            <div>
              <dt class="text-brand-dark/60">Статус</dt>
              <dd class="font-medium">
                ${conversation.status === 'open' ? '<span class="badge badge-new">Открыт</span>' : ''}
                ${conversation.status === 'pending' ? '<span class="badge badge-progress">В ожидании</span>' : ''}
                ${conversation.status === 'resolved' ? '<span class="badge badge-done">Решён</span>' : ''}
                ${conversation.status === 'closed' ? '<span class="badge badge-cancel">Закрыт</span>' : ''}
              </dd>
            </div>
            <div>
              <dt class="text-brand-dark/60">Приоритет</dt>
              <dd class="font-medium">
                ${conversation.priority === 10 ? '<span class="badge badge-xs">Низкий</span>' : ''}
                ${conversation.priority === 20 ? '<span class="badge badge-xs">Обычный</span>' : ''}
                ${conversation.priority === 30 ? '<span class="badge badge-warn badge-xs">Высокий</span>' : ''}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    `;

    infoArea.innerHTML = html;
  }

  /**
   * Отправить сообщение через API
   */
  async sendMessage(event) {
    event.preventDefault();
    
    const form = event.target;
    const conversationId = form.querySelector('[name="conversation_id"]').value;
    const body = form.querySelector('[name="body"]').value.trim();
    const fileInput = form.querySelector('[name="attachments"]');
    const files = fileInput ? Array.from(fileInput.files) : [];
    
    if (!body && files.length === 0) {
      return;
    }

    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>';
    }

    try {
      const formData = new FormData();
      formData.append('body', body);
      formData.append('direction', 'OUT');
      
      files.forEach(file => {
        formData.append('attachments', file);
      });

      const response = await fetch(`/api/messenger/conversations/${conversationId}/messages/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': this.getCsrfToken(),
        },
        body: formData
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Ошибка отправки сообщения');
      }

      const message = await response.json();
      
      // Очистить форму
      form.querySelector('[name="body"]').value = '';
      if (fileInput) {
        fileInput.value = '';
        const namesEl = document.getElementById('messageAttachmentsNames');
        if (namesEl) namesEl.textContent = '';
      }
      
      // Перезагрузить диалог для обновления сообщений
      await this.openConversation(parseInt(conversationId), { force: true });
      
    } catch (error) {
      console.error('Failed to send message:', error);
      alert('Ошибка отправки сообщения: ' + error.message);
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>';
      }
    }
  }

  /**
   * Начать polling для новых сообщений
   */
  startPolling(conversationId) {
    // Остановить предыдущий polling
    this.stopPolling(conversationId);
    
    this.pollingIntervals[conversationId] = setInterval(async () => {
      if (this.currentConversationId !== conversationId) {
        this.stopPolling(conversationId);
        return;
      }

      try {
        const url = `/api/messenger/conversations/${conversationId}/messages/` + 
                    (this.lastMessageTimestamp ? `?since=${encodeURIComponent(this.lastMessageTimestamp)}` : '');
        
        const response = await fetch(url, {
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
          }
        });

        if (response.ok) {
          const messages = await response.json();
          if (messages.length > 0) {
            // Есть новые сообщения - перезагрузить диалог
            await this.openConversation(conversationId, { force: true });
          }
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 3000); // Каждые 3 секунды
  }

  /**
   * Остановить polling
   */
  stopPolling(conversationId) {
    if (this.pollingIntervals[conversationId]) {
      clearInterval(this.pollingIntervals[conversationId]);
      delete this.pollingIntervals[conversationId];
    }
  }

  /**
   * Получить CSRF токен
   */
  getCsrfToken() {
    const cookieMatch = document.cookie.match(/csrftoken=([^;]+)/);
    return cookieMatch ? cookieMatch[1] : '';
  }

  /**
   * Экранировать HTML
   */
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}

// Глобальный экземпляр
const panel = new MessengerOperatorPanel();

// Экспорт для использования в других скриптах
window.MessengerPanel = panel;
