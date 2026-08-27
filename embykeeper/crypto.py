"""凭据缓存的加解密工具.

用账号密码派生密钥 (scrypt + Fernet/AES-GCM) 加密缓存中的敏感凭据
(如 Emby token). 密文与盐一起存进缓存, 重启后可用同一密码再次解密.

威胁模型: 防止 cache.json 单独泄露 (如误被 git 提交 / 上传到公开仓库) 时
凭据可直接读取. 不防能同时拿到配置文件 (含密码) 的同机攻击者.
"""

import base64
import binascii
import hashlib
import json
import os
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

_VERSION = 1
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32


def _derive_key(password: str, salt: bytes) -> bytes:
    """从密码与盐派生 32 字节 Fernet 密钥 (base64 urlsafe)."""
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return base64.urlsafe_b64encode(key)


def is_encrypted_credential(data: Any) -> bool:
    """判断缓存值是否为加密凭据信封."""
    return isinstance(data, dict) and data.get("v") == _VERSION


def encrypt_credential(data: Dict[str, Any], password: str) -> Dict[str, Any]:
    """将凭据字典加密为可存入缓存的信封 (含随机盐)."""
    salt = os.urandom(16)
    f = Fernet(_derive_key(password, salt))
    payload = f.encrypt(json.dumps(data).encode("utf-8"))
    return {
        "v": _VERSION,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "data": payload.decode("ascii"),
    }


def decrypt_credential(envelope: Dict[str, Any], password: str) -> Optional[Dict[str, Any]]:
    """解密缓存中的凭据信封; 密码错误 / 密文被篡改 / 格式非法时返回 None."""
    if not is_encrypted_credential(envelope):
        return None
    try:
        salt = base64.urlsafe_b64decode(envelope["salt"])
        payload = envelope["data"].encode("ascii")
        f = Fernet(_derive_key(password, salt))
        return json.loads(f.decrypt(payload))
    except (InvalidToken, KeyError, TypeError, ValueError, binascii.Error):
        return None
