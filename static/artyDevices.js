'use strict';

const API = '/api/arty-devices';
const CODE_PARAM = 'code';
const CONFIRM_MS = 4000;
const COPIED_MS = 2000;
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const QR_LIGHT = '#ffffff';
const QR_DARK = '#000000';
const FORBIDDEN = 403;

function svg(tag, attributes) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}

function call(method, path, body) {
  return callApi(API, method, path, body);
}

function qrImage(text, label) {
  const { size, path } = qrPath(qrModules(window.qrcode, text));
  const image = svg('svg', { viewBox: `0 0 ${size} ${size}`, role: 'img', 'aria-label': label, class: 'arty-qr', 'shape-rendering': 'crispEdges' });
  image.append(svg('rect', { width: size, height: size, fill: QR_LIGHT }), svg('path', { d: path, fill: QR_DARK }));
  return image;
}

function twoStep(label, confirmLabel, action) {
  let armed = null;
  const button = el('button', { type: 'button', class: 'btn ghost' }, label);
  button.addEventListener('click', async () => {
    if (!armed) {
      button.textContent = confirmLabel;
      armed = setTimeout(() => { armed = null; button.textContent = label; }, CONFIRM_MS);
      return;
    }
    clearTimeout(armed);
    armed = null;
    button.disabled = true;
    await action();
  });
  return button;
}

function renderApprove(card, code) {
  const status = hint('Checking the request...');
  const buttons = el('div', { class: 'settings-row' });
  card.replaceChildren(el('h3', {}, 'Approve a computer'), el('p', { class: 'arty-code' }, code), status, buttons);
  const answer = async (approve) => {
    buttons.replaceChildren();
    try {
      await call('POST', '/approve', { code, approve });
      status.replaceWith(hint(approve ? 'Approved. Go back to the computer: it is connected now.' : 'Turned down. Nothing was shared.', 'ok'));
    } catch (error) {
      status.replaceWith(hint(error.message, 'error'));
    }
  };
  call('GET', `/pending?code=${encodeURIComponent(code)}`).then((pending) => {
    status.textContent = pendingSummary(pending);
    const approve = el('button', { type: 'button', class: 'btn send', disabled: true, onclick: () => answer(true) }, 'Approve');
    const typed = el('input', {
      type: 'text', autocomplete: 'off', autocapitalize: 'characters', spellcheck: 'false',
      placeholder: 'Code the computer shows', 'aria-label': 'Type the code the computer shows',
      oninput: (event) => { approve.disabled = !codesMatch(code, event.target.value); },
    });
    buttons.replaceChildren(typed, approve, el('button', { type: 'button', class: 'btn ghost', onclick: () => answer(false) }, 'Deny'));
    card.append(hint('Only approve a computer you started yourself. Type the code it shows: Approve only works when it matches.'));
  }).catch((error) => {
    status.replaceWith(hint(error.message, 'error'));
  });
}

function remoteControlSwitch(card, device) {
  const toggle = el('input', {
    type: 'checkbox', class: 'switch', checked: Boolean(device.remote_control),
    'aria-label': `Let ${device.name || 'this device'} control Arty CLI`,
  });
  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    try {
      await call('POST', `/${encodeURIComponent(device.id)}/remote-control`, { allow: toggle.checked });
    } catch (error) {
      toggle.checked = !toggle.checked;
      card.append(hint(error.message, 'error'));
    } finally {
      toggle.disabled = false;
    }
  });
  return el('label', { class: 'arty-remote-switch', title: 'Can follow and steer Arty CLI sessions' }, toggle, 'Arty CLI');
}

async function renderDevices(card) {
  const list = el('ul', { class: 'arty-device-list' });
  card.replaceChildren(el('h3', {}, 'Approved devices'), list);
  try {
    const { devices } = await call('GET', '');
    if (!devices.length) {
      list.replaceWith(hint('No devices yet.'));
      return;
    }
    list.replaceChildren(...devices.map((device) => el('li', {},
      el('span', { class: 'grow' }, deviceLine(device, navigator.language)),
      remoteControlSwitch(card, device),
      twoStep('Turn off', 'Tap again to turn off', async () => {
        try {
          await call('DELETE', `/${encodeURIComponent(device.id)}`);
        } catch (error) {
          card.append(hint(error.message, 'error'));
        }
        renderDevices(card);
      }))));
  } catch (error) {
    list.replaceWith(hint(error.message, 'error'));
  }
}

function showMadeKey(result, made, onDone) {
  const keyField = el('input', { type: 'text', readonly: true, value: made.api_key, 'aria-label': 'The new key', spellcheck: 'false' });
  const copy = el('button', { type: 'button', class: 'btn ghost' }, 'Copy key');
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(made.api_key);
      copy.textContent = 'Copied';
    } catch {
      keyField.select();
      copy.textContent = 'Press Ctrl+C';
    }
    setTimeout(() => { copy.textContent = 'Copy key'; }, COPIED_MS);
  });
  const done = el('button', { type: 'button', class: 'btn send', onclick: onDone }, 'Done');
  result.replaceChildren(
    hint(`Made a key for “${made.name}”. It is shown only now: copy it, or scan the code in Arty on your phone (Scan QR code).`, 'ok'),
    el('div', { class: 'settings-row' }, keyField, copy),
    el('div', { class: 'arty-qr-slot' }, qrImage(made.setup_code, `Phone setup code for ${made.name}`)),
    el('div', { class: 'settings-row' }, done),
  );
}

function renderMakeKey(card, devicesCard) {
  const name = el('input', { type: 'text', maxlength: '80', placeholder: 'Name, e.g. My phone', 'aria-label': 'Name for the new key', autocomplete: 'off' });
  const make = el('button', { type: 'submit', class: 'btn send' }, 'Make key');
  const result = el('div');
  const form = el('form', { novalidate: true }, el('div', { class: 'settings-row' }, name, make));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!name.value.trim()) {
      result.replaceChildren(hint('Give the key a name so you know which device it is.', 'error'));
      return;
    }
    make.disabled = true;
    try {
      const made = await call('POST', '/keys', { name: name.value.trim() });
      name.value = '';
      showMadeKey(result, made, () => result.replaceChildren());
      renderDevices(devicesCard);
    } catch (error) {
      result.replaceChildren(hint(error.message, 'error'));
    } finally {
      make.disabled = false;
    }
  });
  card.replaceChildren(el('h3', {}, 'Make a key'),
    hint('For a phone or a computer that can\'t open the approve link. The key works until you turn it off above.'), form, result);
}

async function start() {
  const page = document.getElementById('artyDevices');
  const code = normalisedCode(new URLSearchParams(window.location.search).get(CODE_PARAM));
  try {
    const status = await call('GET', '/status');
    if (!status.ready) {
      page.replaceChildren(hint(status.message, 'error'));
      return;
    }
  } catch (error) {
    const message = error.status === FORBIDDEN ? 'Only an admin can manage Arty\'s devices. Log in with an admin account.' : error.message;
    page.replaceChildren(hint(message, 'error'));
    return;
  }
  const approveCard = el('section', { class: 'settings-card' });
  const devicesCard = el('section', { class: 'settings-card' });
  const keyCard = el('section', { class: 'settings-card' });
  page.replaceChildren(...(code ? [approveCard] : []), devicesCard, keyCard);
  if (code) renderApprove(approveCard, code);
  renderDevices(devicesCard);
  renderMakeKey(keyCard, devicesCard);
}

start();
