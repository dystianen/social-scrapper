# src/core/otp_handler.py
"""Ambil OTP dari Gmail secara otomatis via IMAP."""

import re
import time
import imaplib
import email
from email.header import decode_header
from rich.console import Console

console = Console()


class OTPHandler:
    """
    Hubungkan ke Gmail via IMAP, cari email OTP dari Instagram,
    extract kode OTP-nya.
    """

    def __init__(self, gmail_email: str, app_password: str):
        self.gmail_email = gmail_email
        self.app_password = app_password
        self.imap_server = "imap.gmail.com"
        self.imap_port = 993

    def get_latest_otp(self, max_wait_seconds: int = 120, poll_interval: int = 5) -> str | None:
        """
        Tunggu dan ambil OTP terbaru dari Instagram.

        Args:
            max_wait_seconds: Berapa lama menunggu email OTP masuk
            poll_interval: Seberapa sering cek inbox (detik)

        Returns:
            Kode OTP (6 digit) atau None kalau gagal
        """
        console.print(f"  [cyan]📧 Menunggu OTP dari Instagram di {self.gmail_email}...[/cyan]")
        console.print(f"  [dim]Maksimal menunggu {max_wait_seconds} detik[/dim]")

        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            try:
                otp = self._check_inbox()
                if otp:
                    console.print(f"  [bold green]✓ OTP ditemukan: {otp}[/bold green]")
                    return otp
                else:
                    elapsed = int(time.time() - start_time)
                    remaining = max_wait_seconds - elapsed
                    console.print(f"  [dim]Belum ada OTP... ({elapsed}s, sisa {remaining}s)[/dim]")
                    time.sleep(poll_interval)

            except Exception as e:
                console.print(f"  [yellow]⚠ Error cek inbox: {e}[/yellow]")
                time.sleep(poll_interval)

        console.print(f"  [red]✗ Timeout: OTP tidak ditemukan dalam {max_wait_seconds} detik[/red]")
        return None

    def _check_inbox(self) -> str | None:
        """Cek inbox Gmail, cari email OTP dari Instagram."""
        mail = None
        try:
            # Koneksi ke Gmail IMAP
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.gmail_email, self.app_password)
            mail.select("INBOX")

            # Cari email dari Instagram (baru-baru ini)
            # Search unread emails from Instagram
            search_criteria = '(UNSEEN FROM "instagram")'
            status, message_ids = mail.search(None, search_criteria)

            if status != "OK" or not message_ids[0]:
                # Coba juga dari "notification" atau "security"
                search_criteria = '(UNSEEN FROM "security@instagrammail.com")'
                status, message_ids = mail.search(None, search_criteria)

            if status != "OK" or not message_ids[0]:
                # Coba lebih luas
                search_criteria = '(UNSEEN SUBJECT "Instagram")'
                status, message_ids = mail.search(None, search_criteria)

            if status != "OK" or not message_ids[0]:
                return None

            # Ambil email terbaru
            ids = message_ids[0].split()
            latest_id = ids[-1]  # ID terakhir = terbaru

            # Fetch email
            status, msg_data = mail.fetch(latest_id, "(RFC822)")

            if status != "OK":
                return None

            # Parse email
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Ambil subject
            subject = self._decode_subject(msg)
            console.print(f"  [dim]Email subject: {subject}[/dim]")

            # Ambil body
            body = self._get_body(msg)

            # Extract OTP dari subject atau body
            otp = self._extract_otp(subject + " " + body)

            # Tandai email sebagai sudah dibaca
            mail.store(latest_id, "+FLAGS", "\\Seen")

            return otp

        except imaplib.IMAP4.error as e:
            console.print(f"  [red]Gmail login gagal: {e}[/red]")
            console.print(f"  [yellow]Pastikan App Password benar dan IMAP aktif[/yellow]")
            return None
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass

    def _decode_subject(self, msg) -> str:
        """Decode email subject."""
        subject = msg.get("Subject", "")
        if subject:
            decoded = decode_header(subject)
            parts = []
            for content, charset in decoded:
                if isinstance(content, bytes):
                    parts.append(content.decode(charset or "utf-8", errors="ignore"))
                else:
                    parts.append(content)
            return " ".join(parts)
        return ""

    def _get_body(self, msg) -> str:
        """Ambil text body dari email."""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="ignore")
                        break
                elif content_type == "text/html" and not body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="ignore")

        return body

    def _extract_otp(self, text: str) -> str | None:
        """
        Extract kode OTP dari text email.
        Instagram biasanya pakai 6 digit kode.
        """
        # Bersihkan HTML tags
        clean = re.sub(r'<[^>]+>', ' ', text)

        # Pola yang umum dipakai Instagram:
        patterns = [
            r'(\d{6})',                                    # 6 digit
            r'code[:\s]+(\d{6})',                          # "code: 123456"
            r'kode[:\s]+(\d{6})',                          # "kode: 123456"
            r'verifikasi[:\s]+(\d{6})',                    # "verifikasi: 123456"
            r'confirm.*?(\d{6})',                          # "confirm ... 123456"
            r'security code[:\s]+(\d{6})',                 # "security code: 123456"
            r'(\d{3})[\s-]*(\d{3})',                       # "123 456" atau "123-456"
        ]

        for pattern in patterns:
            match = re.search(pattern, clean, re.IGNORECASE)
            if match:
                if match.lastindex and match.lastindex >= 2:
                    # Pattern dengan 2 groups (123 456)
                    return match.group(1) + match.group(2)
                return match.group(1) if match.lastindex else match.group(0)

        return None