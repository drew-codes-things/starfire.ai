'use strict';

const CallRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let callActive = false;
let callRecognition = null;

function callModeActive() { return callActive; }

const CALL_LABELS = { listening: 'listening…', thinking: 'thinking…', speaking: 'Arty is speaking' };

function setCallState(state, heard) {
  $('callOverlay').dataset.state = state;
  $('callState').textContent = CALL_LABELS[state];
  if (heard !== undefined) $('callHeard').textContent = heard;
}

function britishVoice() {
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  return voices.find(v => v.lang === 'en-GB' && /male|daniel|arthur|george/i.test(v.name))
    || voices.find(v => v.lang === 'en-GB') || null;
}

function listenForCallTurn() {
  if (!callActive || busy) return;
  setCallState('listening');
  try { callRecognition.start(); } catch (_) {  }
}

function stopCall() {
  callActive = false;
  $('callBtn').classList.remove('active');
  $('callOverlay').hidden = true;
  try { callRecognition.abort(); } catch (_) {}
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  stopServerSpeech();
}

function speakThenListen(text) {
  if (!window.speechSynthesis) { listenForCallTurn(); return; }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = britishVoice();
  if (voice) utterance.voice = voice;
  utterance.onstart = () => setCallState('speaking');
  utterance.onend = () => setTimeout(listenForCallTurn, 250);
  window.speechSynthesis.speak(utterance);
}

if (CallRecognition && window.speechSynthesis) {
  callRecognition = new CallRecognition();
  callRecognition.continuous = false;
  callRecognition.interimResults = false;
  callRecognition.lang = navigator.language || 'en-US';
  callRecognition.onresult = e => {
    const transcript = e.results[0][0].transcript.trim();
    if (!transcript || !callActive) return;
    setCallState('thinking', `“${transcript}”`);
    send(transcript);
  };
  callRecognition.onerror = e => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      stopCall();
      printLine('voice call needs microphone permission', 'err');
    }
  };
  callRecognition.onend = () => { if (callActive && !busy && !window.speechSynthesis.speaking) setTimeout(listenForCallTurn, 300); };
  $('callBtn').hidden = false;
  $('callBtn').onclick = () => {
    if (callActive) { stopCall(); return; }
    if (!currentModel()) return;
    callActive = true;
    $('callBtn').classList.add('active');
    $('callOverlay').hidden = false;
    setCallState('listening', '');
    listenForCallTurn();
  };
  $('callEnd').onclick = stopCall;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && callActive) stopCall(); });
}
