(() => {
  'use strict';
  const storage = {
    get(key, fallback) { try { return JSON.parse(localStorage.getItem('mainichi:' + key)) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem('mainichi:' + key, JSON.stringify(value)); } catch { /* Reading works without storage. */ } }
  };
  let ruby = storage.get('ruby', true) !== false;
  let fontSize = Number(storage.get('font-size', 22));
  if (!Number.isFinite(fontSize)) fontSize = 22;
  fontSize = Math.max(18, Math.min(30, fontSize));
  const savedRead = storage.get('read', []);
  const read = new Set(Array.isArray(savedRead) ? savedRead.filter(x => typeof x === 'string') : []);
  let filter = 'all';
  function updateRuby() {
    document.documentElement.classList.toggle('hide-ruby', !ruby);
    document.querySelectorAll('[data-ruby-toggle]').forEach(button => {
      button.textContent = 'Furigana ' + (ruby ? 'on' : 'off');
      button.setAttribute('aria-pressed', String(ruby));
    });
  }
  function updateSize() {
    document.documentElement.style.setProperty('--body-size', fontSize + 'px');
    document.querySelectorAll('[data-font]').forEach(button => {
      button.disabled = button.dataset.font === 'smaller' ? fontSize <= 18 : fontSize >= 30;
    });
  }
  function updateCards() {
    let visible = 0;
    document.querySelectorAll('[data-story]').forEach(card => {
      const isRead = read.has(card.dataset.story);
      card.querySelector('.read-badge').hidden = !isRead;
      card.hidden = filter === 'unread' && isRead;
      if (!card.hidden) visible++;
    });
    document.querySelectorAll('.edition').forEach(section => {
      section.hidden = !section.querySelector('[data-story]:not([hidden])');
    });
    const empty = document.getElementById('filter-empty');
    if (empty) empty.hidden = filter !== 'unread' || visible > 0;
  }
  document.querySelectorAll('.library-tools,.reader-tools,[data-copy]').forEach(el => { el.hidden = false; });
  document.querySelectorAll('[data-ruby-toggle]').forEach(button => button.addEventListener('click', () => {
    ruby = !ruby; storage.set('ruby', ruby); updateRuby();
  }));
  document.querySelectorAll('[data-font]').forEach(button => button.addEventListener('click', () => {
    fontSize = Math.max(18, Math.min(30, fontSize + (button.dataset.font === 'larger' ? 2 : -2)));
    storage.set('font-size', fontSize); updateSize();
  }));
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
    filter = button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(b => {
      b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', String(b === button));
    });
    updateCards();
  }));
  const reader = document.querySelector('[data-reader-id]');
  const mark = document.getElementById('mark-read');
  if (reader && mark) {
    mark.hidden = false;
    const updateMark = () => {
      const done = read.has(reader.dataset.readerId);
      mark.textContent = done ? 'Read ✓ · Mark unread' : 'Mark as read ✓';
      mark.setAttribute('aria-pressed', String(done));
    };
    mark.addEventListener('click', () => {
      const id = reader.dataset.readerId;
      read.has(id) ? read.delete(id) : read.add(id);
      storage.set('read', [...read].slice(-500)); updateMark();
    });
    updateMark();
  }
  const audio = document.querySelector('audio');
  const speed = document.getElementById('audio-speed');
  if (audio && speed) speed.addEventListener('change', () => { audio.playbackRate = Number(speed.value); });
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copy);
    const status = document.getElementById('copy-status');
    try {
      await navigator.clipboard.writeText(input.value);
      status.textContent = 'Feed address copied. Paste it into your app.';
      button.textContent = 'Copied ✓';
      setTimeout(() => { button.textContent = 'Copy'; }, 2500);
    } catch {
      input.focus(); input.select(); input.setSelectionRange(0, input.value.length);
      status.textContent = 'Select and copy the feed address above.';
    }
  }));
  updateRuby(); updateSize(); updateCards();
})();
