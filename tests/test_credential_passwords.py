"""Synthetic payloads only; DPAPI mocked. No user passwords or real keys."""
import json
from types import SimpleNamespace

import pytest

from pdf_studio import credential_passwords as passwords
from pdf_studio import keygen


def test_missing_record_does_not_read_p12_or_call_dpapi(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('DPAPI must not be called')
    monkeypatch.setattr(passwords, '_dpapi', unexpected)
    assert passwords.load_managed_password(tmp_path / 'external.p12') is None


def test_managed_record_roundtrip_is_bound_to_p12(tmp_path, monkeypatch):
    p12 = tmp_path / 'personal-signing.p12'
    p12.write_bytes(b'synthetic P12, not a real credential')
    secret = 'synthetic password for mocked storage'
    captured = {}
    def fake_dpapi(data, *, decrypt=False):
        if decrypt:
            assert data == b'opaque protected data'
            return captured['plaintext']
        captured['plaintext'] = data
        return b'opaque protected data'
    monkeypatch.setattr(passwords, '_dpapi', fake_dpapi)
    stored = passwords.protect_password(secret, p12.read_bytes())
    assert secret.encode() not in stored
    (tmp_path / passwords.PASSWORD_RECORD).write_bytes(stored)
    assert passwords.load_managed_password(p12) == secret
    p12.write_bytes(b'different synthetic credential')
    with pytest.raises(ValueError, match='不对应'):
        passwords.load_managed_password(p12)


def test_bad_record_is_error_not_missing_or_new_password(tmp_path):
    (tmp_path / passwords.PASSWORD_RECORD).write_bytes(b'not a managed record')
    with pytest.raises(ValueError, match='格式不正确'):
        passwords.load_managed_password(tmp_path / 'personal-signing.p12')


def test_other_user_dpapi_error_is_not_silently_replaced(tmp_path, monkeypatch):
    (tmp_path / passwords.PASSWORD_RECORD).write_bytes(passwords._MAGIC + b'opaque')
    def fail(*args, **kwargs):
        raise RuntimeError('wrong Windows user')
    monkeypatch.setattr(passwords, '_dpapi', fail)
    with pytest.raises(RuntimeError, match='wrong Windows user'):
        passwords.load_managed_password(tmp_path / 'personal-signing.p12')


def test_protect_failure_leaves_no_key_files(tmp_path, monkeypatch):
    target = tmp_path / 'new-credentials'
    fake_public = SimpleNamespace(public_bytes=lambda *args, **kwargs: b'fake-public')
    fake_key = SimpleNamespace(public_key=lambda: fake_public)
    fake_cert = SimpleNamespace(public_bytes=lambda *args: b'fake-cert')
    monkeypatch.setattr(keygen.pkcs12, 'serialize_key_and_certificates', lambda **kwargs: b'fake-P12')
    def fail(*args, **kwargs):
        raise RuntimeError('DPAPI unavailable')
    monkeypatch.setattr(passwords, 'protect_password', fail)
    with pytest.raises(RuntimeError, match='DPAPI unavailable'):
        keygen._store_credentials(target, fake_cert, fake_key, 'synthetic test password',
                                 b'fake-encrypted-key', store_password=True)
    assert not target.exists()


def test_random_password_format():
    first, second = passwords.new_password(), passwords.new_password()
    assert first != second
    assert 12 <= len(first) <= 1024

