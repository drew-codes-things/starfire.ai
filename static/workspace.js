'use strict';

els.output.addEventListener('scroll', () => {
  const distance = els.output.scrollHeight - els.output.scrollTop - els.output.clientHeight;
  $('scrollBottomBtn').hidden = distance < SCROLL_BUTTON_THRESHOLD_PX;
});
$('scrollBottomBtn').onclick = () => { els.output.scrollTo({ top: els.output.scrollHeight, behavior: 'smooth' }); };

function copyText(text) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(
    () => showToast('Copied to clipboard'),
    () => showToast('Could not copy to the clipboard', 'error'),
  );
}

document.addEventListener('keydown', e => {
  const key = e.key.toLowerCase();
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.shiftKey && key === 'o') {
    e.preventDefault();
    clearChat();
    els.input.focus();
  } else if (mod && e.shiftKey && key === 's') {
    e.preventDefault();
    toggleSidebar();
  } else if (mod && e.shiftKey && key === 'c') {
    e.preventDefault();
    const last = [...messages].reverse().find(m => m.role === 'assistant');
    if (last) copyText(last.content);
  } else if (mod && e.shiftKey && (key === ';' || key === ':')) {
    e.preventDefault();
    const blocks = els.output.querySelectorAll('.line.ai pre code');
    if (blocks.length) copyText(blocks[blocks.length - 1].textContent);
  } else if (e.shiftKey && key === 'escape') {
    e.preventDefault();
    els.input.focus();
  } else if (key === 'escape' && !e.defaultPrevented) {
    closeAllMenus();
    if (!archivedEls.modal.hidden) archivedEls.modal.hidden = true;
  }
});

const chatFromHash = (location.hash.match(/^#chat=([\w-]+)$/) || [])[1];
if (chatFromHash) currentSessionId = chatFromHash;

function closeAccountMenu() {
  $('accountMenu').hidden = true;
  $('accountMenuBtn').setAttribute('aria-expanded', 'false');
}

$('accountMenuBtn').onclick = e => {
  e.stopPropagation();
  const opening = $('accountMenu').hidden;
  closeAllMenus();
  $('accountMenu').hidden = !opening;
  $('accountMenuBtn').setAttribute('aria-expanded', String(opening));
  if (opening) $('accountMenu').querySelector('button:not([hidden])').focus();
};
$('accountMenu').addEventListener('click', e => { if (e.target.closest('button')) closeAccountMenu(); });
$('accountMenu').addEventListener('keydown', e => {
  const buttons = [...$('accountMenu').querySelectorAll('button:not([hidden])')];
  const at = buttons.indexOf(document.activeElement);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    buttons[(at + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus();
  }
});

function openShortcuts() {
  $('shortcutsModal').hidden = false;
  $('shortcutsCloseBtn').focus();
}

function closeShortcuts() {
  $('shortcutsModal').hidden = true;
}

$('shortcutsBtn').onclick = openShortcuts;
$('openShortcutsBtn').onclick = openShortcuts;
$('shortcutsCloseBtn').onclick = closeShortcuts;
$('shortcutsModal').addEventListener('click', e => { if (e.target === $('shortcutsModal')) closeShortcuts(); });

const ENTER_PRESSES = [['newMemoryText', 'addMemoryEntryBtn'], ['noteNewItem', 'noteAddItemBtn'], ['presetName', 'savePresetBtn']];
ENTER_PRESSES.forEach(([field, button]) => {
  $(field).addEventListener('keydown', submitOnEnter(() => $(button).click()));
});

refreshActiveTools();
refreshSidebar();
loadPrompts();
