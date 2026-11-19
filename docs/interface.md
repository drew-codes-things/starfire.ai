# Interface

The layout is the familiar chat-app one: a collapsible sidebar of chats on the left, a compact
model picker in the header, a centred conversation and a rounded message box. A new chat opens
on a centred greeting with the message box in the middle and four suggestion chips under it;
after the first message the box sits at the bottom. Everything is drawn from one set of design
tokens (colours, radii, spacing, shadows) in `static/style.css`, with a dark and a light set.

- **Sidebar**: new chat, Workspace and Notes shortcuts, search (`tag:name` searches by tag),
  pinned chats, folders (drag a chat onto one; "New chat here" from a folder's menu), and chats
  grouped Today, Yesterday, Previous 7 days, Previous 30 days and then by month. Each chat's
  `···` menu has pin, rename, clone, tags, move to folder, download (.json/.md), archive and
  delete (with a confirmation). On phones the sidebar is a slide-over drawer with a scrim.
- **Account menu** (bottom of the sidebar): Settings, Archived chats, Keyboard shortcuts and
  Log out, under your name and email.
- **Model picker**: searchable; **Arty Auto** first, then every model you may use, each listed
  once. The **Arty Auto** / **Choose a model** switch at the top shows which way the chat's model
  is picked. The star sets the default model. Presets are applied from Workspace → Models. A chat
  reopens on the model it was last used with.
- **Message box**: grows with its text up to a cap. `/` inserts a saved prompt, `#` attaches a
  Knowledge document, `@` sends the next message to a different model. **+** uploads files
  (text files are attached inline; PDFs are added to Knowledge and attached) and toggles tools
  - web search, deep research, image generation and the rest - with the active ones shown as
  chips. Files can also be dropped or pasted in. The microphone dictates; the waveform button
  is a hands-free voice conversation (browser speech recognition and speech synthesis). The
  send button turns into a stop button while a reply streams. If a message fails to send, its
  text is put back in the box.
- **Messages**: yours sit in a soft bubble on the right; replies are plain full-width text
  under the starfire mark and the model name, with a typing indicator until the first words
  arrive. Actions (copy, edit, read aloud, info, rate, continue, delete) appear on hover and
  are always visible on touch screens; regenerate, with its model chooser, is on the newest
  reply only. Deleting a message asks first.
- **Side chats**: type `/side` or `/btw` (with or without a question) to ask something on the
  side. The side chat sees the chat so far, nothing in it is saved, and the main chat is left
  as it was; "Back to chat" (or "Toggle side chat" in the command palette) returns to it.
- **Header buttons**: temporary chat (never saved to history), compare models, download the
  current chat as .json (re-importable), .md or .txt, and chat controls (system prompt plus
  temperature, top p, top k, max tokens, context length, repeat penalty and seed; Ollama takes
  all of them, OpenAI-compatible APIs and Anthropic take the ones they support). Controls are
  kept per chat: what you set on a new chat is your default, each saved chat keeps its own
  copy, and "Use my defaults" and "Save as my defaults" move settings between the two. Chats
  saved before this existed use your defaults.
- **Settings**: a tabbed dialog. General (chat behaviour, shortcuts, version), Appearance
  (System, Light or Dark theme; chat bubbles; wide chat), Account (who you are, change
  password), Chats and Usage; then Workspace (Models, Knowledge, Prompts, Tools), Personal
  (Memory, Notes, Automations, Email) and, for admins, Admin (Connections, Audio & Images,
  Hardware, Data, Accounts). Each tab loads its data when you open it.
- **Themes**: the stored theme is applied before the page paints, so there is no flash; System
  follows the operating system and switches live. The login and setup pages use the same theme.
- **Feedback**: copy, save and error messages show as short toasts. Confirmations use
  starfire's own dialog, and everything that removes something asks first. An offline banner
  appears when the server cannot be reached.
- **Approvals**: a shell command or file edit that is waiting for your answer is drawn again
  when the page loads, so a reload or a second device does not lose it.
- **Phones and tablets**: safe-area insets are respected, inputs are 16px so iOS does not zoom,
  menus open as bottom sheets, and every control is at least 44px tall.

## Keyboard shortcuts

`Ctrl+/` (or `Cmd+/`) shows this list in the app; it is also under the account menu.

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+O` | New chat |
| `Shift+Esc` | Focus the message box |
| `Esc` | Close the top dialog or menu, or stop the reply that is streaming |
| `Enter` / `Shift+Enter` | Send / new line (swapped to `Ctrl+Enter` / `Enter` in Settings → General) |
| `Ctrl+K` | Command palette |
| `Ctrl+Shift+S` | Toggle the sidebar |
| `Ctrl+Shift+C` | Copy the last reply |
| `Ctrl+Shift+;` | Copy the last code block |
| `Ctrl+/` | Show the shortcuts |

## Command palette

`Ctrl+K` / `Cmd+K` opens a fuzzy-filterable list of actions - new chat, export, open any
settings tab, compare models, toggle theme, keyboard shortcuts. Arrow keys to navigate, Enter
to run, Escape to close.
