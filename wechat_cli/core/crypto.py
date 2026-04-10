"""数据库解密 — SQLCipher 4, AES-256-CBC"""

import os
import struct

from Crypto.Cipher import AES

KEY_SZ = 32
SALT_SZ = 16
SQLITE_HDR = b'SQLite format 3\x00'
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24

# 已知的 (page_size, reserve_size) 组合
_KNOWN_CONFIGS = [
    (4096, 80),   # 旧版微信: IV(16) + HMAC-SHA512(64)
    (1024, 48),   # macOS 新版微信: IV(16) + HMAC-SHA256(32)
]


def _detect_config(db_path, enc_key):
    """自动检测 page_size 和 reserve_size。"""
    with open(db_path, 'rb') as f:
        header = f.read(max(ps for ps, _ in _KNOWN_CONFIGS))

    for page_sz, reserve_sz in _KNOWN_CONFIGS:
        if len(header) < page_sz:
            continue
        page_data = header[:page_sz]
        iv = page_data[page_sz - reserve_sz: page_sz - reserve_sz + 16]
        encrypted = page_data[SALT_SZ: page_sz - reserve_sz]
        if len(encrypted) % 16 != 0:
            continue
        try:
            cipher = AES.new(enc_key, AES.MODE_CBC, iv)
            dec = cipher.decrypt(encrypted)
            result = SQLITE_HDR + dec
            ps_field = int.from_bytes(result[16:18], 'big')
            if ps_field == page_sz:
                return page_sz, reserve_sz
        except Exception:
            continue
    # 默认回退到旧版配置
    return 4096, 80


def decrypt_page(enc_key, page_data, pgno, page_sz=4096, reserve_sz=80):
    iv = page_data[page_sz - reserve_sz: page_sz - reserve_sz + 16]
    if pgno == 1:
        encrypted = page_data[SALT_SZ: page_sz - reserve_sz]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytes(bytearray(SQLITE_HDR + decrypted + b'\x00' * reserve_sz))
    else:
        encrypted = page_data[:page_sz - reserve_sz]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b'\x00' * reserve_sz


def full_decrypt(db_path, out_path, enc_key):
    page_sz, reserve_sz = _detect_config(db_path, enc_key)
    file_size = os.path.getsize(db_path)
    total_pages = file_size // page_sz
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(db_path, 'rb') as fin, open(out_path, 'wb') as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(page_sz)
            if len(page) < page_sz:
                if len(page) > 0:
                    page = page + b'\x00' * (page_sz - len(page))
                else:
                    break
            fout.write(decrypt_page(enc_key, page, pgno, page_sz, reserve_sz))
    return total_pages


def decrypt_wal(wal_path, out_path, enc_key, page_sz=None, reserve_sz=None):
    if not os.path.exists(wal_path):
        return 0
    wal_size = os.path.getsize(wal_path)
    if wal_size <= WAL_HEADER_SZ:
        return 0
    # 从已解密的 db 文件推断 page_sz
    if page_sz is None or reserve_sz is None:
        with open(out_path, 'rb') as f:
            hdr = f.read(18)
        ps_field = int.from_bytes(hdr[16:18], 'big')
        if ps_field in [1024, 2048, 4096, 8192, 16384, 32768, 65536]:
            page_sz = ps_field
        else:
            page_sz = 4096
        # 从已知配置中查找对应的 reserve_sz
        reserve_sz = next((rs for ps, rs in _KNOWN_CONFIGS if ps == page_sz), 80)

    patched = 0
    with open(wal_path, 'rb') as wf, open(out_path, 'r+b') as df:
        wal_hdr = wf.read(WAL_HEADER_SZ)
        wal_salt1 = struct.unpack('>I', wal_hdr[16:20])[0]
        wal_salt2 = struct.unpack('>I', wal_hdr[20:24])[0]
        frame_size = WAL_FRAME_HEADER_SZ + page_sz
        while wf.tell() + frame_size <= wal_size:
            fh = wf.read(WAL_FRAME_HEADER_SZ)
            if len(fh) < WAL_FRAME_HEADER_SZ:
                break
            pgno = struct.unpack('>I', fh[0:4])[0]
            frame_salt1 = struct.unpack('>I', fh[8:12])[0]
            frame_salt2 = struct.unpack('>I', fh[12:16])[0]
            ep = wf.read(page_sz)
            if len(ep) < page_sz:
                break
            if pgno == 0 or pgno > 1000000:
                continue
            if frame_salt1 != wal_salt1 or frame_salt2 != wal_salt2:
                continue
            dec = decrypt_page(enc_key, ep, pgno, page_sz, reserve_sz)
            df.seek((pgno - 1) * page_sz)
            df.write(dec)
            patched += 1
    return patched
