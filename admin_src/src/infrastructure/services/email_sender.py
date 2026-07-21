import asyncio
import os
import re
import smtplib
from email.message import EmailMessage

import httpx
from loguru import logger

from src.application.common.email_sender import EmailSender
from src.core.config import AppConfig
from src.core.constants import EMAIL_VERIFICATION_SUBJECT
from src.core.exceptions import EmailDeliveryError
from src.infrastructure.services.email_settings import load_email_settings
from src.infrastructure.services.email_template_config import fill, load_email_template

# На этом хостинге исходящие SMTP-порты (25/465/587) заблокированы провайдером,
# поэтому письма уходят через HTTP API Brevo (порт 443, всегда открыт).
# Если EMAIL_BREVO_API_KEY задан — используем Brevo; иначе откатываемся на SMTP.
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

# Русские тексты письма с кодом подтверждения (без хардкода бренда — он резолвится).
RU_VERIFICATION_SUBJECT = "Код подтверждения"


def _brevo_api_key() -> str:
    return (os.environ.get("EMAIL_BREVO_API_KEY") or "").strip()


def _logo_src() -> str:
    """Абсолютный URL логотипа сервиса для письма (или '' — если логотип не задан).

    Берём тот же логотип, что и кабинет (assets/branding.json → /api/appearance/logo),
    и делаем ссылку абсолютной по WEB_CABINET_URL — в письме картинка грузится извне.
    """
    try:
        from src.web.endpoints.public.appearance import load_branding, logo_url

        rel = logo_url(load_branding().get("logo_file"))
        if not rel:
            return ""
        base = (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")
        if not base:
            origins = (os.environ.get("APP_ORIGINS") or "").strip()
            base = origins.split(",")[0].strip().rstrip("/") if origins else ""
        return f"{base}{rel}" if base else ""
    except Exception:
        return ""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _text_to_html(body: str) -> str:
    """Простейшая обёртка plain-text → HTML."""
    safe = _escape(body).replace("\n", "<br>")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.6;color:#1a1a1a;max-width:560px;margin:0 auto;">'
        f"{safe}"
        "</div>"
    )


def _render_verification(body: str, from_name: str) -> tuple[str, str, str]:
    """
    Письмо с кодом приходит из use-case на английском. Здесь подменяем тему/тело
    на русские (тексты редактируются в админке — см. email_template_config) и
    рисуем аккуратный HTML с кодом в рамке. Возвращает (subject, text, html).
    """
    code_match = re.search(r"\b(\d{4,8})\b", body)
    code = code_match.group(1) if code_match else ""
    minutes_match = re.search(r"(\d+)\s*minutes", body)
    minutes = minutes_match.group(1) if minutes_match else "15"

    # Бренд письма: имя отправителя из настроек почты, иначе — бренд установки
    # (из branding.json / имени бота), без хардкода конкретного бренда.
    if from_name:
        brand = from_name
    else:
        try:
            from src.web.endpoints.public.appearance import resolve_brand_name
            brand = resolve_brand_name() or "VPN"
        except Exception:
            brand = "VPN"
    t = load_email_template()
    subject = fill(t["subject"], brand=brand, code=code, minutes=minutes)
    heading = fill(t["heading"], brand=brand, code=code, minutes=minutes)
    intro = fill(t["intro"], brand=brand, code=code, minutes=minutes)
    expire_note = fill(t["expire_note"], brand=brand, code=code, minutes=minutes)
    ignore_note = fill(t["ignore_note"], brand=brand, code=code, minutes=minutes)

    text = f"{intro}\n\nКод: {code}\n\n{expire_note}\n{ignore_note}"

    safe_code = _escape(code)
    safe_brand = _escape(brand)
    safe_heading = _escape(heading)
    safe_intro = _escape(intro)
    safe_expire = _escape(expire_note)
    safe_ignore = _escape(ignore_note)

    # Логотип сервиса в шапке письма (если задан) — рядом с названием бренда.
    logo_src = _logo_src()
    logo_img = (
        f'<img src="{logo_src}" alt="{safe_brand}" height="36" '
        'style="height:36px;width:auto;max-width:150px;border-radius:8px;'
        'vertical-align:middle;margin-right:12px;">'
        if logo_src
        else ""
    )
    html = f"""\
<div style="font-family:'Segoe UI',Arial,Helvetica,sans-serif;background:#f4f5f7;padding:32px 16px;">
  <div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:16px;
              overflow:hidden;border:1px solid #e6e8eb;">
    <div style="background:#5b5bd6;padding:18px 28px;">
      {logo_img}<span style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:.2px;vertical-align:middle;">{safe_brand}</span>
    </div>
    <div style="padding:28px;">
      <h1 style="margin:0 0 8px;font-size:20px;color:#16181d;">{safe_heading}</h1>
      <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#454a52;">
        {safe_intro}
      </p>
      <div style="text-align:center;margin:0 0 20px;">
        <div style="display:inline-block;background:#f0f0fb;border:1px solid #d9d9f5;
                    border-radius:12px;padding:16px 28px;">
          <span style="font-size:34px;font-weight:700;letter-spacing:10px;
                       color:#5b5bd6;font-family:'Courier New',monospace;">{safe_code}</span>
        </div>
      </div>
      <p style="margin:0 0 6px;font-size:14px;color:#6b7280;">
        {safe_expire}
      </p>
      <p style="margin:0;font-size:13px;color:#9aa1ab;">
        {safe_ignore}
      </p>
    </div>
    <div style="background:#fafbfc;padding:14px 28px;border-top:1px solid #eef0f2;">
      <span style="font-size:12px;color:#9aa1ab;">© {safe_brand}</span>
    </div>
  </div>
</div>"""
    return subject, text, html


class SmtpEmailSender(EmailSender):
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def _settings(self) -> dict:
        # Эффективные настройки: .env + сохранённое из админки (assets/email.json).
        # Читаем при каждой отправке — правки в админке применяются сразу.
        return load_email_settings(self._config)

    @staticmethod
    def _use_brevo(s: dict) -> bool:
        # Brevo используем, только если он ВЫБРАН провайдером и задан ключ.
        return s["provider"] == "brevo" and bool(s["brevo_api_key"])

    @property
    def is_enabled(self) -> bool:
        s = self._settings()
        if not s["enabled"] or not s["from_email"]:
            return False
        # Brevo требует только API-ключ и адрес отправителя.
        if self._use_brevo(s):
            return True
        # Иначе нужен полноценный SMTP-конфиг.
        return bool(s["host"] and s["username"] and s["password"])

    def _localize(self, settings: dict, *, subject: str, body: str) -> tuple[str, str, str]:
        """Возвращает (subject, text, html) — русифицируем письмо с кодом."""
        if subject == EMAIL_VERIFICATION_SUBJECT:
            return _render_verification(body, (settings["from_name"] or "").strip())
        return subject, body, _text_to_html(body)

    async def send(self, *, to: str, subject: str, body: str) -> None:
        try:
            s = self._settings()
            subject, text, html = self._localize(s, subject=subject, body=body)
            if self._use_brevo(s):
                await self._send_brevo(s, to=to, subject=subject, text=text, html=html)
            else:
                await asyncio.to_thread(
                    self._send_smtp, s, to=to, subject=subject, text=text, html=html
                )
        except Exception as e:
            logger.error(f"Failed to send email to '{to}': {e}")
            raise EmailDeliveryError(
                "Failed to send verification email. Please try again later."
            ) from e

    async def _send_brevo(self, s: dict, *, to: str, subject: str, text: str, html: str) -> None:
        from_name = (s["from_name"] or "").strip()
        from_email = (s["from_email"] or "").strip()

        payload = {
            "sender": {"email": from_email, **({"name": from_name} if from_name else {})},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text,
            "htmlContent": html,
        }
        headers = {
            "api-key": s["brevo_api_key"],
            "accept": "application/json",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(BREVO_API_URL, json=payload, headers=headers)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Brevo API returned {resp.status_code}: {resp.text[:300]}"
            )
        logger.info(f"Email sent to '{to}' via Brevo (status {resp.status_code})")

    def _send_smtp(self, s: dict, *, to: str, subject: str, text: str, html: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        from_name = (s["from_name"] or "").strip()
        from_email = (s["from_email"] or "").strip()
        message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        message["To"] = to
        message.set_content(text)
        message.add_alternative(html, subtype="html")

        host, port = s["host"], int(s["port"])
        smtp_user, smtp_password = s["username"], s["password"]

        if s["use_ssl"]:
            with smtplib.SMTP_SSL(host, port, timeout=20) as client:
                client.login(smtp_user, smtp_password)
                client.send_message(message)
            return

        with smtplib.SMTP(host, port, timeout=20) as client:
            client.ehlo()
            if s["use_tls"]:
                client.starttls()
                client.ehlo()
            client.login(smtp_user, smtp_password)
            client.send_message(message)
