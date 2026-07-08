"""Email delivery over Gmail SMTP SSL (same env vars as EdgeBot)."""
from __future__ import annotations

import logging
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jackpot_predictor.config.settings import (EMAIL_PASSWORD, EMAIL_RECEIVER,
                                               EMAIL_SENDER, jackpot_config)

log = logging.getLogger(__name__)


def send_email(subject: str, html_body: str,
               csv_attachment: str | None = None) -> bool:
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        log.warning("Email not configured (EMAIL_SENDER / EMAIL_PASSWORD) "
                    "— skipping")
        return False
    cfg = jackpot_config()["delivery"]["email"]

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if csv_attachment and os.path.exists(csv_attachment):
        part = MIMEBase("text", "csv")
        with open(csv_attachment, "rb") as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(csv_attachment)}"')
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"],
                              timeout=60) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        log.info("email sent to %s", EMAIL_RECEIVER)
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.error("email send failed: %s", e)
        return False
