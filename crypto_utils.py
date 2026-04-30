import os
from pathlib import Path

from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv(Path(__file__).resolve().parent / ".env")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise RuntimeError("ENCRYPTION_KEY is not set")

cipher = Fernet(ENCRYPTION_KEY.encode())


def encrypt_text(text: str) -> bytes:
    return cipher.encrypt(text.encode("utf-8"))


def decrypt_text(token: bytes) -> str:
    return cipher.decrypt(token).decode("utf-8")