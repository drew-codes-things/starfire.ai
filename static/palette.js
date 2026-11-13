'use strict';

const paletteEls = { overlay: $('paletteOverlay'), input: $('paletteInput'), list: $('paletteList') };
let paletteActions = [];
let paletteActiveIndex = 0;

function buildPaletteActions() {
  paletteActions = [
    { label: 'New chat', run: clearChat },
    { label: 'Export chat as markdown', run: () => exportChat('md') },
    { label: 'Export chat as JSON', run: () => exportChat('json') },
    { label: 'Toggle sidebar', run: toggleSidebar },
    { label: 'Toggle chat controls', run: toggleControls },
    { label: 'Toggle temporary chat', run: () => $('tempChatToggle').click() },
    { label: 'Toggle side chat', run: toggleSideChat },
    { label: 'Archived chats', run: openArchived },
    { label: 'New folder', run: createFolder },
    { label: 'Open settings', run: openSettings },
    { label: 'Compare models', run: openCompare },
    { label: 'Toggle light/dark theme', run: toggleLightDark },
    { label: 'Keyboard shortcuts', run: openShortcuts },
  ];
  const isAdmin = document.body.classList.contains('is-admin');
  for (const tab of document.querySelectorAll('.settings-tab')) {
    if (tab.classList.contains('admin-only') && !isAdmin) continue;
    paletteActions.push({
      label: 'Settings → ' + tab.textContent,
      run: () => openSettings(tab.dataset.tab),
    });
  }
}

function renderPalette(filter) {
  const q = filter.trim().toLowerCase();
  const matches = paletteActions.filter(a => a.label.toLowerCase().includes(q));
  paletteEls.list.innerHTML = '';
  paletteActiveIndex = 0;
  matches.forEach((action, idx) => {
    const li = document.createElement('li');
    li.textContent = action.label;
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', String(idx === 0));
    if (idx === 0) li.classList.add('active');
    li.onclick = () => runPaletteAction(action);
    paletteEls.list.appendChild(li);
  });
  if (!matches.length) paletteEls.list.innerHTML = '<li class="palette-empty">No matching commands</li>';
  return matches;
}

let paletteMatches = [];

function openPalette() {
  if (!paletteActions.length) buildPaletteActions();
  paletteEls.overlay.hidden = false;
  paletteEls.input.value = '';
  paletteMatches = renderPalette('');
  paletteEls.input.focus();
}
function closePalette() { paletteEls.overlay.hidden = true; }

function runPaletteAction(action) {
  closePalette();
  action.run();
}

paletteEls.input.addEventListener('input', () => { paletteMatches = renderPalette(paletteEls.input.value); });
paletteEls.overlay.addEventListener('click', e => { if (e.target === paletteEls.overlay) closePalette(); });
paletteEls.input.addEventListener('keydown', e => {
  const items = [...paletteEls.list.children];
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    paletteActiveIndex = Math.min(paletteActiveIndex + 1, items.length - 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    paletteActiveIndex = Math.max(paletteActiveIndex - 1, 0);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (paletteMatches[paletteActiveIndex]) runPaletteAction(paletteMatches[paletteActiveIndex]);
    return;
  } else if (e.key === 'Escape') {
    closePalette();
    return;
  } else {
    return;
  }
  items.forEach((li, idx) => {
    li.classList.toggle('active', idx === paletteActiveIndex);
    li.setAttribute('aria-selected', String(idx === paletteActiveIndex));
  });
  if (items[paletteActiveIndex]) items[paletteActiveIndex].scrollIntoView({ block: 'nearest' });
});
