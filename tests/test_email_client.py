import imaplib
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import email_client
import routes
from api_key_manager import APIKeyManager
from email_store import EmailAccount, EmailAccountStore

PASSWORD = "app-password"
AUTH_FAILED = b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
ACCOUNT = EmailAccount(id="acc1", label="Home", email_address="me@home.example", imap_host="imap.home.example",
                       imap_port=993, smtp_host="", smtp_port=587, username="")


def header_block(sender, subject, date="Fri, 13 Nov 2026 09:00:00 +0000"):
    return f"From: {sender}\r\nTo: me@home.example\r\nSubject: {subject}\r\nDate: {date}\r\n\r\n".encode()


def plain_and_html():
    message = MIMEMultipart("mixed")
    message["From"] = "Ana <ana@example.test>"
    message["Subject"] = "Dinner"
    message["Message-ID"] = "<dinner-1@example.test>"
    body = MIMEMultipart("alternative")
    body.attach(MIMEText("Dinner at eight?", "plain"))
    body.attach(MIMEText("<p>Dinner at <b>eight</b>?</p>", "html"))
    message.attach(body)
    attachment = MIMEText("secret attachment text", "plain")
    attachment.add_header("Content-Disposition", "attachment", filename="notes.txt")
    message.attach(attachment)
    return message.as_bytes()


def html_only():
    message = MIMEText("<html><body><h1>Sale</h1><p>Half price today</p></body></html>", "html")
    message["From"] = "shop@example.test"
    message["Subject"] = "Re: Sale"
    return message.as_bytes()


class FakeMailbox:

    def __init__(self):
        self.folders = {
            "INBOX": {
                b"1": (b"\\Seen", header_block("bob@example.test", "Old news"), None),
                b"2": (b"", header_block("Ana <ana@example.test>", "=?utf-8?b?" + "Q2Fmw6k=" + "?="), plain_and_html()),
                b"3": (b"", header_block("shop@example.test", "Re: Sale"), html_only()),
            },
            "Archive": {},
        }
        self.calls = []
        self.logged_out = 0

    def imap(self, host, port):
        mailbox = self

        class Connection:
            selected = None

            def login(self, user, password):
                mailbox.calls.append(("login", host, port, user))
                if password != PASSWORD:
                    raise imaplib.IMAP4.error(AUTH_FAILED)
                return "OK", [b"LOGIN completed"]

            def logout(self):
                mailbox.logged_out += 1

            def list(self):
                return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Sent Mail"',
                              b"(\\HasNoChildren) NIL Archive", None]

            def select(self, folder, readonly=False):
                mailbox.calls.append(("select", folder, readonly))
                if folder not in mailbox.folders:
                    return "NO", [b"Mailbox doesn't exist"]
                self.selected = folder
                return "OK", [str(len(mailbox.folders[folder])).encode()]

            def uid(self, command, *args):
                messages = mailbox.folders[self.selected]
                mailbox.calls.append((command, *args))
                if command == "search":
                    if args[1] == "TEXT":
                        return "OK", [b"2 3"]
                    return "OK", [b" ".join(messages)]
                uid = args[0].encode() if isinstance(args[0], str) else args[0]
                if command == "fetch":
                    if uid not in messages:
                        return "OK", [None]
                    flags, headers, full = messages[uid]
                    part = headers if "HEADER.FIELDS" in args[1] else full
                    return "OK", [(b"%s (UID %s FLAGS (%s) BODY[] {%d}" % (uid, uid, flags, len(part)), part), b")"]
                if command == "copy":
                    if args[1] not in mailbox.folders:
                        return "NO", [b"[TRYCREATE] no such mailbox"]
                    mailbox.folders[args[1]][uid] = messages[uid]
                    return "OK", [b""]
                if command == "store":
                    return "OK", [b""]
                raise AssertionError(command)

            def expunge(self):
                mailbox.calls.append(("expunge",))
                return "OK", [b""]

        return Connection()


@pytest.fixture
def mailbox(monkeypatch):
    fake = FakeMailbox()
    monkeypatch.setattr(email_client.imaplib, "IMAP4_SSL", fake.imap)
    return fake


class FakeSmtp:

    def __init__(self):
        self.connections = []
        self.sent = []
        self.login_error = None
        self.send_error = None
        self.hangs_up_on_quit = False

    def factory(self, secure):
        smtp = self

        class Connection:

            def __init__(self, host, port):
                self.events = [("connect", "ssl" if secure else "plain", host, port)]
                smtp.connections.append(self.events)

            def starttls(self):
                self.events.append(("starttls",))

            def login(self, user, password):
                self.events.append(("login", user))
                if smtp.login_error:
                    raise smtp.login_error

            def send_message(self, message):
                if smtp.send_error:
                    raise smtp.send_error
                smtp.sent.append(message)

            def quit(self):
                self.events.append(("quit",))
                if smtp.hangs_up_on_quit:
                    raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

            def close(self):
                self.events.append(("close",))

        return Connection


@pytest.fixture
def smtp(monkeypatch):
    fake = FakeSmtp()
    monkeypatch.setattr(email_client.smtplib, "SMTP", fake.factory(secure=False))
    monkeypatch.setattr(email_client.smtplib, "SMTP_SSL", fake.factory(secure=True))
    return fake


def test_a_wrong_imap_password_is_reported_with_the_servers_reason(mailbox):
    with pytest.raises(imaplib.IMAP4.error, match="AUTHENTICATIONFAILED"):
        email_client.list_folders(ACCOUNT, "wrong")
    assert mailbox.calls == [("login", "imap.home.example", 993, "me@home.example")]


def test_folders_are_listed_whether_or_not_their_names_are_quoted(mailbox):
    assert email_client.list_folders(ACCOUNT, PASSWORD) == ["INBOX", "Sent Mail", "Archive"]
    assert mailbox.logged_out == 1


def test_the_inbox_lists_newest_first_with_decoded_subjects_and_unread_marks(mailbox):
    listed = email_client.list_messages(ACCOUNT, PASSWORD, limit=2)
    assert [(m["uid"], m["subject"], m["unread"]) for m in listed] == [("3", "Re: Sale", True), ("2", "Café", True)]
    assert listed[1]["from"] == "Ana <ana@example.test>"
    assert ("select", "INBOX", True) in mailbox.calls


def test_a_folder_that_does_not_exist_is_named_in_the_error(mailbox):
    with pytest.raises(RuntimeError, match="cannot open folder 'Receipts'"):
        email_client.list_messages(ACCOUNT, PASSWORD, folder="Receipts")
    assert mailbox.logged_out == 1


def test_reading_a_message_prefers_its_plain_text_skips_attachments_and_marks_it_read(mailbox):
    message = email_client.read_message(ACCOUNT, PASSWORD, "INBOX", "2")
    assert message["body"] == "Dinner at eight?"
    assert message["message_id"] == "<dinner-1@example.test>"
    assert ("store", "2", "+FLAGS", "\\Seen") in mailbox.calls


def test_reading_an_html_only_message_gives_its_text_without_tags(mailbox):
    message = email_client.read_message(ACCOUNT, PASSWORD, "INBOX", "3", mark_seen=False)
    assert message["body"] == "SaleHalf price today"
    assert not any(call[0] == "store" for call in mailbox.calls)


def test_reading_a_message_that_is_gone_says_which_one(mailbox):
    with pytest.raises(RuntimeError, match="message 9 not found in 'INBOX'"):
        email_client.read_message(ACCOUNT, PASSWORD, "INBOX", "9")


def test_search_quotes_the_words_safely(mailbox):
    found = email_client.search_messages(ACCOUNT, PASSWORD, 'say "hi"')
    assert [m["uid"] for m in found] == ["3", "2"]
    assert ("search", None, "TEXT", '"say \\"hi\\""') in mailbox.calls


def test_archiving_to_a_folder_that_does_not_exist_keeps_the_message(mailbox):
    with pytest.raises(RuntimeError, match="could not copy to 'Old mail' - does that folder exist?"):
        email_client.archive_message(ACCOUNT, PASSWORD, "INBOX", "1", archive_folder="Old mail")
    assert ("expunge",) not in mailbox.calls


def test_archiving_copies_then_removes_from_the_inbox(mailbox):
    email_client.archive_message(ACCOUNT, PASSWORD, "INBOX", "1")
    assert b"1" in mailbox.folders["Archive"]
    assert ("store", "1", "+FLAGS", "\\Deleted") in mailbox.calls
    assert mailbox.calls[-1] == ("expunge",)


def test_mail_goes_out_over_starttls_on_587_from_the_smtp_host_guessed_from_imap(smtp):
    email_client.send_message(ACCOUNT, PASSWORD, "ana@example.test", "Plans", "See you at eight.")
    [events] = smtp.connections
    assert events == [("connect", "plain", "smtp.home.example", 587), ("starttls",), ("login", "me@home.example"), ("quit",)]
    [sent] = smtp.sent
    assert (sent["To"], sent["From"], sent["Subject"]) == ("ana@example.test", "me@home.example", "Plans")
    assert sent.get_payload() == "See you at eight."


def test_mail_goes_out_over_ssl_on_465_without_starttls(smtp):
    account = EmailAccount(**{**ACCOUNT.__dict__, "smtp_host": "mail.home.example", "smtp_port": 465})
    email_client.send_message(account, PASSWORD, "ana@example.test", "Plans", "Hi")
    assert smtp.connections[0][:2] == [("connect", "ssl", "mail.home.example", 465), ("login", "me@home.example")]


def test_a_rejected_smtp_login_is_reported_and_the_connection_closed(smtp):
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    with pytest.raises(smtplib.SMTPAuthenticationError, match="Username and Password not accepted"):
        email_client.send_message(ACCOUNT, "wrong", "ana@example.test", "Plans", "Hi")
    assert smtp.connections[0][-1] == ("quit",)
    assert smtp.sent == []


def test_a_refused_recipient_is_reported(smtp):
    smtp.send_error = smtplib.SMTPRecipientsRefused({"nobody@example.test": (550, b"5.1.1 No such user")})
    with pytest.raises(smtplib.SMTPRecipientsRefused, match="No such user"):
        email_client.send_message(ACCOUNT, PASSWORD, "nobody@example.test", "Plans", "Hi")


def test_a_reply_goes_to_the_sender_threaded_and_with_one_re(mailbox, smtp):
    email_client.reply_message(ACCOUNT, PASSWORD, "INBOX", "2", "Eight works.")
    [sent] = smtp.sent
    assert (sent["To"], sent["Subject"]) == ("Ana <ana@example.test>", "Re: Dinner")
    assert sent["In-Reply-To"] == sent["References"] == "<dinner-1@example.test>"
    email_client.reply_message(ACCOUNT, PASSWORD, "INBOX", "3", "No thanks.")
    assert smtp.sent[1]["Subject"] == "Re: Sale"
    assert smtp.sent[1]["In-Reply-To"] is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "email_accounts", EmailAccountStore(str(tmp_path)))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(str(tmp_path)))
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def add_account(client, password):
    return client.post("/api/email/accounts", json={
        "label": "Home", "email_address": "me@home.example", "imap_host": "imap.home.example",
        "imap_port": 993, "smtp_host": "", "smtp_port": 587, "username": "", "password": password})


def test_adding_an_account_with_a_wrong_password_says_why_and_keeps_nothing(client, mailbox):
    reply = add_account(client, "wrong")
    assert reply.status_code == 502
    assert reply.json()["detail"].startswith("could not connect: ")
    assert "AUTHENTICATIONFAILED" in reply.json()["detail"]
    assert client.get("/api/email/accounts").json() == {"accounts": []}
    assert routes.api_keys.load() == {}


def test_a_send_the_server_refuses_reaches_the_page_with_its_reason(client, mailbox, smtp):
    account_id = add_account(client, PASSWORD).json()["id"]
    smtp.send_error = smtplib.SMTPRecipientsRefused({"nobody@example.test": (550, b"5.1.1 No such user")})
    reply = client.post(f"/api/email/{account_id}/send", json={"to": "nobody@example.test", "subject": "Hi", "body": "Hello"})
    assert reply.status_code == 502
    assert "No such user" in reply.json()["detail"]
    smtp.send_error = None
    assert client.post(f"/api/email/{account_id}/send", json={"to": "ana@example.test", "subject": "Hi", "body": "Hello"}).json() == {"ok": True}
