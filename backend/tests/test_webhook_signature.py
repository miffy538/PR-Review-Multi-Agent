import hashlib
import hmac

from app.api.webhook import verify_signature


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_and_invalid():
    body = b'{"a":1}'
    assert verify_signature("s", body, sign("s", body))
    assert not verify_signature("s", body, sign("other", body))
    assert not verify_signature("s", body, None)
    assert not verify_signature("s", body, "sha1=abc")
    assert not verify_signature("", body, sign("", body))   # unconfigured secret fails closed
