"""Gmail send/receive over SMTP and IMAP, using an app password.

Stdlib only. Threading relies on standard email headers (Message-ID /
References) so a reply lands as a reply in every mail client, and so this
module can find it again by searching for those headers rather than by
subject text, which the user might edit.
"""

from __future__ import annotations

import email
import imaplib
import logging
import smtplib
import time
from dataclasses import dataclass
from email.header import decode_header
from email.message import EmailMessage
from email.utils import make_msgid, parsedate_to_datetime
from typing import List, Optional

log = logging.getLogger(__name__)

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST = "imap.gmail.com"


class MailError(RuntimeError):
    pass


@dataclass
class Credentials:
    address: str
    app_password: str


def send(
    creds: Credentials,
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> str:
    """Send the proposal email. Returns the Message-ID to track replies by."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = creds.address
    msg["To"] = to
    message_id = make_msgid(domain="fantasyagent.local")
    msg["Message-ID"] = message_id
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(creds.address, creds.app_password)
            server.send_message(msg)
    except smtplib.SMTPException as exc:
        raise MailError(f"Failed to send proposal email: {exc}") from exc

    return message_id


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        chunk.decode(enc or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        for chunk, enc in parts
    )


def _plain_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


@dataclass
class Reply:
    sender: str
    body: str
    received_at: float


def fetch_replies(creds: Credentials, in_reply_to: str, timeout: int = 30) -> List[Reply]:
    """All messages in the thread that reference ``in_reply_to``, oldest first."""
    replies: List[Reply] = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, timeout=timeout) as imap:
            imap.login(creds.address, creds.app_password)
            imap.select("INBOX")
            # HEADER search on References covers clients that only set that
            # header (not In-Reply-To) when replying deep in a thread.
            status, data = imap.search(None, "HEADER", "References", in_reply_to)
            if status != "OK":
                raise MailError(f"IMAP search failed: {status}")
            ids = data[0].split()
            if not ids:
                status, data = imap.search(None, "HEADER", "In-Reply-To", in_reply_to)
                ids = data[0].split() if status == "OK" else []

            for msg_id in ids:
                status, msg_data = imap.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)
                try:
                    ts = parsedate_to_datetime(parsed.get("Date")).timestamp()
                except (TypeError, ValueError):
                    ts = time.time()
                replies.append(
                    Reply(
                        sender=_decode(parsed.get("From")),
                        body=_plain_text(parsed),
                        received_at=ts,
                    )
                )
    except imaplib.IMAP4.error as exc:
        raise MailError(f"Failed to read replies: {exc}") from exc

    replies.sort(key=lambda r: r.received_at)
    return replies
