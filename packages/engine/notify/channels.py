"""Slack, email and webhook — SPEC §15.

Three thin renderers over one `Digest`. A channel never decides *whether* to send; that
is `digest.worth_sending`, in one place, so a new channel cannot get the rule wrong.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol

TIMEOUT_SECONDS = 15


class NotifyError(RuntimeError):
    pass


@dataclass
class Channel:
    """Where to send. Secrets are named, never stored (CLAUDE.md)."""

    kind: str
    url_env: str = ""
    """Slack webhook URL and generic webhooks: the URL is itself a credential."""

    url: str = ""
    """For a webhook with no secret in the URL."""

    to: list[str] = field(default_factory=list)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password_env: str = ""
    sender: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def endpoint(self) -> str:
        if self.url_env:
            value = os.environ.get(self.url_env, "")
            if not value:
                raise NotifyError(f"{self.url_env} is not set in this environment")
            return value
        if not self.url:
            raise NotifyError("this channel has neither url nor url_env")
        return self.url


class Sender(Protocol):
    kind: str

    def send(self, digest: Any, channel: Channel) -> str: ...


def _post(url: str, payload: dict[str, Any]) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return f"{response.status}"
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"{url} → {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise NotifyError(f"{url} → {exc.reason}") from exc


class SlackSender:
    kind = "slack"

    def send(self, digest: Any, channel: Channel) -> str:
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": digest.headline()[:150]},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{digest.target}|{digest.target}>"},
            },
        ]
        body = "\n".join(digest.lines())
        if body:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{body}```"}})
        links = " · ".join(
            f"<{url}|{label}>"
            for label, url in (("Report", digest.report_url), ("Board", digest.board_url))
            if url
        )
        if links:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": links}]})
        return _post(channel.endpoint(), {"text": digest.headline(), "blocks": blocks})


class WebhookSender:
    kind = "webhook"

    def send(self, digest: Any, channel: Channel) -> str:
        return _post(
            channel.endpoint(),
            {
                "project": digest.project,
                "target": digest.target,
                "runId": digest.run_id,
                "headline": digest.headline(),
                "new": [{"severity": s, "title": t} for s, t in digest.new],
                "regressed": [{"severity": s, "title": t} for s, t in digest.regressed],
                "fixed": digest.fixed,
                "stillOpen": digest.still_open,
                "reportUrl": digest.report_url,
                "boardUrl": digest.board_url,
            },
        )


class EmailSender:
    kind = "email"

    def send(self, digest: Any, channel: Channel) -> str:
        if not channel.to or not channel.smtp_host:
            raise NotifyError("email needs `to` and `smtp_host`")
        message = EmailMessage()
        message["Subject"] = digest.headline()
        message["From"] = channel.sender or channel.smtp_user or "bureau@localhost"
        message["To"] = ", ".join(channel.to)
        message.set_content(digest.text())
        try:
            with smtplib.SMTP(
                channel.smtp_host, channel.smtp_port, timeout=TIMEOUT_SECONDS
            ) as smtp:
                if channel.smtp_password_env:
                    password = os.environ.get(channel.smtp_password_env, "")
                    if not password:
                        raise NotifyError(f"{channel.smtp_password_env} is not set")
                    smtp.starttls()
                    smtp.login(channel.smtp_user, password)
                smtp.send_message(message)
        except OSError as exc:
            raise NotifyError(f"smtp {channel.smtp_host}: {exc}") from exc
        return "sent"


SENDERS: dict[str, Sender] = {s.kind: s for s in (SlackSender(), WebhookSender(), EmailSender())}


def send(digest: Any, channels: list[Channel]) -> list[tuple[str, str]]:
    """Fan out, and say what happened to each. Nothing goes out for a quiet run."""
    if not digest.worth_sending:
        return []
    out: list[tuple[str, str]] = []
    for channel in channels:
        sender = SENDERS.get(channel.kind)
        if sender is None:
            out.append((channel.kind, "unknown channel"))
            continue
        try:
            out.append((channel.kind, sender.send(digest, channel)))
        except NotifyError as exc:
            out.append((channel.kind, f"failed: {exc}"))
    return out
