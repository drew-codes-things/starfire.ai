const path = require('node:path');
const { secureWebPreferences, STARFIRE_PARTITION, LOCAL_PARTITION } = require('./webPreferences');
const { createNavigationGuard, isSafeExternalUrl, guardWebContents } = require('./navigation');
const { SHOWN_EVENT } = require('./ipc');
const { deliverQuestion } = require('./quickask');

const MAIN_SIZE = { width: 1200, height: 800, minWidth: 480, minHeight: 480 };
const CONNECT_SIZE = { width: 520, height: 560 };
const SETTINGS_SIZE = { width: 520, height: 760 };
const QUICK_ASK_SIZE = { width: 620, height: 176 };
const QUICK_ASK_TOP_FRACTION = 0.2;
const BACKGROUND = '#19191c';
const ABORTED_LOAD_CODE = -3;
const SIGN_IN_HINT = 'Sign in to starfire, then ask again.';

function createWindowManager({ electron, controller, pagesDir, preloadPath, webPreloadPath, iconPath }) {
  const { BrowserWindow, Notification, screen, shell } = electron;
  let main = null;
  let connect = null;
  let settings = null;
  let quick = null;
  let quitting = false;
  let loadedAddress = '';

  function localWebPreferences() {
    return secureWebPreferences({ partition: LOCAL_PARTITION, preload: preloadPath });
  }

  function lockLocalWindow(window) {
    window.webContents.on('will-navigate', (event) => event.preventDefault());
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  }

  function openExternal(url) {
    if (isSafeExternalUrl(url)) shell.openExternal(url);
  }

  function pageUrlFile(name) {
    return path.join(pagesDir, name);
  }

  function loadStarfire() {
    const { address } = controller.get();
    loadedAddress = address;
    main.loadURL(`${address}/`);
  }

  function createMain() {
    main = new BrowserWindow({
      ...MAIN_SIZE,
      show: false,
      title: 'starfire',
      icon: iconPath,
      backgroundColor: BACKGROUND,
      autoHideMenuBar: true,
      webPreferences: secureWebPreferences({ partition: STARFIRE_PARTITION, preload: webPreloadPath, spellcheck: true }),
    });
    const guard = { decide: (url) => createNavigationGuard(controller.get().address).decide(url) };
    guardWebContents(main.webContents, guard, {
      openExternal,
      loadInside: (url) => main.loadURL(url),
    });
    main.webContents.on('did-fail-load', (event, code, description, url, isMainFrame) => {
      if (!isMainFrame || code === ABORTED_LOAD_CODE) return;
      main.hide();
      showConnect('unreachable', { host: new URL(controller.get().address).host, reason: description });
    });
    main.on('close', (event) => {
      if (quitting || !controller.get().closeToTray) return;
      event.preventDefault();
      main.hide();
    });
    main.on('closed', () => {
      main = null;
      if (!quitting && !controller.get().closeToTray) electron.app.quit();
    });
    loadStarfire();
  }

  function showMain() {
    if (!controller.get().address) {
      showConnect('first-run');
      return null;
    }
    if (!main) createMain();
    if (loadedAddress !== controller.get().address) loadStarfire();
    if (main.isMinimized()) main.restore();
    main.show();
    main.focus();
    return main;
  }

  function showConnect(mode, detail = {}) {
    if (connect) {
      connect.close();
    }
    connect = new BrowserWindow({
      ...CONNECT_SIZE,
      title: 'starfire',
      icon: iconPath,
      backgroundColor: BACKGROUND,
      autoHideMenuBar: true,
      resizable: false,
      minimizable: false,
      webPreferences: localWebPreferences(),
    });
    lockLocalWindow(connect);
    connect.removeMenu();
    const query = { mode };
    if (detail.host) query.host = detail.host;
    if (detail.reason) query.reason = detail.reason;
    connect.loadFile(pageUrlFile('connect.html'), { query });
    const own = connect;
    connect.on('closed', () => { if (connect === own) connect = null; });
  }

  function closeConnect() {
    if (connect) connect.close();
  }

  function showSettings() {
    if (settings) {
      settings.show();
      settings.focus();
      return;
    }
    settings = new BrowserWindow({
      ...SETTINGS_SIZE,
      title: 'starfire settings',
      icon: iconPath,
      backgroundColor: BACKGROUND,
      autoHideMenuBar: true,
      resizable: false,
      minimizable: false,
      webPreferences: localWebPreferences(),
    });
    lockLocalWindow(settings);
    settings.removeMenu();
    settings.loadFile(pageUrlFile('settings.html'));
    settings.on('closed', () => { settings = null; });
  }

  function quickAskPosition() {
    const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    const area = display.workArea;
    return {
      x: Math.round(area.x + (area.width - QUICK_ASK_SIZE.width) / 2),
      y: Math.round(area.y + area.height * QUICK_ASK_TOP_FRACTION),
    };
  }

  function createQuick() {
    quick = new BrowserWindow({
      ...QUICK_ASK_SIZE,
      show: false,
      frame: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      title: 'starfire quick ask',
      icon: iconPath,
      backgroundColor: BACKGROUND,
      webPreferences: localWebPreferences(),
    });
    lockLocalWindow(quick);
    quick.removeMenu();
    quick.loadFile(pageUrlFile('quick-ask.html'));
    quick.on('blur', () => { if (quick && quick.isVisible()) quick.hide(); });
    quick.on('closed', () => { quick = null; });
  }

  function showQuickAsk() {
    if (!controller.get().address) {
      showConnect('first-run');
      return;
    }
    if (!quick) createQuick();
    const { x, y } = quickAskPosition();
    quick.setPosition(x, y);
    quick.show();
    quick.focus();
    quick.webContents.send(SHOWN_EVENT);
  }

  function hideQuickAsk() {
    if (quick && quick.isVisible()) quick.hide();
  }

  function toggleQuickAsk() {
    if (quick && quick.isVisible()) {
      quick.hide();
      return;
    }
    showQuickAsk();
  }

  function warn(body) {
    if (Notification.isSupported()) new Notification({ title: 'starfire', body }).show();
  }

  async function submitQuickAsk(question) {
    hideQuickAsk();
    const window = showMain();
    if (!window) return { ok: false, error: 'Set up your starfire address first.' };
    const outcome = await deliverQuestion({
      question,
      run: (script) => window.webContents.executeJavaScript(script),
      wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    });
    if (outcome === 'sent') return { ok: true };
    warn(SIGN_IN_HINT);
    return { ok: false, error: SIGN_IN_HINT };
  }

  function retry() {
    closeConnect();
    if (!main) {
      showMain();
      return;
    }
    loadStarfire();
    main.show();
  }

  function markQuitting() {
    quitting = true;
  }

  function mainIsHidden() {
    return !main || !main.isVisible() || main.isMinimized();
  }

  function isMainContents(contents) {
    return Boolean(main) && contents === main.webContents;
  }

  function reloadMain() {
    if (main) main.webContents.reload();
  }

  function mainIsFocused() {
    return Boolean(main) && main.isFocused();
  }

  function allWindows() {
    return [main, connect, settings, quick].filter(Boolean);
  }

  return {
    showMain,
    showConnect,
    closeConnect,
    showSettings,
    showQuickAsk,
    hideQuickAsk,
    toggleQuickAsk,
    submitQuickAsk,
    retry,
    markQuitting,
    mainIsHidden,
    mainIsFocused,
    isMainContents,
    reloadMain,
    allWindows,
    isMainVisible: () => !mainIsHidden(),
  };
}

module.exports = { createWindowManager };
