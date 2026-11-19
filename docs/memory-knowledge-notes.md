# Memory, knowledge and notes

## Memory

Settings → Memory is a persistent fact store - "user prefers dark mode", "user's dog is named
Rex" - that survives across chats. Add facts manually there, or just type `remember: ...` in
the chat (handled instantly, without calling the model at all; the colon is required, and
temporary and side chats send it to the model like any other message). Enable "Remember & recall
facts" in Settings → Tools to let the model itself list, add, edit, delete, or search your
memory during a conversation. Search is keyword/token overlap by default - fast,
dependency-free, no setup - **optionally blended with semantic similarity** when a local Ollama
with an embedding model (`nomic-embed-text` by default) is reachable, so a paraphrase with no
shared words can still surface. No Ollama/embedding model pulled → falls back to exactly the
old keyword-only behavior, automatically, with no configuration needed either way.

## Knowledge (documents)

Settings → Knowledge lets you upload `.txt`, `.md`, or `.pdf` files, which are chunked and
indexed for the model to search. Enable "Search my documents" in Settings → Tools so the model
can pull in relevant passages while answering. Same keyword-search-by-default,
semantic-when-available behavior as Memory above.

## Notes

Settings → Notes is a to-do list / notes app - plain notes or checklists, with due dates,
repeat (daily/weekly/monthly/yearly), pinning, labels, and color. Due dates accept natural
language - "tomorrow", "next friday", "in 3 days", each optionally with "at 9am" / "at 14:30" -
as well as a plain `YYYY-MM-DD` or full ISO datetime; anything not recognized as one of those
phrases is assumed to already be an ISO date and passed through unchanged. Enable "Manage notes
& to-dos" in Settings → Tools to let the model add/update/delete/toggle items itself (using the
same natural-language dates).

**Templates** - save a note/checklist's shape (title, content or checklist items, type, label,
color, repeat) and instantiate it with one click instead of rebuilding a recurring structure by
hand. Five starter templates (Meeting notes, Daily to-do, Weekly review, Shopping list, Project
brainstorm) are seeded on first run.

Two things worth knowing:
- **Recurring due dates only advance while the Notes tab is open** - a lightweight timer
  checks for overdue repeating notes and pushes their due date forward one period at a time.
  The server never rewrites a due date on its own; only a browser tab open on the Notes page does.
- **No reminder popups, emails, or push notifications** - a proper reminder system is a large
  subsystem of its own (delivery channels, SSRF-guarded webhooks, a background poller, a dedup
  cache). starfire shows overdue/due-today notes with a highlighted badge in the list
  instead. If you want to be notified outside the app, that's not built.
