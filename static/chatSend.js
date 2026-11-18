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
        showModelChoice(session);
        rerenderAll();
        onChatChanged();
        followRunningReply(session.running_reply);
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
  if (!model || isAutoModel(model)) return;
  fetch('/api/warm', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id }),
  }).catch(() => {});
}

function isAutoModel(model) {
  return isAutoChoice(model);
}

function currentModel() {
  const opt = els.modelList.selectedOptions[0];
  if (!opt) return null;
  if (isAutoChoice({ endpoint_id: opt.dataset.endpointId, id: opt.value })) return autoModelEntry();
  return models.find(m => m.endpoint_id === opt.dataset.endpointId && m.id === opt.value) || null;
}

function everydayModel() {
  const pick = autoEverydayModel;
  return pick ? models.find(m => m.endpoint_id === pick.endpoint_id && m.id === pick.model) || null : null;
}

function concreteModel() {
  const model = currentModel();
  return isAutoModel(model) ? everydayModel() : model;
}

function modelOption(m) {
  const opt              = document.createElement('option');
  opt.value              = m.id;
  opt.dataset.endpointId = m.endpoint_id;
  if (isAutoModel(m)) {
    opt.textContent = AUTO_MODEL_NAME + ' - ' + AUTO_MODEL_HINT;
    return opt;
  }
  const label            = window.providerLabel ? window.providerLabel(m.provider) : m.provider;
  opt.textContent        = m.id + (m.size ? ' (' + (m.size / 1073741824).toFixed(1) + ' GB)' : '') + ' - ' + label;
  return opt;
}

async function loadAccountModelChoice() {
  try {
    return await apiJson('/api/model-choice');
  } catch (e) {
    printLine('could not load your model choice, so Auto is used: ' + e.message, 'err');
    return AUTO_CHOICE;
  }
}

async function loadModels() {
  setStatus('loading', 'connecting...');
  try {
    const r                       = await fetch('/api/models');
    const { models: list = [], auto_everyday: everyday = null } = await r.json();
    const shownBefore = currentModel();
    models = list;
    autoEverydayModel = everyday;
    accountModelChoice = await loadAccountModelChoice();
    try { localStorage.removeItem('sf_default_model'); } catch (_) {}

    els.modelList.innerHTML = '';
    if (models.length) els.modelList.appendChild(modelOption(autoModelEntry()));
    for (const m of models) els.modelList.appendChild(modelOption(m));

    showModelChoice(shownBefore ? choiceOf(shownBefore) : null);
    renderModelPicker();
    if (!messages.length) renderWelcome();

    if (!models.length) {
      setStatus('error', 'no models configured');
      els.input.disabled   = true;
      els.sendBtn.disabled = true;
      return;
    }

    setStatus('ready', modelPickerText(currentModel()));
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

function assistantMessage(text, model, readUntrusted, record) {
  const message = { role: 'assistant', content: text, model: model.id, created: new Date().toISOString(), ...record };
  if (readUntrusted) message.untrustedRead = true;
  return message;
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

  const startsNewChat = !currentSessionId && !isTemporaryChat();
  await persistSession();
  const sessionId = isTemporaryChat() ? '' : currentSessionId || '';
  const aiIdx = messages.length;
  const desktopScreen = await desktopScreenOffered();

  await streamReply({
    model, aiIdx, startsNewChat,
    open: signal => fetch('/api/chat_stream', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
      body:    JSON.stringify({
        endpoint_id: model.endpoint_id,
        model:       model.id,
        messages:    apiMessages(sideChatContext(sideChat, messages)),
        system:      els.sysPrompt.value.trim() || undefined,
        options:     chatOptions(),
        enabled_mcp_servers: modelHasOwnTools(model) ? [] : [...enabledMcpServers],
        enabled_builtin_tools: modelHasOwnTools(model) ? [] : [...enabledBuiltinTools],
        earlier_untrusted_content: sideChatContext(sideChat, messages).some(m => m.untrustedRead === true),
        ...(desktopScreen ? { desktop_screen: true } : {}),
        memory_commands: !isTemporaryChat(),
        ...(sessionId ? { session_id: sessionId, reply_index: aiIdx } : {}),
      }),
    }),
    giveBack: () => {
      messages.splice(userIdx, 1);
      rerenderAll();
      if (sentFromComposer && !els.input.value) {
        els.input.value = text;
        els.input.style.height = Math.min(els.input.scrollHeight, COMPOSER_MAX_HEIGHT_PX) + 'px';
      }
      if (sessionId) persistSession();
    },
  });
}

function followRunningReply(running) {
  if (busy || !running || running.reply_index !== messages.length) return;
  busy = true;
  els.sendBtn.disabled = true;
  els.input.disabled   = true;
  els.stopBtn.hidden    = false;
  activeReplyId = running.id;
  streamReply({
    model: currentModel() || autoModelEntry(), aiIdx: running.reply_index, startsNewChat: running.reply_index === 1,
    open: signal => fetch(`/api/chat/replies/${encodeURIComponent(running.id)}/events`, { signal }),
    giveBack: () => printLine('this reply is no longer kept on the server; ask again', 'err'),
  });
}

async function readReplyFrames(reader, onEvent) {
  const dec = new TextDecoder();
  let buf = '';
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
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch (_) {}
    }
  }
}

function showToolResult(toolResult) {
  const toolName = toolResult.name;
  if (toolName === 'edit_file') {
    renderDiffCard(toolResult.result);
  } else if (toolName === 'generate_image' || toolName === 'generate_audio' || toolName === 'generate_document' || toolName === 'find_image') {
    renderGeneratedFileCard(toolName, toolResult.result);
  } else if (toolName === 'generate_video') {
    renderGeneratedVideoCard(toolResult.result);
  } else if (toolName === 'run_shell') {
    renderShellResultCard(toolResult.result);
  } else {
    const outcome = toolOutcomeLine(toolName, toolResult.result);
    printLine(outcome.text, outcome.cls);
  }
}

function pickedModel(choice) {
  return models.find(m => m.endpoint_id === choice.endpoint_id && m.id === choice.model)
    || { id: choice.model, endpoint_id: choice.endpoint_id, provider: '' };
}

async function streamReply({ model, aiIdx, startsNewChat, open, giveBack }) {
  abortController = new AbortController();
  const typing = showTypingIndicator(isAutoModel(model) ? AUTO_MODEL_NAME : model.id);

  let replyModel = model;
  let autoRole = null;
  let aiLine = null;
  let aiActions = null;
  let aiRow = null;
  let renderer = null;
  let gotToken = false;
  let replyStarted = false;
  stopServerSpeech();
  let speaker = null;
  activeSpeaker = null;
  const startSpeaker = chosen => {
    if ((callModeActive() || prefs.autoSpeak) && modelHasServerVoice(chosen)) {
      speaker = createServerSpeaker(chosen, () => { if (callModeActive()) setCallState('speaking'); });
    }
    activeSpeaker = speaker;
  };
  if (!isAutoModel(model)) startSpeaker(model);
  const replyLabel = () => autoPickLabel(replyModel.id, autoRole);

  setStatus('loading', 'waiting...');
  const replyMeter = createReplyMeter({ countsSteps: !modelHasOwnTools(model) });
  liveChatStats.follow(replyMeter);
  liveChatStats.redraw();
  let readUntrusted = false;
  let streamError = null;

  const onEvent = obj => {
    if (obj.reply) {
      activeReplyId = obj.reply.id;
      replyStarted = true;
    } else if (obj.model_choice) {
      replyModel = pickedModel(obj.model_choice);
      autoRole = obj.model_choice.role || null;
      replyMeter.countsSteps = !modelHasOwnTools(replyModel);
      if (!speaker) startSpeaker(replyModel);
      setStatus('loading', replyLabel());
    } else if (obj.delta) {
      replyStarted = true;
      if (!gotToken) { gotToken = true; setStatus('ready', replyLabel()); }
      if (!aiLine) {
        typing.remove();
        const created = appendMessageRow('ai', aiIdx, replyLabel());
        aiLine = created.content; aiActions = created.actions; aiRow = created.row;
        aiRow.classList.add('fresh');
        aiLine.classList.add('streaming');
        renderer = createStreamRenderer(aiLine, { render: markdownRender });
      }
      renderer.push(obj.delta);
      if (speaker) speaker.push(obj.delta);
      els.output.scrollTop = els.output.scrollHeight;
      noteText(replyMeter, obj.delta.length, performance.now());
      liveChatStats.follow(replyMeter);
    } else if (obj.tool_start) {
      noteStep(replyMeter);
      liveChatStats.follow(replyMeter);
      printLine('» calling ' + obj.tool_start.replace(/^mcp__[^_]+__/, '') + '…', 'info');
    } else if (obj.tool_result) {
      showToolResult(obj.tool_result);
    } else if (obj.shell_pending) {
      renderShellApprovalCard(obj.shell_pending);
    } else if (obj.screen_request) {
      startScreenLook(obj.screen_request);
    } else if (obj.usage) {
      noteRoundUsage(replyMeter, obj.usage);
      liveChatStats.follow(replyMeter);
    } else if (obj.untrusted_read) {
      readUntrusted = true;
    } else if (obj.error) {
      streamError = obj.error;
    }
  };
  const replyRecordNow = () => ({ ...replyRecord(replyMeter), ...(autoRole ? { auto: autoRole } : {}) });

  try {
    const r = await open(abortController.signal);
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'server error ' + r.status);
    }
    await readReplyFrames(r.body.getReader(), onEvent);
    if (leavingPage) return;

    if (streamError) throw Object.assign(new Error(streamError), { fromServer: true });

    if (aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      decorateCodeBlocks(aiLine, renderer.text());
      showInlineMedia(aiLine);
    }
    setStatus('ready', replyLabel());
    const fullText = renderer ? renderer.text() : '';
    if (fullText) {
      messages[aiIdx] = assistantMessage(fullText, replyModel, readUntrusted, replyRecordNow());
      if (aiActions) addActionButtons('ai', aiRow, aiActions, aiIdx);
      if (speaker) speaker.finish().then(() => { if (callModeActive() && activeSpeaker === speaker) setTimeout(listenForCallTurn, 250); });
      else if (callModeActive()) speakThenListen(fullText);
      else if (prefs.autoSpeak) speakText(fullText);
      if (prefs.followUps && aiRow && !callModeActive()) showFollowUps(aiRow, replyModel);
    }
    await persistSession();
    if (startsNewChat && fullText && currentSessionId) describeNewSession(currentSessionId, replyModel);
    if (aiLine && fullText) verifyCodeBlocks(aiLine, aiIdx, replyModel);
  } catch (e) {
    if (leavingPage) return;
    const aborted = e.name === 'AbortError';
    if (speaker && aborted) speaker.stop();
    if (!aborted && !e.fromServer && replyStarted && activeReplyId) {
      rerenderAll();
      setStatus('error', 'connection lost');
      printLine('the connection dropped, but the reply is still being written: open this chat again to see it', 'info');
      return;
    }
    if (gotToken && aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      decorateCodeBlocks(aiLine, renderer.text());
      showInlineMedia(aiLine);
      const fullText = renderer.text();
      if (fullText) {
        messages[aiIdx] = assistantMessage(fullText, replyModel, readUntrusted, replyRecordNow());
        if (aiActions) addActionButtons('ai', aiRow, aiActions, aiIdx);
      }
    } else if (!aborted) {
      giveBack();
    }
    if (aborted) {
      setStatus('ready', replyLabel());
      printLine('- stopped -', 'info');
      persistSession();
    } else {
      setStatus('error', 'error');
      printLine('error: ' + e.message, 'err');
    }
    if (callModeActive()) setTimeout(() => speakThenListen("Sorry, that didn't work. Try again."), 0);
  } finally {
    typing.remove();
    liveChatStats.stop();
    busy                   = false;
    abortController        = null;
    activeReplyId          = null;
    els.input.disabled     = false;
    els.sendBtn.disabled   = false;
    els.stopBtn.hidden      = true;
    els.input.focus();
  }
}

const CONTINUE_INSTRUCTION = 'Continue your previous reply exactly where it stopped. Do not repeat anything or add a preamble.';
const CHARS_PER_TOKEN_ESTIMATE = 4;
const COMPOSER_MAX_HEIGHT_PX = 240;

function modelThatWrote(message) {
  return models.find(m => m.id === message.model) || concreteModel();
}

async function continueResponse(idx) {
  const target = messages[idx];
  const shown = currentModel();
  const model = isAutoModel(shown) && target ? modelThatWrote(target) : shown;
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
  const continueMeter = createReplyMeter({ countsSteps: false });
  liveChatStats.follow(continueMeter);
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
      if (obj.usage) noteRoundUsage(continueMeter, obj.usage);
      if (!obj.delta) return;
      noteText(continueMeter, obj.delta.length, performance.now());
      liveChatStats.follow(continueMeter);
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
      const { usage } = replyRecord(continueMeter);
      if (usage) target.usage = usage;
    }
    liveChatStats.stop();
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
  showModelChoice(null);
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

function stopReplyOnServer(replyId) {
  fetch(`/api/chat/replies/${encodeURIComponent(replyId)}/stop`, { method: 'POST' })
    .catch(e => printLine('could not stop the reply on the server: ' + e.message, 'err'));
}

els.stopBtn.onclick = () => {
  if (activeReplyId) stopReplyOnServer(activeReplyId);
  if (abortController) abortController.abort();
};

els.modelList.addEventListener('change', () => {
  warmModel(currentModel());
  renderModelPicker();
  if (!messages.length) renderWelcome();
  setStatus('ready', modelPickerText(currentModel()));
  checkModelToolCapability();
});

function modelHasOwnTools(model) {
  return !!model && model.own_tools === true;
}

async function checkModelToolCapability() {
  const model = currentModel();
  const ownTools = modelHasOwnTools(model);
  document.body.classList.toggle('own-tools', ownTools);
  const anyToolsEnabled = enabledMcpServers.size > 0 || enabledBuiltinTools.size > 0;
  if (!model || ownTools || isAutoModel(model) || !anyToolsEnabled) {
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
    showModelChoice(session);
    rerenderAll();
    closeSettings();
    onChatChanged();
    followRunningReply(session.running_reply);
  } catch (e) {
    printLine('error: could not open chat - ' + e.message, 'err');
  }
}
