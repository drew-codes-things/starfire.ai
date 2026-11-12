'use strict';

const SIDE_CHAT_COMMANDS = ['side', 'btw'];
const SIDE_CHAT_COMMAND_PATTERN = /^\/(side|btw)(?:\s+([\s\S]*))?$/i;

function parseSideChatCommand(text) {
  const match = SIDE_CHAT_COMMAND_PATTERN.exec(String(text ?? '').trim());
  if (!match) return null;
  return { question: (match[2] || '').trim() };
}

function sideChatCommandsMatching(query) {
  const typed = String(query ?? '').toLowerCase();
  return SIDE_CHAT_COMMANDS.filter(command => command.startsWith(typed));
}

function createSideChat(mainMessages, mainSessionId) {
  return { mainMessages: mainMessages.slice(), mainSessionId: mainSessionId || null };
}

function sideChatContext(state, sideMessages) {
  if (!state) return sideMessages;
  return [...state.mainMessages, ...sideMessages];
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { SIDE_CHAT_COMMANDS, parseSideChatCommand, sideChatCommandsMatching, createSideChat, sideChatContext };
}
