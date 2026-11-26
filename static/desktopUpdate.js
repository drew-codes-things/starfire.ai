'use strict';

startDesktopUpdateCheck({
  win: window,
  document,
  storage: { getItem: (key) => window.localStorage.getItem(key), setItem: (key, value) => window.localStorage.setItem(key, value) },
  timers: { setTimeout: (callback, ms) => window.setTimeout(callback, ms), setInterval: (callback, ms) => window.setInterval(callback, ms) },
  log: (line) => console.info(line),
  fetchLatest: async () => {
    const answer = await fetch('/api/desktop/latest');
    if (!answer.ok) throw new Error(`the server answered ${answer.status}`);
    return answer.json();
  },
});
