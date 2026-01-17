// Кастомный календарик для datetime-local с подписями "Часы" и "Минуты"
(function() {
  function initCustomDatetimePicker() {
    document.querySelectorAll('input[type="datetime-local"]').forEach(function(input) {
      if (input.dataset.customPickerInitialized) return;
      input.dataset.customPickerInitialized = 'true';
      
      // Скрываем нативный календарик
      input.style.position = 'absolute';
      input.style.opacity = '0';
      input.style.width = '100%';
      input.style.height = '100%';
      input.style.pointerEvents = 'none';
      
      // Создаем обёртку
      const wrapper = document.createElement('div');
      wrapper.className = 'custom-datetime-picker';
      wrapper.style.position = 'relative';
      input.parentNode.insertBefore(wrapper, input);
      wrapper.appendChild(input);
      
      // Создаем визуальный input
      const visualInput = document.createElement('div');
      visualInput.className = 'input';
      visualInput.style.cursor = 'pointer';
      visualInput.style.display = 'flex';
      visualInput.style.alignItems = 'center';
      visualInput.style.justifyContent = 'space-between';
      wrapper.appendChild(visualInput);
      
      // Обновляем визуальный input
      function updateVisualInput() {
        if (input.value) {
          const date = new Date(input.value);
          const formatted = date.toLocaleString('ru-RU', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
          });
          visualInput.textContent = formatted;
          visualInput.style.color = 'var(--brand-dark)';
        } else {
          visualInput.textContent = 'Выберите дату и время';
          visualInput.style.color = 'rgba(0,61,56,.5)';
        }
      }
      updateVisualInput();
      
      // Создаем overlay и popup
      const overlay = document.createElement('div');
      overlay.className = 'custom-datetime-overlay';
      document.body.appendChild(overlay);
      
      const popup = document.createElement('div');
      popup.className = 'custom-datetime-popup';
      overlay.appendChild(popup);
      
      let currentDate = input.value ? new Date(input.value) : new Date();
      let selectedDate = currentDate;
      let selectedHour = currentDate.getHours();
      let selectedMinute = currentDate.getMinutes();
      
      function renderCalendar() {
        const year = currentDate.getFullYear();
        const month = currentDate.getMonth();
        const firstDay = new Date(year, month, 1);
        const lastDay = new Date(year, month + 1, 0);
        const daysInMonth = lastDay.getDate();
        const startDay = firstDay.getDay() === 0 ? 6 : firstDay.getDay() - 1;
        
        const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
        const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
        
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        
        popup.innerHTML = `
          <div class="calendar-header">
            <div class="month-year">${monthNames[month]} ${year}</div>
            <div class="nav-buttons">
              <button class="prev-month">‹</button>
              <button class="next-month">›</button>
            </div>
          </div>
          <div class="calendar-weekdays">
            ${weekdays.map(day => `<span>${day}</span>`).join('')}
          </div>
          <div class="calendar-days">
            ${Array(startDay).fill(0).map((_, i) => {
              const date = new Date(year, month, -startDay + i + 1);
              return `<div class="calendar-day other-month">${date.getDate()}</div>`;
            }).join('')}
            ${Array(daysInMonth).fill(0).map((_, i) => {
              const day = i + 1;
              const date = new Date(year, month, day);
              date.setHours(0, 0, 0, 0);
              const isToday = date.getTime() === today.getTime();
              const isSelected = selectedDate && date.getTime() === selectedDate.getTime();
              return `<div class="calendar-day ${isToday ? 'today' : ''} ${isSelected ? 'selected' : ''}" data-day="${day}">${day}</div>`;
            }).join('')}
            ${Array(42 - startDay - daysInMonth).fill(0).map((_, i) => {
              const date = new Date(year, month + 1, i + 1);
              return `<div class="calendar-day other-month">${date.getDate()}</div>`;
            }).join('')}
          </div>
          <div class="time-picker">
            <div class="time-column">
              <div class="time-column-label">Часы</div>
              <div class="time-scroll" id="hours-scroll">
                ${Array(24).fill(0).map((_, i) => {
                  const hour = String(i).padStart(2, '0');
                  return `<div class="time-item ${i === selectedHour ? 'selected' : ''}" data-hour="${i}">${hour}</div>`;
                }).join('')}
              </div>
            </div>
            <div class="time-column">
              <div class="time-column-label">Минуты</div>
              <div class="time-scroll" id="minutes-scroll">
                ${Array(60).fill(0).map((_, i) => {
                  const minute = String(i).padStart(2, '0');
                  return `<div class="time-item ${i === selectedMinute ? 'selected' : ''}" data-minute="${i}">${minute}</div>`;
                }).join('')}
              </div>
            </div>
          </div>
          <div class="calendar-actions">
            <button class="btn-delete">Удалить</button>
            <button class="btn-today">Сегодня</button>
            <button class="btn-primary btn-apply">Применить</button>
          </div>
        `;
        
        // Обработчики событий
        popup.querySelector('.prev-month').addEventListener('click', () => {
          currentDate.setMonth(currentDate.getMonth() - 1);
          renderCalendar();
        });
        
        popup.querySelector('.next-month').addEventListener('click', () => {
          currentDate.setMonth(currentDate.getMonth() + 1);
          renderCalendar();
        });
        
        popup.querySelectorAll('.calendar-day:not(.other-month)').forEach(day => {
          day.addEventListener('click', () => {
            const dayNum = parseInt(day.dataset.day);
            selectedDate = new Date(year, month, dayNum);
            renderCalendar();
          });
        });
        
        popup.querySelectorAll('.time-item[data-hour]').forEach(item => {
          item.addEventListener('click', () => {
            selectedHour = parseInt(item.dataset.hour);
            renderCalendar();
            // Прокручиваем к выбранному часу
            setTimeout(() => {
              const selectedElement = popup.querySelector(`.time-item[data-hour="${selectedHour}"]`);
              if (selectedElement) {
                selectedElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
            }, 50);
          });
        });
        
        popup.querySelectorAll('.time-item[data-minute]').forEach(item => {
          item.addEventListener('click', () => {
            selectedMinute = parseInt(item.dataset.minute);
            renderCalendar();
            // Прокручиваем к выбранной минуте
            setTimeout(() => {
              const selectedElement = popup.querySelector(`.time-item[data-minute="${selectedMinute}"]`);
              if (selectedElement) {
                selectedElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
            }, 50);
          });
        });
        
        popup.querySelector('.btn-delete').addEventListener('click', () => {
          input.value = '';
          updateVisualInput();
          closePopup();
        });
        
        popup.querySelector('.btn-today').addEventListener('click', () => {
          const now = new Date();
          currentDate = new Date(now);
          selectedDate = new Date(now);
          selectedHour = now.getHours();
          selectedMinute = now.getMinutes();
          renderCalendar();
        });
        
        popup.querySelector('.btn-apply').addEventListener('click', () => {
          if (selectedDate) {
            selectedDate.setHours(selectedHour, selectedMinute, 0, 0);
            const isoString = selectedDate.toISOString().slice(0, 16);
            input.value = isoString;
            updateVisualInput();
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }
          closePopup();
        });
        
        // Прокручиваем к выбранным значениям
        setTimeout(() => {
          const selectedHourEl = popup.querySelector(`.time-item[data-hour="${selectedHour}"]`);
          const selectedMinuteEl = popup.querySelector(`.time-item[data-minute="${selectedMinute}"]`);
          if (selectedHourEl) {
            selectedHourEl.scrollIntoView({ behavior: 'auto', block: 'center' });
          }
          if (selectedMinuteEl) {
            selectedMinuteEl.scrollIntoView({ behavior: 'auto', block: 'center' });
          }
        }, 100);
      }
      
      function openPopup() {
        overlay.classList.add('active');
        const rect = visualInput.getBoundingClientRect();
        popup.style.top = (rect.bottom + 8) + 'px';
        popup.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 340)) + 'px';
        renderCalendar();
      }
      
      function closePopup() {
        overlay.classList.remove('active');
      }
      
      visualInput.addEventListener('click', openPopup);
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          closePopup();
        }
      });
      
      input.addEventListener('change', updateVisualInput);
    });
  }
  
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCustomDatetimePicker);
  } else {
    initCustomDatetimePicker();
  }
  
  const observer = new MutationObserver(initCustomDatetimePicker);
  observer.observe(document.body, { childList: true, subtree: true });
})();
