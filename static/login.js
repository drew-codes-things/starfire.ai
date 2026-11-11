'use strict';

const form = document.getElementById('loginForm');
const errorBox = document.getElementById('loginError');
const submit = document.getElementById('loginSubmit');

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

async function readError(response) {
  try {
    const body = await response.json();
    return body.detail || `login failed (${response.status})`;
  } catch {
    return `login failed (${response.status})`;
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  errorBox.hidden = true;
  const email = form.email.value.trim();
  const password = form.password.value;
  if (!email || !password) {
    showError('Enter your email and password.');
    return;
  }
  submit.disabled = true;
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Starfire-Client': '1' },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      showError(await readError(response));
      return;
    }
    window.location.replace(safeNextPath(window.location.search));
  } catch {
    showError("Couldn't reach starfire.ai - check your connection and try again.");
  } finally {
    submit.disabled = false;
  }
});
