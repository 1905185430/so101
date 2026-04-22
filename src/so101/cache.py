"""
so101.cache — 设备检测缓存
==========================

提供设备信息的智能缓存，减少重复的系统调用。

使用示例：
    from so101.cache import DeviceCache
    
    cache = DeviceCache(ttl=60)
    
    # 获取缓存
    cameras = cache.get('cameras')
    if cameras is None:
        cameras = detect_cameras()
        cache.set('cameras', cameras)
    
    # 或使用装饰器
    @cache.cached(ttl=60)
    def detect_cameras():
        # ... 检测逻辑
        return cameras
"""

import time
import hashlib
import pickle
from pathlib import Path
from typing import Any, Optional, Callable
from functools import wraps
from threading import Lock
from datetime import datetime, timedelta

from so101.logger import get_logger

logger = get_logger(__name__)


class DeviceCache:
    """
    设备信息缓存管理器。
    
    特性：
    - 基于 TTL 的自动过期
    - 线程安全
    - 可选的持久化存储
    - 设备变化检测
    """
    
    def __init__(
        self,
        ttl: int = 60,
        persistent: bool = False,
        cache_dir: Optional[Path] = None,
    ):
        """
        Args:
            ttl: 缓存过期时间（秒）
            persistent: 是否持久化到磁盘
            cache_dir: 缓存目录，默认 ~/.so101/cache/
        """
        self.ttl = ttl
        self.persistent = persistent
        
        if cache_dir is None:
            cache_dir = Path.home() / '.so101' / 'cache'
        self.cache_dir = cache_dir
        
        if persistent:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self._memory_cache: dict[str, tuple[Any, float]] = {}
        self._lock = Lock()
        
        # 设备指纹（用于检测设备变化）
        self._device_fingerprint: Optional[str] = None
    
    def _get_fingerprint(self) -> str:
        """计算当前设备指纹"""
        try:
            # 检查 /dev 目录变化
            dev_files = sorted([
                str(p) for p in Path('/dev').glob('video*')
                if p.name[5:].isdigit()
            ])
            
            serial_files = []
            serial_dir = Path('/dev/serial/by-id')
            if serial_dir.exists():
                serial_files = sorted([str(p) for p in serial_dir.iterdir()])
            
            content = '|'.join(dev_files + serial_files)
            return hashlib.md5(content.encode()).hexdigest()
        except Exception:
            return ''
    
    def _is_fingerprint_changed(self) -> bool:
        """检查设备指纹是否变化"""
        current = self._get_fingerprint()
        if self._device_fingerprint is None:
            self._device_fingerprint = current
            return False
        
        if current != self._device_fingerprint:
            logger.debug("设备指纹变化，缓存失效")
            self._device_fingerprint = current
            return True
        
        return False
    
    def _get_cache_path(self, key: str) -> Path:
        """获取缓存文件路径"""
        safe_key = key.replace('/', '_').replace(' ', '_')
        return self.cache_dir / f"{safe_key}.cache"
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存值。
        
        Args:
            key: 缓存键
        
        Returns:
            缓存值，如果不存在或已过期返回 None
        """
        with self._lock:
            # 检查设备指纹
            if self._is_fingerprint_changed():
                self.invalidate()
                return None
            
            # 先检查内存缓存
            if key in self._memory_cache:
                value, timestamp = self._memory_cache[key]
                if time.time() - timestamp < self.ttl:
                    logger.debug(f"内存缓存命中: {key}")
                    return value
                else:
                    # 过期，删除
                    del self._memory_cache[key]
            
            # 检查磁盘缓存
            if self.persistent:
                cache_path = self._get_cache_path(key)
                if cache_path.exists():
                    try:
                        with open(cache_path, 'rb') as f:
                            data = pickle.load(f)
                        
                        value, timestamp = data
                        if time.time() - timestamp < self.ttl:
                            # 加载到内存
                            self._memory_cache[key] = (value, timestamp)
                            logger.debug(f"磁盘缓存命中: {key}")
                            return value
                        else:
                            # 过期，删除文件
                            cache_path.unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning(f"读取缓存失败: {e}")
                        cache_path.unlink(missing_ok=True)
            
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        设置缓存值。
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 自定义过期时间（秒），None 使用默认值
        """
        with self._lock:
            timestamp = time.time()
            self._memory_cache[key] = (value, timestamp)
            
            # 持久化到磁盘
            if self.persistent:
                cache_path = self._get_cache_path(key)
                try:
                    with open(cache_path, 'wb') as f:
                        pickle.dump((value, timestamp), f)
                    logger.debug(f"缓存已持久化: {key}")
                except Exception as e:
                    logger.warning(f"持久化缓存失败: {e}")
    
    def invalidate(self, key: Optional[str] = None):
        """
        使缓存失效。
        
        Args:
            key: 指定要失效的键，None 则清除所有
        """
        with self._lock:
            if key:
                # 清除指定键
                self._memory_cache.pop(key, None)
                if self.persistent:
                    cache_path = self._get_cache_path(key)
                    cache_path.unlink(missing_ok=True)
                logger.debug(f"缓存已失效: {key}")
            else:
                # 清除所有
                self._memory_cache.clear()
                if self.persistent:
                    for cache_file in self.cache_dir.glob('*.cache'):
                        cache_file.unlink(missing_ok=True)
                logger.debug("所有缓存已清除")
    
    def cached(self, ttl: Optional[int] = None, key: Optional[str] = None):
        """
        缓存装饰器。
        
        Args:
            ttl: 自定义过期时间
            key: 自定义缓存键，默认使用函数名
        
        Example:
            @cache.cached(ttl=60)
            def detect_cameras():
                # ... 检测逻辑
                return cameras
        """
        def decorator(func: Callable) -> Callable:
            cache_key = key or func.__name__
            
            @wraps(func)
            def wrapper(*args, **kwargs):
                # 生成带参数的键
                if args or kwargs:
                    param_hash = hashlib.md5(
                        pickle.dumps((args, kwargs))
                    ).hexdigest()[:8]
                    full_key = f"{cache_key}_{param_hash}"
                else:
                    full_key = cache_key
                
                # 尝试从缓存获取
                cached_value = self.get(full_key)
                if cached_value is not None:
                    return cached_value
                
                # 执行函数
                result = func(*args, **kwargs)
                
                # 缓存结果
                self.set(full_key, result, ttl=ttl)
                
                return result
            
            # 添加缓存控制方法
            wrapper.invalidate = lambda: self.invalidate(cache_key)
            wrapper.cache = self
            
            return wrapper
        
        return decorator
    
    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        with self._lock:
            memory_count = len(self._memory_cache)
            
            disk_count = 0
            total_size = 0
            if self.persistent and self.cache_dir.exists():
                for cache_file in self.cache_dir.glob('*.cache'):
                    disk_count += 1
                    total_size += cache_file.stat().st_size
            
            return {
                'memory_entries': memory_count,
                'disk_entries': disk_count,
                'disk_size_kb': total_size / 1024,
                'ttl_seconds': self.ttl,
                'fingerprint': self._device_fingerprint or 'N/A',
            }
    
    def cleanup_expired(self):
        """清理过期的缓存文件"""
        if not self.persistent or not self.cache_dir.exists():
            return
        
        cleaned = 0
        for cache_file in self.cache_dir.glob('*.cache'):
            try:
                with open(cache_file, 'rb') as f:
                    _, timestamp = pickle.load(f)
                
                if time.time() - timestamp >= self.ttl:
                    cache_file.unlink()
                    cleaned += 1
            except Exception:
                cache_file.unlink(missing_ok=True)
                cleaned += 1
        
        if cleaned > 0:
            logger.debug(f"清理了 {cleaned} 个过期缓存文件")


# ============================================================================
# 全局缓存实例
# ============================================================================

# 默认缓存实例
default_cache = DeviceCache(ttl=60, persistent=True)


def get_cache() -> DeviceCache:
    """获取默认缓存实例"""
    return default_cache


# ============================================================================
# 便捷装饰器
# ============================================================================

def cached(ttl: int = 60, key: Optional[str] = None):
    """
    使用默认缓存的装饰器。
    
    Example:
        @cached(ttl=60)
        def detect_cameras():
            # ... 检测逻辑
            return cameras
    """
    return default_cache.cached(ttl=ttl, key=key)
