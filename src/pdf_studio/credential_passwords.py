"""Passwords for newly generated credentials, protected for this Windows user.

Importing this module does not generate a password or access any credentials.
Only the adjacent application-created record is read; no credential discovery.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets

PASSWORD_RECORD = '.qpdf-password.dpapi'
_MAGIC = b'QPDF-STUDIO-DPAPI-1\x00'
_MAX_RECORD = 65536


def new_password() -> str:
    return secrets.token_urlsafe(32)


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('自动密码保管需要 Windows 当前用户的 DPAPI。')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    protect = crypt.CryptProtectData
    protect.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    protect.restype = wintypes.BOOL
    unprotect = crypt.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    unprotect.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    incoming = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = _Blob()
    # UI_FORBIDDEN only: no LOCAL_MACHINE flag, so protection is per user.
    # 限制：自动密码只绑定当前 Windows 用户，不能给整台机器或其他用户解密
    try:
        if decrypt:
            ok = unprotect(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
        else:
            ok = protect(ctypes.byref(incoming), 'qpdf Studio credential password',
                         None, None, None, 1, ctypes.byref(outgoing))
        if not ok:
            code = ctypes.get_last_error()
            raise RuntimeError(f'Windows 密码保护操作失败（错误 {code}），请使用生成凭据时的 Windows 用户。')
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel.LocalFree(ctypes.cast(outgoing.pbData, ctypes.c_void_p))


def protect_password(password: str, p12_bytes: bytes) -> bytes:
    """Prepare encrypted storage BEFORE committing any generated key files."""
    payload = json.dumps({'version': 1, 'p12_sha256': hashlib.sha256(p12_bytes).hexdigest(),
                          'password': password}, separators=(',', ':')).encode('utf-8')
    return _MAGIC + _dpapi(payload)


def load_managed_password(p12_path: Path) -> str | None:
    """None means no managed record; a broken/mismatched record is an error."""
    path = Path(p12_path)
    record = path.parent / PASSWORD_RECORD
    try:
        with record.open('rb') as stream:
            data = stream.read(_MAX_RECORD + 1)
    except FileNotFoundError:
        return None
    if len(data) > _MAX_RECORD or not data.startswith(_MAGIC):
        raise ValueError('本机密码记录格式不正确，未更改或替换已有凭据。')
    raw = _dpapi(data[len(_MAGIC):], decrypt=True)
    try:
        payload = json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict) or payload.get('version') != 1:
            raise ValueError('unsupported record')
        password = payload['password']
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError('invalid password record')
        expected = payload['p12_sha256']
        if not isinstance(expected, str):
            raise ValueError('invalid credential binding')
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError('本机密码记录无法解析。') from exc
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if not secrets.compare_digest(digest.hexdigest(), expected):
        raise ValueError('本机密码记录与所选 P12 不对应，请选择原凭据。')
    return password
