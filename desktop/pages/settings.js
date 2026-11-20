(function () {
  const bridge = window.starfireDesktop;
  const status = document.getElementById('status');
  const shortcutValue = document.getElementById('shortcut-value');
  const shortcutChange = document.getElementById('shortcut-change');
  const shortcutHelp = document.getElementById('shortcut-help');
  const addressValue = document.getElementById('address-value');
  const TOGGLES = ['startWithSystem', 'notifications', 'closeToTray', 'artyLink', 'artyAutoLink'];
  const linkLight = document.getElementById('link-light');
  const linkLabel = document.getElementById('link-label');
  const linkPairing = document.getElementById('link-pairing');
  const linkPaired = document.getElementById('link-paired');
  const linkDetail = document.getElementById('link-detail');
  const linkPairedTitle = document.getElementById('link-paired-title');
  const linkPairingHelp = document.getElementById('link-pairing-help');
  const CODE_HELP = 'In Arty Desktop open the tray menu, choose Link starfire, then Make a code. Type the 6 digits here within two minutes.';
  const AUTO_LINK_HELP = 'starfire links by itself when Arty Desktop is running as you on this PC. If it does not, pair with a code:';
  const linkCode = document.getElementById('link-code');
  const linkPairButton = document.getElementById('link-pair');
  const capabilityPanel = document.getElementById('link-capabilities');
  const capabilityList = document.getElementById('capability-list');
  const CAPABILITY_LABELS = { 'screen.look': 'Look at your screen', 'browser.open': 'Open pages in your browsers', 'browser.own': "Arty's own browser", 'screen.control': 'Use your mouse and keyboard', quiz: 'Quiz helper' };
  const LINK_LABELS = { off: 'Off', unpaired: 'Not paired', unreachable: 'Arty Desktop is not running', unproven: 'Arty Desktop did not prove itself', connected: 'Connected' };
  const LINK_POLL_MS = 3000;
  const STEP_PREVIEW_CHARS = 300;
  const DEFAULT_HELP = shortcutHelp.textContent;
  let capturing = false;
  let autoLinkOn = true;

  function say(text, kind) {
    status.textContent = text;
    status.className = kind ? `status ${kind}` : 'status';
  }

  function render(settings, shortcutProblem) {
    shortcutValue.textContent = settings.shortcut;
    addressValue.textContent = settings.address || 'Not set';
    for (const key of TOGGLES) document.getElementById(key).checked = settings[key];
    autoLinkOn = settings.artyAutoLink === true;
    if (shortcutProblem) say(shortcutProblem, 'bad');
  }

  function linkDetailFor(state) {
    if (state.state === 'unreachable') return 'Open Arty Desktop on this PC to use the link.';
    if (state.state === 'unproven') return 'Nothing was sent. If you unlinked in Arty Desktop, Unpair here and pair again. If not, something else may be answering on its port.';
    return state.computer ? 'Arty Desktop computer control is on.' : 'Computer control is off in Arty Desktop, so it will refuse steps.';
  }

  function capabilityLabel(capability) {
    const label = CAPABILITY_LABELS[capability.id];
    const browsers = Array.isArray(capability.browsers) ? capability.browsers.map((browser) => browser.name) : [];
    return browsers.length > 0 ? `${label} (${browsers.join(', ')})` : label;
  }

  function capabilityItem(capability) {
    const item = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = capabilityLabel(capability);
    const availability = document.createElement('span');
    availability.className = capability.available ? 'capability-on' : 'capability-off';
    availability.textContent = capability.available ? 'available' : 'not available';
    item.append(name, availability);
    return item;
  }

  function renderCapabilities(state) {
    const known = state.state === 'connected' && Array.isArray(state.capabilities) ? state.capabilities.filter((capability) => Object.hasOwn(CAPABILITY_LABELS, capability.id)) : [];
    capabilityList.replaceChildren(...known.map(capabilityItem));
    capabilityPanel.hidden = known.length === 0;
  }

  function renderLink(state) {
    linkLight.dataset.state = state.state;
    linkLabel.textContent = LINK_LABELS[state.state];
    linkPairing.hidden = state.state !== 'unpaired';
    linkPaired.hidden = !(state.paired && state.state !== 'off');
    linkDetail.textContent = linkPaired.hidden ? '' : linkDetailFor(state);
    linkPairedTitle.textContent = state.linkedByItself ? 'Linked to Arty Desktop (it linked by itself)' : 'Linked to Arty Desktop';
    linkPairingHelp.textContent = `Not linked. ${autoLinkOn ? AUTO_LINK_HELP : CODE_HELP}`;
    renderCapabilities(state);
  }

  async function refreshLink() {
    if (document.hidden) return;
    renderLink(await bridge.linkStatus());
  }

  async function pairWithArty() {
    linkPairButton.disabled = true;
    const result = await bridge.linkPair(linkCode.value);
    linkPairButton.disabled = false;
    if (result.ok) {
      linkCode.value = '';
      say('Paired with Arty Desktop.', 'good');
    } else {
      say(result.error, 'bad');
    }
    await refreshLink();
  }

  async function tryTheLink() {
    say('Waiting for your OK in Arty Desktop...', '');
    const result = await bridge.linkTry();
    if (result.ok) say(`Arty Desktop says: ${result.result.slice(0, STEP_PREVIEW_CHARS)}`, 'good');
    else say(result.declined ? 'You said no in Arty Desktop.' : result.error, 'bad');
  }

  async function unpairFromArty() {
    await bridge.linkUnpair();
    say('Unpaired.', 'good');
    await refreshLink();
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
    document.getElementById(key).addEventListener('change', (event) => save({ [key]: event.target.checked }).then(refreshLink));
  }
  linkPairButton.addEventListener('click', pairWithArty);
  linkCode.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') pairWithArty();
  });
  document.getElementById('link-test').addEventListener('click', tryTheLink);
  document.getElementById('link-unpair').addEventListener('click', unpairFromArty);
  document.getElementById('address-change').addEventListener('click', () => bridge.changeAddress());

  bridge.getSettings().then(({ settings, shortcutProblem }) => render(settings, shortcutProblem)).then(refreshLink);
  setInterval(refreshLink, LINK_POLL_MS);
})();
