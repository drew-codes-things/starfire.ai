'use strict';

const MAX_TAGS = 3;
const MAX_TAG_CHARS = 24;
const MAX_FOLLOW_UPS = 3;
const MAX_FOLLOW_UP_CHARS = 140;

async function askHelper(model, prompt) {
  const r = await fetch('/api/quick-complete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id, prompt, helper: true }),
  });
  if (!r.ok) throw new Error(`helper request failed (${r.status})`);
  const { text } = await r.json();
  return (text || '').trim();
}

function conversationExcerpt(count) {
  return messages.slice(-count).map(m => `${m.role}: ${m.content}`).join('\n').slice(0, 2000);
}

async function describeNewSession(sessionId, model) {
  if (prefs.autoTitle) await generateSessionTitle(sessionId, model);
  if (prefs.autoTags) await generateSessionTags(sessionId, model);
}

async function updateSession(sessionId, fields) {
  await fetch('/api/chat/sessions/' + sessionId, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  refreshSidebar();
}

async function generateSessionTitle(sessionId, model) {
  try {
    const text = await askHelper(model,
      `Give this conversation a short title (3-6 words, no quotes, no trailing punctuation):\n\n${conversationExcerpt(2)}`);
    const title = text.replace(/^["']|["']$/g, '').replace(/[.!?]+$/, '').slice(0, 60);
    if (title) await updateSession(sessionId, { title });
  } catch (e) {
    console.warn('starfire.ai: no title generated', e);
  }
}

function parseTags(text) {
  const tags = text.toLowerCase().split(/[,\n]/)
    .map(t => t.replace(/^[\s#*\-\d.)]+/, '').replace(/[^a-z0-9 -]/g, '').trim().replace(/\s+/g, '-'))
    .filter(t => t && t.length <= MAX_TAG_CHARS);
  return [...new Set(tags)].slice(0, MAX_TAGS);
}

async function generateSessionTags(sessionId, model) {
  try {
    const text = await askHelper(model,
      `Give 1 to 3 short, lowercase topic tags for this conversation (for example: networking, cooking, python). ` +
      `Reply with only the tags, separated by commas.\n\n${conversationExcerpt(2)}`);
    const tags = parseTags(text);
    if (tags.length) await updateSession(sessionId, { tags });
  } catch (e) {
    console.warn('starfire.ai: no tags generated', e);
  }
}

function parseFollowUps(text) {
  return text.split('\n')
    .map(line => line.replace(/^[\s\-*•\d.)]+/, '').replace(/^["']|["']$/g, '').trim())
    .filter(line => line.length > 3 && line.length <= MAX_FOLLOW_UP_CHARS)
    .slice(0, MAX_FOLLOW_UPS);
}

function clearFollowUps() {
  document.querySelectorAll('.follow-ups').forEach(el => el.remove());
}

async function showFollowUps(row, model) {
  const messageCount = messages.length;
  try {
    const text = await askHelper(model,
      `Suggest ${MAX_FOLLOW_UPS} short follow-up questions the user might ask next, written as the user, ` +
      `one per line, with no numbering or extra text.\n\n${conversationExcerpt(4)}`);
    const questions = parseFollowUps(text);
    if (!questions.length || busy || messages.length !== messageCount || !row.isConnected) return;
    clearFollowUps();
    const box = document.createElement('div');
    box.className = 'follow-ups';
    for (const question of questions) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'follow-up';
      btn.textContent = question;
      btn.onclick = () => { clearFollowUps(); send(question); };
      box.appendChild(btn);
    }
    row.appendChild(box);
  } catch (e) {
    console.warn('starfire.ai: no follow-up suggestions', e);
  }
}
