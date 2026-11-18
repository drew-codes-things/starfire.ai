(function () {
  const bridge = window.starfireDesktop;
  const status = document.getElementById('status');
  const shortcutValue = document.getElementById('shortcut-value');
  const shortcutChange = document.getElementById('shortcut-change');
  const shortcutHelp = document.getElementById('shortcut-help');
  const addressValue = document.getElementById('address-value');
  const TOGGLES = ['startWithSystem', 'notifications', 'closeToTray'];
  const DEFAULT_HELP = shortcutHelp.textContent;
  let capturing = false;

  function say(text, kind) {
    status.textContent = text;
    status.className = kind ? `status ${kind}` : 'status';
  }

  function render(settings, shortcutProblem) {
    shortcutValue.textContent = settings.shortcut;
    addressValue.textContent = settings.address || 'Not set';
    for (const key of TOGGLES) document.getElementById(key).checked = settings[key];
    if (shortcutProblem) say(shortcutProblem, 'bad');
  }

  async function save(patch) {
    const result = await bridge.saveSettings(patch);
    if (result.ok) {
      render(result.settings, null);
      say('Saved.', 'good');
    } else {
      say(result.error, 'bad');
      const current = await bridge.getSettings();
      render(current.settings, null);
    }
    return result.ok;
  }

  function stopCapturing() {
    capturing = false;
    shortcutChange.textContent = 'Change';
    shortcutHelp.textContent = DEFAULT_HELP;
    document.removeEventListener('keydown', onKey, true);
  }

  async function onKey(event) {
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Escape') {
      stopCapturing();
      say('', '');
      return;
    }
    const described = await bridge.describeKey({
      code: event.code,
      ctrlKey: event.ctrlKey,
      altKey: event.altKey,
      shiftKey: event.shiftKey,
      metaKey: event.metaKey,
    });
    if (described.incomplete) {
      shortcutHelp.textContent = described.error;
      return;
    }
    if (!described.ok) {
      say(described.error, 'bad');
      return;
    }
    stopCapturing();
    await save({ shortcut: described.accelerator });
  }

  shortcutChange.addEventListener('click', () => {
    if (capturing) {
      stopCapturing();
      return;
    }
    capturing = true;
    shortcutChange.textContent = 'Cancel';
    shortcutHelp.textContent = 'Press the new keys now, for example Ctrl+Alt+K. Esc cancels.';
    say('', '');
    document.addEventListener('keydown', onKey, true);
  });

  for (const key of TOGGLES) {
    document.getElementById(key).addEventListener('change', (event) => save({ [key]: event.target.checked }));
  }
  document.getElementById('address-change').addEventListener('click', () => bridge.changeAddress());

  bridge.getSettings().then(({ settings, shortcutProblem }) => render(settings, shortcutProblem));
})();
