"""
so101.exceptions — 自定义异常类
===============================

提供结构化的错误信息，包含问题描述和解决建议。

使用示例：
    from so101.exceptions import DeviceNotFoundError, ConfigError
    
    # 抛出异常
    raise DeviceNotFoundError(
        "摄像头 orbbec_1 未找到",
        suggestion="请检查摄像头是否连接，或运行 'so101 scan' 重新检测"
    )
    
    # 捕获并显示
    try:
        # ... 某些操作
    except SO101Error as e:
        print(f"错误: {e}")
        if e.suggestion:
            print(f"建议: {e.suggestion}")
"""

from typing import Optional


class SO101Error(Exception):
    """
    SO-101 基础异常类。
    
    所有 SO-101 相关异常都继承自此类。
    """
    
    def __init__(self, message: str, suggestion: Optional[str] = None):
        """
        Args:
            message: 错误描述
            suggestion: 解决建议（可选）
        """
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
    
    def __str__(self):
        return self.message
    
    def format_error(self) -> str:
        """格式化错误信息，包含建议"""
        lines = [f"错误: {self.message}"]
        if self.suggestion:
            lines.append(f"建议: {self.suggestion}")
        return '\n'.join(lines)


# ============================================================================
# 设备相关异常
# ============================================================================

class DeviceNotFoundError(SO101Error):
    """设备未找到（摄像头、机械臂等）"""
    pass


class CameraNotFoundError(DeviceNotFoundError):
    """摄像头未找到"""
    
    def __init__(self, camera_name: str, serial: str = ""):
        msg = f"摄像头 '{camera_name}' 未找到"
        if serial:
            msg += f" (serial: {serial})"
        
        suggestion = "请检查：\n"
        suggestion += "  1. 摄像头是否正确连接\n"
        suggestion += "  2. 运行 'so101 scan' 重新检测设备\n"
        suggestion += "  3. 检查 config/camera_config.yaml 中的配置"
        
        super().__init__(msg, suggestion)
        self.camera_name = camera_name
        self.serial = serial


class ArmNotFoundError(DeviceNotFoundError):
    """机械臂未找到"""
    
    def __init__(self, arm_name: str, port: str = ""):
        msg = f"机械臂 '{arm_name}' 未找到"
        if port:
            msg += f" (端口: {port})"
        
        suggestion = "请检查：\n"
        suggestion += "  1. 机械臂 USB 是否正确连接\n"
        suggestion += "  2. 串口权限：sudo usermod -aG dialout $USER\n"
        suggestion += "  3. 运行 'so101 scan' 重新检测设备"
        
        super().__init__(msg, suggestion)
        self.arm_name = arm_name
        self.port = port


class ConnectionError(SO101Error):
    """设备连接失败"""
    
    def __init__(self, device_type: str, device_id: str, reason: str = ""):
        msg = f"{device_type} '{device_id}' 连接失败"
        if reason:
            msg += f": {reason}"
        
        suggestion = "请尝试：\n"
        suggestion += "  1. 拔插 USB 线缆\n"
        suggestion += "  2. 检查设备是否被其他程序占用\n"
        suggestion += "  3. 重启设备"
        
        super().__init__(msg, suggestion)


# ============================================================================
# 配置相关异常
# ============================================================================

class ConfigError(SO101Error):
    """配置错误"""
    pass


class ConfigNotFoundError(ConfigError):
    """配置文件不存在"""
    
    def __init__(self, config_path: str):
        msg = f"配置文件不存在: {config_path}"
        suggestion = "请运行 'so101 scan' 生成初始配置"
        super().__init__(msg, suggestion)
        self.config_path = config_path


class ConfigParseError(ConfigError):
    """配置文件解析失败"""
    
    def __init__(self, config_path: str, reason: str):
        msg = f"配置文件解析失败: {config_path}\n原因: {reason}"
        suggestion = "请检查 YAML 语法是否正确"
        super().__init__(msg, suggestion)
        self.config_path = config_path


class SceneNotFoundError(ConfigError):
    """场景未定义"""
    
    def __init__(self, scene_name: str, available_scenes: list = None):
        msg = f"场景 '{scene_name}' 未定义"
        
        suggestion = ""
        if available_scenes:
            suggestion = f"可用场景: {', '.join(available_scenes)}"
        else:
            suggestion = "请运行 'so101 list --scenes' 查看可用场景"
        
        super().__init__(msg, suggestion)
        self.scene_name = scene_name


# ============================================================================
# 录制相关异常
# ============================================================================

class RecordError(SO101Error):
    """录制错误"""
    pass


class DatasetError(RecordError):
    """数据集操作失败"""
    pass


class DatasetCorruptedError(DatasetError):
    """数据集损坏"""
    
    def __init__(self, dataset_path: str):
        msg = f"数据集损坏: {dataset_path}"
        suggestion = "Parquet 文件 footer 可能不完整。建议：\n"
        suggestion += "  1. 检查是否有备份\n"
        suggestion += "  2. 使用 'so101 dataset repair' 尝试修复\n"
        suggestion += "  3. 重新录制数据"
        super().__init__(msg, suggestion)
        self.dataset_path = dataset_path


class EncodingError(RecordError):
    """视频编码失败"""
    
    def __init__(self, codec: str, reason: str = ""):
        msg = f"视频编码失败 (codec: {codec})"
        if reason:
            msg += f": {reason}"
        
        suggestion = "请尝试：\n"
        suggestion += "  1. 使用 h264 编码器 (--vcodec h264)\n"
        suggestion += "  2. 降低分辨率或帧率\n"
        suggestion += "  3. 检查 ffmpeg 是否正确安装"
        
        super().__init__(msg, suggestion)
        self.codec = codec


# ============================================================================
# 部署相关异常
# ============================================================================

class DeployError(SO101Error):
    """部署错误"""
    pass


class ModelLoadError(DeployError):
    """模型加载失败"""
    
    def __init__(self, model_path: str, reason: str = ""):
        msg = f"模型加载失败: {model_path}"
        if reason:
            msg += f"\n原因: {reason}"
        
        suggestion = "请检查：\n"
        suggestion += "  1. 模型路径是否正确\n"
        suggestion += "  2. 模型文件是否完整\n"
        suggestion += "  3. 是否有足够的 GPU 内存"
        
        super().__init__(msg, suggestion)
        self.model_path = model_path


class InferenceError(DeployError):
    """推理失败"""
    pass


# ============================================================================
# 权限相关异常
# ============================================================================

class PermissionError(SO101Error):
    """权限不足"""
    
    def __init__(self, resource: str, required_permission: str = ""):
        msg = f"权限不足: {resource}"
        
        suggestion = ""
        if '/dev/ttyACM' in resource or '/dev/serial' in resource:
            suggestion = "串口权限修复：sudo usermod -aG dialout $USER\n"
            suggestion += "然后注销并重新登录"
        elif '/dev/video' in resource:
            suggestion = "摄像头权限修复：sudo usermod -aG video $USER"
        else:
            suggestion = f"请确保有 {required_permission or '适当'} 的权限"
        
        super().__init__(msg, suggestion)
        self.resource = resource


# ============================================================================
# 工具函数
# ============================================================================

def format_exception(e: Exception) -> str:
    """
    格式化异常为用户友好的错误信息。
    
    Args:
        e: 异常对象
    
    Returns:
        格式化的错误字符串
    """
    if isinstance(e, SO101Error):
        return e.format_error()
    else:
        return f"错误: {e}"


def handle_error(e: Exception, exit_code: int = 1):
    """
    处理异常并退出程序。
    
    Args:
        e: 异常对象
        exit_code: 退出码
    """
    import sys
    from so101.logger import get_logger
    
    logger = get_logger('so101')
    
    if isinstance(e, SO101Error):
        logger.error(e.message)
        if e.suggestion:
            logger.info(e.suggestion)
    else:
        logger.error(f"未预期的错误: {e}", exc_info=True)
    
    sys.exit(exit_code)
