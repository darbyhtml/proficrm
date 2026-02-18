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
    this.composeMode = 'OUT'; // OUT | INTERNAL
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
    const contactName = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
    
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

        const dir = (message.direction || '').toLowerCase();
        const isOutgoing = dir === 'out' || dir === 'internal';
        const senderName = isOutgoing
          ? (message.sender_user_name || message.sender_user_username || 'Оператор')
          : (message.sender_contact_name || 'Клиент');
        const avatarInitial = isOutgoing
          ? (senderName[0] || 'О').toUpperCase()
          : (senderName[0] || 'К').toUpperCase();
        
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
        <div class="flex items-center justify-between mb-2">
          <div class="inline-flex rounded-xl border border-brand-soft/80 bg-white overflow-hidden">
            <button type="button" id="composeModeOut" class="px-3 py-1.5 text-xs font-medium ${this.composeMode === 'OUT' ? 'bg-brand-teal text-white' : 'text-brand-dark/70 hover:bg-brand-soft/30'}">Ответить</button>
            <button type="button" id="composeModeInternal" class="px-3 py-1.5 text-xs font-medium ${this.composeMode === 'INTERNAL' ? 'bg-brand-orange text-brand-dark' : 'text-brand-dark/70 hover:bg-brand-soft/30'}">Заметка</button>
          </div>
          <div class="text-[10px] text-brand-dark/40">Ctrl+Enter</div>
        </div>
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
          <p class="text-[10px] text-brand-dark/40 mt-1.5 px-1">Макс. 5 МБ на файл • изображения и PDF</p>
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

    // Переключение режима (Ответить/Заметка)
    const btnOut = document.getElementById('composeModeOut');
    const btnInternal = document.getElementById('composeModeInternal');
    const applyModeUI = () => {
      if (!btnOut || !btnInternal) return;
      if (this.composeMode === 'OUT') {
        btnOut.className = 'px-3 py-1.5 text-xs font-medium bg-brand-teal text-white';
        btnInternal.className = 'px-3 py-1.5 text-xs font-medium text-brand-dark/70 hover:bg-brand-soft/30';
      } else {
        btnOut.className = 'px-3 py-1.5 text-xs font-medium text-brand-dark/70 hover:bg-brand-soft/30';
        btnInternal.className = 'px-3 py-1.5 text-xs font-medium bg-brand-orange text-brand-dark';
      }
    };
    if (btnOut) btnOut.addEventListener('click', () => { this.composeMode = 'OUT'; applyModeUI(); });
    if (btnInternal) btnInternal.addEventListener('click', () => { this.composeMode = 'INTERNAL'; applyModeUI(); });
  }

  /**
   * Рендерить информацию о диалоге в правой колонке
   */
  renderConversationInfo(conversation) {
    const infoArea = document.getElementById('conversationInfo');
    if (!infoArea) return;

    const contactName = conversation.contact_name || conversation.contact_email || conversation.contact_phone || 'Без имени';
    const contactEmail = conversation.contact_email || '';
    const contactPhone = conversation.contact_phone || '';
    
    const ctx = window.MESSENGER_CONTEXT || {};
    const assignees = Array.isArray(ctx.assignees) ? ctx.assignees : [];
    const currentUserId = ctx.currentUserId;

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
                <dd class="font-medium">${this.escapeHtml(conversation.branch_name || '—')}</dd>
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
        
        <div class="card" style="box-shadow:none">
          <div class="card-pad">
            <h3 class="text-sm font-semibold mb-3">Действия</h3>
            <div class="space-y-3">
              <div class="flex gap-2">
                <button type="button" class="btn btn-outline btn-sm flex-1" id="assignMeBtn">Назначить меня</button>
                <button type="button" class="btn btn-outline btn-sm" id="closeConvBtn">Закрыть</button>
              </div>
              <div>
                <label class="block text-xs text-brand-dark/70 mb-1">Статус</label>
                <select class="select text-sm w-full" id="convStatusSelect">
                  <option value="open" ${conversation.status === 'open' ? 'selected' : ''}>Открыт</option>
                  <option value="pending" ${conversation.status === 'pending' ? 'selected' : ''}>В ожидании</option>
                  <option value="resolved" ${conversation.status === 'resolved' ? 'selected' : ''}>Решён</option>
                  <option value="closed" ${conversation.status === 'closed' ? 'selected' : ''}>Закрыт</option>
                </select>
              </div>
              <div>
                <label class="block text-xs text-brand-dark/70 mb-1">Оператор</label>
                <select class="select text-sm w-full" id="convAssigneeSelect">
                  <option value="">Не назначен</option>
                ${assignees.map(a => `<option value="${a.id}" ${conversation.assignee === a.id ? 'selected' : ''}>${this.escapeHtml(a.name)}</option>`).join('')}
                </select>
              </div>
              <div>
                <label class="block text-xs text-brand-dark/70 mb-1">Приоритет</label>
                <select class="select text-sm w-full" id="convPrioritySelect">
                  <option value="10" ${conversation.priority === 10 ? 'selected' : ''}>Низкий</option>
                  <option value="20" ${conversation.priority === 20 ? 'selected' : ''}>Обычный</option>
                  <option value="30" ${conversation.priority === 30 ? 'selected' : ''}>Высокий</option>
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    infoArea.innerHTML = html;

    const statusSelect = document.getElementById('convStatusSelect');
    const assigneeSelect = document.getElementById('convAssigneeSelect');
    const prioritySelect = document.getElementById('convPrioritySelect');
    const assignMeBtn = document.getElementById('assignMeBtn');
    const closeBtn = document.getElementById('closeConvBtn');

    if (statusSelect) statusSelect.addEventListener('change', () => this.patchConversation(conversation.id, { status: statusSelect.value }));
    if (prioritySelect) prioritySelect.addEventListener('change', () => this.patchConversation(conversation.id, { priority: parseInt(prioritySelect.value) }));
    if (assigneeSelect) assigneeSelect.addEventListener('change', () => this.patchConversation(conversation.id, { assignee: assigneeSelect.value ? parseInt(assigneeSelect.value) : null }));
    if (assignMeBtn) assignMeBtn.addEventListener('click', () => {
      if (!currentUserId) return;
      this.patchConversation(conversation.id, { assignee: currentUserId });
    });
    if (closeBtn) closeBtn.addEventListener('click', () => this.patchConversation(conversation.id, { status: 'closed' }));
  }

  async patchConversation(conversationId, payload) {
    try {
      const response = await fetch(`/api/messenger/conversations/${conversationId}/`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': this.getCsrfToken(),
        },
        body: JSON.stringify(payload || {}),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Ошибка сохранения');
      }
      const updated = await response.json();
      // Перерисовать правую колонку (и заголовок диалога) актуальными значениями
      await this.openConversation(conversationId, { force: true });
      this.updateConversationCard(updated);
    } catch (e) {
      console.error('patchConversation failed:', e);
      alert('Не удалось сохранить: ' + (e.message || e));
    }
  }

  updateConversationCard(conversation) {
    const card = document.querySelector(`.conversation-card[data-conversation-id="${conversation.id}"]`);
    if (!card) return;
    const avatar = card.querySelector('.conversation-avatar');
    if (avatar) {
      let bg = '#94a3b8';
      if (conversation.status === 'open') bg = '#01948E';
      else if (conversation.status === 'pending') bg = '#FDAD3A';
      else if (conversation.status === 'resolved') bg = '#22c55e';
      avatar.style.background = bg;
    }
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
      formData.append('direction', this.composeMode === 'INTERNAL' ? 'internal' : 'out');
      
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

      await response.json();
      
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
