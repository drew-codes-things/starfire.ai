(function () {
  const bridge = window.starfireDesktop;
  const form = document.getElementById('form');
  const question = document.getElementById('question');
  const status = document.getElementById('status');

  function reset() {
    question.value = '';
    status.textContent = '';
    question.focus();
  }

  async function send() {
    const text = question.value.trim();
    if (text === '') {
      status.textContent = 'Type a question first.';
      return;
    }
    status.textContent = 'Sending...';
    try {
      const result = await bridge.sendQuickAsk(text);
      if (!result.ok) status.textContent = result.error;
    } catch (error) {
      status.textContent = 'Could not send that.';
    }
  }

  form.addEventListener('submit', (event) => event.preventDefault());
  question.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      bridge.hideQuickAsk();
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
  bridge.onShown(reset);
  question.focus();
})();
