'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'uiLogic.js'));

const NOON_ON_11_NOV = new Date(2026, 10, 11, 12, 0, 0);

function daysBefore(days, hour = 9) {
  return new Date(2026, 10, 11 - days, hour, 0, 0).toISOString();
}

function fakeDocument() {
  const appended = [];
  const box = { appendChild: node => appended.push(node) };
  return {
    appended,
    getElementById: id => (id === 'toasts' ? box : null),
    createElement: () => {
      const classes = new Set();
      const node = {
        attributes: {},
        className: '',
        textContent: '',
        removed: false,
        classList: { add: name => classes.add(name), contains: name => classes.has(name) },
        setAttribute(name, value) { node.attributes[name] = value; },
        remove() { node.removed = true; },
      };
      return node;
    },
  };
}

test('chats are grouped by how long ago they were updated', () => {
  assert.equal(logic.dateGroupLabel(daysBefore(0, 8), NOON_ON_11_NOV), 'Today');
  assert.equal(logic.dateGroupLabel(daysBefore(1, 23), NOON_ON_11_NOV), 'Yesterday');
  assert.equal(logic.dateGroupLabel(daysBefore(3), NOON_ON_11_NOV), 'Previous 7 days');
  assert.equal(logic.dateGroupLabel(daysBefore(20), NOON_ON_11_NOV), 'Previous 30 days');
  assert.match(logic.dateGroupLabel(daysBefore(90), NOON_ON_11_NOV), /2026/);
});

test('a chat updated in the future still counts as today', () => {
  assert.equal(logic.dateGroupLabel(new Date(2026, 10, 12, 9).toISOString(), NOON_ON_11_NOV), 'Today');
});

test('a chat with no usable date goes under Older', () => {
  assert.equal(logic.dateGroupLabel('', NOON_ON_11_NOV), 'Older');
  assert.equal(logic.dateGroupLabel('not a date', NOON_ON_11_NOV), 'Older');
});

test('the saved theme wins, then the old light setting, then dark', () => {
  const reader = settings => key => (key in settings ? settings[key] : null);
  assert.equal(logic.themeChoiceFrom(reader({ sf_theme: 'system', sf_light: '1' })), 'system');
  assert.equal(logic.themeChoiceFrom(reader({ sf_theme: 'light' })), 'light');
  assert.equal(logic.themeChoiceFrom(reader({ sf_light: '1' })), 'light');
  assert.equal(logic.themeChoiceFrom(reader({ sf_light: '0' })), 'dark');
  assert.equal(logic.themeChoiceFrom(reader({})), 'dark');
  assert.equal(logic.themeChoiceFrom(reader({ sf_theme: 'purple' })), 'dark');
});

test('system theme follows the operating system', () => {
  assert.equal(logic.themeIsLight('system', true), true);
  assert.equal(logic.themeIsLight('system', false), false);
  assert.equal(logic.themeIsLight('light', false), true);
  assert.equal(logic.themeIsLight('dark', true), false);
});

test('a toast is announced, fades and is removed', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  global.document = fakeDocument();
  t.after(() => { delete global.document; });

  const toast = logic.showToast('Copied to clipboard');
  assert.equal(global.document.appended.length, 1);
  assert.equal(toast.textContent, 'Copied to clipboard');
  assert.equal(toast.className, 'toast');
  assert.equal(toast.attributes.role, 'status');

  t.mock.timers.tick(logic.TOAST_MS - 1);
  assert.equal(toast.classList.contains('leaving'), false);
  t.mock.timers.tick(1);
  assert.equal(toast.classList.contains('leaving'), true);
  assert.equal(toast.removed, false);
  t.mock.timers.tick(logic.TOAST_FADE_MS);
  assert.equal(toast.removed, true);
});

test('an error toast is an alert and stays longer', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  global.document = fakeDocument();
  t.after(() => { delete global.document; });

  const toast = logic.showToast('Could not save', 'error');
  assert.equal(toast.className, 'toast error');
  assert.equal(toast.attributes.role, 'alert');
  t.mock.timers.tick(logic.TOAST_MS);
  assert.equal(toast.classList.contains('leaving'), false);
  t.mock.timers.tick(logic.TOAST_ERROR_MS - logic.TOAST_MS);
  assert.equal(toast.classList.contains('leaving'), true);
});

test('an empty toast or a page with no toast area shows nothing', t => {
  global.document = fakeDocument();
  t.after(() => { delete global.document; });
  assert.equal(logic.showToast(''), null);
  global.document.getElementById = () => null;
  assert.equal(logic.showToast('Saved'), null);
});

test('removing something asks first with a danger dialog', async () => {
  const asked = [];
  const yes = await logic.confirmRemove('this preset', async question => { asked.push(question); return true; });
  assert.equal(yes, true);
  assert.deepEqual(asked, [{ title: 'Remove this preset?', text: 'This cannot be undone.', okLabel: 'Remove', danger: true }]);
});

test('cancelling the dialog means nothing is removed', async () => {
  assert.equal(await logic.confirmRemove('this memory', async () => null), false);
  assert.equal(await logic.confirmRemove('this memory', async () => false), false);
});

test('waiting approvals become cards the chat can draw', () => {
  const cards = logic.pendingApprovalCards(
    [{ id: 'abc123', command: 'df -h', cwd: '/srv', created: 1 }],
    [{ id: 'def456', path: '/srv/app.yaml', diff: '--- a\n+++ b', content: 'secret file body' }],
  );
  assert.deepEqual(cards, [
    { kind: 'shell', id: 'abc123', data: { pending_id: 'abc123', command: 'df -h', cwd: '/srv', tool: 'run_shell', untrusted: false, unsandboxed: false } },
    { kind: 'edit', id: 'def456', data: { staged: true, pending_id: 'def456', path: '/srv/app.yaml', diff: '--- a\n+++ b', untrusted: false } },
  ]);
});

test('a waiting card keeps which tool asked and whether untrusted content was read first', () => {
  const [memory, edit] = logic.pendingApprovalCards(
    [{ id: 'm1', command: '{"action": "add"}', tool: 'manage_memory', untrusted: true }],
    [{ id: 'e1', path: '/w/a.txt', diff: '', untrusted: true }],
  );
  assert.equal(memory.data.tool, 'manage_memory');
  assert.equal(memory.data.untrusted, true);
  assert.equal(edit.data.untrusted, true);
  assert.equal(logic.pendingApprovalCards([{ id: 'x', untrusted: 'yes' }], [])[0].data.untrusted, false);
});

test('an approval card says which tool is waiting', () => {
  assert.equal(logic.approvalHeading({ tool: 'run_shell' }), 'shell command - waiting for your approval');
  assert.equal(logic.approvalHeading({}), 'shell command - waiting for your approval');
  assert.equal(logic.approvalHeading({ tool: 'manage_email' }), 'manage_email - waiting for your approval to run with these details');
  assert.equal(logic.approvalHeading({ tool: 'mcp__files__write_file' }), 'write_file - waiting for your approval to run with these details');
  assert.match(logic.UNTRUSTED_APPROVAL_NOTE, /untrusted content/i);
});

function cardText(fields) {
  return JSON.stringify(fields, null, 2);
}

test('an approval card heading names the action, the recipient and the mailbox of the call', () => {
  const shown = cardText({ action: 'send', to: 'attacker@evil.example', account_id: 'box1', body: 'x'.repeat(18000) });
  assert.equal(
    logic.approvalHeading({ tool: 'manage_email', command: shown }),
    'manage_email (action: "send", to: "attacker@evil.example", account_id: "box1") - waiting for your approval to run with these details',
  );
  assert.equal(
    logic.approvalHeading({ tool: 'manage_email', command: cardText({ action: 'send', to: ['a@x.example', 'b@y.example'], body: 'hi' }) }),
    'manage_email (action: "send", to: [ "a@x.example", "b@y.example" ]) - waiting for your approval to run with these details',
  );
  assert.equal(
    logic.approvalHeading({ tool: 'manage_tasks', command: cardText({ action: 'create', prompt: 'p' }) }),
    'manage_tasks (action: "create") - waiting for your approval to run with these details',
  );
  assert.equal(logic.approvalHeading({ tool: 'run_shell', command: cardText({ action: 'send' }) }),
    'shell command - waiting for your approval');
  assert.equal(logic.approvalHeading({ tool: 'manage_notes', command: 'not json' }),
    'manage_notes - waiting for your approval to run with these details');
});

test('a long or padded value in an approval heading is shortened where everyone can see it', () => {
  const padded = cardText({ action: 'send', to: ' '.repeat(5000) + 'attacker@evil.example' });
  assert.match(logic.approvalHeading({ tool: 'manage_email', command: padded }), /to: " attacker@evil\.example"/);
  const long = cardText({ action: 'send', to: 'a'.repeat(1000) + '@evil.example' });
  const heading = logic.approvalHeading({ tool: 'manage_email', command: long });
  assert.ok(heading.length < 600, heading.length);
  assert.match(heading, /… \(\d+ more characters, see the full call below\)/);
});

test('missing or malformed approval lists give no cards', () => {
  assert.deepEqual(logic.pendingApprovalCards(undefined, undefined), []);
  assert.deepEqual(logic.pendingApprovalCards({ not: 'a list' }, null), []);
  assert.deepEqual(logic.pendingApprovalCards([null, {}, { id: '' }, { id: 7 }], [{ path: '/x' }]), []);
});

test('a chat with its own controls uses them', () => {
  const defaults = { system_prompt: 'Default prompt', params: { temperature: 0.7 } };
  assert.deepEqual(
    logic.resolveChatControls({ system_prompt: 'Be brief', params: { top_p: 0.5 } }, defaults),
    { system_prompt: 'Be brief', params: { top_p: 0.5 } },
  );
  assert.deepEqual(
    logic.resolveChatControls({ system_prompt: '', params: {} }, defaults),
    { system_prompt: '', params: {} },
  );
});

test('an older chat with no stored controls falls back to the defaults', () => {
  const defaults = { system_prompt: 'Default prompt', params: { temperature: 0.7 } };
  const resolved = logic.resolveChatControls({ system_prompt: null, params: null }, defaults);
  assert.deepEqual(resolved, defaults);
  resolved.params.temperature = 2;
  assert.equal(defaults.params.temperature, 0.7);
  assert.deepEqual(logic.resolveChatControls(null, defaults), defaults);
});

test('only the newest assistant reply can be regenerated', () => {
  const messages = [
    { role: 'user', content: 'a' }, { role: 'assistant', content: 'b' },
    { role: 'user', content: 'c' }, { role: 'assistant', content: 'd' },
    { role: 'user', content: 'e' },
  ];
  assert.equal(logic.isLastAssistantMessage(messages, 1), false);
  assert.equal(logic.isLastAssistantMessage(messages, 3), true);
  assert.equal(logic.isLastAssistantMessage(messages, 4), false);
  assert.equal(logic.isLastAssistantMessage(messages, 9), false);
});

test('labels start with a capital letter', () => {
  assert.equal(logic.sentenceCase('web search'), 'Web search');
  assert.equal(logic.sentenceCase(''), '');
});

test('finished sentences are taken for speaking and the unfinished end is kept', () => {
  assert.deepEqual(logic.takeSpokenSentences('Hey you. Come closer! I want'),
    { sentences: ['Hey you.', 'Come closer!'], rest: 'I want' });
  assert.deepEqual(logic.takeSpokenSentences('"Stay," she says. '), { sentences: ['"Stay," she says.'], rest: '' });
  assert.deepEqual(logic.takeSpokenSentences('One line\nand the next'), { sentences: ['One line'], rest: 'and the next' });
  assert.deepEqual(logic.takeSpokenSentences('No end yet'), { sentences: [], rest: 'No end yet' });
  assert.deepEqual(logic.takeSpokenSentences('Wait...'), { sentences: [], rest: 'Wait...' });
});

test('spoken text drops markdown marks and long sentences are cut at spaces', () => {
  assert.equal(logic.speakableText('*leans in* **closer**  _now_'), 'leans in closer now');
  const long = Array(200).fill('word').join(' ');
  const pieces = logic.spokenPieces(long);
  assert.ok(pieces.every(piece => piece.length <= logic.MAX_SPOKEN_CHARS));
  assert.equal(pieces.join(' '), long);
  assert.deepEqual(logic.spokenPieces('Short.'), ['Short.']);
});

function keyPress(key, extra = {}) {
  const event = { key, prevented: false, preventDefault() { event.prevented = true; }, ...extra };
  return event;
}

test('Enter in a field submits once and is used up, so it cannot press whatever gets focus next', () => {
  let submitted = 0;
  const onKey = logic.submitOnEnter(() => { submitted += 1; });
  const enter = keyPress('Enter');
  onKey(enter);
  assert.equal(submitted, 1);
  assert.equal(enter.prevented, true);
});

test('other keys, and Enter that only confirms an input method choice, do not submit', () => {
  let submitted = 0;
  const onKey = logic.submitOnEnter(() => { submitted += 1; });
  const letter = keyPress('a');
  const composing = keyPress('Enter', { isComposing: true });
  onKey(letter);
  onKey(composing);
  assert.equal(submitted, 0);
  assert.equal(letter.prevented, false);
  assert.equal(composing.prevented, false);
});

test('the dialog field and the one-line add fields hand Enter to that helper', () => {
  const fs = require('node:fs');
  const workspace = ['workspaceCore.js', 'workspace.js']
    .map(name => fs.readFileSync(path.join(__dirname, '..', '..', 'static', name), 'utf8')).join('');
  assert.match(workspace, /dialogEls\.input\.onkeydown = submitOnEnter\(/);
  for (const field of ['newMemoryText', 'noteNewItem', 'presetName']) {
    assert.match(workspace, new RegExp(`\\['${field}', '\\w+'\\]`), `${field} has no Enter key`);
  }
});

test('a chat is named after its first user message', () => {
  const messages = [{ role: 'user', content: 'Plan a calm weekend' }, { role: 'assistant', content: 'Sure' }];
  assert.equal(logic.chatTitleFrom(messages), 'Plan a calm weekend');
  assert.equal(logic.chatTitleFrom([{ role: 'user', content: 'x\n\nAttached files:\n\n<file>', display: 'Read this' }]), 'Read this');
  assert.equal(logic.chatTitleFrom([{ role: 'user', content: 'a'.repeat(200) }]).length, logic.CHAT_TITLE_CHARS);
});

test('a branch cut before the first message is named after the text being sent, not "New chat"', () => {
  assert.equal(logic.chatTitleFrom([], '  Plan a calm Sunday  '), 'Plan a calm Sunday');
  assert.equal(logic.chatTitleFrom([], ''), 'New chat');
  assert.equal(logic.chatTitleFrom([{ role: 'user', content: '' }], 'Second try'), 'Second try');
});

test('a new compare column takes the first model no other column shows', () => {
  const models = [{ endpoint_id: 'a', id: 'one' }, { endpoint_id: 'a', id: 'two' }, { endpoint_id: 'b', id: 'one' }];
  const key = model => model.endpoint_id + '/' + model.id;
  assert.equal(logic.firstUnusedModel(models, [], key), models[0]);
  assert.equal(logic.firstUnusedModel(models, ['a/one'], key), models[1]);
  assert.equal(logic.firstUnusedModel(models, ['a/one', 'a/two'], key), models[2]);
  assert.equal(logic.firstUnusedModel(models, ['a/one', 'a/two', 'b/one'], key), models[0]);
  assert.equal(logic.firstUnusedModel([], [], key), null);
});

test('Escape on a menu or dialog closes it and is used up, so it cannot also close what is behind or stop a reply', () => {
  let closed = 0;
  const onKey = logic.closeOnEscape(() => { closed += 1; });
  const escape = keyPress('Escape');
  const other = keyPress('Enter');
  onKey(escape);
  onKey(other);
  assert.equal(closed, 1);
  assert.equal(escape.prevented, true);
  assert.equal(other.prevented, false);
});

test('the dialog, the chat row menu and the model menu close through that helper, and the page-wide Escape respects it', () => {
  const fs = require('node:fs');
  const workspace = ['workspaceCore.js', 'sidebarMenus.js', 'modelPicker.js', 'workspace.js']
    .map(name => fs.readFileSync(path.join(__dirname, '..', '..', 'static', name), 'utf8')).join('');
  assert.match(workspace, /dialogEls\.modal\.onkeydown = closeOnEscape\(/);
  assert.match(workspace, /\$\('chatMenu'\)\.addEventListener\('keydown', closeOnEscape\(closeContextMenu\)\)/);
  assert.match(workspace, /\$\('modelSearch'\)\.addEventListener\('keydown', closeOnEscape\(/);
  assert.match(workspace, /key === 'escape' && !e\.defaultPrevented/);
});

test('a regenerate pick names the model and its connection, and the "Regenerate with" heading names none', () => {
  const picked = { value: 'fake-echo', dataset: { endpointId: 'ep1' } };
  assert.deepEqual(logic.regenerateChoice(picked), { id: 'fake-echo', endpoint_id: 'ep1' });
  assert.equal(logic.regenerateChoice({ value: '', dataset: {} }), null);
  assert.equal(logic.regenerateChoice(undefined), null);
  assert.equal(logic.REGENERATE_WITH, 'Regenerate with…');
});

test('arrow keys move through a menu and wrap, Home and End jump to the ends', () => {
  assert.equal(logic.menuFocusIndex('ArrowDown', -1, 4), 0);
  assert.equal(logic.menuFocusIndex('ArrowUp', -1, 4), 3);
  assert.equal(logic.menuFocusIndex('ArrowDown', 1, 4), 2);
  assert.equal(logic.menuFocusIndex('ArrowDown', 3, 4), 0);
  assert.equal(logic.menuFocusIndex('ArrowUp', 0, 4), 3);
  assert.equal(logic.menuFocusIndex('Home', 2, 4), 0);
  assert.equal(logic.menuFocusIndex('End', 0, 4), 3);
  assert.equal(logic.menuFocusIndex('Enter', 1, 4), -1);
  assert.equal(logic.menuFocusIndex('ArrowDown', -1, 0), -1);
  assert.equal(logic.menuFocusIndex('toString', 0, 4), -1);
});

test('the menu key handler focuses the next item, and Escape closes the menu and is used up', () => {
  const focused = [];
  const items = ['upload', 'knowledge', 'prompts'].map(name => ({ name, focus() { focused.push(name); } }));
  let current = items[0];
  let closed = 0;
  const onKey = logic.menuKeyHandler(() => items, () => { closed += 1; }, () => current);
  const down = keyPress('ArrowDown');
  onKey(down);
  assert.deepEqual(focused, ['knowledge']);
  assert.equal(down.prevented, true);
  current = items[1];
  onKey(keyPress('End'));
  onKey(keyPress('Home'));
  assert.deepEqual(focused, ['knowledge', 'prompts', 'upload']);
  const typed = keyPress('a');
  onKey(typed);
  assert.equal(typed.prevented, false);
  const escape = keyPress('Escape');
  onKey(escape);
  assert.equal(closed, 1);
  assert.equal(escape.prevented, true);
});

test('the plus menu is driven by that key handler and gives focus back to its button', () => {
  const fs = require('node:fs');
  const workspace = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'composerTools.js'), 'utf8');
  assert.match(workspace, /\$\('plusMenu'\)\.addEventListener\('keydown', menuKeyHandler\(plusMenuItems, closePlusMenu\)\)/);
  assert.match(workspace, /function closePlusMenu\(\) \{[^}]*\$\('plusBtn'\)\.focus\(\);/);
  assert.match(workspace, /if \(opening\) \{[^}]*plusMenuItems\(\)\[0\]/);
});


test('an empty settings list shows a loading row, a list already showing items is left alone', () => {
  const empty = { children: [], innerHTML: '' };
  assert.equal(logic.showListLoading(empty), true);
  assert.match(empty.innerHTML, /<li class="settings-hint" role="status">loading…<\/li>/);
  const filled = { children: [{}], innerHTML: '<li>kept</li>' };
  assert.equal(logic.showListLoading(filled), false);
  assert.equal(filled.innerHTML, '<li>kept</li>');
  assert.equal(logic.showListLoading(null), false);
});

test('a failed request is explained with the server detail, or the fallback when there is none', async () => {
  const answer = body => ({ json: async () => body });
  assert.equal(await logic.failureMessage(answer({ detail: 'text is required' }), 'could not add'), 'text is required');
  assert.equal(await logic.failureMessage(answer({ detail: [{ msg: 'field required' }] }), 'could not add'), 'could not add');
  assert.equal(await logic.failureMessage(answer({}), 'could not add'), 'could not add');
  assert.equal(await logic.failureMessage({ json: async () => { throw new SyntaxError('not json'); } }, 'could not add'), 'could not add');
});

test('the rule account choices list each account, or say there is none yet so the select is never blank', () => {
  const some = logic.ruleAccountChoices([{ id: 'a1', label: 'Work' }, { id: 'a2', label: 'Home' }]);
  assert.equal(some.usable, true);
  assert.deepEqual(some.options, [{ value: 'a1', label: 'Work' }, { value: 'a2', label: 'Home' }]);
  for (const nothing of [[], undefined, null]) {
    const none = logic.ruleAccountChoices(nothing);
    assert.equal(none.usable, false);
    assert.deepEqual(none.options, [{ value: '', label: logic.NO_EMAIL_ACCOUNTS }]);
  }
  assert.match(logic.NO_EMAIL_ACCOUNTS, /add one above/);
});

test('the model server card offers to start Ollama only when Ollama is installed and nothing answers', () => {
  const stopped = logic.backendStatusPlan({ installed: true, running: false, backend: null });
  assert.equal(stopped.offerStart, true);
  assert.equal(stopped.showInstall, false);
  for (const status of [
    { installed: false, running: false, backend: null },
    { installed: false, running: true, backend: 'ArtyEngine' },
    { installed: true, running: true, backend: 'Ollama' },
    { installed: true, running: true, backend: 'ArtyEngine' },
  ]) {
    assert.equal(logic.backendStatusPlan(status).offerStart, false);
  }
});

test('the model server card names the backend that answered and says a server without Ollama runs on its own', () => {
  const engine = logic.backendStatusPlan({ installed: false, running: true, backend: 'ArtyEngine' });
  assert.equal(engine.headline, '✓ ArtyEngine is running.');
  assert.equal(engine.note, logic.BACKEND_RUNS_ON_ITS_OWN);
  assert.equal(engine.showInstall, false);
  const ollama = logic.backendStatusPlan({ installed: true, running: true, backend: 'Ollama' });
  assert.equal(ollama.headline, '✓ Ollama is running.');
  assert.equal(ollama.note, '');
  const nothing = logic.backendStatusPlan({ installed: false, running: false, backend: null });
  assert.equal(nothing.note, logic.NO_LOCAL_OLLAMA);
  assert.equal(nothing.showInstall, true);
});

test('a model server that answers but was never added is shown as found, not in use', () => {
  const found = logic.backendStatusPlan({
    installed: true, running: true, backend: 'ArtyEngine', in_use: false, found_at: 'http://127.0.0.1:11438',
  });
  assert.equal(found.headline, 'Found ArtyEngine at http://127.0.0.1:11438, not added.');
  assert.equal(found.note, logic.FOUND_NOT_ADDED);
  assert.equal(found.offerStart, false);
  assert.equal(found.showInstall, false);
  const inUse = logic.backendStatusPlan({ installed: true, running: true, backend: 'Ollama', in_use: true, found_at: null });
  assert.equal(inUse.headline, '✓ Ollama is running.');
});

test('a scanned server is stored with the kind the scan found, not always as Ollama', () => {
  const body = logic.scanEndpointBody({ base_url: 'http://127.0.0.1:8080', kind: 'openai-compatible', label: 'Local server (127.0.0.1:8080)' });
  assert.deepEqual(body, { base_url: 'http://127.0.0.1:8080', kind: 'openai-compatible', label: 'Local server (127.0.0.1:8080)' });
  assert.equal(logic.scanEndpointBody({ base_url: 'http://127.0.0.1:11434', label: 'Ollama' }).kind, 'ollama');
});

test('the scan summary names the servers that were added and the ones that were not', () => {
  const summary = logic.scanSummary([
    { label: 'Ollama (127.0.0.1:11434)', ok: true, kind: 'ollama' },
    { label: 'Local server (127.0.0.1:1234)', ok: true, kind: 'openai-compatible' },
    { label: 'Local server (127.0.0.1:8080)', ok: false, detail: 'server returned 500' },
    { label: 'Ollama (127.0.0.1:11438)', ok: false, status: 409, detail: 'that endpoint is already configured' },
  ]);
  assert.equal(summary.text,
    'added Ollama (127.0.0.1:11434), Local server (127.0.0.1:1234) (OpenAI-compatible, not used for memory search or pulls); '
    + 'already added Ollama (127.0.0.1:11438); not added Local server (127.0.0.1:8080): server returned 500');
  assert.equal(summary.ok, false);
  assert.deepEqual(logic.scanSummary([{ label: 'Ollama', ok: true, kind: 'ollama' }]), { text: 'added Ollama', ok: true });
});

test('adding a local server says when it was stored as OpenAI-compatible', () => {
  assert.equal(logic.addedLocalMessage({ kind: 'ollama', base_url: 'http://localhost:11434' }), 'added');
  assert.equal(logic.addedLocalMessage({ kind: 'openai-compatible', base_url: 'http://localhost:1234/v1' }),
    'added http://localhost:1234/v1 as an OpenAI-compatible server: it has no Ollama API, so it is used for chat '
    + 'but not for memory search or pulls');
});

test('only media from starfire itself, data: or blob: may load in a chat message', () => {
  const origin = 'https://starfire.example';
  assert.equal(logic.isLocalMediaUrl('/api/generated/abc', origin), true);
  assert.equal(logic.isLocalMediaUrl('assets/logo.svg', origin), true);
  assert.equal(logic.isLocalMediaUrl('data:image/png;base64,AAAA', origin), true);
  assert.equal(logic.isLocalMediaUrl('blob:https://starfire.example/1-2', origin), true);
  assert.equal(logic.isLocalMediaUrl('https://exfil.invalid/p.png?d=SECRET', origin), false);
  assert.equal(logic.isLocalMediaUrl('//exfil.invalid/p.png', origin), false);
  assert.equal(logic.isLocalMediaUrl('https://starfire.example.exfil.invalid/p.png', origin), false);
  assert.equal(logic.isLocalMediaUrl('http://[::1', origin), false);
});

test('a chat message may only carry code language classes, never the page\'s own classes', () => {
  assert.equal(logic.isCodeLanguageClass('language-python'), true);
  assert.equal(logic.isCodeLanguageClass('language-c++ language-cpp'), true);
  assert.equal(logic.isCodeLanguageClass('diff-card shell-card'), false);
  assert.equal(logic.isCodeLanguageClass('language-js btn'), false);
  assert.equal(logic.isCodeLanguageClass(''), false);
});

test('a tool that was refused shows as blocked, one that broke as failed, and only a real result as done', () => {
  assert.deepEqual(logic.toolOutcome('{"applied": true}'), { state: 'done', reason: '' });
  assert.equal(logic.toolOutcome("error: only an admin account can use edit_file, because it acts directly on the server").state, 'blocked');
  assert.equal(logic.toolOutcome('error: the user rejected this command, so it was not run').state, 'blocked');
  assert.equal(logic.toolOutcome('error: the user did not approve this gh command within 5 minutes, so it was not run').state, 'blocked');
  assert.equal(logic.toolOutcome("error: edit_file can only change files in this account's starfire workspace folder").state, 'blocked');
  assert.equal(logic.toolOutcome('error: failed to start command: there is no folder /x').state, 'failed');
  assert.equal(logic.toolOutcome(undefined).state, 'done');
});

test('the chat line names the tool and says blocked or failed with the reason instead of done', () => {
  assert.deepEqual(logic.toolOutcomeLine('edit_file', 'error: only an admin account can use edit_file, because it acts directly on the server'),
    { text: '» edit_file blocked: only an admin account can use edit_file, because it acts directly on the server', cls: 'err' });
  assert.deepEqual(logic.toolOutcomeLine('mcp__files__write_file', 'error: disk full'),
    { text: '» write_file failed: disk full', cls: 'err' });
  assert.deepEqual(logic.toolOutcomeLine('manage_notes', 'added'), { text: '» manage_notes done', cls: 'info' });
  const long = logic.toolOutcomeLine('run_shell', 'error: ' + 'x'.repeat(500));
  assert.equal(long.text.length, '» run_shell failed: '.length + 201);
});

const ACCESS_VIEW = {
  models: ['gpt-4o', 'retired:7b', 'llama*'],
  kept: ['retired:7b', 'llama*'],
  available: [
    { id: 'gpt-4o', endpoint_id: 'a', connection: 'Remote', allowed: true, by_pattern: false, restricted: false },
    { id: 'llama3.1:8b', endpoint_id: 'a', connection: 'Remote', allowed: true, by_pattern: true, restricted: false },
    { id: 'qwen3:14b', endpoint_id: 'b', connection: 'Arty', allowed: false, by_pattern: false, restricted: false },
    { id: 'x-abl', endpoint_id: 'b', connection: 'Arty', allowed: false, by_pattern: false, restricted: true },
  ],
};

test('models are grouped by connection in the order they were listed', () => {
  const groups = logic.groupModelsByConnection(ACCESS_VIEW.available);
  assert.deepEqual(groups.map(g => [g.connection, g.models.map(m => m.id)]),
    [['Remote', ['gpt-4o', 'llama3.1:8b']], ['Arty', ['qwen3:14b', 'x-abl']]]);
  assert.deepEqual(logic.groupModelsByConnection([{ id: 'm', endpoint_id: '' }]).map(g => g.connection), ['Other']);
  assert.deepEqual(logic.groupModelsByConnection(undefined), []);
});

test('the model search matches the name or the connection, ignoring case', () => {
  const [first] = ACCESS_VIEW.available;
  assert.equal(logic.accessMatchesSearch(first, ''), true);
  assert.equal(logic.accessMatchesSearch(first, ' GPT '), true);
  assert.equal(logic.accessMatchesSearch(first, 'remote'), true);
  assert.equal(logic.accessMatchesSearch(first, 'qwen'), false);
});

test('only models named exactly start ticked, and saving keeps what the list cannot show', () => {
  const ticked = logic.ticksFromAccess(ACCESS_VIEW);
  assert.deepEqual([...ticked], ['gpt-4o']);
  ticked.add('qwen3:14b');
  assert.deepEqual(logic.modelListToSave({ everything: false, ticked, available: ACCESS_VIEW.available, kept: ACCESS_VIEW.kept }),
    ['gpt-4o', 'qwen3:14b', 'retired:7b', 'llama*']);
  assert.equal(logic.modelListToSave({ everything: true, ticked, available: ACCESS_VIEW.available, kept: [] }), null);
  assert.deepEqual(logic.modelListToSave({ everything: false, ticked: new Set(), available: [], kept: [] }), []);
  assert.equal(logic.modelAccessSummary(null), 'every model except the restricted ones');
  assert.equal(logic.modelAccessSummary([]), 'no models');
  assert.equal(logic.modelAccessCount(true, ticked, 0), 'Every model except the restricted ones');
  assert.equal(logic.modelAccessCount(false, ticked, 1), '3 chosen');
});

const ALL_VIEW = {
  models: null,
  kept: [],
  available: [
    { id: 'gpt-4o', endpoint_id: 'a', connection: 'Remote', allowed: true, by_pattern: false, restricted: false },
    { id: 'qwen3:14b', endpoint_id: 'b', connection: 'Arty', allowed: true, by_pattern: false, restricted: false },
    { id: 'x-abl', endpoint_id: 'b', connection: 'Arty', allowed: false, by_pattern: false, restricted: true },
  ],
};
const ALL_ON = { everything: true, ticked: logic.ticksFromAccess(ALL_VIEW) };
const ALL_MODELS = ALL_VIEW.available;
const [GPT, QWEN, ABL] = ALL_MODELS;

test('with All on every unrestricted model shows ticked and a restricted one shows unticked', () => {
  assert.deepEqual(ALL_MODELS.map(m => logic.accessBoxState(ALL_ON, m, false).checked), [true, true, false]);
  assert.equal(logic.accessBoxState(ALL_ON, GPT, true).disabled, false);
  const off = { everything: false, ticked: new Set(['gpt-4o']) };
  assert.deepEqual(ALL_MODELS.map(m => logic.accessBoxState(off, m, false).checked), [true, false, false]);
  assert.deepEqual(logic.accessBoxState(off, QWEN, true), { checked: true, disabled: true });
});

test('unticking a model while All is on turns All off and ticks every other unrestricted model', () => {
  const next = logic.toggleModelTick(ALL_ON, ALL_MODELS, 'gpt-4o', false);
  assert.equal(next.everything, false);
  assert.deepEqual([...next.ticked], ['qwen3:14b']);
  assert.deepEqual(logic.modelListToSave({ ...next, available: ALL_MODELS, kept: [] }), ['qwen3:14b']);
});

test('ticking a restricted model while All is on turns All off and adds it to the unrestricted ones', () => {
  const next = logic.toggleModelTick(ALL_ON, ALL_MODELS, 'x-abl', true);
  assert.equal(next.everything, false);
  assert.deepEqual([...next.ticked].sort(), ['gpt-4o', 'qwen3:14b', 'x-abl']);
});

test('ticking an unrestricted model while All is on changes nothing', () => {
  assert.deepEqual(logic.toggleModelTick(ALL_ON, ALL_MODELS, 'gpt-4o', true), ALL_ON);
});

test('ticking and unticking with All off only changes that model', () => {
  const off = { everything: false, ticked: new Set(['gpt-4o']) };
  assert.deepEqual([...logic.toggleModelTick(off, ALL_MODELS, 'qwen3:14b', true).ticked], ['gpt-4o', 'qwen3:14b']);
  assert.deepEqual([...logic.toggleModelTick(off, ALL_MODELS, 'gpt-4o', false).ticked], []);
  assert.equal(off.ticked.size, 1);
});

test('turning All off keeps what was shown ticked, turning it on goes back to every model', () => {
  const off = logic.switchEverything(ALL_ON, ALL_MODELS, false);
  assert.equal(off.everything, false);
  assert.deepEqual([...off.ticked].sort(), ['gpt-4o', 'qwen3:14b']);
  assert.deepEqual(off.dropped, []);
  const back = logic.switchEverything({ everything: false, ticked: new Set(['gpt-4o', 'x-abl']) }, ALL_MODELS, true);
  assert.equal(back.everything, true);
  assert.deepEqual(back.dropped, ['x-abl']);
  assert.equal(logic.modelListToSave({ ...back, available: ALL_MODELS, kept: [] }), null);
});

test('the dropped restricted models are named in a note, and no note when none were dropped', () => {
  assert.equal(logic.droppedRestrictedNote([]), '');
  assert.match(logic.droppedRestrictedNote(['x-abl', 'y-abl']), /x-abl, y-abl/);
  assert.match(logic.droppedRestrictedNote(['x-abl']), /no longer allowed/);
});

test('Select none with All on turns All off with nothing ticked, Select all shown keeps All on', () => {
  const none = logic.selectShown(ALL_ON, ALL_MODELS, ALL_MODELS, false);
  assert.equal(none.everything, false);
  assert.deepEqual([...none.ticked], []);
  assert.deepEqual(logic.selectShown(ALL_ON, ALL_MODELS, ALL_MODELS, true), ALL_ON);
});

test('Select none with a search on and All on leaves the hidden unrestricted models ticked', () => {
  const none = logic.selectShown(ALL_ON, ALL_MODELS, [QWEN], false);
  assert.deepEqual([...none.ticked], ['gpt-4o']);
});

test('Select all shown with All off skips restricted models and Select none clears the shown ones', () => {
  const off = { everything: false, ticked: new Set() };
  assert.deepEqual([...logic.selectShown(off, ALL_MODELS, ALL_MODELS, true).ticked].sort(), ['gpt-4o', 'qwen3:14b']);
  const some = { everything: false, ticked: new Set(['gpt-4o', 'x-abl']) };
  assert.deepEqual([...logic.selectShown(some, ALL_MODELS, [GPT], false).ticked], ['x-abl']);
});

test('a model on a down connection is tagged offline in the access list and can still be ticked', () => {
  const down = { id: 'remote-7b', endpoint_id: 'f', connection: 'Remote', offline: true, allowed: true, by_pattern: false, restricted: false };
  assert.deepEqual(logic.accessModelTags(down, false), ['offline']);
  assert.deepEqual(logic.accessModelTags({ ...down, restricted: true }, true), ['restricted', 'offline', 'allowed by a kept pattern']);
  assert.deepEqual(logic.accessModelTags(ALL_MODELS[0], false), []);
  const available = [...ALL_MODELS, down];
  const state = { everything: false, ticked: new Set() };
  const ticked = logic.toggleModelTick(state, available, 'remote-7b', true);
  assert.equal(logic.accessBoxState(ticked, down, false).checked, true);
  assert.deepEqual(logic.modelListToSave({ ...ticked, available, kept: [] }), ['remote-7b']);
  const all = { everything: true, ticked: logic.ticksFromAccess({ available }) };
  assert.equal(logic.accessBoxState(all, down, false).checked, true);
});

test('remote models get their own section after the local ones, each keeping its connections in order', () => {
  const mixed = [
    { id: 'a', endpoint_id: 'far', connection: 'Remote', remote: true },
    { id: 'b', endpoint_id: 'home', connection: 'Home', remote: false },
    { id: 'c', endpoint_id: 'far', connection: 'Remote', remote: true, offline: true },
    { id: 'd', endpoint_id: 'lan', connection: 'Lan' },
  ];
  const sections = logic.accessSections(mixed);
  assert.deepEqual(sections.map(s => s.title), ['', 'Remote']);
  assert.deepEqual(sections.map(s => s.groups.map(g => [g.connection, g.models.map(m => m.id)])),
    [[['Home', ['b']], ['Lan', ['d']]], [['Remote', ['a', 'c']]]]);
  assert.deepEqual(logic.accessSections(mixed.filter(m => m.remote)).map(s => s.title), ['Remote']);
  assert.deepEqual(logic.accessSections(mixed.filter(m => !m.remote)).map(s => s.title), ['']);
  assert.deepEqual(logic.accessSections([]), []);
});

test('the chosen-model tab lists local models, then a Remote heading and the remote ones', () => {
  const listed = [
    { id: 'far-7b', endpoint_id: 'far', remote: true, offline: true },
    { id: 'home-8b', endpoint_id: 'home', remote: false },
  ];
  const chosen = logic.PICKER_MODE_CHOSEN;
  assert.deepEqual(logic.modelMenuEntries(listed, '', chosen).map(e => e.heading || e.id), ['home-8b', 'Remote', 'far-7b']);
  assert.deepEqual(logic.modelMenuEntries([listed[1]], '', chosen).map(e => e.heading || e.id), ['home-8b']);
  assert.deepEqual(logic.modelMenuEntries(listed, 'far', chosen).map(e => e.heading || e.id), ['Remote', 'far-7b']);
});

test('turning All on names the saved patterns it will drop as well as restricted models', () => {
  const off = { everything: false, ticked: new Set(['gpt-4o', 'x-abl']) };
  const back = logic.switchEverything(off, ALL_MODELS, true, ['llama*', 'retired:7b']);
  assert.deepEqual(back.dropped, ['x-abl', 'llama*', 'retired:7b']);
  assert.deepEqual(logic.switchEverything(off, ALL_MODELS, true, []).dropped, ['x-abl']);
  assert.deepEqual(logic.switchEverything(ALL_ON, ALL_MODELS, true, ['llama*']).dropped, []);
  assert.deepEqual(logic.switchEverything(ALL_ON, ALL_MODELS, false, ['llama*']).dropped, []);
  const note = logic.droppedRestrictedNote(back.dropped);
  assert.match(note, /x-abl, llama\*, retired:7b/);
  assert.match(note, /patterns come back if you switch All models off/i);
  assert.match(note, /restricted models need ticking again/i);
});

test('the kept list and the pattern row show only while All is off', () => {
  assert.equal(logic.keptControlsShown(ALL_ON), false);
  assert.equal(logic.keptControlsShown({ everything: false, ticked: new Set() }), true);
});

test('a shell approval card warns when the command would run without a sandbox', () => {
  assert.deepEqual(logic.shellApprovalNotes({ untrusted: false, unsandboxed: false }), []);
  assert.deepEqual(logic.shellApprovalNotes({ unsandboxed: true }), [logic.UNSANDBOXED_SHELL_NOTE]);
  assert.deepEqual(logic.shellApprovalNotes({ untrusted: true, unsandboxed: true }),
    [logic.UNTRUSTED_APPROVAL_NOTE, logic.UNSANDBOXED_SHELL_NOTE]);
  assert.match(logic.UNSANDBOXED_SHELL_NOTE, /without a sandbox/);
  const [card] = logic.pendingApprovalCards([{ id: 'p1', command: 'ls', unsandboxed: true }], []);
  assert.equal(card.data.unsandboxed, true);
});

const ARTY_MEMORY = [
  { name: 'Rowan', type: 'person', facts: ['Lives in Bath', 'Likes tea'] },
  { name: 'Garden', type: 'topic', facts: ['Has a pond', 'Rowan planted the roses'] },
  { name: 7, type: 'topic', facts: ['dropped'] },
];

test('the Arty memory list is searched by name, type or fact and keeps the matching facts', () => {
  assert.deepEqual(logic.artyMemoryRows(ARTY_MEMORY, ''), [
    { name: 'Rowan', type: 'person', facts: ['Lives in Bath', 'Likes tea'] },
    { name: 'Garden', type: 'topic', facts: ['Has a pond', 'Rowan planted the roses'] },
  ]);
  assert.deepEqual(logic.artyMemoryRows(ARTY_MEMORY, '  ROWAN '), [
    { name: 'Rowan', type: 'person', facts: ['Lives in Bath', 'Likes tea'] },
    { name: 'Garden', type: 'topic', facts: ['Rowan planted the roses'] },
  ]);
  assert.deepEqual(logic.artyMemoryRows(ARTY_MEMORY, 'person').map(row => row.name), ['Rowan']);
  assert.deepEqual(logic.artyMemoryRows(ARTY_MEMORY, 'nothing like this'), []);
  assert.deepEqual(logic.artyMemoryRows(null, ''), []);
  assert.deepEqual(logic.artyMemoryRows([{ name: 'Odd', facts: [3, 'kept'] }], ''), [{ name: 'Odd', type: '', facts: ['kept'] }]);
});

const DOWN = { id: 'remote-model-a', endpoint_id: 'f', connection: 'remote-a', offline: true,
  health: { available: false, error_kind: 'server_error', last_checked: 1000 } };

test('an offline model is recognised and described with its connection, reason and last check', () => {
  assert.equal(logic.isOfflineModel(DOWN), true);
  assert.equal(logic.isOfflineModel({ id: 'm', offline: false }), false);
  assert.equal(logic.isOfflineModel(null), false);
  assert.equal(logic.offlineTooltip(DOWN, 1130), 'Offline: remote-a is server error, checked 2 min ago');
  assert.equal(logic.offlineTooltip({ id: 'm', offline: true }, 5), 'Offline: this connection is not answering, not checked yet');
  assert.match(logic.offlineNotice(DOWN), /^remote-model-a is offline right now \(remote-a\)\. Pick another model/);
});

test('models on a down connection are left out of the lists that must only offer working models', () => {
  const up = { id: 'ok', endpoint_id: 'a' };
  assert.deepEqual(logic.withoutOffline([up, DOWN]), [up]);
  assert.deepEqual(logic.withoutOffline(undefined), []);
});

test('the picker can tell a changed list of models from a changed health', () => {
  const up = { id: 'remote-model-a', endpoint_id: 'f' };
  assert.equal(logic.sameModelSet([up], [DOWN]), true);
  assert.equal(logic.sameModelSet([up], []), false);
  assert.equal(logic.sameModelSet([up], [{ id: 'other', endpoint_id: 'f' }]), false);
});

test('a ticked name with wildcard characters is saved escaped, so it stays that one name', () => {
  assert.equal(logic.escapeModelName('mixtral[q4]*.gguf'), 'mixtral[[]q4][*].gguf');
  assert.equal(logic.isAccessPattern(logic.escapeModelName('what?-7b')), false);
  assert.equal(logic.isAccessPattern('llama*'), true);
  assert.equal(logic.isAccessPattern('plain-name:8b'), false);
});

test('access patterns match like the server does', () => {
  const cases = [
    ['llama*', 'llama3.1:8b', true], ['llama3', 'llama3:latest', true], ['LLAMA*', 'Llama3', true],
    ['mixtral[[]q4].gguf', 'mixtral[q4].gguf', true], ['mixtral[[]q4].gguf', 'mixtralq.gguf', false],
    ['[!a]bc', 'xbc', true], ['[!a]bc', 'abc', false], ['[]a]x', ']x', true], ['a.b', 'axb', false],
    ['*', 'someone/llama3', true], ['own/*', 'other/x', false],
  ];
  for (const [pattern, name, expected] of cases) assert.equal(logic.accessPatternMatches(pattern, name), expected, `${pattern} ${name}`);
});

const ODD_VIEW = {
  models: ['mixtral[[]q4].gguf', 'gpt-4o', 'llama*', 'retired:7b'],
  available: [
    { id: 'mixtral[q4].gguf', allowed: true, by_pattern: false, restricted: false },
    { id: 'gpt-4o', allowed: true, by_pattern: false, restricted: false },
    { id: 'llama3.1:8b', allowed: true, by_pattern: true, restricted: false },
    { id: 'mixtralq.gguf', allowed: false, by_pattern: false, restricted: false },
  ],
  kept: ['llama*', 'retired:7b'],
};

test('saving an unchanged checklist sends back the same entries, so access never narrows', () => {
  const saved = logic.modelListToSave({ everything: false, ticked: logic.ticksFromAccess(ODD_VIEW),
                                        available: ODD_VIEW.available, kept: ODD_VIEW.kept });
  assert.deepEqual(new Set(saved), new Set(ODD_VIEW.models));
});

test('a row is covered by a kept pattern only while that pattern is kept', () => {
  const llama = ODD_VIEW.available[2];
  assert.equal(logic.coveredByKeptPattern(llama, ODD_VIEW.kept), true);
  assert.equal(logic.coveredByKeptPattern(llama, ['retired:7b']), false);
  assert.equal(logic.coveredByKeptPattern({ id: 'llama-abl', restricted: true }, ['llama*']), false);
  assert.equal(logic.coveredByKeptPattern({ id: 'llama3', restricted: false }, ['llama3']), false);
});

test('a list over the cap is stopped before it is sent', () => {
  assert.equal(logic.tooManyModelsNote(['a', 'b'], 2), '');
  assert.match(logic.tooManyModelsNote(['a', 'b', 'c'], 2), /at most 2/);
  assert.equal(logic.tooManyModelsNote(null, 2), '');
});

test('the model menu keeps its highlight across a refresh', () => {
  assert.equal(logic.highlightIndexAfterRender(['a', 'b', 'c'], 'c'), 2);
  assert.equal(logic.highlightIndexAfterRender(['a', 'b'], 'gone'), 0);
  assert.equal(logic.highlightIndexAfterRender(['a', 'b'], ''), 0);
});

test('the picker refresh waits while a reply runs or the page is hidden', () => {
  assert.equal(logic.shouldRefreshModelHealth({ hidden: false, busy: false, haveModels: true }), true);
  assert.equal(logic.shouldRefreshModelHealth({ hidden: false, busy: true, haveModels: true }), false);
  assert.equal(logic.shouldRefreshModelHealth({ hidden: true, busy: false, haveModels: true }), false);
  assert.equal(logic.shouldRefreshModelHealth({ hidden: false, busy: false, haveModels: false }), false);
});

test('a reply never switches to another model on a fallback event', () => {
  const send = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'static', 'chatSend.js'), 'utf8');
  assert.doesNotMatch(send, /obj\.fallback/);
  assert.equal(logic.fallbackChoice, undefined);
});

test('the context length of a model with a fixed window shows the fixed size, locked, with the reason', () => {
  const main = { id: 'big:35b', endpoint_id: 'engine', fixed_context: 24576 };
  const state = logic.paramFieldState('num_ctx', { num_ctx: 4096 }, main);
  assert.deepEqual(state, { value: '24576', disabled: true, note: logic.fixedContextNote(24576) });
  assert.match(state.note, /^Fixed at 24,576 for this model/);
  assert.deepEqual(logic.paramFieldState('temperature', { temperature: 0.3 }, main), { value: 0.3, disabled: false, note: '' });
});

test('other models keep the context length chosen for the chat and its field stays open', () => {
  const other = { id: 'qwen3:14b', endpoint_id: 'engine' };
  assert.deepEqual(logic.paramFieldState('num_ctx', { num_ctx: 4096 }, other), { value: 4096, disabled: false, note: '' });
  assert.deepEqual(logic.paramFieldState('num_ctx', {}, null), { value: '', disabled: false, note: '' });
  assert.deepEqual(logic.paramFieldState('num_ctx', {}, { id: 'x', fixed_context: 'big' }), { value: '', disabled: false, note: '' });
});

test('a chat on a model with a fixed window never sends its own context length', () => {
  const keys = ['temperature', 'num_ctx'];
  const chosen = { temperature: 0.3, num_ctx: 4096 };
  assert.deepEqual(logic.chatOptionsFor(chosen, keys, { id: 'big:35b', fixed_context: 24576 }), { temperature: 0.3 });
  assert.deepEqual(logic.chatOptionsFor(chosen, keys, { id: 'qwen3:14b' }), { temperature: 0.3, num_ctx: 4096 });
  assert.equal(logic.chatOptionsFor({ num_ctx: 4096 }, keys, { id: 'big:35b', fixed_context: 24576 }), undefined);
  assert.equal(logic.chatOptionsFor({}, keys, null), undefined);
});

test('the chat controls draw each field from paramFieldState and send chatOptionsFor', () => {
  const source = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'static', 'chatControls.js'), 'utf8');
  assert.match(source, /paramFieldState\(param\.key, chatParams, currentModel\(\)\)/);
  assert.match(source, /chatOptionsFor\(chatParams, CHAT_PARAMS\.map\(param => param\.key\), currentModel\(\)\)/);
  assert.match(source, /els\.modelList\.addEventListener\('change', renderParamGrid\)/);
});

test('a cut-off note is taken off a reply before Continue carries it on', () => {
  const note = '\n\n(This reply hit the length limit and was cut off here. Use Continue to carry on.)';
  assert.equal(logic.withoutCutOffNote('The answer is' + note), 'The answer is');
  assert.equal(logic.withoutCutOffNote('A whole reply.'), 'A whole reply.');
  assert.equal(logic.withoutCutOffNote(note + ' more text'), note + ' more text');
});

test('the length-limit notes are taken off whatever trails them and never sent, spoken or carried on', () => {
  const note = logic.CUT_OFF_NOTE;
  assert.equal(logic.withoutCutOffNote('Half' + note + '\n '), 'Half');
  assert.equal(logic.withoutCutOffNote(logic.THINKING_ONLY_NOTE), '');
  assert.equal(logic.withoutCutOffNote(' ' + logic.THINKING_ONLY_NOTE + '\n'), '');
  assert.equal(logic.withoutCutOffNote(note.trim()), '');
  assert.equal(logic.isCutOffNote(note), true);
  assert.equal(logic.isCutOffNote(logic.THINKING_ONLY_NOTE), true);
  assert.equal(logic.isCutOffNote('Half'), false);
  const fs = require('node:fs');
  const source = name => fs.readFileSync(path.join(__dirname, '..', '..', 'static', name), 'utf8');
  assert.match(source('chatSend.js'), /const message = \{ role: m\.role, content: m\.role === 'assistant' \? withoutCutOffNote\(m\.content\) : m\.content \};/);
  assert.match(source('chatSend.js'), /if \(speaker && !isCutOffNote\(obj\.delta\)\) speaker\.push\(obj\.delta\);/);
  assert.match(source('chatSend.js'), /speakThenListen\(withoutCutOffNote\(fullText\)\)/);
  assert.match(source('chatSend.js'), /speakText\(withoutCutOffNote\(fullText\)\)/);
  assert.match(source('chatMessages.js'), /speakText\(withoutCutOffNote\(getText\(\)\)\)/);
});

test('a staying switch named in a message gives the picker that model', () => {
  assert.deepEqual(logic.persistedChoice({ endpoint_id: 'e1', model: 'qwen3:14b', persist: true }), { endpoint_id: 'e1', model: 'qwen3:14b' });
  assert.equal(logic.persistedChoice({ endpoint_id: 'e1', model: 'qwen3:14b' }), null);
  assert.equal(logic.persistedChoice({ endpoint_id: 'e1', model: '', persist: true }), null);
  assert.equal(logic.persistedChoice(undefined), null);
});

test('the chat page opts in to named models and moves the picker on a staying switch', () => {
  const send = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'static', 'chatSend.js'), 'utf8');
  assert.match(send, /names_a_model: true/);
  assert.match(send, /persistedChoice\(obj\.model_choice\)/);
  assert.equal(send.match(/names_a_model/g).length, 1);
});
