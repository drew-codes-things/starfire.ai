'use strict';

const MIN_PASSWORD_CHARS = 10;
const form = document.getElementById('setupForm');
const errorBox = document.getElementById('setupError');
const submit = document.getElementById('setupSubmit');

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function problemWith(fields) {
  if (!fields.email.includes('@')) return 'Enter your email address.';
  if (fields.password.length < MIN_PASSWORD_CHARS) return `Use at least ${MIN_PASSWORD_CHARS} characters for the password.`;
  if (fields.password !== fields.confirm) return "The two passwords don't match.";
  return '';
}

async function readError(response) {
  try {
    const body = await response.json();
    return body.detail || `setup failed (${response.status})`;
  } catch {
    return `setup failed (${response.status})`;
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  errorBox.hidden = true;
  const fields = {
    name: form.name.value.trim(),
    email: form.email.value.trim(),
    password: form.password.value,
    confirm: form.confirm.value,
  };
  const problem = problemWith(fields);
  if (problem) {
    showError(problem);
    return;
  }
  submit.disabled = true;
  try {
    const response = await fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Starfire-Client': '1' },
      body: JSON.stringify(fields),
    });
    if (response.status === 409) {
      window.location.replace('/login.html');
      return;
    }
    if (!response.ok) {
      showError(await readError(response));
      return;
    }
    window.location.replace('/');
  } catch {
    showError("Couldn't reach starfire.ai - check your connection and try again.");
  } finally {
    submit.disabled = false;
  }
});
