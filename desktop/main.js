const electron = require('electron');
const path = require('node:path');
const { createSettingsController } = require('./src/controller');
const { loadSettings } = require('./src/settings');
const { applyAutostart } = require('./src/autostart');
const { acceleratorFromKeyEvent } = require('./src/shortcut');
const { registerIpc } = require('./src/ipc');
const { isLocalPageUrl, isPermissionAllowed } = require('./src/navigation');
const { isReplyFinished, shouldNotify, replyNotification } = require('./src/notify');
const { parseLaunchArgs } = require('./src/launch');
const { createWindowManager } = require('./src/windows');
const { createTray } = require('./src/tray');
const { STARFIRE_PARTITION, LOCAL_PARTITION } = require('./src/webPreferences');

const { app, Menu, Notification, Tray, nativeImage, globalShortcut, ipcMain, session } = electron;

const APP_ID = 'app.starfire.desktop';
const PAGES_DIR = path.join(__dirname, 'pages');
const PRELOAD_PATH = path.join(__dirname, 'preload.js');
const ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const TRAY_ICON_PATH = path.join(__dirname, 'assets', 'tray.png');
const CONNECTED_PAUSE_MS = 700;
const CHAT_STREAM_FILTER = { urls: ['*://*/api/chat_stream*'] };

let windows = null;
let controller = null;
let trayHandle = null;

function programPath() {
  return process.env.APPIMAGE || process.execPath;
}

function setAutostart(enabled) {
  applyAutostart({ enabled, platform: process.platform, execPath: programPath(), app });
}

function installStarfireSession() {
  const starfire = session.fromPartition(STARFIRE_PARTITION);
  starfire.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(isPermissionAllowed(permission, details.requestingUrl, controller.get().address));
  });
  starfire.setPermissionCheckHandler((contents, permission, requestingOrigin) => (
    isPermissionAllowed(permission, requestingOrigin, controller.get().address)
  ));
  starfire.webRequest.onCompleted(CHAT_STREAM_FILTER, (details) => {
    const settings = controller.get();
    if (!isReplyFinished(details, settings.address)) return;
    const visible = windows.isMainVisible();
    if (!shouldNotify({ enabled: settings.notifications, windowVisible: visible, windowFocused: windows.mainIsFocused() })) return;
    if (!Notification.isSupported()) return;
    const notification = new Notification(replyNotification());
    notification.on('click', () => windows.showMain());
    notification.show();
  });
}

function installLocalSession() {
  const local = session.fromPartition(LOCAL_PARTITION);
  local.setPermissionRequestHandler((contents, permission, callback) => callback(false));
  local.setPermissionCheckHandler(() => false);
  local.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => callback({ cancel: true }));
}

function isLocalSender(event) {
  return Boolean(event.senderFrame) && isLocalPageUrl(event.senderFrame.url, PAGES_DIR);
}

function buildMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'starfire',
      submenu: [
        { label: 'Quick ask', click: () => windows.showQuickAsk() },
        { label: 'Settings', click: () => windows.showSettings() },
        { type: 'separator' },
        { label: 'Quit', click: () => app.quit() },
      ],
    },
    { role: 'editMenu' },
    { role: 'viewMenu' },
  ]));
}

function handleLaunch(argv) {
  const launch = parseLaunchArgs(argv);
  if (launch.quickAsk) {
    windows.showQuickAsk();
    return;
  }
  windows.showMain();
}

function startApp() {
  app.setAppUserModelId(APP_ID);
  const settingsFile = path.join(app.getPath('userData'), 'config.json');
  const loaded = loadSettings(settingsFile);
  if (loaded.warning) console.error(loaded.warning);

  controller = createSettingsController({
    file: settingsFile,
    initial: loaded.settings,
    globalShortcut,
    onQuickAsk: () => windows.toggleQuickAsk(),
    setAutostart,
    onChange: () => {
      if (trayHandle) trayHandle.refresh();
    },
  });
  windows = createWindowManager({ electron, controller, pagesDir: PAGES_DIR, preloadPath: PRELOAD_PATH, iconPath: ICON_PATH });

  installStarfireSession();
  installLocalSession();
  registerIpc({
    ipcMain,
    controller,
    isLocalSender,
    describeKey: acceleratorFromKeyEvent,
    actions: {
      shortcutProblem: () => controller.shortcutProblem(),
      submitQuickAsk: (text) => windows.submitQuickAsk(text),
      hideQuickAsk: () => windows.hideQuickAsk(),
      openConnect: (mode) => windows.showConnect(mode),
      retry: () => windows.retry(),
      closeConnect: () => windows.closeConnect(),
      connected: () => setTimeout(() => {
        windows.closeConnect();
        windows.showMain();
      }, CONNECTED_PAUSE_MS),
    },
  });

  const registered = controller.start();
  if (!registered.ok) console.error(registered.error);
  if (loaded.settings.startWithSystem) {
    try {
      setAutostart(true);
    } catch (error) {
      console.error(`Could not refresh the start-with-the-PC entry: ${error.message}`);
    }
  }

  buildMenu();
  trayHandle = createTray({
    Tray,
    Menu,
    nativeImage,
    iconPath: TRAY_ICON_PATH,
    shortcutLabel: () => (controller.shortcutProblem() ? '' : controller.get().shortcut),
    actions: {
      open: () => windows.showMain(),
      quickAsk: () => windows.showQuickAsk(),
      settings: () => windows.showSettings(),
      quit: () => app.quit(),
    },
  });

  const launch = parseLaunchArgs(process.argv);
  if (launch.quickAsk) windows.showQuickAsk();
  else if (!launch.hidden || !controller.get().address) windows.showMain();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', (event, argv) => handleLaunch(argv));
  app.on('before-quit', () => windows && windows.markQuitting());
  app.on('window-all-closed', () => {});
  app.on('will-quit', () => globalShortcut.unregisterAll());
  app.whenReady().then(startApp).catch((error) => {
    console.error(`starfire could not start: ${error.stack || error.message}`);
    app.exit(1);
  });
}
