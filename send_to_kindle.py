"""Send an edition through Gmail and persist its receipt in the repository."""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATE_PATH = ".state/deliveries.json"


class GitHubLedger:
    """GitHub's file SHA gives the delivery ledger optimistic write protection."""

    def __init__(self, repository: str, token: str, branch: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid GITHUB_REPOSITORY")
        self.url = f"https://api.github.com/repos/{repository}/contents/{STATE_PATH}"
        self.token = token
        self.branch = branch
        self.sha = None

    def request(self, method: str, payload: dict | None = None):
        url = self.url + ("?" + urlencode({"ref": self.branch}) if method == "GET" else "")
        request = Request(url, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        }, data=json.dumps(payload).encode() if payload is not None else None)
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def load(self) -> dict:
        try:
            result = self.request("GET")
        except HTTPError as error:
            if error.code == 404:
                return {}
            raise RuntimeError(f"Could not read delivery history (GitHub HTTP {error.code})") from None
        self.sha = result["sha"]
        history = json.loads(base64.b64decode(result["content"]))
        if not isinstance(history, dict):
            raise ValueError("Delivery history must be a JSON object")
        return history

    def save(self, history: dict):
        payload = {
            "message": "Record NHK Easy Kindle delivery [skip ci]", "branch": self.branch,
            "content": base64.b64encode((json.dumps(history, indent=2, sort_keys=True) + "\n").encode()).decode(),
        }
        if self.sha:
            payload["sha"] = self.sha
        result = self.request("PUT", payload)
        self.sha = result["content"]["sha"]


def read_edition(directory: Path) -> tuple[dict, Path]:
    edition = json.loads((directory / "edition.json").read_text(encoding="utf-8"))
    date.fromisoformat(edition["date"])
    name = edition["file"]
    if Path(name).name != name or not name.endswith(".epub"):
        raise ValueError("Edition filename must be a local EPUB")
    if not 1 <= len(edition["articles"]) <= 5:
        raise ValueError("Edition must contain 1 to 5 articles")
    path = directory / name
    if not 0 < path.stat().st_size <= 18 * 1024 * 1024:
        raise ValueError("EPUB is empty or too large for Gmail delivery")
    return edition, path


def create_message(edition: dict, path: Path, sender: str, recipient: str) -> EmailMessage:
    for address in (sender, recipient):
        if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", address):
            raise ValueError("Email settings must contain a single plain email address")
    domain = recipient.rsplit("@", 1)[1].lower()
    if domain not in {"kindle.com", "free.kindle.com"}:
        raise ValueError("KINDLE_EMAIL must be your Amazon @kindle.com delivery address")
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = edition["title"]
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    message.set_content(f"{edition['title']}\n\n全{len(edition['articles'])}記事を収録しています。\n")
    message.add_attachment(path.read_bytes(), maintype="application", subtype="epub+zip", filename=path.name)
    return message


def send_edition(edition: dict, path: Path, sender: str, password: str,
                 recipient: str, ledger: GitHubLedger) -> bool:
    history = ledger.load()
    key = edition["date"]
    if key in history:
        summary(f"Edition {key} was already sent. Skipping duplicate delivery.")
        return False
    message = create_message(edition, path, sender, recipient)
    # Never retry SMTP automatically: a lost acknowledgement could send a duplicate.
    client = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60)
    try:
        client.login(sender, "".join(password.split()))
        refused = client.send_message(message)
        if refused:
            raise RuntimeError("Gmail rejected the Kindle recipient")
    finally:
        client.close()
    history[key] = {
        "article_ids": [a["id"] for a in edition["articles"]],
        "epub_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "accepted_by_gmail_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        ledger.save(history)
    except Exception:
        raise RuntimeError(
            "Gmail accepted the email, but saving the delivery receipt failed. "
            "Check Gmail Sent and repair .state/deliveries.json before rerunning to avoid a duplicate."
        ) from None
    summary(f"Gmail accepted edition {key} ({len(edition['articles'])} articles). "
            "Amazon still needs to convert and deliver it to the Kindle.")
    return True


def summary(message: str):
    print(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(message + "\n\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    required = ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "KINDLE_EMAIL", "GH_TOKEN", "GITHUB_REPOSITORY"]
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit("Missing settings: " + ", ".join(missing) +
                         ". Add the three email secrets in repository Settings > Secrets and variables > Actions.")
    edition, path = read_edition(args.directory)
    ledger = GitHubLedger(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"],
                          os.environ.get("GITHUB_REF_NAME", "main"))
    try:
        send_edition(edition, path, os.environ["GMAIL_ADDRESS"].strip(), os.environ["GMAIL_APP_PASSWORD"],
                     os.environ["KINDLE_EMAIL"].strip(), ledger)
    except smtplib.SMTPAuthenticationError:
        raise SystemExit("Gmail login failed. Use a Google app password with 2-Step Verification enabled.") from None
    except (smtplib.SMTPException, OSError):
        raise SystemExit("Email transmission failed. Check Gmail Sent before retrying if acceptance is uncertain.") from None


if __name__ == "__main__":
    main()
