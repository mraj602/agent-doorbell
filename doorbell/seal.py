"""Password sealing for the sender token.

The token that can post to Slack is stored encrypted: scrypt derives two keys from the owner's
password, one encrypts the token (AES-256-GCM), the other signs per-agent send grants
(HMAC-SHA256). Without the password, nothing on disk lets anyone, including an agent running
as the same user, decrypt the token or forge a grant.
"""
import base64
import hashlib
import hmac
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# scrypt cost: about 64 MB and a fraction of a second, done once per password check.
N, R, P = 2 ** 16, 8, 1
MIN_PASSWORD = 8


class BadPassword(Exception):
    pass


def _b64(b):
    return base64.b64encode(b).decode()


def _unb64(s):
    return base64.b64decode(s.encode())


def derive(password, salt, n=N, r=R, p=P):
    k = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=64)
    return k[:32], k[32:]   # encryption key, grant-signing key


def seal(token, password):
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"use a password of at least {MIN_PASSWORD} characters")
    salt, nonce = os.urandom(16), os.urandom(12)
    enc, _ = derive(password, salt)
    ct = AESGCM(enc).encrypt(nonce, token.encode(), b"agent-doorbell sender token v1")
    return json.dumps({"v": 1, "kdf": "scrypt", "n": N, "r": R, "p": P,
                       "salt": _b64(salt), "nonce": _b64(nonce), "ct": _b64(ct)})


def open_sealed(blob, password):
    """Returns (plaintext, encryption key, grant key). Raises BadPassword if the password is wrong.
    The plaintext is a bare token (0.2) or a credentials JSON object (0.3, see oauth.py)."""
    b = json.loads(blob)
    enc, mac = derive(password, _unb64(b["salt"]), b["n"], b["r"], b["p"])
    try:
        plain = AESGCM(enc).decrypt(_unb64(b["nonce"]), _unb64(b["ct"]), b"agent-doorbell sender token v1")
    except InvalidTag:
        raise BadPassword("wrong password") from None
    return plain.decode(), enc, mac


def unseal(blob, password):
    """Returns (plaintext, grant_key). Raises BadPassword if the password is wrong."""
    plain, _, mac = open_sealed(blob, password)
    return plain, mac


def reseal(blob, enc_key, plaintext):
    """Seal new plaintext under the same password without asking for it again (token renewal
    while unlocked): same salt and key, fresh nonce. The grant key stays the same."""
    b = json.loads(blob)
    nonce = os.urandom(12)
    ct = AESGCM(enc_key).encrypt(nonce, plaintext.encode(), b"agent-doorbell sender token v1")
    return json.dumps(dict(b, nonce=_b64(nonce), ct=_b64(ct)))


def grant_mac(grant_key, identity, agent):
    return hmac.new(grant_key, f"send-grant:{identity}:{agent}".encode(), hashlib.sha256).hexdigest()


def grant_ok(grant_key, identity, agent, mac):
    return bool(mac) and hmac.compare_digest(grant_mac(grant_key, identity, agent), mac)
