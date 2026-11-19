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

## Set up starfire Desktop

You need a starfire server that is already running somewhere you can reach (see the main [README](../README.md#setup)),
and its address, for example `https://starfire.example.com`. The app is only a window onto that server: it does not
contain one.

### 1. Install

**Debian, Ubuntu and relatives (.deb, recommended on Linux)**

```bash
sudo apt install ./starfire_1.0.0_amd64.deb
```

Run it from the folder that holds the file. This installs starfire under `/opt/starfire`, a `starfire` command, a menu
entry (Network) and the icon. Start it from the menu or with `starfire`.

**Any Linux (AppImage)**

```bash
sudo apt install libfuse2t64     # only needed once, on Debian 13 and recent Ubuntu; older releases call it libfuse2
chmod +x starfire-1.0.0-x86_64.AppImage
./starfire-1.0.0-x86_64.AppImage
```

To get a menu entry, copy the file to `~/Applications/starfire.AppImage` and add
`~/.local/share/applications/starfire.desktop` with `Exec` set to that path, `Icon=starfire` and
`StartupWMClass=starfire`.

**Windows**

Run `starfire-Setup-1.0.0.exe`. It installs for your user only (no administrator rights), lets you pick the folder and
starts starfire when it finishes. The installer is not signed, so Windows SmartScreen warns first: choose **More info**,
then **Run anyway**.

The `.deb`, the AppImage and the installer are attached to the desktop release on the
[starfire.ai releases page](https://github.com/drew-codes-things/starfire.ai/releases), or you can build them yourself
(see [Build](#build)).

### 2. First start: tell it where your starfire is

The first time the app runs it shows **Welcome to starfire** and asks for **Your starfire address**.

1. Type the address of your server. Leaving out `https://` is fine, the app adds it. Include a port if your server uses
   one, for example `https://starfire.example.com:8443`.
2. Press **Connect**. The app asks the server for `/api/health` and only saves an address that answers like starfire.
   On success it says `Connected to starfire <version>` and opens the main window.
3. If the server is on your own computer or home network and has no https, for example `http://10.0.0.5:8080`, tick
   **This runs on my own computer or home network without https** first. The tick box is accepted only for `localhost`,
   `.local` names and private addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x, link-local). Any address on the public
   internet must be https.

The address is saved in the app's config file, so this question is asked once. Change it later in **Settings**, under
**Address**.

### 3. Sign in

The main window now shows your server's own sign-in page. Sign in with your starfire email and password. The window has
its own session, so it stays signed in across restarts until your server's session lifetime ends (30 days by default for
a starfire server).

**A brand-new server** has no accounts yet, so the first page is the setup page instead. The setup code on it is not
something the app can tell you: starfire prints a one-time code in its own log when it starts with no account, in a line
like `no account yet: open /setup.html and enter the setup code ...`. Read it where the server runs (the terminal for
`python server.py`, `docker compose logs` for Docker, `journalctl -u <service>` for a service), type it into the setup
page, then choose your name, email and a password of at least 10 characters. That first account is the admin. The code
changes every time the server restarts and stops existing once the account is made. Five wrong codes from one address lock
that address out of setup for 15 minutes.

### 4. Settings worth knowing

Open Settings from the tray icon menu or the **starfire** menu (press Alt if the menu bar is hidden). Everything is saved the moment you change it.

| Setting | What it does |
|---|---|
| Quick ask shortcut | Default **Ctrl+Shift+Space**. Opens a small always-on-top window; type a question and press Enter and a new chat is started in the main window. Press **Change**, then the new keys; Esc cancels. A shortcut another program already holds is refused and the old one keeps working. |
| Start with the PC | Off by default. Starts starfire hidden in the tray when you log in. |
| Notify when a reply finishes | On by default. Only fires when the starfire window is hidden, minimised or in the background. Click the notification to open the window. |
| Closing the window keeps starfire in the tray | On by default, so closing the window does not quit the app. Turn it off to quit when the window closes (do this on a desktop with no system tray). Use the tray menu's **Quit** to exit fully. |
| Address | Shows the server address; **Change** reopens the address screen. |
| Link to Arty Desktop | See [Link starfire Desktop with Arty Desktop](#5-link-starfire-desktop-with-arty-desktop-optional). |

Starting starfire again while it is running does not open a second copy: it brings the existing window forward.

### 5. Link starfire Desktop with Arty Desktop (optional)

The link lets the chat in starfire Desktop ask Arty Desktop, running on the same PC, to look at your screen. Nothing
reaches anything beyond this PC: the app only talks to `127.0.0.1`.

You need Arty Desktop 1.5.8 or newer on the same PC, started and running. Then:

1. In starfire Desktop open **Settings** and switch **Link to Arty Desktop** on. The light reads **Not paired** (or
   **Arty Desktop is not running** if Arty Desktop is not up yet, in which case start it).
2. In Arty Desktop open the tray menu, choose **Link starfire...** and make a code. It is 6 digits, works once and
   expires after two minutes. Five wrong tries use it up.
3. Type the 6 digits into the box that appeared under **Pair with Arty Desktop** (a space in the middle is fine) and press
   **Pair**. Nothing sends the code itself: both apps prove they know it and work out the shared token on their own.
4. The light turns **Connected** and the row says **Paired with Arty Desktop**. If it says computer control is off in
   Arty Desktop, turn on its computer control (the HANDS setting) or every step will be refused.
5. Optional check: press **Try it**. Arty Desktop shows an Allow card for a step that lists the open windows. Allow it and
   starfire shows what Arty answered.

To end the link, press **Unpair** (it forgets the pairing on both sides) or switch the link off. If you unlink inside
Arty Desktop, Unpair in starfire as well and pair again later.

### 6. "Look at my ..." and the Allow card

With the link **Connected**, an **admin** account can write things like "look at my discord", "look at my screen" or
"what is on my browser" in a chat opened in starfire Desktop. The chat then looks at your screen:

1. The chat shows **Looking at Discord on your PC...** and then **Waiting for you to allow it in Arty**.
2. Arty Desktop shows an **Allow card**. When it can find that app's window it names the window's real title and says only
   that window is captured by itself; otherwise it says plainly that the whole screen would be sent.
3. Nothing is captured until you press **Allow**. **Not now**, no answer within two minutes, or Stop, counts as no.
4. After Allow, the chat shows **Looked at Discord** with a small thumbnail (click it to enlarge) and the model answers
   from the picture. Use a model that can read images. The picture is part of that chat message and is not kept anywhere
   else on the server.

If something stops it, the chat says so on its own line and the model is told to say it could not look instead of
guessing: you said no, Arty Desktop is not running or the link is off, computer control is off, no window matches the
name, several windows match (ask again with the window's full title), the window changed after you were asked, or the
window is one that is never looked at. The tool is not offered in an ordinary browser, to non-admin accounts, or in
scheduled tasks. On Wayland, windows of apps that are not X11 apps may not be found by name, so the card can say the
whole screen would be sent.

### 7. Update

- The starfire web app updates with your server: when the server is updated, the app shows the new version the next time
  you open or reload it. Nothing to install.
- The desktop shell itself (the window, tray, shortcut and link) has no built-in updater. For a new release, download it and
  install it over the old one: `sudo apt install ./starfire_<new version>_amd64.deb`, replace the AppImage file, or run
  the new Windows installer. Your address, shortcut and pairing are kept.

### 8. Uninstall

- **Debian package:** `sudo apt remove starfire`.
- **AppImage:** delete the AppImage and the `~/.local/share/applications/starfire.desktop` entry you added.
- **Windows:** Settings, Apps, starfire, Uninstall (or run the uninstaller from the installation folder).

Your settings and the signed-in session stay behind on purpose, so a later reinstall picks up where you left off. To
remove them as well, delete the config folder: `~/.config/starfire` on Linux, `%APPDATA%\starfire` on Windows. If you
switched on Start with the PC, switch it off first or delete `~/.config/autostart/starfire-desktop.desktop` on Linux.
Deleting the config folder also forgets the Arty link on this PC; unlink in Arty Desktop too.

### 9. Troubleshooting

| What you see | What to do |
|---|---|
| `Could not reach <host>. Check the address and your connection.` | The address is wrong, the server is down or you are offline. Open the same address in a web browser; if that fails too the problem is the server or network. |
| `<host> answered, but it does not look like starfire.` | The address answers, but not as starfire: wrong port, a different service, or a proxy page in the way. Use the address you use for starfire in your browser. |
| `<host> did not answer in time.` | The server is slow or blocked by a firewall; try again, or check it is reachable from this PC. |
| `Only https is allowed outside your own network.` | A public address must start with `https://`. Fix the address or put https in front of the server. |
| `http is only for localhost and your own network. Tick the box to allow it.` | The address is a local `http` one: tick the box on the address screen. |
| **Cannot reach `<host>`** after it worked, with a reason line | The server is down, or the network changed. Press **Try again**, or **Change address**. |
| The reason line names a certificate error (for example `ERR_CERT_AUTHORITY_INVALID`), or Connect says it could not reach a `https` address on your home network | The app never overrides certificate errors, so the server must present a certificate this PC trusts and that matches the name you typed. A certificate for a different name or an address typed as a bare IP will not match. Use the server's real https name (a public one, or a name with a certificate from an authority you have installed on this PC), or, on a private network without https, use `http` with the tick box. |
| The sign-in page comes back after you change the address | A login only works on the host name it was made on, so sign in again after changing the address (for example from a LAN address to the public one). |
| Link light says **Arty Desktop is not running** | Start Arty Desktop on this PC and wait for it to finish starting. |
| `The pairing was not accepted...` or `Whatever answered did not prove it is Arty Desktop holding that code...` | The code expired (two minutes), was already used, was typed wrong five times or has a typo. Make a new code in Arty Desktop (**Link starfire...**) and type it again within two minutes. |
| `The code from Arty Desktop is 6 digits.` | Type only the six digits. |
| Light says **Arty Desktop did not prove itself** | Nothing was sent. Unpair in starfire, then pair again. If you did not unlink in Arty Desktop, something other than Arty Desktop may be answering on its port, so do not pair until you know what. |
| "Look at my ..." does nothing or the chat answers without looking | You need an admin account, starfire Desktop (not a browser), the link **Connected** and the request made in a chat window of the app. A look is also refused while Arty Desktop's computer control is off, while Arty Desktop is already waiting on another step, or for a minute after three refusals in a row. |
| The shortcut does nothing | Another program may hold it (starfire says so in Settings); choose a different one. On a Wayland desktop that does not pass global shortcuts through, bind a shortcut in your desktop's own settings to the command `starfire --quick-ask` (or `~/Applications/starfire.AppImage --quick-ask`). |
| No tray icon or the window does not come back after closing it | Some desktops (plain GNOME) have no tray. Switch off **Closing the window keeps starfire in the tray**. |
| Need to start over | Quit starfire and delete the config folder (see Uninstall). The next start shows the first-run screen again. |

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
  names the app, and nothing is captured unless you click Allow. When an app is named and Arty Desktop can find its
  window (X11, or X11 windows under Wayland), the card names that window's real title, that window is captured by
  itself and nothing else is sent; when several windows match, nothing is asked and the model is told to use the full
  title; otherwise the card says plainly that the whole screen would be sent. The chat shows each step (Looking at Discord on your
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

## Packaging notes

The Linux menu entry, the settings location and the Windows build are covered in [Set up starfire Desktop](#set-up-starfire-desktop).
`npm run deb` builds the Debian package into `release/` (the first build downloads electron-builder's packaging tool,
`fpm`, once). The config is `~/.config/starfire/config.json` (address, shortcut and the switches), written with owner-only
permissions; delete it to see the first-run screen again.

### Windows build

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
./build-app.sh        # runs the tests, then builds release/starfire-<version>-x86_64.AppImage and release/starfire_<version>_amd64.deb
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
