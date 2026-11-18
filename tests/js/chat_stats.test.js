'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const stats = require(path.join(__dirname, '..', '..', 'static', 'chatStatsLogic.js'));

const USER = { role: 'user', content: 'hi' };

function reply(extra = {}) {
  return { role: 'assistant', content: 'hello', ...extra };
}

function texts(groups) {
  return groups.map(group => group.items.map(item => item.text));
}

test('token counts are shown compactly the way the status bar wants', () => {
  assert.equal(stats.compactTokens(108000), '108K');
  assert.equal(stats.compactTokens(108499), '108K');
  assert.equal(stats.compactTokens(950), '950');
  assert.equal(stats.compactTokens(0), '0');
  assert.equal(stats.compactTokens(1500), '1.5K');
  assert.equal(stats.compactTokens(12288), '12K');
  assert.equal(stats.compactTokens(2000000), '2M');
  assert.equal(stats.compactTokens(-1), '');
  assert.equal(stats.compactTokens(undefined), '');
});

test('shares become whole percents that stay between 0 and 100', () => {
  assert.equal(stats.wholePercent(0.99), 99);
  assert.equal(stats.wholePercent(0.1), 10);
  assert.equal(stats.wholePercent(1.4), 100);
  assert.equal(stats.wholePercent(-0.2), 0);
});

test('100% is shown only when the share really is whole', () => {
  assert.equal(stats.wholePercent(0.9996), 99);
  assert.equal(stats.wholePercent(1), 100);
  const messages = [USER, reply({ usage: { prompt_tokens: 104011, completion_tokens: 18, cached_prompt_tokens: 103971 } })];
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats(messages)))[1], ['104K tok', 'Cache hit 99%']);
});

test('the full bar matches the screenshot when every figure is known', () => {
  const messages = [
    USER, reply({ steps: 4 }), USER, reply({ steps: 6 }), USER,
    reply({ steps: 0, usage: { prompt_tokens: 107000, completion_tokens: 1000, cached_prompt_tokens: 105930,
      context_window: 1080000, tokens_per_second: 27.4, speed_from: 'server' } }),
  ];
  const groups = stats.statusGroups(stats.chatStats(messages));
  assert.deepEqual(texts(groups), [['3 turns', '10 steps', '27 tok/s'], ['108K tok', 'Cache hit 99%'], ['10%']]);
  assert.equal(groups[2].items[0].label, '10% of the 1.1M token context window used');
});

test('figures the model server never reported are hidden, not guessed', () => {
  const messages = [USER, reply({ usage: { prompt_tokens: 5400, completion_tokens: 80 } })];
  const groups = stats.statusGroups(stats.chatStats(messages));
  assert.deepEqual(texts(groups), [['1 turn'], ['5.5K tok']]);
});

test('an old chat with no recorded usage shows only its turns', () => {
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats([USER, reply(), USER, reply()]))), [['2 turns']]);
});

test('a cache figure of zero that the server did report is shown as 0%', () => {
  const messages = [USER, reply({ usage: { prompt_tokens: 400, completion_tokens: 10, cached_prompt_tokens: 0 } })];
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats(messages)))[1], ['410 tok', 'Cache hit 0%']);
});

test('the server timing wins over the stream and is summed over tool rounds', () => {
  const meter = stats.createReplyMeter();
  stats.noteText(meter, 400, 0);
  stats.noteText(meter, 400, 2000);
  stats.noteRoundUsage(meter, { prompt_tokens: 900, completion_tokens: 30, generation_seconds: 1 });
  stats.noteStep(meter);
  stats.noteRoundUsage(meter, { prompt_tokens: 1000, completion_tokens: 70, generation_seconds: 3 });
  assert.deepEqual(stats.replySpeed(meter), { value: 25, from: stats.SPEED_FROM_SERVER });
  assert.deepEqual(stats.replyRecord(meter), {
    usage: { prompt_tokens: 1000, completion_tokens: 70, tokens_per_second: 25, speed_from: 'server' }, steps: 1 });
});

test('without server timing the speed is measured from the stream with the reported token count', () => {
  const meter = stats.createReplyMeter();
  stats.noteText(meter, 10, 1000);
  stats.noteText(meter, 10, 3000);
  stats.noteRoundUsage(meter, { prompt_tokens: 50, completion_tokens: 54 });
  assert.deepEqual(stats.replySpeed(meter), { value: 27, from: stats.SPEED_FROM_STREAM });
  assert.equal(stats.speedText(stats.replySpeed(meter)), '27 tok/s');
});

test('while text streams in the speed is a marked estimate from the characters', () => {
  const meter = stats.createReplyMeter();
  stats.noteText(meter, 40, 0);
  stats.noteText(meter, 80, 1000);
  const speed = stats.replySpeed(meter);
  assert.deepEqual(speed, { value: 120 / stats.CHARS_PER_TOKEN_GUESS, from: stats.SPEED_GUESSED });
  assert.equal(stats.speedText(speed), '~30 tok/s');
  assert.equal(stats.speedText({ value: 4.46, from: stats.SPEED_FROM_SERVER }), '4.5 tok/s');
});

test('time spent running tools does not count as generation time', () => {
  const meter = stats.createReplyMeter();
  stats.noteText(meter, 40, 0);
  stats.noteText(meter, 40, 1000);
  stats.noteStep(meter);
  stats.noteText(meter, 40, 9000);
  stats.noteText(meter, 40, 10000);
  assert.equal(stats.replySpeed(meter).value, 160 / stats.CHARS_PER_TOKEN_GUESS / 2);
});

test('a reply that arrived in one piece has no speed rather than a made-up one', () => {
  const meter = stats.createReplyMeter();
  stats.noteText(meter, 500, 0);
  stats.noteRoundUsage(meter, { prompt_tokens: 50, completion_tokens: 120 });
  assert.equal(stats.replySpeed(meter), null);
  assert.deepEqual(stats.replyRecord(meter), { usage: { prompt_tokens: 50, completion_tokens: 120 }, steps: 0 });
});

test('a model that runs its own tools records no step count, so steps stay hidden', () => {
  const meter = stats.createReplyMeter({ countsSteps: false });
  assert.deepEqual(stats.replyRecord(meter), {});
  const live = stats.chatStats([USER], meter);
  assert.equal(live.steps, null);
});

test('the bar updates live: the new turn, its steps and the latest round count before the reply is saved', () => {
  const messages = [USER, reply({ steps: 2, usage: { prompt_tokens: 100, completion_tokens: 20, context_window: 1000 } }), USER];
  const meter = stats.createReplyMeter();
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats(messages, meter))), [['2 turns', '2 steps'], ['120 tok'], ['12%']]);
  stats.noteStep(meter);
  stats.noteRoundUsage(meter, { prompt_tokens: 300, completion_tokens: 40, context_window: 1000 });
  stats.noteText(meter, 40, 0);
  stats.noteText(meter, 40, 500);
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats(messages, meter))), [['2 turns', '3 steps', '~40 tok/s'], ['340 tok'], ['34%']]);
});

test('items drop off in the footer order as the bar gets narrower and the ring stays last', () => {
  const messages = [USER, reply({ steps: 10, usage: { prompt_tokens: 107000, completion_tokens: 1000,
    cached_prompt_tokens: 105930, context_window: 1080000, tokens_per_second: 27, speed_from: 'server' } })];
  const groups = stats.statusGroups(stats.chatStats(messages));
  const full = stats.statusChars(groups);
  assert.equal(stats.fitStatusGroups(groups, full), groups);
  const keys = width => stats.fitStatusGroups(groups, width).flatMap(group => group.items.map(item => item.key));
  assert.equal(full, 65);
  assert.deepEqual(keys(full - 1), ['turns', 'steps', 'speed', 'tokens', 'ring']);
  assert.deepEqual(keys(40), ['turns', 'speed', 'tokens', 'ring']);
  assert.deepEqual(keys(31), ['speed', 'tokens', 'ring']);
  assert.deepEqual(keys(18), ['tokens', 'ring']);
  assert.deepEqual(keys(5), ['ring']);
  for (const width of [64, 49, 40, 31, 18, 5]) assert.ok(stats.statusChars(stats.fitStatusGroups(groups, width)) <= width);
});

test('the summary for screen readers and tooltips names every visible figure', () => {
  const messages = [USER, reply({ usage: { prompt_tokens: 900, completion_tokens: 100, context_window: 4000 } })];
  assert.equal(stats.statusSummary(stats.statusGroups(stats.chatStats(messages))),
    '1 turn; 1K tok; 25% of the 4K token context window used');
});

test('a window that overflows still draws a full ring and is flagged as nearly full', () => {
  const messages = [USER, reply({ usage: { prompt_tokens: 5000, completion_tokens: 100, context_window: 4096 } })];
  const [, , ring] = stats.statusGroups(stats.chatStats(messages));
  assert.equal(ring.items[0].text, '100%');
  assert.equal(ring.items[0].share, 1);
  assert.equal(ring.items[0].nearlyFull, true);
});

test('bad stored values are ignored instead of breaking the bar', () => {
  const messages = [USER, reply({ steps: 'many', usage: { prompt_tokens: 'x', context_window: -1 } }), null];
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats(messages.filter(Boolean)))), [['1 turn']]);
  assert.deepEqual(texts(stats.statusGroups(stats.chatStats(undefined))), [['0 turns']]);
});
