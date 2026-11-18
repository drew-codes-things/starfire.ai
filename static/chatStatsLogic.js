'use strict';

const TOKEN_UNITS = [[1e6, 'M'], [1e3, 'K']];
const ONE_DECIMAL_BELOW = 10;
const CHARS_PER_TOKEN_GUESS = 4;
const MIN_MEASURED_MS = 250;
const MS_PER_SECOND = 1000;
const FULL_PERCENT = 100;
const WINDOW_NEARLY_FULL = 0.9;
const STAT_DROP_ORDER = ['cache', 'steps', 'turns', 'speed', 'tokens'];
const ICON_CHARS = 2;
const ITEM_GAP_CHARS = 3;
const WORD_GAP_CHARS = 1;
const GROUP_GAP_CHARS = 3;
const SPEED_FROM_SERVER = 'server';
const SPEED_FROM_STREAM = 'stream';
const SPEED_GUESSED = 'estimate';
const STORED_USAGE_FIELDS = ['prompt_tokens', 'completion_tokens', 'cached_prompt_tokens', 'context_window'];
const RING_MARK = 'ring';

function isCount(value) {
  return Number.isFinite(value) && value >= 0;
}

function oneDecimal(value) {
  return Math.round(value * 10) / 10;
}

function compactTokens(count) {
  if (!isCount(count)) return '';
  for (const [size, suffix] of TOKEN_UNITS) {
    if (count < size) continue;
    const scaled = count / size;
    const shown = scaled < ONE_DECIMAL_BELOW ? oneDecimal(scaled) : Math.round(scaled);
    return shown + suffix;
  }
  return String(Math.round(count));
}

function wholePercent(share) {
  const bounded = Math.min(Math.max(share, 0), 1);
  const rounded = Math.round(bounded * FULL_PERCENT);
  return bounded < 1 ? Math.min(rounded, FULL_PERCENT - 1) : rounded;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function speedText(speed) {
  const value = speed.value < ONE_DECIMAL_BELOW ? oneDecimal(speed.value) : Math.round(speed.value);
  return `${speed.from === SPEED_GUESSED ? '~' : ''}${value} tok/s`;
}

function createReplyMeter({ countsSteps = true } = {}) {
  return { countsSteps, steps: 0, rounds: [], streamedChars: 0, activeMs: 0, burstStartedAt: null, lastTextAt: null };
}

function noteText(meter, chars, nowMs) {
  if (meter.burstStartedAt === null) meter.burstStartedAt = nowMs;
  meter.streamedChars += chars;
  meter.lastTextAt = nowMs;
}

function endBurst(meter) {
  if (meter.burstStartedAt !== null) meter.activeMs += meter.lastTextAt - meter.burstStartedAt;
  meter.burstStartedAt = null;
}

function noteStep(meter) {
  endBurst(meter);
  meter.steps += 1;
}

function noteRoundUsage(meter, usage) {
  endBurst(meter);
  if (usage && typeof usage === 'object') meter.rounds.push(usage);
}

function activeMs(meter) {
  const burst = meter.burstStartedAt === null ? 0 : meter.lastTextAt - meter.burstStartedAt;
  return meter.activeMs + burst;
}

function serverSpeed(rounds) {
  const timed = rounds.filter(round => round.generation_seconds > 0 && isCount(round.completion_tokens));
  if (!timed.length) return null;
  const tokens = timed.reduce((sum, round) => sum + round.completion_tokens, 0);
  const seconds = timed.reduce((sum, round) => sum + round.generation_seconds, 0);
  return { value: tokens / seconds, from: SPEED_FROM_SERVER };
}

function replySpeed(meter) {
  const fromServer = serverSpeed(meter.rounds);
  if (fromServer) return fromServer;
  const elapsed = activeMs(meter);
  if (elapsed < MIN_MEASURED_MS) return null;
  const [onlyRound] = meter.rounds;
  if (meter.rounds.length === 1 && meter.steps === 0 && isCount(onlyRound.completion_tokens)) {
    return { value: onlyRound.completion_tokens / (elapsed / MS_PER_SECOND), from: SPEED_FROM_STREAM };
  }
  if (!meter.streamedChars) return null;
  return { value: meter.streamedChars / CHARS_PER_TOKEN_GUESS / (elapsed / MS_PER_SECOND), from: SPEED_GUESSED };
}

function replyRecord(meter) {
  const record = {};
  const last = meter.rounds[meter.rounds.length - 1];
  const usage = {};
  for (const field of STORED_USAGE_FIELDS) {
    if (last && isCount(last[field])) usage[field] = last[field];
  }
  const speed = replySpeed(meter);
  if (speed) Object.assign(usage, { tokens_per_second: oneDecimal(speed.value), speed_from: speed.from });
  if (Object.keys(usage).length) record.usage = usage;
  if (meter.countsSteps) record.steps = meter.steps;
  return record;
}

function contextTokens(usage) {
  if (!usage || !(isCount(usage.prompt_tokens) || isCount(usage.completion_tokens))) return null;
  return (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
}

function latestUsage(messages) {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (message.role === 'assistant' && message.usage && typeof message.usage === 'object') return message.usage;
  }
  return null;
}

function chatStats(messages, live = null) {
  const list = Array.isArray(messages) ? messages : [];
  const recordedSteps = list.filter(m => m.role === 'assistant' && Number.isInteger(m.steps) && m.steps >= 0);
  const liveCountsSteps = !!live && live.countsSteps;
  const steps = recordedSteps.length || liveCountsSteps
    ? recordedSteps.reduce((sum, m) => sum + m.steps, 0) + (liveCountsSteps ? live.steps : 0)
    : null;
  const liveRound = live && live.rounds.length ? live.rounds[live.rounds.length - 1] : null;
  const stored = latestUsage(list);
  const usage = liveRound || stored;
  const liveSpeed = live ? replySpeed(live) : null;
  const storedSpeed = stored && stored.tokens_per_second > 0 ? { value: stored.tokens_per_second, from: stored.speed_from } : null;
  const tokens = contextTokens(usage);
  const cached = usage && isCount(usage.cached_prompt_tokens) && usage.prompt_tokens > 0
    ? usage.cached_prompt_tokens / usage.prompt_tokens : null;
  const contextWindow = usage && usage.context_window > 0 ? usage.context_window : null;
  return {
    turns: list.filter(m => m.role === 'user').length,
    steps,
    speed: live ? liveSpeed : storedSpeed,
    tokens,
    cacheShare: cached,
    contextWindow: tokens !== null ? contextWindow : null,
    windowShare: tokens !== null && contextWindow ? tokens / contextWindow : null,
  };
}

function statusGroups(stats) {
  const activity = [{ key: 'turns', text: plural(stats.turns, 'turn') }];
  if (stats.steps !== null) activity.push({ key: 'steps', text: plural(stats.steps, 'step'), joinsWithSpace: true });
  if (stats.speed) activity.push({ key: 'speed', text: speedText(stats.speed), estimated: stats.speed.from === SPEED_GUESSED });
  const usage = [];
  if (stats.tokens !== null) usage.push({ key: 'tokens', text: `${compactTokens(stats.tokens)} tok` });
  if (stats.cacheShare !== null) usage.push({ key: 'cache', text: `Cache hit ${wholePercent(stats.cacheShare)}%` });
  const ring = [];
  if (stats.windowShare !== null) {
    const percent = wholePercent(stats.windowShare);
    ring.push({
      key: 'ring', text: `${percent}%`, share: Math.min(stats.windowShare, 1), nearlyFull: stats.windowShare >= WINDOW_NEARLY_FULL,
      label: `${percent}% of the ${compactTokens(stats.contextWindow)} token context window used`,
    });
  }
  return [
    { key: 'activity', icon: 'gauge', items: activity },
    { key: 'usage', icon: 'database', items: usage },
    { key: 'window', icon: RING_MARK, items: ring },
  ].filter(group => group.items.length);
}

function itemChars(item, index) {
  if (index === 0) return item.text.length;
  return item.text.length + (item.joinsWithSpace ? WORD_GAP_CHARS : ITEM_GAP_CHARS);
}

function groupChars(group) {
  const mark = group.icon ? ICON_CHARS : 0;
  return mark + group.items.reduce((sum, item, index) => sum + itemChars(item, index), 0);
}

function statusChars(groups) {
  return groups.reduce((sum, group, index) => sum + groupChars(group) + (index ? GROUP_GAP_CHARS : 0), 0);
}

function withoutItem(groups, key) {
  return groups
    .map(group => ({ ...group, items: group.items.filter(item => item.key !== key) }))
    .map(group => ({ ...group, items: group.items.map((item, index) => (index === 0 ? { ...item, joinsWithSpace: false } : item)) }))
    .filter(group => group.items.length);
}

function fitStatusGroups(groups, maxChars) {
  let fitted = groups;
  for (const key of STAT_DROP_ORDER) {
    if (statusChars(fitted) <= maxChars) return fitted;
    fitted = withoutItem(fitted, key);
  }
  return fitted;
}

function statusSummary(groups) {
  return groups.map(group => group.items.map(item => item.label || item.text).join(', ')).join('; ');
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    compactTokens, wholePercent, speedText,
    createReplyMeter, noteText, noteStep, noteRoundUsage, replySpeed, replyRecord,
    chatStats, statusGroups, statusChars, fitStatusGroups, statusSummary,
    SPEED_FROM_SERVER, SPEED_FROM_STREAM, SPEED_GUESSED, RING_MARK, CHARS_PER_TOKEN_GUESS, MIN_MEASURED_MS,
  };
}
