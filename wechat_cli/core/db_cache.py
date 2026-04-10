"""解密数据库缓存 — mtime 检测变化，跨会话复用"""

import hashlib
import json
import os
import tempfile

from .crypto import full_decrypt, decrypt_wal
from .key_utils import get_key_info


class DBCache:
    CACHE_DIR = os.path.join(tempfile.gettempdir(), "wechat_cli_cache")
    MTIME_FILE = os.path.join(tempfile.gettempdir(), "wechat_cli_cache", "_mtimes.json")

    # macOS 新版微信路径映射: CLI 期望路径 -> 实际相对路径
    _MACOS_NEW_PATH_MAP = {
        'session/session.db': '../Session/session_new.db',
        'contact/contact.db': '../Contact/wccontact_new2.db',
        'group/group.db': '../Group/group_new.db',
        'favorite/favorite.db': '../Favorites/favorites.db',
    }

    def __init__(self, all_keys, db_dir):
        self._all_keys = all_keys
        self._db_dir = db_dir
        self._cache = {}  # rel_key -> (db_mtime, wal_mtime, tmp_path)
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        self._detect_path_rewrites()
        self._load_persistent_cache()

    def _detect_path_rewrites(self):
        """检测 macOS 新版路径，构建重写表。"""
        self._path_rewrites = {}
        # message/message_N.db -> msg_N.db
        import re, glob as glob_mod
        for f in glob_mod.glob(os.path.join(self._db_dir, "msg_*.db")):
            base = os.path.basename(f)
            m = re.match(r'msg_(\d+)\.db', base)
            if m:
                old_key = f'message/message_{m.group(1)}.db'
                self._path_rewrites[old_key] = base

        for old_key, new_rel in self._MACOS_NEW_PATH_MAP.items():
            real_path = os.path.normpath(os.path.join(self._db_dir, new_rel))
            if os.path.exists(real_path):
                self._path_rewrites[old_key] = new_rel

    def _cache_path(self, rel_key):
        h = hashlib.md5(rel_key.encode()).hexdigest()[:12]
        return os.path.join(self.CACHE_DIR, f"{h}.db")

    def _load_persistent_cache(self):
        if not os.path.exists(self.MTIME_FILE):
            return
        try:
            with open(self.MTIME_FILE, encoding="utf-8") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for rel_key, info in saved.items():
            tmp_path = info["path"]
            if not os.path.exists(tmp_path):
                continue
            actual_rel = self._path_rewrites.get(rel_key, rel_key)
            rel_path = actual_rel.replace('\\', os.sep)
            db_path = os.path.join(self._db_dir, rel_path)
            wal_path = db_path + "-wal"
            try:
                db_mtime = os.path.getmtime(db_path)
                wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
            except OSError:
                continue
            if db_mtime == info["db_mt"] and wal_mtime == info["wal_mt"]:
                self._cache[rel_key] = (db_mtime, wal_mtime, tmp_path)

    def _save_persistent_cache(self):
        data = {}
        for rel_key, (db_mt, wal_mt, path) in self._cache.items():
            data[rel_key] = {"db_mt": db_mt, "wal_mt": wal_mt, "path": path}
        try:
            with open(self.MTIME_FILE, 'w', encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def get(self, rel_key):
        key_info = get_key_info(self._all_keys, rel_key)
        if not key_info:
            return None
        # 应用路径重写（macOS 新版微信兼容）
        actual_rel = self._path_rewrites.get(rel_key, rel_key)
        rel_path = actual_rel.replace('\\', '/').replace('/', os.sep)
        db_path = os.path.join(self._db_dir, rel_path)
        wal_path = db_path + "-wal"
        if not os.path.exists(db_path):
            return None

        try:
            db_mtime = os.path.getmtime(db_path)
            wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
        except OSError:
            return None

        if rel_key in self._cache:
            c_db_mt, c_wal_mt, c_path = self._cache[rel_key]
            if c_db_mt == db_mtime and c_wal_mt == wal_mtime and os.path.exists(c_path):
                return c_path

        tmp_path = self._cache_path(rel_key)
        enc_key = bytes.fromhex(key_info["enc_key"])
        full_decrypt(db_path, tmp_path, enc_key)
        if os.path.exists(wal_path):
            decrypt_wal(wal_path, tmp_path, enc_key)
        self._cache[rel_key] = (db_mtime, wal_mtime, tmp_path)
        self._save_persistent_cache()
        return tmp_path

    def cleanup(self):
        self._save_persistent_cache()
