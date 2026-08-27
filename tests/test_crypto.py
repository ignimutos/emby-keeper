"""凭据缓存加解密测试."""

from embykeeper.crypto import (
    decrypt_credential,
    encrypt_credential,
    is_encrypted_credential,
)


def test_roundtrip_with_same_password():
    data = {"token": "abc123", "userid": "42"}
    envelope = encrypt_credential(data, "secret")
    assert is_encrypted_credential(envelope)
    assert decrypt_credential(envelope, "secret") == data


def test_wrong_password_returns_none():
    envelope = encrypt_credential({"token": "abc"}, "secret")
    assert decrypt_credential(envelope, "wrong") is None


def test_tampered_ciphertext_returns_none():
    envelope = encrypt_credential({"token": "abc"}, "secret")
    tampered = dict(envelope)
    tampered["data"] = envelope["data"][:-4] + "AAAA"
    assert decrypt_credential(tampered, "secret") is None


def test_legacy_plaintext_is_not_a_valid_envelope():
    assert is_encrypted_credential({"token": "abc"}) is False
    assert decrypt_credential({"token": "abc"}, "secret") is None


def test_each_encryption_uses_fresh_salt():
    envelope1 = encrypt_credential({"token": "abc"}, "secret")
    envelope2 = encrypt_credential({"token": "abc"}, "secret")
    assert envelope1["salt"] != envelope2["salt"]
    assert envelope1["data"] != envelope2["data"]


def test_unicode_password_and_data():
    envelope = encrypt_credential({"token": "中文", "userid": ""}, "密码😀")
    assert decrypt_credential(envelope, "密码😀") == {"token": "中文", "userid": ""}
