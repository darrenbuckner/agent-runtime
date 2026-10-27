"""Email capability: Resend for sending, the inbound/ directory for
receiving (filled by the webhook worker), GitHub Issues as the review
gate.

Recipient is always config.email.to_address. The v1 gate re-parsed the
recipient out of the issue body, so anyone who could edit the issue
could redirect the email; here the body is display only.

An approved issue closes only when Resend returned 200. On failure the
error is posted as a comment and the issue stays open for the next
cycle, so a send that never went out is never marked done.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from .config import EmailConfig
from .errors import ApiError, ConfigError
from .github import GitHub

LABEL_DRAFT = ("email-draft", "1d76db", "Outbound email awaiting review")
LABEL_APPROVED = ("approved", "0e8a16", "Email approved to send")
INBOUND_DIR = "inbound"
OUTBOUND_DIR = "outbound"


@dataclass(frozen=True)
class SendOutcome:
    ok: bool
    detail: str


class Resend:
    def __init__(self, api_key: str | None = None, session: Any | None = None):
        self.api_key = api_key or os.environ.get("RESEND_API_KEY", "")
        if not self.api_key:
            raise ConfigError("RESEND_API_KEY is not set but email is enabled")
        self.http = session or requests.Session()

    def send(self, cfg: EmailConfig, subject: str, body: str) -> SendOutcome:
        payload: dict[str, Any] = {
            "from": f"{cfg.from_name} <{cfg.from_address}>" if cfg.from_name else cfg.from_address,
            "to": [cfg.to_address],
            "subject": subject,
            "text": body,
        }
        if cfg.reply_to:
            payload["reply_to"] = cfg.reply_to
        resp = self.http.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code == 200:
            return SendOutcome(True, f"Email sent to {cfg.to_address}: {subject}")
        return SendOutcome(False, f"Resend returned {resp.status_code}: {resp.text[:300]}")


# --- review gate --------------------------------------------------------------

_SUBJECT_RE = re.compile(r"^\*\*Subject:\*\*\s*(.*)$", re.MULTILINE)


def draft_issue_body(subject: str, body: str, to_address: str) -> str:
    return (
        f"**To:** {to_address}\n"
        f"**Subject:** {subject}\n\n---\n\n"
        f"{body}\n\n---\n\n"
        "*Add the `approved` label to send this email. Close the issue to discard.*"
    )


def parse_draft_issue(issue_body: str) -> tuple[str, str]:
    """(subject, body) from an issue created by draft_issue_body. Raises
    if the shape is not recognised; a hand-edited issue that no longer
    parses must not silently send an empty email."""
    m = _SUBJECT_RE.search(issue_body or "")
    if not m:
        raise ApiError("issue body has no **Subject:** line")
    parts = (issue_body or "").split("\n---\n")
    if len(parts) < 3:
        raise ApiError("issue body does not have the two --- separators")
    body = parts[1].strip()
    if not body:
        raise ApiError("issue body has an empty email section")
    return m.group(1).strip(), body


def create_review_issue(gh: GitHub, cfg: EmailConfig, subject: str, body: str) -> int:
    return gh.create_issue(
        title=f"Email: {subject[:80]}",
        body=draft_issue_body(subject, body, cfg.to_address),
        labels=[LABEL_DRAFT[0]],
    )


def send_approved(gh: GitHub, resend: Resend, cfg: EmailConfig) -> tuple[list[str], list[str]]:
    """Send every open issue labelled email-draft + approved. Returns
    (sent, failed) descriptions for the cycle log."""
    gh.ensure_label(*LABEL_DRAFT)
    gh.ensure_label(*LABEL_APPROVED)
    sent: list[str] = []
    failed: list[str] = []
    for issue in gh.list_open_issues([LABEL_DRAFT[0], LABEL_APPROVED[0]]):
        number = int(issue["number"])
        try:
            subject, body = parse_draft_issue(issue.get("body") or "")
        except ApiError as exc:
            gh.comment(number, f"Could not send: {exc}. Fix the issue body or close to discard.")
            failed.append(f"Issue #{number}: unparseable ({exc})")
            continue
        outcome = resend.send(cfg, subject, body)
        if outcome.ok:
            gh.comment(number, f"Sent.\n\n{outcome.detail}")
            gh.close(number)
            sent.append(f"Sent approved email #{number}: {subject[:50]}")
        else:
            gh.comment(number, f"Send failed, leaving open for retry next cycle.\n\n{outcome.detail}")
            failed.append(f"Issue #{number}: {outcome.detail}")
    return sent, failed
