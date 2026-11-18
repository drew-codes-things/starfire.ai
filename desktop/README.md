<div align="center">

# starfire Desktop

**starfire.ai in its own window on your Linux or Windows PC, with a tray icon, a quick-ask shortcut and desktop notifications.**

[![Electron](https://img.shields.io/badge/electron-44-47848F?style=flat-square&logo=electron&logoColor=white)](https://www.electronjs.org/)
[![Linux](https://img.shields.io/badge/linux-AppImage-FCC624?style=flat-square&logo=linux&logoColor=black)](https://appimage.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](../LICENSE)

</div>

---

## Overview

starfire Desktop is a small Electron shell around your own starfire. It does not bundle a server and it holds no
address of its own: the first time it runs it asks where your starfire lives, checks that the address answers like
starfire (`GET /api/health`), and keeps it in its config. Everything you do in the window is the normal starfire web app,
so a new starfire release shows up in the app without updating it.

---

## Features

- **First run** - a clean screen asks for the address. The app calls `/api/health` and only saves an address that
  answers as starfire. Nothing is hard-coded.
- **Stays signed in** - the window uses its own persistent session, so a restart does not log you out (until your
  starfire's own session lifetime ends).
- **Tray icon** - Open, Quick ask, Settings and Quit. Closing the window keeps starfire in the tray (switch this off in
  Settings to quit on close).
- **Quick ask** - **Ctrl+Shift+Space** opens a small always-on-top window. Type a question and press Enter: starfire Desktop
  opens a new chat in the main window and sends it. The shortcut is shown and can be changed in Settings; an invalid or
  already used shortcut is refused and the old one keeps working.
- **Notifications** - when a reply finishes and the window is hidden, minimised or in the background, a desktop
  notification says so. Click it to open the window. Off in Settings if you prefer.
- **Start with the PC** - off by default. On Linux it writes `~/.config/autostart/starfire-desktop.desktop`; on Windows it
  uses the login item. It starts hidden in the tray.
- **Link to Arty Desktop** - a switch in Settings with a light that says Off, Not paired, Arty Desktop is not running,
  Arty Desktop did not prove itself, or Connected. Switched on, you pair once: Arty Desktop's tray menu has **Link
  starfire...**, which shows a 6 digit code (one use, two minutes); type it into Settings. After that starfire Desktop can
  ask Arty Desktop's computer control to take a step (Settings has a **Try it** button that asks for the list of open
  windows; it works only from a click in that window). Arty Desktop puts every step to you as its normal Allow / Not now
  card, showing the whole step, so starfire never gets round it, and a step is refused while the switch is off or
  Arty's HANDS is off. Everything goes to `127.0.0.1` only (the port is Arty Desktop's 8765, `artyPort` in the config
  file for a different one); the token is kept in `~/.config/starfire/config.json` (mode 600) and never reaches the
  app's pages. Unpair in Settings forgets it on both sides.
- **Look at my screen** - with the link connected, an admin of your starfire can say "look at my discord" (or "look at my
  screen", "what's on my browser") and the chat model calls the `look_at_screen` tool. The page asks starfire Desktop,
  which sends only a screenshot request (an optional app name) to Arty Desktop. Arty puts it to you as an Allow card that
  names the app, and nothing is captured unless you click Allow. When an app is named and Arty Desktop can find where its
  window is (X11, or X11 windows under Wayland), only that part of the screen is sent; otherwise the card says plainly
  that the whole screen would be sent. The chat shows each step (Looking at Discord on your
  PC..., Waiting for you to allow it in Arty, Looked at Discord with a small thumbnail you can click to enlarge) and a
  clear line when you said no, Arty is not running or no window matches; the model is told to say it could not look. The
  tool is never offered in a normal browser or to an ordinary account, and the picture is not kept on the server beyond
  that chat message.
- **Its own icon** - starfire's red logo on a red tile, so it never gets mixed up with Arty Desktop in the taskbar.

### Wayland

Global shortcuts need X11. The AppImage starts as an X11 client (through XWayland) like Arty Desktop does. The shortcut
registers in the headless checks below but has not been tried on a real Wayland desktop. If your desktop does not pass
it through, bind a shortcut in your desktop's own settings to `~/Applications/starfire.AppImage --quick-ask`: it asks the
running app to show the quick-ask window. `STARFIRE_WAYLAND=1` starts the app as a native Wayland client instead.

---

## Security

- `contextIsolation` on, `nodeIntegration` off, `sandbox` on and no remote module in every window. The starfire window
  has one narrow preload (`preload-web.js`): only the top frame of the configured starfire origin gets it, only while the
  Arty link is on and paired, and it exposes just `linkStatus()` and `lookAtScreen(app)`. The main process checks the
  sender again on every call. Nothing else in the page can reach the app.
- Navigation, redirects and new windows are limited to the configured starfire origin. Any other link opens in the
  system browser (web and mail links only), and only for the main frame right after a click or key press in the window
  (one opening per click); other schemes (`file:`, `javascript:`, `data:` and the like) are refused.
- The app's own pages (address, settings, quick ask) are local files with a strict Content Security Policy
  (`default-src 'none'`, scripts and styles from the file itself only, no network), in a separate session with no
  permissions and all web requests cancelled. Their bridge to the app is a handful of checked calls, and the app only
  answers calls that come from those pages.
- The address must be **https**. `http` is accepted only for `localhost` and local network addresses (192.168.x.x, 10.x.x.x,
  172.16-31.x.x, link-local, `.local`), and only with the tick box on the address screen.
- The Arty Desktop link talks to `127.0.0.1` over plain http from the app's main process only (the pages cannot reach it),
  sends no Origin header, follows no redirects and caps what it reads. Only the settings page can use it. Pairing uses
  SPAKE2 (RFC 3526 group 14): both sides prove they know the 6 digit code without sending it, so whatever answers on
  the port learns nothing it could try codes against, and the token is worked out on both sides rather than sent.
  Before the token is sent on any call, Arty Desktop must answer a fresh challenge with an HMAC keyed by the hash of the
  token it keeps from pairing (`GET /link/proof`); if it cannot, nothing is sent, the light says so and the pairing is
  kept until you Unpair.
- Only the starfire origin is given notification, clipboard-write and full-screen permission, and the microphone (never
  the camera).
- Certificate errors are never overridden.
- The quick-ask text is passed to the page as data, not built into script text.

---

## Install (Linux)

```bash
sudo apt install libfuse2t64   # FUSE, to run AppImages
chmod +x starfire-1.0.0-x86_64.AppImage
./starfire-1.0.0-x86_64.AppImage
```

Copy the AppImage to `~/Applications/starfire.AppImage` and add a `~/.local/share/applications/starfire.desktop`
(`Exec` = that path, `Icon=starfire`, `StartupWMClass=starfire`) to get a menu entry.

The config is `~/.config/starfire/config.json` (address, shortcut and the switches), written with owner-only permissions.
Delete it to see the first-run screen again.

### Windows

`npm run installer` builds an NSIS installer (`release/starfire-Setup-1.0.0.exe`, about 110 MB, per user, unsigned so
Windows SmartScreen will warn). It must be built on Windows. It was built in a Windows 10 VM with the same Electron and
electron-builder versions as Arty Desktop, the tests were run there (all pass, the three that read Unix permission
bits skip), and the installer was installed silently (`starfire-Setup-1.0.0.exe /S`), launched, driven through the
first-run screen to a local test server, and uninstalled: the program folder, both shortcuts and the uninstall entry go,
your settings in `%APPDATA%\starfire` stay. Start with the PC writes and removes the `Run` registry entry. Not tried on
Windows: the tray menu, the global shortcut (the test session had no interactive desktop, so Windows refused every
shortcut there), toast notifications, and the Arty Desktop link (Arty Desktop 1.5.5 has not been built for Windows).

---

## Build

```bash
cd desktop
npm ci
./build-app.sh        # runs the tests, then builds release/starfire-<version>-x86_64.AppImage
```

`npm test` runs the unit tests (Node 20 or newer, nothing else to install). `npm start` runs the app from source.

---

## Tests

```bash
cd desktop && npm test
```

Covers the address rules and health check, the navigation guard and permissions, settings, the shortcut parsing and
registration, autostart entries, notifications, quick-ask delivery, the settings controller, the IPC checks, and static
checks that every local page keeps the strict CSP and that no insecure Electron option is used.

The packaged AppImage was also launched headless (`--ozone-platform=headless`, no X server needed) against a local starfire
on `127.0.0.1` with a scratch data folder and driven over the Chrome DevTools port: first run, address checks, sign in, a
restart that stays signed in, quick ask sending a chat, the settings window and a reply notification while the window
was hidden.

---

## Next steps

- Try the Windows tray, shortcut and notifications on a signed-in Windows desktop, and the Arty Desktop link once Arty
  Desktop 1.5.5 has a Windows build.
- Chats kept in step live between starfire Desktop, the website and ArtyCode.
- Carry the quick-ask question through a sign-in when the window shows the login page.

## License

[Apache License 2.0](../LICENSE).
