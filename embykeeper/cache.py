import asyncio
import atexit
import json
import os
from typing import Any, List

from loguru import logger

from .utils import CachedFuncProxy
from .config import config

# 写盘去抖窗口: 合并同一事件循环周期内的多次变更, 避免每次 set/delete 都全量写盘
# (运行时各账号并发登录/保活时, 每次全量写盘都是阻塞事件循环的同步 I/O).
_FLUSH_DEBOUNCE = 1.0


class Cache:
    def __init__(self):
        self._cache_file = config.basedir / "cache.json"
        self._data = {}
        self._dirty = False
        self._flush_task = None
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except json.JSONDecodeError:
                logger.warning("缓存文件损坏, 将使用全新缓存.")
        atexit.register(self.flush)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data
        try:
            for part in key.split("."):
                value = value.get(part, {})
            return default if value == {} else value
        except (AttributeError, TypeError):
            return default

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        current = self._data
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
        self._mark_dirty()

    def delete(self, key: str) -> None:
        parts = key.split(".")
        current = self._data
        path = []

        # 遍历路径, 检查每一层 (path 记录 (键, 父字典) 以便清理空字典)
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                return
            path.append((part, current))
            current = current[part]

        # 检查并删除最后一个键
        if isinstance(current, dict) and parts[-1] in current:
            del current[parts[-1]]

            # 清理空字典
            for part, parent in reversed(path):
                if isinstance(parent, dict) and part in parent and not parent[part]:
                    del parent[part]
                else:
                    break

            self._mark_dirty()

    def find_by_prefix(self, prefix: str) -> List[str]:
        def get_keys_with_prefix(d, current_path="", keys=None):
            if keys is None:
                keys = []
            for k, v in d.items():
                path = f"{current_path}.{k}" if current_path else k
                if isinstance(v, dict):
                    get_keys_with_prefix(v, path, keys)
                else:
                    if path.startswith(prefix):
                        keys.append(path)
            return keys

        return get_keys_with_prefix(self._data)

    def delete_by_prefix(self, prefix: str) -> None:
        keys = self.find_by_prefix(prefix)
        for key in keys:
            self.delete(key)

    def delete_many(self, keys: List[str]) -> None:
        """批量删除多个键的缓存

        Args:
            keys: 要删除的键列表
        """
        # 批量删除所有键, 只写入一次文件
        changed = False
        for key in keys:
            parts = key.split(".")
            current = self._data
            path = []

            # 遍历路径, 检查每一层 (path 记录 (键, 父字典) 以便清理空字典)
            for part in parts[:-1]:
                if not isinstance(current, dict) or part not in current:
                    break
                path.append((part, current))
                current = current[part]

            # 检查并删除最后一个键
            if isinstance(current, dict) and parts[-1] in current:
                del current[parts[-1]]
                changed = True

                # 清理空字典
                for part, parent in reversed(path):
                    if isinstance(parent, dict) and part in parent and not parent[part]:
                        del parent[part]
                    else:
                        break

        # 只在有改动时写入一次文件
        if changed:
            self._mark_dirty()

    # --- 持久化 (去抖 + 原子写) ---

    def _mark_dirty(self):
        """标记缓存已变更, 并安排一次去抖写盘."""
        if self._dirty:
            return
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and (self._flush_task is None or self._flush_task.done()):
            self._flush_task = loop.create_task(self._debounced_flush())
        elif loop is None:
            # 无运行中的事件循环 (如模块初始化期), 立即同步写盘.
            self.flush()

    async def _debounced_flush(self):
        await asyncio.sleep(_FLUSH_DEBOUNCE)
        self.flush()
        self._flush_task = None
        if self._dirty:  # pragma: no cover  # 仅在 flush 期间发生写入时触发, 时序上难以构造
            self._mark_dirty()

    def flush(self):
        """把内存中的缓存写入磁盘 (原子写)."""
        if not self._dirty:
            return
        try:
            tmp_file = self._cache_file.with_suffix(".json.tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp_file, self._cache_file)
        except OSError as e:
            logger.warning(f"缓存写入失败: {e}")
            return  # 保持 _dirty, 等待下次写入重试
        self._dirty = False


cache: Cache = CachedFuncProxy(lambda: Cache())
