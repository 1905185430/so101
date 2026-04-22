"""
so101.logger — 统一日志系统
==========================

提供分级日志、文件输出、彩色终端显示。

使用示例：
    from so101.logger import get_logger, setup_logging
    
    # 初始化（在 cli.py 的 main() 中调用一次）
    setup_logging(verbose=True)
    
    # 在各模块中使用
    logger = get_logger(__name__)
    logger.info("设备检测完成")
    logger.warning("摄像头未找到")
    logger.error("连接失败", exc_info=True)
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# ============================================================================
# 颜色支持
# ============================================================================

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

# 颜色映射
COLOR_MAP = {
    logging.DEBUG: Fore.CYAN if HAS_COLOR else '',
    logging.INFO: Fore.GREEN if HAS_COLOR else '',
    logging.WARNING: Fore.YELLOW if HAS_COLOR else '',
    logging.ERROR: Fore.RED if HAS_COLOR else '',
    logging.CRITICAL: Fore.RED + Style.BRIGHT if HAS_COLOR else '',
}
RESET = Style.RESET_ALL if HAS_COLOR else ''

# ============================================================================
# 自定义 Formatter
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """带颜色的控制台日志格式"""
    
    def __init__(self, verbose=False):
        if verbose:
            fmt = '%(asctime)s [%(levelname)-8s] %(name)s: %(message)s'
            datefmt = '%H:%M:%S'
        else:
            fmt = '[%(levelname)-8s] %(message)s'
            datefmt = None
        super().__init__(fmt=fmt, datefmt=datefmt)
    
    def format(self, record):
        # 添加颜色
        if HAS_COLOR:
            record.levelname = f"{COLOR_MAP.get(record.levelno, '')}{record.levelname}{RESET}"
        return super().format(record)


class FileFormatter(logging.Formatter):
    """文件日志格式（无颜色，含完整信息）"""
    
    def __init__(self):
        fmt = '%(asctime)s [%(levelname)-8s] %(name)s (%(filename)s:%(lineno)d): %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S'
        super().__init__(fmt=fmt, datefmt=datefmt)

# ============================================================================
# 全局配置
# ============================================================================

_LOG_DIR: Optional[Path] = None
_INITIALIZED = False
_VERBOSE = False
_QUIET = False


def setup_logging(
    verbose: bool = False,
    quiet: bool = False,
    log_dir: Optional[Path] = None,
    log_level: int = logging.INFO,
):
    """
    初始化全局日志系统。
    
    Args:
        verbose: 显示详细日志（DEBUG 级别）
        quiet: 安静模式（只显示 WARNING 及以上）
        log_dir: 日志文件目录，默认 ~/.so101/logs/
        log_level: 默认日志级别
    """
    global _LOG_DIR, _INITIALIZED, _VERBOSE, _QUIET
    
    _VERBOSE = verbose
    _QUIET = quiet
    
    # 确定日志级别
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = log_level
    
    # 配置根 logger
    root_logger = logging.getLogger('so101')
    root_logger.setLevel(level)
    
    # 清除已有 handlers
    root_logger.handlers.clear()
    
    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(ColoredFormatter(verbose=verbose))
    root_logger.addHandler(console_handler)
    
    # 文件 handler
    if log_dir is None:
        log_dir = Path.home() / '.so101' / 'logs'
    
    _LOG_DIR = log_dir
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # 按日期命名日志文件
    log_file = _LOG_DIR / f"so101_{datetime.now():%Y%m%d}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)  # 文件始终记录 DEBUG
    file_handler.setFormatter(FileFormatter())
    root_logger.addHandler(file_handler)
    
    _INITIALIZED = True
    
    # 记录初始化
    logger = get_logger('so101.logger')
    logger.debug(f"日志系统初始化完成，日志文件: {log_file}")


def get_logger(name: str) -> logging.Logger:
    """
    获取模块 logger。
    
    Args:
        name: 模块名称，通常用 __name__
    
    Returns:
        Logger 实例
    
    Example:
        logger = get_logger(__name__)
        logger.info("操作成功")
    """
    if not _INITIALIZED:
        # 未初始化时使用默认配置
        setup_logging()
    
    return logging.getLogger(name)


def get_log_dir() -> Path:
    """获取日志目录路径"""
    if _LOG_DIR is None:
        return Path.home() / '.so101' / 'logs'
    return _LOG_DIR


def set_level(level: int, logger_name: str = 'so101'):
    """动态调整日志级别"""
    logging.getLogger(logger_name).setLevel(level)


# ============================================================================
# 便捷函数
# ============================================================================

def debug(msg: str, *args, **kwargs):
    """快捷 debug 日志"""
    get_logger('so101').debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs):
    """快捷 info 日志"""
    get_logger('so101').info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs):
    """快捷 warning 日志"""
    get_logger('so101').warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs):
    """快捷 error 日志"""
    get_logger('so101').error(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs):
    """快捷 critical 日志"""
    get_logger('so101').critical(msg, *args, **kwargs)
