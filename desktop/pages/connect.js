(function () {
  const bridge = window.starfireDesktop;
  const params = new URLSearchParams(window.location.search);
  const mode = params.get('mode') || 'first-run';
  const host = params.get('host') || '';
  const reason = params.get('reason') || '';

  const heading = document.getElementById('heading');
  const intro = document.getElementById('intro');
  const detail = document.getElementById('detail');
  const retryActions = document.getElementById('retry-actions');
  const form = document.getElementById('form');
  const address = document.getElementById('address');
  const insecure = document.getElementById('insecure');
  const connect = document.getElementById('connect');
  const cancel = document.getElementById('cancel');
  const status = document.getElementById('status');

  function say(text, kind) {
    status.textContent = text;
    status.className = kind ? `status ${kind}` : 'status';
  }

  function showForm() {
    retryActions.hidden = true;
    form.hidden = false;
    heading.textContent = 'Change address';
    intro.textContent = 'Enter the address of your starfire.';
    detail.hidden = true;
    cancel.hidden = false;
    address.focus();
  }

  function showUnreachable() {
    heading.textContent = host ? `Cannot reach ${host}` : 'Cannot reach starfire';
    intro.textContent = 'Check that you are online and that starfire is running, then try again.';
    detail.textContent = reason;
    detail.hidden = reason === '';
    retryActions.hidden = false;
    form.hidden = true;
  }

  async function fillCurrent() {
    const { settings } = await bridge.getSettings();
    address.value = settings.address;
    insecure.checked = settings.allowInsecure;
  }

  async function submit(event) {
    event.preventDefault();
    connect.disabled = true;
    say('Checking the address...', '');
    try {
      const result = await bridge.connect(address.value, insecure.checked);
      if (result.ok) say(`Connected to starfire ${result.version}.`, 'good');
      else say(result.error, 'bad');
    } catch (error) {
      say('Something went wrong while checking the address.', 'bad');
    } finally {
      connect.disabled = false;
    }
  }

  form.addEventListener('submit', submit);
  cancel.addEventListener('click', () => bridge.cancelConnect());
  document.getElementById('retry').addEventListener('click', () => bridge.retry());
  document.getElementById('show-form').addEventListener('click', showForm);

  if (mode === 'unreachable') {
    showUnreachable();
    fillCurrent();
  } else if (mode === 'change') {
    showForm();
    fillCurrent();
  } else {
    address.focus();
  }
})();
