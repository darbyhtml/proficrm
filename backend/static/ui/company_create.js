(function(){
    // Дубликаты не блокируют создание: только подсказка.

    // Live duplicate check (debounced)
    const box = document.getElementById('dupBox');
    const list = document.getElementById('dupList');
    const meta = document.getElementById('dupMeta');
    const hidden = document.getElementById('dupHidden');
    const nameEl = document.querySelector('input[name="name"]');
    const innEl = document.querySelector('textarea[name="inn"], input[name="inn"]');
    const kppEl = document.querySelector('input[name="kpp"]');
    const addrEl = document.querySelector('textarea[name="address"], input[name="address"]');

    let t = null;
    let dupAbort = null;

    function normalizeIdent(v) {
      return (v || '').replace(/\s+/g, '');
    }

    async function run(){
      const name = (nameEl && nameEl.value || '').trim();
      const inn = normalizeIdent(innEl && innEl.value || '');
      const kpp = normalizeIdent(kppEl && kppEl.value || '');
      const address = (addrEl && addrEl.value || '').trim();
      if (!name && !inn && !kpp && !address) {
        if (box) box.style.display = 'none';
        return;
      }
      const qs = new URLSearchParams({name, inn, kpp, address});

      if (dupAbort) {
        dupAbort.abort();
      }
      dupAbort = new AbortController();

      let data;
      try {
        const res = await fetch('/companies/duplicates/?' + qs.toString(), {
          headers: {'X-Requested-With':'fetch'},
          signal: dupAbort.signal,
        });
        data = await res.json();
      } catch (e) {
        if (e.name === 'AbortError') {
          return;
        }
        console && console.warn && console.warn('duplicate check failed', e);
        return;
      }
      if (!box || !list || !meta || !hidden) return;

      const items = Array.isArray(data.items) ? data.items : [];
      const backendHidden = data.hidden_count || 0;

      if (!items.length && backendHidden === 0) {
        box.style.display = 'none';
        return;
      }
      box.style.display = 'block';
      const reasonsText = data.reasons && data.reasons.length ? ('По: ' + data.reasons.join(', ')) : '';
      const totalMatches = items.length;
      meta.textContent = 'Найдено совпадений: ' + totalMatches + (reasonsText ? ' • ' + reasonsText : '');

      function createDupCard(it) {
        const card = document.createElement('div');
        card.className = 'block rounded-xl border border-brand-soft/80 bg-white/90 px-3 py-2 shadow-sm transition-all hover:border-brand-teal/50 hover:bg-brand-soft/10 cursor-default';
        const matchBadges = (it.match && it.match.length)
          ? (`<div class="mt-1 flex flex-wrap gap-1">
               <span class="badge badge-warn">Совпало:</span>
               ${it.match.map(m => `<span class="badge badge-warn">${m}</span>`).join('')}
             </div>`)
          : '';
        card.innerHTML = `<div class="font-medium">${it.name}</div>
          <div class="text-xs muted">ИНН: ${it.inn||'—'} · КПП: ${it.kpp||'—'} · ${it.branch?('Филиал: '+it.branch+' · '):''}${it.responsible?('Ответственный: '+it.responsible):''}</div>
          ${matchBadges}
          <div class="text-xs muted">${it.address||'—'}</div>
          <div class="text-xs muted mt-1">ID: ${it.id}</div>
          <div class="mt-2 flex flex-wrap gap-2 text-xs">
            <button type="button" class="btn btn-outline btn-xxs" data-dup-open="${it.url}">Открыть в новой вкладке</button>
            <button type="button" class="btn btn-outline btn-xxs" data-dup-goto="${it.url}">Это дубль — перейти и не создавать</button>
          </div>`;
        return card;
      }

      list.innerHTML = '';

      const visibleItems = items.slice(0, 1);
      const hiddenItems = items.slice(1);

      visibleItems.forEach(function (it) {
        list.appendChild(createDupCard(it));
      });

      if (hiddenItems.length) {
        const extraContainer = document.createElement('div');
        extraContainer.className = 'mt-2 space-y-2';
        extraContainer.style.display = 'none';
        hiddenItems.forEach(function (it) {
          extraContainer.appendChild(createDupCard(it));
        });

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'mt-2 text-xs text-brand-dark/70 hover:text-brand-teal underline decoration-dotted';
        toggleBtn.textContent = `Показать ещё ${hiddenItems.length} совпадений`;
        let expanded = false;
        toggleBtn.addEventListener('click', function () {
          expanded = !expanded;
          extraContainer.style.display = expanded ? 'block' : 'none';
          toggleBtn.textContent = expanded
            ? 'Скрыть список совпадений'
            : `Показать ещё ${hiddenItems.length} совпадений`;
        });

        list.appendChild(toggleBtn);
        list.appendChild(extraContainer);
      }

      hidden.style.display = backendHidden > 0 ? 'block' : 'none';
      hidden.textContent = backendHidden > 0 ? 'На списке компаний могут быть и другие похожие карточки.' : '';
    }
    function schedule(){
      if (t) clearTimeout(t);
      t = setTimeout(run, 350);
    }
    [nameEl, innEl, kppEl, addrEl].filter(Boolean).forEach(el => el.addEventListener('input', schedule));

    if (list) {
      list.addEventListener('click', function(e) {
        const openBtn = e.target.closest('button[data-dup-open]');
        const gotoBtn = e.target.closest('button[data-dup-goto]');
        const form = document.getElementById('companyCreateForm');
        const warnText = 'Вы уйдёте со страницы создания компании. Несохранённые данные могут быть потеряны.';

        if (openBtn) {
          const url = openBtn.getAttribute('data-dup-open');
          if (!url) return;
          if (!window.confirm(warnText + '\n\nОткрыть карточку в новой вкладке?')) return;
          window.open(url, '_blank', 'noopener');
          return;
        }
        if (gotoBtn) {
          const url = gotoBtn.getAttribute('data-dup-goto');
          if (!url) return;
          if (!window.confirm(warnText + '\n\nПерейти на существующую карточку и не создавать новую?')) return;
          if (form) {
            form.dataset.submitted = '1';
            form.dataset.allowNavigate = '1';
          }
          window.location.href = url;
        }
      });
    }
  })();

  // Управление дополнительными телефонами и email + автосохранение формы
  document.addEventListener('DOMContentLoaded', function() {
    const formEl = document.getElementById('companyCreateForm');
    const DRAFT_KEY = 'companyCreate:draft';
    const draftBannerEl = document.getElementById('companyCreateDraftBanner');
    const draftMetaEl = document.getElementById('companyCreateDraftMeta');
    const draftRestoreBtn = document.getElementById('companyCreateDraftRestoreBtn');
    const draftDiscardBtn = document.getElementById('companyCreateDraftDiscardBtn');
    let formDirty = false;
    let draftSnapshot = null;

    function collectFormData(form) {
      const data = {};
      if (!form) return data;
      const elements = form.querySelectorAll('input, textarea, select');
      elements.forEach(function(el) {
        if (!el.name || el.type === 'hidden') return;
        if (el.name === 'csrfmiddlewaretoken') return;
        if ((el.type === 'checkbox' || el.type === 'radio') && !el.checked) return;
        data[el.name] = el.value;
      });
      return data;
    }

    function hasUserData(form) {
      if (!form) return false;
      const elements = form.querySelectorAll('input, textarea, select');
      for (const el of elements) {
        if (!el.name || el.type === 'hidden' || el.name === 'csrfmiddlewaretoken') continue;
        if ((el.type === 'checkbox' || el.type === 'radio')) {
          if (el.checked) return true;
          continue;
        }
        if ((el.value || '').trim()) return true;
      }
      return false;
    }

    function loadDraft() {
      if (draftSnapshot) return draftSnapshot;
      let raw = null;
      try {
        raw = localStorage.getItem(DRAFT_KEY);
      } catch (e) {
        return null;
      }
      if (!raw) return null;
      let draft;
      try {
        draft = JSON.parse(raw);
      } catch (e) {
        return null;
      }
      if (!draft || typeof draft !== 'object') return null;
      draftSnapshot = draft;
      return draftSnapshot;
    }

    function applyDraftToExistingControls(form, draft) {
      if (!form || !draft) return;
      const elements = form.querySelectorAll('input, textarea, select');
      const triggerNames = new Set(['name', 'inn', 'kpp', 'address']);
      elements.forEach(function(el) {
        const name = el.name;
        if (!name || !(name in draft)) return;
        if (el.type === 'checkbox' || el.type === 'radio') {
          el.checked = !!draft[name];
        } else {
          el.value = draft[name];
        }
        if (triggerNames.has(name)) {
          try {
            el.dispatchEvent(new Event('input', {bubbles: true}));
          } catch (e) {
            // ignore
          }
        }
      });
    }

    function saveDraftSoon() {
      if (!formEl) return;
      formDirty = true;
      window.clearTimeout(saveDraftSoon._t);
      saveDraftSoon._t = window.setTimeout(function() {
        try {
          const payload = collectFormData(formEl);
          const now = new Date();
          payload.__meta = {
            savedAt: now.toLocaleString('ru-RU'),
          };
          localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
        } catch (e) {
          // localStorage может быть недоступен — просто молча пропускаем.
        }
      }, 400);
    }

    if (formEl) {
      const draft = loadDraft();
      if (draft && draftBannerEl) {
        draftBannerEl.classList.remove('hidden');
        const meta = (draft.__meta && draft.__meta.savedAt) ? String(draft.__meta.savedAt) : '';
        if (draftMetaEl) {
          draftMetaEl.textContent = meta ? ` (сохранено: ${meta})` : '';
        }
      }

      // Основной блок: делаем обязательными только название и ИНН.
      const requiredNames = [
        'name',
        'inn',
      ];
      requiredNames.forEach(function (fieldName) {
        const el = formEl.querySelector('[name="' + fieldName + '"]');
        if (el) {
          el.required = true;
        }
      });

      formEl.addEventListener('input', saveDraftSoon, {capture: true});
      formEl.addEventListener('change', saveDraftSoon, {capture: true});
      formEl.addEventListener('submit', function(e) {
        const submitBtn = formEl.querySelector('button[type="submit"]');

        // Если уже идёт отправка формы — блокируем повторный submit
        if (submitBtn && submitBtn.disabled) {
          e.preventDefault();
          return;
        }

        if (submitBtn) {
          const originalText = submitBtn.dataset.originalText || submitBtn.textContent || '';
          submitBtn.dataset.originalText = originalText;
          submitBtn.disabled = true;
          submitBtn.textContent = 'Создание...';
        }

        formEl.dataset.submitted = '1';
        formEl.dataset.allowNavigate = '1';
        formDirty = false;
        try {
          localStorage.removeItem(DRAFT_KEY);
        } catch (e) {}
      });

      window.addEventListener('beforeunload', function(e) {
        if (!formDirty) return;
        if (formEl.dataset.submitted === '1') return;
        if (formEl.dataset.allowNavigate === '1') return;
        e.preventDefault();
        e.returnValue = '';
      });
    }

    // EMAILS
    const emailsContainer = document.getElementById('company-emails-container');
    const addEmailBtn = document.getElementById('add-email-btn');
    let emailIndex = (emailsContainer ? emailsContainer.querySelectorAll('input[name^="company_emails_"]').length : 0);

    function addEmailRow(val) {
      const row = document.createElement('div');
      row.className = 'flex gap-2 items-center company-email-row';
      row.innerHTML = `
        <input type="email" name="company_emails_${emailIndex}" value=""
               class="flex-1 rounded-lg border px-3 py-2" placeholder="email@example.com" />
        <button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-email-btn" title="Удалить email">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6l-1 14H6L5 6"></path>
            <path d="M10 11v6"></path>
            <path d="M14 11v6"></path>
            <path d="M9 6l1-3h4l1 3"></path>
          </svg>
        </button>
      `;
      emailsContainer.appendChild(row);
      const input = row.querySelector('input');
      if (input && val) input.value = val;
      emailIndex++;
      return input;
    }

    if (addEmailBtn) {
      addEmailBtn.addEventListener('click', function() {
        addEmailRow('');
      });
    }

    if (emailsContainer) {
      emailsContainer.addEventListener('click', function(e) {
        const btn = e.target.closest('.remove-email-btn');
        if (!btn) return;
        const row = btn.closest('.company-email-row');
        if (row) row.remove();
      });
    }

    // PHONES
    const phonesContainer = document.getElementById('company-phones-container');
    const addPhoneBtn = document.getElementById('add-phone-btn');
    let phoneIndex = (phonesContainer ? phonesContainer.querySelectorAll('input[name^="company_phones_"]').length : 0);

    function formatRuPhone11(d11) {
      const p = d11.slice(1);
      return `+7 (${p.slice(0,3)}) ${p.slice(3,6)}-${p.slice(6,8)}-${p.slice(8,10)}`;
    }

    function normalizePhoneToDisplay(raw) {
      const digits = String(raw || '').replace(/\D/g, '');
      if (!digits) return null;
      if (digits.length === 10) {
        return formatRuPhone11('7' + digits);
      }
      if (digits.length >= 11) {
        let base = digits;
        if (base[0] === '8') base = '7' + base.slice(1);
        if (base[0] === '7') {
          base = base.slice(0, 11);
          return formatRuPhone11(base);
        }
        return '+' + digits;
      }
      return null;
    }

    function extractPhones(text) {
      const src = String(text || '');
      const re = /(\+?\d[\d()\s\-]{8,}\d)/g;
      const matches = [];
      let m;
      while ((m = re.exec(src)) !== null) matches.push(m[1]);
      const candidates = matches.length ? matches : src.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
      const out = [];
      const seen = new Set();
      for (const c of candidates) {
        const normalized = normalizePhoneToDisplay(c);
        if (!normalized) continue;
        if (seen.has(normalized)) continue;
        seen.add(normalized);
        out.push(normalized);
      }
      return out;
    }

    function addPhoneRow(val) {
      const row = document.createElement('div');
      row.className = 'flex gap-2 items-center company-phone-row';
      row.innerHTML = `
        <input type="text" name="company_phones_${phoneIndex}" value=""
               class="flex-1 rounded-lg border px-3 py-2" placeholder="+7 ..." />
        <button type="button" class="btn btn-outline text-red-600 hover:bg-red-50 remove-phone-btn" title="Удалить номер">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6l-1 14H6L5 6"></path>
            <path d="M10 11v6"></path>
            <path d="M14 11v6"></path>
            <path d="M9 6l1-3h4l1 3"></path>
          </svg>
        </button>
      `;
      phonesContainer.appendChild(row);
      const input = row.querySelector('input');
      if (input && val) input.value = val;
      phoneIndex++;
      return input;
    }

    function restoreDynamicFromDraft(draft) {
      if (!draft || typeof draft !== 'object') return;

      const emailValuesByIndex = {};
      const phoneValuesByIndex = {};

      Object.entries(draft).forEach(function([key, value]) {
        const v = String(value || '').trim();
        if (!v) return;
        let m = key.match(/^company_emails_(\d+)$/);
        if (m) {
          emailValuesByIndex[m[1]] = v;
          return;
        }
        m = key.match(/^company_phones_(\d+)$/);
        if (m) {
          phoneValuesByIndex[m[1]] = v;
        }
      });

      if (emailsContainer && Object.keys(emailValuesByIndex).length) {
        Object.keys(emailValuesByIndex).forEach(function(idx) {
          const name = 'company_emails_' + idx;
          let input = emailsContainer.querySelector('input[name="' + name + '"]');
          while (!input) {
            const created = addEmailRow('');
            if (!created) break;
            if (created.name === name) {
              input = created;
              break;
            }
          }
          if (input) {
            input.value = emailValuesByIndex[idx];
          }
        });
      }

      if (phonesContainer && Object.keys(phoneValuesByIndex).length) {
        Object.keys(phoneValuesByIndex).forEach(function(idx) {
          const name = 'company_phones_' + idx;
          let input = phonesContainer.querySelector('input[name="' + name + '"]');
          while (!input) {
            const created = addPhoneRow('');
            if (!created) break;
            if (created.name === name) {
              input = created;
              break;
            }
          }
          if (input) {
            const val = phoneValuesByIndex[idx];
            const normalized = normalizePhoneToDisplay(val);
            input.value = normalized || val;
          }
        });
      }
    }

    // Приводим уже загруженные телефоны к читаемому виду
    if (phonesContainer) {
      phonesContainer.querySelectorAll('input[name^="company_phones_"]').forEach(function(inp){
        const normalized = normalizePhoneToDisplay(inp.value);
        if (normalized) inp.value = normalized;
      });
    }

    if (addPhoneBtn) {
      addPhoneBtn.addEventListener('click', function() {
        addPhoneRow('');
      });
    }

    if (phonesContainer) {
      phonesContainer.addEventListener('click', function(e) {
        const btn = e.target.closest('.remove-phone-btn');
        if (!btn) return;
        const row = btn.closest('.company-phone-row');
        if (row) row.remove();
      });
    }

    if (phonesContainer) {
      // Вставка нескольких телефонов за раз (из чекко/таблиц): создаём нужное число полей автоматически.
      phonesContainer.addEventListener('paste', function(e) {
        const t = e.target;
        if (!t || t.tagName !== 'INPUT') return;
        const txt = (e.clipboardData || window.clipboardData).getData('text');
        const phones = extractPhones(txt);
        if (!phones || phones.length <= 1) return; // обычная вставка
        e.preventDefault();
        e.stopPropagation();
        t.value = phones[0] || '';
        for (let i = 1; i < phones.length; i++) {
          addPhoneRow(phones[i]);
        }
      });
    }

    function applyDraftFull(form, draft) {
      if (!form || !draft) return;
      const hadData = hasUserData(form);
      if (hadData) {
        const overwrite = window.confirm('Будут восстановлены данные из черновика и могут быть перезаписаны текущие значения. Продолжить?');
        if (!overwrite) return;
      }
      applyDraftToExistingControls(form, draft);
      restoreDynamicFromDraft(draft);
      if (draftBannerEl) {
        draftBannerEl.classList.add('hidden');
      }
      formDirty = true;
      saveDraftSoon();
    }

    if (draftRestoreBtn) {
      draftRestoreBtn.addEventListener('click', function() {
        const draft = draftSnapshot || loadDraft();
        if (!draft) return;
        applyDraftFull(formEl, draft);
      });
    }

    if (draftDiscardBtn) {
      draftDiscardBtn.addEventListener('click', function() {
        try {
          localStorage.removeItem(DRAFT_KEY);
        } catch (e) {}
        draftSnapshot = null;
        if (draftBannerEl) {
          draftBannerEl.classList.add('hidden');
        }
      });
    }

    // Автоматическое форматирование времени в поле "Режим работы"
    const workScheduleField = document.querySelector('textarea[name="work_schedule"]');
    if (workScheduleField) {
      // Функция для форматирования времени в формат HH:MM
      function formatTimeInText(text) {
        // Регулярное выражение для поиска времени в различных форматах:
        // - 9:00, 9-00, 9.00, 09:00, 09-00, 09.00
        // - 9:00-18:00, 9-00-18-00, 9.00.18.00
        // - 9:00:00 (с секундами)
        // Паттерн: одна или две цифры, затем : или - или ., затем две цифры (опционально еще : или - или . и еще время)
        return text.replace(/\b(\d{1,2})[:.\-](\d{2})(?::\d{2})?\b/g, function(match, hours, minutes) {
          // Форматируем часы: добавляем ведущий ноль, если нужно
          const formattedHours = hours.padStart(2, '0');
          // Возвращаем в формате HH:MM
          return formattedHours + ':' + minutes;
        });
      }

      function normalizeScheduleText(text) {
        let s = String(text || '');
        // unify line separators
        s = s.replace(/;+/g, '\n');
        // normalize dashes
        s = s.replace(/[—–−]/g, '-');
        // normalize time dashes to en-dash
        s = s.replace(/(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})/g, '$1–$2');
        // normalize day ranges and add colon if missing (best-effort)
        s = s.replace(/\b(пн|вт|ср|чт|пт|сб|вс)\s*-\s*(пн|вт|ср|чт|пт|сб|вс)\b/gi, function(_, a, b){
          const cap = x => x.charAt(0).toUpperCase() + x.slice(1).toLowerCase();
          return cap(a) + '–' + cap(b);
        });
        s = s.replace(/\b(пн|вт|ср|чт|пт|сб|вс)\b/gi, function(m){
          return m.charAt(0).toUpperCase() + m.slice(1).toLowerCase();
        });
        // add colon after leading day spec if missing and times exist
        s = s.split('\n').map(function(line){
          const l = line.trim();
          if (!l) return '';
          if (l.includes(':')) return l;
          if (/\b(Пн|Вт|Ср|Чт|Пт|Сб|Вс)(?:[–, ](Пн|Вт|Ср|Чт|Пт|Сб|Вс))*/.test(l) && /\d{1,2}:\d{2}/.test(l)) {
            return l.replace(/^((?:Пн|Вт|Ср|Чт|Пт|Сб|Вс)(?:[–, ](?:Пн|Вт|Ср|Чт|Пт|Сб|Вс))*)\s+/, '$1: ');
          }
          return l;
        }).filter(Boolean).join('\n');
        return s.trim();
      }

      // Обработчик события input (при вводе)
      workScheduleField.addEventListener('input', function(e) {
        const cursorPosition = e.target.selectionStart;
        const originalValue = e.target.value;
        const formattedValue = formatTimeInText(originalValue);

        if (formattedValue !== originalValue) {
          // Вычисляем новую позицию курсора
          const diff = formattedValue.length - originalValue.length;
          e.target.value = formattedValue;
          // Восстанавливаем позицию курсора с учетом изменений
          e.target.setSelectionRange(cursorPosition + diff, cursorPosition + diff);
        }
      });

      // Обработчик события blur (при потере фокуса) - финальное форматирование
      workScheduleField.addEventListener('blur', function(e) {
        const formattedValue = normalizeScheduleText(formatTimeInText(e.target.value));
        if (formattedValue !== e.target.value) e.target.value = formattedValue;
      });
    }

    // Инициализация ms-виджетов после возможного восстановления черновика
    if (window.initMsSingleWidgets) {
      window.initMsSingleWidgets();
    }
    if (window.initMsMultiWidgets) {
      window.initMsMultiWidgets();
    }
    if (window.initMsWidgets) {
      window.initMsWidgets();
    }
  });
