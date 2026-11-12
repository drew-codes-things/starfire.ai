'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'artyCliLogic.js'));

test('a session line names the computer, folder, model and mode', () => {
  assert.equal(logic.sessionLine({ name: 'VivaDrew', folder: 'arty-cli', model: 'arty/qwen3:14b', mode: 'plan' }),
    'VivaDrew · arty-cli · arty/qwen3:14b · plan mode');
  assert.equal(logic.sessionLine({}), 'A computer · default mode');
});

test('a waiting permission request is described with its tool and what it wants to do', () => {
  assert.equal(logic.permissionText({ tool: 'bash', text: 'rm -rf build' }), 'Arty wants to use bash: rm -rf build');
  assert.equal(logic.permissionText({}), 'Arty wants to use a tool');
});

test('requests still open are the waiting ones minus those answered since', () => {
  const waiting = [{ request_id: 'r1' }, { request_id: 'r2' }];
  const events = [{ type: 'permission', request_id: 'r3', tool: 'edit', text: 'a.py' },
    { type: 'permission_answered', request_id: 'r1', allow: true, by: 'Phone' }];
  assert.deepEqual(logic.openRequests(waiting, events).map((request) => request.request_id), ['r2', 'r3']);
});

test('only real session ids are used in addresses', () => {
  assert.equal(logic.isSessionId('0123456789abcdef'), true);
  assert.equal(logic.isSessionId('../devices'), false);
  assert.equal(logic.isSessionId('0123456789ABCDEF'), false);
});

test('after the first read every poll waits, even before the session has any events', () => {
  assert.deepEqual(logic.FIRST_POLL, { after: 0, wait: 0 });
  assert.deepEqual(logic.nextPoll(logic.FIRST_POLL, { next: 0 }, 20), { after: 0, wait: 20 });
  assert.deepEqual(logic.nextPoll({ after: 4, wait: 20 }, { next: 7 }, 20), { after: 7, wait: 20 });
  assert.deepEqual(logic.nextPoll({ after: 4, wait: 20 }, {}, 20), { after: 4, wait: 20 });
});

function fed(...events) {
  const transcript = logic.newTranscript();
  events.forEach((event, index) => logic.addEvent(transcript, { seq: index + 1, ...event }));
  return transcript;
}

function call(tool, input) {
  return { type: 'tool_call', tool, text: typeof input === 'string' ? input : JSON.stringify(input) };
}

test('people\'s messages are bubbles naming who sent them, and a streamed reply is one message', () => {
  const transcript = logic.newTranscript();
  logic.addEvent(transcript, { type: 'user', text: 'fix it' });
  logic.addEvent(transcript, { type: 'remote_message', text: 'run tests', by: 'Phone' });
  const first = logic.addEvent(transcript, { type: 'assistant', text: 'All ' });
  const second = logic.addEvent(transcript, { type: 'assistant', text: 'green.' });
  assert.equal(first.isNew, true);
  assert.equal(second.isNew, false);
  assert.deepEqual(transcript.items.map(({ kind, who, text }) => ({ kind, who, text })), [
    { kind: 'user', who: 'On the computer', text: 'fix it' }, { kind: 'user', who: 'Phone', text: 'run tests' },
    { kind: 'assistant', who: undefined, text: 'All green.' }]);
});

test('tool steps in a row are one quiet group with plain-English summaries', () => {
  const { items } = fed(call('read_file', { path: 'src/app.py' }), { type: 'tool_result', tool: 'read_file', text: '1\tprint()' },
    call('read_file', { path: 'b.py' }), call('search', { pattern: 'TODO' }), { type: 'tool_result', tool: 'search', text: 'Error: bad pattern' },
    { type: 'assistant', text: 'Found it.' }, call('run_command', { command: 'pytest -q' }));
  assert.deepEqual(items.map((item) => item.kind), ['tools', 'assistant', 'tools']);
  assert.equal(logic.toolSummary(items[0].steps), 'Read 2 files, searched the project');
  assert.equal(logic.toolSummary(items[2].steps), 'Ran a command');
  assert.deepEqual(items[0].steps.map(({ output, failed }) => ({ output, failed })), [
    { output: '1\tprint()', failed: false }, { output: null, failed: false }, { output: 'Error: bad pattern', failed: true }]);
  assert.equal(logic.toolSummary(fed(call('mcp__github__open_issue', {})).items[0].steps), 'Used open_issue from github');
  assert.equal(logic.toolSummary(fed(call('edit_file', { path: 'a/app.py' })).items[0].steps), 'Edited app.py');
});

test('each step has its own line and readable details, never raw JSON', () => {
  const [command, edit, other] = fed(call('run_command', { command: 'pytest -q' }),
    call('edit_file', { path: 'app.py', old_text: 'a = 1', new_text: 'a = 2' }), call('todo_write', { items: ['one', 'two'] })).items[0].steps;
  assert.equal(logic.stepLine(command), 'Ran a command: pytest -q');
  assert.equal(logic.stepLine(edit), 'Edited app.py');
  assert.equal(logic.readableInput(command), '$ pytest -q');
  assert.equal(logic.readableInput(edit), 'path: app.py\n- a = 1\n+ a = 2');
  assert.equal(logic.readableInput(other), 'items: one, two');
  assert.deepEqual(['+ new', '- old', '@@ -1 +1 @@', '--- a.py', 'same'].map(logic.diffTone), ['added', 'removed', 'marker', '', '']);
});

test('an answered request becomes one line saying who answered, how and what it was', () => {
  const { items } = fed({ type: 'permission', request_id: 'r1', text: 'Run command: pytest\n$ pytest' },
    { type: 'permission_answered', request_id: 'r1', allow: true, by: 'Phone' },
    { type: 'permission_answered', request_id: 'r2', allow: false, by: 'the computer' });
  assert.deepEqual(items.map((item) => item.text), ['Allowed by Phone: Run command: pytest', 'Denied on the computer']);
});

test('a request is shown as its question with the rest as a preview', () => {
  assert.deepEqual(logic.permissionPrompt({ text: 'Edit app.py?\n--- app.py\n+x' }), { heading: 'Edit app.py?', preview: '--- app.py\n+x' });
  assert.deepEqual(logic.permissionPrompt({ tool: 'bash', text: 'rm -rf build' }), { heading: 'Arty wants to use bash: rm -rf build', preview: '' });
});

test('status lines, problems and the end are notes, and only the newest items are kept', () => {
  const { items } = fed({ type: 'status', text: 'Mode: plan' }, { type: 'status', mode: 'plan' }, { type: 'error', text: 'Stopped by Phone.' },
    { type: 'end' }, { type: 'something new' });
  assert.deepEqual(items.map(({ text, problem }) => ({ text, problem })), [
    { text: 'Mode: plan', problem: false }, { text: 'Stopped by Phone.', problem: true }, { text: 'The session ended on the computer.', problem: false }]);
  const transcript = logic.newTranscript();
  for (let index = 0; index < logic.MAX_KEPT_ITEMS + 2; index += 1) logic.addEvent(transcript, { type: 'user', text: `line ${index}` });
  assert.equal(logic.trimTranscript(transcript), 2);
  assert.equal(transcript.items[0].text, 'line 2');
  assert.equal(logic.addNote(transcript, 'gap'), transcript.items.at(-1));
});

test('the activity line says what Arty is doing now, and when it is finished or waiting', () => {
  const activity = (events, options) => logic.activityOf(fed(...events), options);
  assert.deepEqual(activity([]), { phase: 'idle', text: 'Waiting for a message' });
  assert.deepEqual(activity([{ type: 'user', text: 'go' }]), { phase: 'working', text: 'Thinking' });
  assert.deepEqual(activity([call('run_command', { command: 'pytest -q' })]), { phase: 'working', text: 'Running pytest -q' });
  assert.deepEqual(activity([{ type: 'assistant', text: 'Hi' }], { quietMs: 0 }), { phase: 'working', text: 'Writing a reply' });
  assert.deepEqual(activity([{ type: 'assistant', text: 'Hi' }], { quietMs: logic.FINISHED_AFTER_MS }), { phase: 'finished', text: 'Finished. Your turn.' });
  assert.deepEqual(activity([{ type: 'error', text: 'Stopped.' }]), { phase: 'finished', text: 'Stopped. Your turn.' });
  assert.deepEqual(activity([{ type: 'user', text: 'go' }], { waiting: 1 }), { phase: 'waiting', text: 'Waiting for you to allow or deny a step' });
  assert.deepEqual(activity([{ type: 'user', text: 'go' }], { ended: true }), { phase: 'ended', text: 'This session has ended' });
});

test('the view follows new lines only when already at the bottom', () => {
  assert.equal(logic.isNearBottom({ scrollTop: 552, scrollHeight: 1000, clientHeight: 400 }), true);
  assert.equal(logic.isNearBottom({ scrollTop: 100, scrollHeight: 1000, clientHeight: 400 }), false);
  assert.equal(logic.isNearBottom(null), true);
});

test('the header names the computer and its folder, model and mode', () => {
  assert.equal(logic.computerName({}), 'A computer');
  assert.equal(logic.sessionDetails({ folder: 'arty-cli', model: 'arty/qwen3:14b', mode: 'plan' }), 'arty-cli · arty/qwen3:14b · plan mode');
});

test('markdown becomes a safe tree of text: html stays text and only web links are links', () => {
  const blocks = logic.markdownBlocks('# Done\n<img src=x onerror=alert(1)> **bold** `code`\n[bad](javascript:alert(1)) [good](https://example.com).\n\n- one\n1. first\n\n```\n<script>x</script>\n```\n> quoted');
  assert.deepEqual(blocks, [
    { type: 'heading', spans: [{ type: 'text', text: 'Done' }] },
    { type: 'paragraph', lines: [
      [{ type: 'text', text: '<img src=x onerror=alert(1)> ' }, { type: 'strong', text: 'bold' }, { type: 'text', text: ' ' }, { type: 'code', text: 'code' }],
      [{ type: 'text', text: '[bad](javascript:alert(1)) ' }, { type: 'link', text: 'good', href: 'https://example.com' }, { type: 'text', text: '.' }]] },
    { type: 'list', ordered: false, items: [[{ type: 'text', text: 'one' }]] },
    { type: 'list', ordered: true, items: [[{ type: 'text', text: 'first' }]] },
    { type: 'code', text: '<script>x</script>' },
    { type: 'quote', lines: [[{ type: 'text', text: 'quoted' }]] }]);
  assert.deepEqual(logic.markdownBlocks('see https://example.com/a. 2*3*4 and *this*')[0].lines[0], [
    { type: 'text', text: 'see ' }, { type: 'link', text: 'https://example.com/a', href: 'https://example.com/a' },
    { type: 'text', text: '. 2*3*4 and ' }, { type: 'em', text: 'this' }]);
});

test('a request asked again under an answered id comes back, but a replay of the old one does not', () => {
  const answered = new Map([['r1', 4]]);
  assert.deepEqual(logic.askedAgain([{ type: 'permission', request_id: 'r1', seq: 3 }], answered), []);
  assert.deepEqual(logic.askedAgain([{ type: 'permission', request_id: 'r1', seq: 5 }, { type: 'user', seq: 6 }], answered), ['r1']);
});

test('a terminal is listed by its own title, with the computer beside it and no repeated folder', () => {
  const named = { title: 'greeter 2', name: 'VivaDrew', folder: 'greeter', model: 'arty/qwen3:14b', mode: 'plan' };
  assert.equal(logic.sessionTitle(named), 'greeter 2');
  assert.equal(logic.sessionLine(named), 'greeter 2 · VivaDrew · arty/qwen3:14b · plan mode');
  assert.equal(logic.sessionLine({ title: 'docs work', name: 'VivaDrew', folder: 'greeter' }), 'docs work · VivaDrew · greeter · default mode');
  assert.equal(logic.sessionTitle({ name: 'VivaDrew' }), 'VivaDrew');
  assert.equal(logic.sessionTitle({}), 'A computer');
});

test('the conversation from before sharing shows in full, then one line marks where sharing began', () => {
  const transcript = logic.newTranscript();
  const events = [
    { type: 'user', text: 'fix the greet test', history: true },
    { type: 'tool_call', tool: 'read_file', text: '{"path":"greet.py"}', history: true },
    { type: 'tool_result', tool: 'read_file', text: 'def greet', history: true },
    { type: 'assistant', text: 'Fixed.', history: true },
    { type: 'user', text: 'now the docs' },
    { type: 'assistant', text: 'On it.' },
  ];
  for (const event of events) logic.addEvent(transcript, event);
  assert.deepEqual(transcript.items.map((item) => item.kind), ['user', 'tools', 'assistant', 'note', 'user', 'assistant']);
  assert.equal(transcript.items[3].text, logic.SHARED_FROM_HERE);
  assert.equal(transcript.items[2].text, 'Fixed.');
  assert.equal(transcript.items[5].text, 'On it.');
});

test('earlier conversation does not count as activity, so a quiet shared chat reads as waiting', () => {
  const transcript = logic.newTranscript();
  logic.addEvent(transcript, { type: 'user', text: 'fix the greet test', history: true });
  logic.addEvent(transcript, { type: 'tool_call', tool: 'read_file', text: '{}', history: true });
  assert.deepEqual(logic.activityOf(transcript), { phase: 'idle', text: 'Waiting for a message' });
});

test('a compacted conversation shows as a marker holding the summary, and the chat carries on after it', () => {
  const transcript = logic.newTranscript();
  logic.addEvent(transcript, { type: 'user', text: 'long job' });
  logic.addEvent(transcript, { type: 'assistant', text: 'Working' });
  const change = logic.addEvent(transcript, { type: 'compacted', text: 'We fixed greet().' });
  logic.addEvent(transcript, { type: 'assistant', text: 'Carrying on.' });
  assert.equal(change.isNew, true);
  assert.deepEqual(transcript.items.map((item) => item.kind), ['user', 'assistant', 'compacted', 'assistant']);
  assert.equal(transcript.items[2].text, 'We fixed greet().');
  assert.equal(transcript.items[3].text, 'Carrying on.');
  assert.equal(logic.activityOf(transcript).phase, 'working');
});

test('a compacted marker with no summary still shows, and the viewer keeps room for a long history', () => {
  const transcript = logic.newTranscript();
  logic.addEvent(transcript, { type: 'compacted', history: true });
  assert.equal(transcript.items[0].kind, 'compacted');
  assert.equal(transcript.items[0].text, '');
  assert.ok(logic.MAX_KEPT_ITEMS >= 2000);
});
