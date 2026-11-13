'use strict';

const ANSWER_PAUSE_MS = 1000;
const NEW_QUESTION = 'New question';
const ANSWER_WAITING_CLASS = 'answer-waiting';

function answerGuard(clock = () => Date.now()) {
  let seenAt = null;
  return {
    see() {
      if (seenAt === null) seenAt = clock();
    },
    isReady() {
      return seenAt !== null && clock() - seenAt >= ANSWER_PAUSE_MS;
    },
  };
}

function whenSeen(element, onSeen) {
  if (typeof IntersectionObserver !== 'function') {
    onSeen();
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    onSeen();
  });
  observer.observe(element);
}

function flashNewQuestion(card, buttons, guard) {
  const flash = document.createElement('span');
  flash.className = 'new-question';
  flash.setAttribute('role', 'status');
  flash.textContent = NEW_QUESTION;
  card.classList.add(ANSWER_WAITING_CLASS);
  card.append(flash);
  buttons.forEach((button) => button.setAttribute('aria-disabled', 'true'));
  whenSeen(card, () => {
    guard.see();
    setTimeout(() => {
      card.classList.remove(ANSWER_WAITING_CLASS);
      flash.remove();
      buttons.forEach((button) => button.removeAttribute('aria-disabled'));
    }, ANSWER_PAUSE_MS);
  });
  return card;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ANSWER_PAUSE_MS, NEW_QUESTION, answerGuard, flashNewQuestion };
}
