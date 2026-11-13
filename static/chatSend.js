'use strict';

function saveDraft() {
  if (isSideChatOpen()) return;
  try { localStorage.setItem('sf_draft_messages', JSON.stringify(messages)); } catch (_) {}
}

async function persistSession() {
  if (isTemporaryChat()) return;
  const model = currentModel();
  const isNewSession = !currentSessionId;
  try {
    if (!currentSessionId) {
      const r = await fetch('/api/chat/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: (messages[0]?.content || 'New chat').slice(0, 60) }),
      });
      if (!r.ok) return;
      const session = await r.json();
      currentSessionId = session.id;
      try { localStorage.setItem('sf_current_session', currentSessionId); } catch (_) {}
      onChatChanged();
    }
    const folderId = isNewSession ? takePendingFolderId() : '';
    await fetch('/api/chat/sessions/' + currentSessionId, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages, endpoint_id: model ? model.endpoint_id : '', model: model ? model.id : '',
        ...currentChatControls(),
        ...(folderId ? { folder_id: folderId } : {}),
      }),
    });
    try { localStorage.removeItem('sf_draft_messages'); } catch (_) {}
    refreshSidebar();

    if (isNewSession && model && messages.length >= 2) describeNewSession(currentSessionId, model);
  } catch (_) {

  }
}

async function restoreSession() {
  if (currentSessionId) {
    try {
      const r = await fetch('/api/chat/sessions/' + currentSessionId);
      if (r.ok) {
        const session = await r.json();
        messages = session.messages || [];
        showSessionControls(session);
        selectModel(session.endpoint_id, session.model);
        rerenderAll();
        onChatChanged();
        return;
      }
    } catch (_) {}
  }
  try {
    const draft = JSON.parse(localStorage.getItem('sf_draft_messages') || 'null');
    if (Array.isArray(draft) && draft.length) {
      messages = draft;
      rerenderAll();
    }
  } catch (_) {}
}

function warmModel(model) {
  if (!model) return;
  fetch('/api/warm', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id }),
  }).catch(() => {});
}

function currentModel() {
  const opt = els.modelList.selectedOptions[0];
  if (!opt) return null;
  return models.find(m => m.endpoint_id === opt.dataset.endpointId && m.id === opt.value) || null;
}

async function loadModels() {
  setStatus('loading', 'connecting...');
  try {
    const r                       = await fetch('/api/models');
    const { models: list = [] }   = await r.json();
    models = list;

    els.modelList.innerHTML = '';
    for (const m of models) {
      const opt            = document.createElement('option');
      opt.value             = m.id;
      opt.dataset.endpointId = m.endpoint_id;
      const label           = window.providerLabel ? window.providerLabel(m.provider) : m.provider;
      opt.textContent        = m.id + (m.size ? ' (' + (m.size / 1073741824).toFixed(1) + ' GB)' : '') + ' - ' + label;
      els.modelList.appendChild(opt);
    }

    selectDefaultModel();
    renderModelPicker();
    if (!messages.length) renderWelcome();

    if (!models.length) {
      setStatus('error', 'no models configured');
      els.input.disabled   = true;
      els.sendBtn.disabled = true;
      return;
    }

    setStatus('ready', els.modelList.value);
    setOffline(false);
    warmModel(currentModel());
    els.input.disabled   = false;
    els.sendBtn.disabled = false;
    els.input.focus();
  } catch (e) {
    setStatus('error', 'cannot reach starfire backend');
    setOffline(true);
    printLine('error: ' + e.message, 'err');
  }
}

function apiMessages(list) {
  return list.map(m => {
    const message = { role: m.role, content: m.content };
    if (m.attachments && m.attachments.length) message.attachments = m.attachments.map(a => a.id);
    return message;
  });
}

async function send(text, overrideModel, extra = {}) {
  if (busy) return;
  const model = overrideModel || currentModel();
  if (!model) return;

  busy = true;
  clearFollowUps();
  els.sendBtn.disabled = true;
  els.input.disabled   = true;
  els.stopBtn.hidden    = false;
  abortController       = new AbortController();

  const userIdx = messages.length;
  const userMessage = { role: 'user', content: extra.content || text, created: new Date().toISOString() };
  if (extra.content || extra.files) Object.assign(userMessage, { display: text, files: extra.files || [] });
  if (extra.attachments && extra.attachments.length) userMessage.attachments = extra.attachments;
  messages.push(userMessage);
  const userRow = appendMessageRow('user', userIdx, null, userMessage.files, userMessage.attachments);
  userRow.content.textContent = text;
  userRow.row.classList.add('fresh');
  addActionButtons('user', userRow.row, userRow.actions, userIdx);
  saveDraft();
  const sentFromComposer = els.input.value.trim() === text;
  if (sentFromComposer) {
    els.input.value = '';
    els.input.style.height = 'auto';
  }
  const typing = showTypingIndicator(model.id);

  let aiLine = null;
  let aiActions = null;
  let aiRow = null;
  let renderer = null;
  let gotToken = false;
  const aiIdx = messages.length;
  stopServerSpeech();
  const speaker = (callModeActive() || prefs.autoSpeak) && modelHasServerVoice(model)
    ? createServerSpeaker(model, () => { if (callModeActive()) setCallState('speaking'); })
    : null;
  activeSpeaker = speaker;

  setStatus('loading', 'waiting...');

  try {

    const r = await fetch('/api/chat_stream', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      signal:  abortController.signal,
      body:    JSON.stringify({
        endpoint_id: model.endpoint_id,
        model:       model.id,
        messages:    apiMessages(sideChatContext(sideChat, messages)),
        system:      els.sysPrompt.value.trim() || undefined,
        options:     chatOptions(),
        enabled_mcp_servers: modelHasOwnTools(model) ? [] : [...enabledMcpServers],
        enabled_builtin_tools: modelHasOwnTools(model) ? [] : [...enabledBuiltinTools],
        require_edit_approval: requireEditApproval,
        require_shell_approval: requireShellApproval,
        memory_commands: !isTemporaryChat(),
      }),
    });

    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'server error ' + r.status);
    }

    const reader = r.body.getReader();
    const dec    = new TextDecoder();
    let   buf    = '';
    let   streamError = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const obj = JSON.parse(line.slice(5).trim());
          if (obj.delta) {
            if (!gotToken) { gotToken = true; setStatus('ready', model.id); }
            if (!aiLine) {
              typing.remove();
              const created = appendMessageRow('ai', aiIdx, model.id);
              aiLine = created.content; aiActions = created.actions; aiRow = created.row;
              aiRow.classList.add('fresh');
              aiLine.classList.add('streaming');
              renderer = createStreamRenderer(aiLine, { render: markdownRender });
            }
            renderer.push(obj.delta);
            if (speaker) speaker.push(obj.delta);
            els.output.scrollTop = els.output.scrollHeight;
          } else if (obj.tool_start) {
            printLine('» calling ' + obj.tool_start.replace(/^mcp__[^_]+__/, '') + '…', 'info');
          } else if (obj.tool_result) {
            const toolName = obj.tool_result.name;
            if (toolName === 'edit_file') {
              renderDiffCard(obj.tool_result.result);
            } else if (toolName === 'generate_image' || toolName === 'generate_audio' || toolName === 'generate_document' || toolName === 'find_image') {
              renderGeneratedFileCard(toolName, obj.tool_result.result);
            } else if (toolName === 'generate_video') {
              renderGeneratedVideoCard(obj.tool_result.result);
            } else if (toolName === 'run_shell') {
              renderShellResultCard(obj.tool_result.result);
            } else {
              printLine('» ' + toolName.replace(/^mcp__[^_]+__/, '') + ' done', 'info');
            }
          } else if (obj.shell_pending) {
            renderShellApprovalCard(obj.shell_pending);
          } else if (obj.error) {
            streamError = obj.error;
          }
        } catch (_) {}
      }
    }

    if (streamError) throw new Error(streamError);

    if (aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      decorateCodeBlocks(aiLine, renderer.text());
      showInlineMedia(aiLine);
    }
    setStatus('ready', model.id);
    const fullText = renderer ? renderer.text() : '';
    if (fullText) {
      messages.push({ role: 'assistant', content: fullText, model: model.id, created: new Date().toISOString() });
      if (aiActions) addActionButtons('ai', aiRow, aiActions, aiIdx);
      if (speaker) speaker.finish().then(() => { if (callModeActive() && activeSpeaker === speaker) setTimeout(listenForCallTurn, 250); });
      else if (callModeActive()) speakThenListen(fullText);
      else if (prefs.autoSpeak) speakText(fullText);
      if (prefs.followUps && aiRow && !callModeActive()) showFollowUps(aiRow, model);
    }
    persistSession();
    if (aiLine && fullText) verifyCodeBlocks(aiLine, aiIdx, model);
  } catch (e) {
    const aborted = e.name === 'AbortError';
    if (speaker && aborted) speaker.stop();
    if (gotToken && aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      decorateCodeBlocks(aiLine, renderer.text());
      showInlineMedia(aiLine);
      const fullText = renderer.text();
      if (fullText) {
        messages.push({ role: 'assistant', content: fullText, model: model.id, created: new Date().toISOString() });
        if (aiActions) addActionButtons('ai', aiRow, aiActions, aiIdx);
      }
    } else if (!aborted) {
      messages.splice(userIdx, 1);
      rerenderAll();
      if (sentFromComposer && !els.input.value) {
        els.input.value = text;
        els.input.style.height = Math.min(els.input.scrollHeight, COMPOSER_MAX_HEIGHT_PX) + 'px';
      }
    }
    if (aborted) {
      setStatus('ready', model.id);
      printLine('- stopped -', 'info');
      persistSession();
    } else {
      setStatus('error', 'error');
      printLine('error: ' + e.message, 'err');
    }
    if (callModeActive()) setTimeout(() => speakThenListen("Sorry, that didn't work. Try again."), 0);
  } finally {
    typing.remove();
    busy                   = false;
    abortController        = null;
    els.input.disabled     = false;
    els.sendBtn.disabled   = false;
    els.stopBtn.hidden      = true;
    els.input.focus();
  }
}

const CONTINUE_INSTRUCTION = 'Continue your previous reply exactly where it stopped. Do not repeat anything or add a preamble.';
const CHARS_PER_TOKEN_ESTIMATE = 4;
const COMPOSER_MAX_HEIGHT_PX = 240;

async function continueResponse(idx) {
  const target = messages[idx];
  const model = currentModel();
  if (busy || !target || target.role !== 'assistant' || !model) return;
  const contentEl = els.output.querySelector(`[data-msg-index="${idx}"]`);
  if (!contentEl) return;

  busy = true;
  els.sendBtn.disabled = true;
  els.stopBtn.hidden = false;
  abortController = new AbortController();
  contentEl.classList.add('streaming');
  setStatus('loading', 'continuing...');
  let streamError = null;
  const separator = /\s$/.test(target.content) ? '' : ' ';
  let added = '';
  try {
    const r = await fetch('/api/chat_stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      signal: abortController.signal,
      body: JSON.stringify({
        endpoint_id: model.endpoint_id, model: model.id,
        messages: [...apiMessages(sideChatContext(sideChat, messages.slice(0, idx + 1))), { role: 'user', content: CONTINUE_INSTRUCTION }],
        system: els.sysPrompt.value.trim() || undefined,
        options: chatOptions(),
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'server error ' + r.status);
    }
    await readProgressStream(r, obj => {
      if (obj.error) { streamError = obj.error; return; }
      if (!obj.delta) return;
      added += obj.delta;
      contentEl.innerHTML = markdownRender(target.content + separator + added);
    });
    showInlineMedia(contentEl);
    if (streamError) throw new Error(streamError);
    setStatus('ready', model.id);
  } catch (e) {
    if (e.name !== 'AbortError') {
      setStatus('error', 'error');
      printLine('error: ' + e.message, 'err');
    } else {
      setStatus('ready', model.id);
    }
  } finally {
    if (added) {
      target.content += separator + added;
      delete target.code_checks;
    }
    busy = false;
    abortController = null;
    els.sendBtn.disabled = false;
    els.stopBtn.hidden = true;
    rerenderAll();
    persistSession();
    const refreshed = els.output.querySelector(`[data-msg-index="${idx}"]`);
    if (added && refreshed) verifyCodeBlocks(refreshed, idx, model);
  }
}

function downloadText(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a   = Object.assign(document.createElement('a'), { href: url, download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

function exportChat(format = 'md') {
  if (!messages.length) return;
  const base = 'starfire-' + Date.now();
  if (format === 'json') {
    const model = currentModel();
    const chat = { title: currentChatTitle(), messages, model: model ? model.id : '' };
    downloadText(base + '.json', JSON.stringify([chat], null, 2), 'application/json');
    return;
  }
  const separator = format === 'md' ? '\n\n---\n\n' : '\n\n';
  const heading = m => format === 'md' ? '### ' + m.role : m.role.toUpperCase() + ':';
  const text = messages.map(m => heading(m) + '\n\n' + (m.display ?? m.content)).join(separator);
  downloadText(base + '.' + format, text, 'text/plain');
}

function clearChat() {
  discardSideChat();
  messages = [];
  currentSessionId = null;
  showChatControls(defaultChatControls());
  try {
    localStorage.removeItem('sf_current_session');
    localStorage.removeItem('sf_draft_messages');
  } catch (_) {}
  rerenderAll();
  onChatChanged();
}

function isSideChatOpen() {
  return sideChat !== null;
}

function isChatOpen(sessionId) {
  return sessionId === currentSessionId || (isSideChatOpen() && sessionId === sideChat.mainSessionId);
}

function enterSideChat() {
  sideChat = createSideChat(messages, currentSessionId);
  messages = [];
  currentSessionId = null;
  $('sideBanner').hidden = false;
  rerenderAll();
}

function startSideChat(question, overrideModel, extra) {
  if (busy) return;
  if (!isSideChatOpen()) enterSideChat();
  if (question) send(question, overrideModel, extra);
}

function discardSideChat() {
  sideChat = null;
  $('sideBanner').hidden = true;
}

function leaveSideChat() {
  if (busy || !isSideChatOpen()) return;
  messages = sideChat.mainMessages;
  currentSessionId = sideChat.mainSessionId;
  discardSideChat();
  rerenderAll();
  onChatChanged();
}

function toggleSideChat() {
  if (isSideChatOpen()) leaveSideChat();
  else startSideChat('');
}

function submitSideChatCommand(question) {
  els.input.value = question;
  els.input.style.height = 'auto';
  if (!question) {
    startSideChat('');
    return;
  }
  const { overrideModel, extra } = takeComposerExtras(question);
  startSideChat(question, overrideModel, extra);
}

$('sideChatCloseBtn').onclick = leaveSideChat;

els.stopBtn.onclick = () => { if (abortController) abortController.abort(); };

els.modelList.addEventListener('change', () => {
  warmModel(currentModel());
  renderModelPicker();
  if (!messages.length) renderWelcome();
  setStatus('ready', els.modelList.value);
  checkModelToolCapability();
});

function modelHasOwnTools(model) {
  return !!model && appConfig.own_tools_models.some(pattern => globMatch(model.id, pattern));
}

async function checkModelToolCapability() {
  const model = currentModel();
  const ownTools = modelHasOwnTools(model);
  document.body.classList.toggle('own-tools', ownTools);
  const anyToolsEnabled = enabledMcpServers.size > 0 || enabledBuiltinTools.size > 0;
  if (!model || ownTools || !anyToolsEnabled) {
    els.capabilityWarning.hidden = true;
    return;
  }
  try {
    const r = await fetch(`/api/model-capabilities?provider=${encodeURIComponent(model.provider)}&model=${encodeURIComponent(model.id)}`);
    const { supports_tools, tool_tier, basic_tools } = await r.json();
    if (supports_tools === false) {
      els.capabilityWarning.textContent =
        `⚠️ ${model.id} likely doesn't support tool-calling - enabled tools may be silently ignored.`;
      els.capabilityWarning.hidden = false;
    } else if (tool_tier === 'basic') {
      const basic = new Set(basic_tools || []);
      const heldBack = [...enabledBuiltinTools].filter(name => !basic.has(name));
      const note = heldBack.length ? ` Held back for this model: ${heldBack.map(n => n.replace(/_/g, ' ')).join(', ')}.` : '';
      els.capabilityWarning.textContent =
        `ℹ️ ${model.id} is a smaller model, so it gets the basic tool set only (web search, knowledge, memory, notes, images, scheduled tasks; at most 8 tools). Models of 12B and up get everything.${note}`;
      els.capabilityWarning.hidden = false;
    } else {
      els.capabilityWarning.hidden = true;
    }
  } catch (_) {
    els.capabilityWarning.hidden = true;
  }
}

els.clearBtn.onclick  = clearChat;

els.form.addEventListener('submit', e => {
  e.preventDefault();
  const t = els.input.value.trim();
  if (!t || busy) return;
  const sideCommand = parseSideChatCommand(t);
  if (sideCommand) {
    submitSideChatCommand(sideCommand.question);
    return;
  }
  const { overrideModel, extra } = takeComposerExtras(t);
  send(t, overrideModel, extra);
});

els.input.addEventListener('keydown', e => {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
  const wantsSend = prefs.ctrlEnterSends ? (e.ctrlKey || e.metaKey) : !(e.ctrlKey || e.metaKey);
  if (!wantsSend) return;
  e.preventDefault();
  els.form.requestSubmit();
});

els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, COMPOSER_MAX_HEIGHT_PX) + 'px';
});

try { els.sysPrompt.value = localStorage.getItem('sf_sys') || ''; } catch (_) {}
els.sysPrompt.addEventListener('input', () => onChatControlsEdited());

async function openSession(id) {
  if (busy) return;
  try {
    const r = await fetch('/api/chat/sessions/' + id);
    if (!r.ok) return;
    const session = await r.json();
    currentSessionId = id;
    try { localStorage.setItem('sf_current_session', id); } catch (_) {}
    discardSideChat();
    messages = session.messages || [];
    showSessionControls(session);
    selectModel(session.endpoint_id, session.model);
    rerenderAll();
    closeSettings();
    onChatChanged();
  } catch (e) {
    printLine('error: could not open chat - ' + e.message, 'err');
  }
}
