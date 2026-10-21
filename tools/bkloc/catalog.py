"""Чтение каталога Addressables (catalog.json) без изменений.

Разворачивает m_InternalIds с префиксами, декодирует ключи, корзины и записи,
связывает ассет с его бандлом и читает параметры бандлов из m_ExtraDataString.
"""
import base64
import json
import re
import struct

from tools.bkloc.paths import CATALOG_SUB, game_dir


class Catalog:
    def __init__(self, game, path=None):
        self.game = game
        self.path = path or game_dir(game) / CATALOG_SUB
        data = json.loads(self.path.read_text(encoding="utf-8"))
        prefixes = data["m_InternalIdPrefixes"]
        self.internal_ids = []
        for item in data["m_InternalIds"]:
            match = re.match(r"^(\d+)#(.*)$", item)
            self.internal_ids.append(prefixes[int(match.group(1))] + match.group(2) if match else item)
        self._keys_raw = base64.b64decode(data["m_KeyDataString"])
        self._extra = base64.b64decode(data["m_ExtraDataString"])
        self.buckets = self._read_buckets(base64.b64decode(data["m_BucketDataString"]))
        self.keys = [self._read_object(self._keys_raw, offset)[0] for offset, _ in self.buckets]
        self.entries = self._read_entries(base64.b64decode(data["m_EntryDataString"]))

    @staticmethod
    def _read_buckets(raw):
        count = struct.unpack_from("<i", raw, 0)[0]
        pos = 4
        buckets = []
        for _ in range(count):
            offset, n = struct.unpack_from("<ii", raw, pos)
            pos += 8
            buckets.append((offset, list(struct.unpack_from("<%di" % n, raw, pos))))
            pos += 4 * n
        return buckets

    @staticmethod
    def _read_entries(raw):
        count = struct.unpack_from("<i", raw, 0)[0]
        names = ("internal_id", "provider", "dependency_key", "dep_hash", "data_index", "primary_key", "resource_type")
        return [dict(zip(names, struct.unpack_from("<7i", raw, 4 + i * 28))) for i in range(count)]

    @staticmethod
    def _read_object(raw, offset):
        kind = raw[offset]
        offset += 1
        if kind in (0, 1):
            n = struct.unpack_from("<i", raw, offset)[0]
            text = raw[offset + 4: offset + 4 + n]
            return text.decode("ascii" if kind == 0 else "utf-16le", "replace"), offset + 4 + n
        if kind == 2:
            return struct.unpack_from("<H", raw, offset)[0], offset + 2
        if kind == 3:
            return struct.unpack_from("<I", raw, offset)[0], offset + 4
        if kind == 4:
            return struct.unpack_from("<i", raw, offset)[0], offset + 4
        if kind == 7:
            parts = []
            for _ in range(2):
                n = raw[offset]
                offset += 1
                parts.append(raw[offset: offset + n].decode("ascii", "replace"))
                offset += n
            n = struct.unpack_from("<i", raw, offset)[0]
            offset += 4
            parts.append(raw[offset: offset + n].decode("utf-16le", "replace"))
            offset += n
            return {"assembly": parts[0], "class": parts[1], "json": parts[2]}, offset
        return {"unknown_type": kind}, offset

    def entry_path(self, entry):
        return self.internal_ids[entry["internal_id"]]

    def dependencies(self, entry):
        if entry["dependency_key"] < 0:
            return []
        return [self.entry_path(self.entries[i]) for i in self.buckets[entry["dependency_key"]][1]]

    def bundle_of(self, asset_path):
        for entry in self.entries:
            if self.entry_path(entry) == asset_path:
                deps = self.dependencies(entry)
                return deps[0].split("\\")[-1] if deps else None
        return None

    def bundle_options(self, bundle_name):
        """Параметры AssetBundleRequestOptions (m_Crc, m_BundleSize и т.д.) для бандла."""
        for entry in self.entries:
            path = self.entry_path(entry)
            if path.endswith("\\" + bundle_name) and entry["data_index"] >= 0:
                obj, _ = self._read_object(self._extra, entry["data_index"])
                if isinstance(obj, dict) and "json" in obj:
                    try:
                        return json.loads(obj["json"])
                    except json.JSONDecodeError:
                        return obj
                return obj
        return None
