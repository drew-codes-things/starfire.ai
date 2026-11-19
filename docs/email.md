# Email

Settings → Email connects a mailbox via IMAP/SMTP with an **app password** (Gmail, Outlook,
iCloud, Fastmail, or any provider that issues one - no OAuth, no Google Cloud project setup).
Once connected you get a simple inbox: browse folders, read, reply, send, mark read, archive,
delete, plus three on-demand AI actions per message - Summarize, Check Urgency, and AI Draft
Reply. Enable "Read & send email" in Settings → Tools to let the model do the same during a
conversation.

Every action opens a live IMAP/SMTP connection - there's no background inbox sync or caching,
so listing a folder takes a moment each time rather than being instant from a local cache.
That's the deliberate tradeoff for not needing a database or a background sync service.

Mail runs on its own four threads. Each mail call has 60 seconds in total, connection and every
reply included, and is cut off after that. An account may have two mail calls running at once and
make `MAIL_REQUESTS_PER_MINUTE` (60) mail requests a minute, admins included; past either it gets
a 429 with `Retry-After`. Over IMAP a mail server may send at most 32 MB for one request, every
line counted with 256 bytes more for what it costs to keep, so a flood of short lines is stopped
before it fills memory; a message larger than 25 MB is read up to 25 MB and says so at its end.
Over SMTP one reply may be at most 64 KB and each of its lines at most 2 KB. Ordinary accounts can
only use mail servers on public addresses.
