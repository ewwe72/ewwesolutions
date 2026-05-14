"""Pull invoice PDFs from Gmail into eval_set/ via IMAP.

Credentials are read from invoice_idp/.env:
    GMAIL_EMAIL   — your Gmail address
    GMAIL         — 16-char app password from myaccount.google.com -> Security -> App passwords

Search: Gmail X-GM-RAW with:
    has:attachment filename:pdf (faktura OR invoice OR receipt OR paragon OR rachunek)

Output filenames: {YYYY-MM-DD}_{sender_domain}_{original_stem}.pdf
Idempotent: re-running skips files already present (same name + same size).
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import os
import re
import sys
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
OUT_DIR = ROOT / "eval_set"

SEARCH_QUERY = (
    "has:attachment filename:pdf "
    "(faktura OR invoice OR receipt OR paragon OR rachunek)"
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def decode_str(s: str | None) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out: list[str] = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def find_all_mail(imap: imaplib.IMAP4_SSL) -> str | None:
    """Return the All Mail mailbox name regardless of Gmail UI language."""
    typ, data = imap.list()
    if typ != "OK" or not data:
        return None
    for raw in data:
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if "\\All" in line:
            m = re.search(r'"([^"]*)"\s*$', line)
            if m:
                return m.group(1)
    return None


def safe_filename(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s[:max_len] if s else "unknown"


def main() -> int:
    load_env(ENV_PATH)
    user = os.environ.get("GMAIL_EMAIL", "").strip()
    pwd = os.environ.get("GMAIL", "").strip().replace(" ", "")

    if not user:
        print("ERROR: set GMAIL_EMAIL=<your-address>@gmail.com in .env", file=sys.stderr)
        return 1
    if not pwd:
        print("ERROR: set GMAIL=<app-password> in .env", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output: {OUT_DIR}")
    print(f"Connecting to imap.gmail.com as {user}...")

    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        M.login(user, pwd)
    except imaplib.IMAP4.error as e:
        print(f"LOGIN FAILED: {e}", file=sys.stderr)
        print(
            "Hint: confirm 2FA is enabled and the app password is correct. "
            "App passwords: https://myaccount.google.com/apppasswords",
            file=sys.stderr,
        )
        return 1

    try:
        mailbox = find_all_mail(M) or "[Gmail]/All Mail"
        print(f"Selecting mailbox: {mailbox}")
        typ, _ = M.select(f'"{mailbox}"', readonly=True)
        if typ != "OK":
            print("Falling back to INBOX")
            M.select("INBOX", readonly=True)

        print(f"Search (X-GM-RAW): {SEARCH_QUERY}")
        typ, data = M.search(None, "X-GM-RAW", f'"{SEARCH_QUERY}"')
        if typ != "OK":
            print("SEARCH FAILED", file=sys.stderr)
            return 1

        ids = data[0].split() if data and data[0] else []
        print(f"Matched {len(ids)} messages\n")

        saved = 0
        skipped_dup = 0
        errors = 0

        for i, msg_id in enumerate(ids, 1):
            try:
                typ, msg_data = M.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    errors += 1
                    continue

                raw_msg = msg_data[0][1]
                if not isinstance(raw_msg, (bytes, bytearray)):
                    errors += 1
                    continue

                msg = email.message_from_bytes(raw_msg)

                try:
                    dt = parsedate_to_datetime(msg.get("Date", ""))
                    date_str = dt.strftime("%Y-%m-%d") if dt else "undated"
                except (TypeError, ValueError):
                    date_str = "undated"

                _, addr = parseaddr(msg.get("From", ""))
                sender_domain = addr.split("@")[-1] if "@" in addr else "unknown"
                sender = sender_domain.split(".")[0]

                for part in msg.walk():
                    ctype = (part.get_content_type() or "").lower()
                    orig_name = decode_str(part.get_filename() or "")
                    is_pdf = ctype == "application/pdf" or orig_name.lower().endswith(".pdf")
                    if not is_pdf:
                        continue

                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    base = safe_filename(Path(orig_name).stem) if orig_name else "invoice"
                    filename = f"{date_str}_{safe_filename(sender)}_{base}.pdf"
                    target = OUT_DIR / filename

                    if target.exists() and target.stat().st_size == len(payload):
                        skipped_dup += 1
                        continue

                    if target.exists():
                        h = hashlib.sha1(payload).hexdigest()[:8]
                        target = OUT_DIR / f"{date_str}_{safe_filename(sender)}_{base}_{h}.pdf"
                        if target.exists():
                            skipped_dup += 1
                            continue

                    target.write_bytes(payload)
                    saved += 1
                    print(f"  [{i}/{len(ids)}] {target.name} ({len(payload)//1024} KB)")
            except Exception as e:  # noqa: BLE001 — keep walking even on a bad message
                errors += 1
                print(f"  [{i}/{len(ids)}] error: {e}", file=sys.stderr)

        print(f"\nDone. Saved: {saved}  Skipped (already had): {skipped_dup}  Errors: {errors}")
        return 0
    finally:
        try:
            M.close()
        except imaplib.IMAP4.error:
            pass
        M.logout()


if __name__ == "__main__":
    sys.exit(main())
