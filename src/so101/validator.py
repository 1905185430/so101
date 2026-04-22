"""
so101.validator — 配置验证器
============================

验证配置文件和场景定义的正确性。

使用示例：
    from so101.validator import ConfigValidator
    
    validator = ConfigValidator()
    
    # 验证配置文件
    is_valid, errors = validator.validate_config()
    
    # 验证场景
    is_valid, errors = validator.validate_scene('grab_redcube')
    
    # 打印验证报告
    validator.print_report()
"""

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from so101.logger import get_logger
from so101.console import console, print_table, print_error, print_warning, print_success

logger = get_logger(__name__)


class ValidationLevel(Enum):
    """验证级别"""
    ERROR = "error"      # 必须修复
    WARNING = "warning"  # 建议修复
    INFO = "info"        # 信息提示


@dataclass
class ValidationIssue:
    """验证问题"""
    level: ValidationLevel
    message: str
    path: str = ""  # 配置路径，如 "cameras.orbbec_1.serial"
    suggestion: str = ""


@dataclass
class ValidationResult:
    """验证结果"""
    valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    
    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.ERROR]
    
    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.WARNING]
    
    @property
    def infos(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.INFO]


class ConfigValidator:
    """配置验证器"""
    
    # Serial 格式正则
    SERIAL_PATTERN = re.compile(r'^[A-Fa-f0-9]{8,16}$')
    
    # by-id 路径格式
    CAMERA_BY_ID_PATTERN = re.compile(r'^/dev/v4l/by-id/')
    SERIAL_BY_ID_PATTERN = re.compile(r'^/dev/serial/by-id/')
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Args:
            config: 配置字典，None 则自动加载
        """
        if config is None:
            from so101.config import load_config
            config = load_config()
        
        self.config = config
        self.issues: List[ValidationIssue] = []
    
    def add_issue(
        self,
        level: ValidationLevel,
        message: str,
        path: str = "",
        suggestion: str = "",
    ):
        """添加验证问题"""
        issue = ValidationIssue(
            level=level,
            message=message,
            path=path,
            suggestion=suggestion,
        )
        self.issues.append(issue)
        
        if level == ValidationLevel.ERROR:
            logger.error(f"验证错误 [{path}]: {message}")
        elif level == ValidationLevel.WARNING:
            logger.warning(f"验证警告 [{path}]: {message}")
        else:
            logger.debug(f"验证信息 [{path}]: {message}")
    
    def validate_config(self) -> Tuple[bool, List[ValidationIssue]]:
        """
        验证整个配置文件。
        
        Returns:
            (是否有效, 问题列表)
        """
        self.issues = []
        
        # 验证顶级结构
        self._validate_structure()
        
        # 验证摄像头
        cameras = self.config.get('cameras', {})
        for name, cam in cameras.items():
            self._validate_camera(name, cam)
        
        # 验证机械臂
        arms = self.config.get('arms', {})
        for name, arm in arms.items():
            self._validate_arm(name, arm)
        
        # 验证场景
        scenes = self.config.get('scenes', {})
        for name, scene in scenes.items():
            self._validate_scene(name, scene)
        
        valid = len(self.errors) == 0
        return valid, self.issues
    
    def validate_scene(self, scene_name: str) -> Tuple[bool, List[ValidationIssue]]:
        """
        验证指定场景。
        
        Args:
            scene_name: 场景名称
        
        Returns:
            (是否有效, 问题列表)
        """
        self.issues = []
        
        scenes = self.config.get('scenes', {})
        if scene_name not in scenes:
            self.add_issue(
                ValidationLevel.ERROR,
                f"场景 '{scene_name}' 不存在",
                path="scenes",
                suggestion=f"可用场景: {', '.join(scenes.keys())}",
            )
            return False, self.issues
        
        scene = scenes[scene_name]
        self._validate_scene(scene_name, scene)
        
        # 验证引用的设备是否存在
        self._validate_scene_references(scene_name, scene)
        
        valid = len(self.errors) == 0
        return valid, self.issues
    
    def _validate_structure(self):
        """验证配置结构"""
        required_keys = ['cameras', 'arms', 'scenes']
        
        for key in required_keys:
            if key not in self.config:
                self.add_issue(
                    ValidationLevel.ERROR,
                    f"缺少必需的顶级键: {key}",
                    path=key,
                    suggestion=f"请添加 {key}: {{}} 到配置文件",
                )
            elif not isinstance(self.config[key], dict):
                self.add_issue(
                    ValidationLevel.ERROR,
                    f"{key} 必须是字典类型",
                    path=key,
                )
    
    def _validate_camera(self, name: str, cam: dict):
        """验证摄像头配置"""
        path_prefix = f"cameras.{name}"
        
        # 必需字段
        required = ['serial', 'by_id', 'type']
        for field in required:
            if field not in cam:
                self.add_issue(
                    ValidationLevel.ERROR,
                    f"缺少必需字段: {field}",
                    path=f"{path_prefix}.{field}",
                )
        
        # Serial 格式
        serial = cam.get('serial', '')
        if serial and not self.SERIAL_PATTERN.match(serial.split('_')[-1]):
            self.add_issue(
                ValidationLevel.WARNING,
                f"Serial 格式可能不正确: {serial}",
                path=f"{path_prefix}.serial",
                suggestion="Serial 通常是 8-16 位十六进制字符",
            )
        
        # by-id 路径
        by_id = cam.get('by_id', '')
        if by_id and not self.CAMERA_BY_ID_PATTERN.match(by_id):
            self.add_issue(
                ValidationLevel.WARNING,
                f"by-id 路径格式不标准: {by_id}",
                path=f"{path_prefix}.by_id",
                suggestion="建议使用 /dev/v4l/by-id/... 格式",
            )
        
        # 摄像头类型
        cam_type = cam.get('type', '')
        valid_types = ['orbbec', 'icspring', 'usb_cam', 'realsense']
        if cam_type and cam_type not in valid_types:
            self.add_issue(
                ValidationLevel.WARNING,
                f"未知的摄像头类型: {cam_type}",
                path=f"{path_prefix}.type",
                suggestion=f"常见类型: {', '.join(valid_types)}",
            )
        
        # 分辨率和帧率（如果指定）
        for field in ['width', 'height', 'fps']:
            value = cam.get(field)
            if value is not None:
                if not isinstance(value, (int, float)) or value <= 0:
                    self.add_issue(
                        ValidationLevel.ERROR,
                        f"{field} 必须是正数: {value}",
                        path=f"{path_prefix}.{field}",
                    )
    
    def _validate_arm(self, name: str, arm: dict):
        """验证机械臂配置"""
        path_prefix = f"arms.{name}"
        
        # 必需字段
        required = ['serial', 'port', 'role']
        for field in required:
            if field not in arm:
                self.add_issue(
                    ValidationLevel.ERROR,
                    f"缺少必需字段: {field}",
                    path=f"{path_prefix}.{field}",
                )
        
        # Serial 格式
        serial = arm.get('serial', '')
        if serial and not self.SERIAL_PATTERN.match(serial):
            self.add_issue(
                ValidationLevel.WARNING,
                f"Serial 格式可能不正确: {serial}",
                path=f"{path_prefix}.serial",
            )
        
        # 端口路径
        port = arm.get('port', '')
        if port and not self.SERIAL_BY_ID_PATTERN.match(port):
            self.add_issue(
                ValidationLevel.WARNING,
                f"端口路径格式不标准: {port}",
                path=f"{path_prefix}.port",
                suggestion="建议使用 /dev/serial/by-id/... 格式",
            )
        
        # 角色
        role = arm.get('role', '')
        valid_roles = ['follower', 'leader']
        if role and role not in valid_roles:
            self.add_issue(
                ValidationLevel.ERROR,
                f"无效的角色: {role}",
                path=f"{path_prefix}.role",
                suggestion=f"有效角色: {', '.join(valid_roles)}",
            )
    
    def _validate_scene(self, name: str, scene: dict):
        """验证场景配置"""
        path_prefix = f"scenes.{name}"
        
        # 必需字段
        if 'task' not in scene:
            self.add_issue(
                ValidationLevel.WARNING,
                "缺少任务描述",
                path=f"{path_prefix}.task",
                suggestion="建议添加 task 字段描述场景目标",
            )
        
        # 摄像头
        cameras = scene.get('cameras', {})
        if not cameras:
            self.add_issue(
                ValidationLevel.WARNING,
                "场景未配置摄像头",
                path=f"{path_prefix}.cameras",
                suggestion="至少配置一个摄像头",
            )
        
        # 验证摄像头角色
        valid_roles = ['top', 'wrist', 'front', 'side', 'overhead']
        for role in cameras.keys():
            if role not in valid_roles:
                self.add_issue(
                    ValidationLevel.INFO,
                    f"非常规摄像头角色: {role}",
                    path=f"{path_prefix}.cameras.{role}",
                    suggestion=f"常见角色: {', '.join(valid_roles)}",
                )
        
        # 机械臂引用
        for arm_type in ['follower', 'leader']:
            if arm_type not in scene:
                self.add_issue(
                    ValidationLevel.WARNING,
                    f"场景未配置 {arm_type}",
                    path=f"{path_prefix}.{arm_type}",
                )
    
    def _validate_scene_references(self, scene_name: str, scene: dict):
        """验证场景引用的设备是否存在"""
        cameras_cfg = self.config.get('cameras', {})
        arms_cfg = self.config.get('arms', {})
        
        path_prefix = f"scenes.{scene_name}"
        
        # 验证摄像头引用
        for role, cam_ref in scene.get('cameras', {}).items():
            if isinstance(cam_ref, str) and cam_ref not in cameras_cfg:
                # 可能是直接的 by-id 路径
                if not cam_ref.startswith('/dev/'):
                    self.add_issue(
                        ValidationLevel.ERROR,
                        f"摄像头引用不存在: {cam_ref}",
                        path=f"{path_prefix}.cameras.{role}",
                        suggestion=f"可用摄像头: {', '.join(cameras_cfg.keys())}",
                    )
        
        # 验证机械臂引用
        for arm_type in ['follower', 'leader']:
            arm_ref = scene.get(arm_type)
            if arm_ref is None:
                continue
            
            if isinstance(arm_ref, str) and arm_ref not in arms_cfg:
                self.add_issue(
                    ValidationLevel.ERROR,
                    f"机械臂引用不存在: {arm_ref}",
                    path=f"{path_prefix}.{arm_type}",
                    suggestion=f"可用机械臂: {', '.join(arms_cfg.keys())}",
                )
    
    # ========================================================================
    # 报告生成
    # ========================================================================
    
    @property
    def errors(self) -> List[ValidationIssue]:
        """获取错误列表"""
        return [i for i in self.issues if i.level == ValidationLevel.ERROR]
    
    def get_report(self) -> Dict:
        """生成验证报告"""
        return {
            'valid': len(self.errors) == 0,
            'total_issues': len(self.issues),
            'errors': len(self.errors),
            'warnings': len(self.warnings),
            'infos': len(self.infos),
            'issues': [
                {
                    'level': i.level.value,
                    'message': i.message,
                    'path': i.path,
                    'suggestion': i.suggestion,
                }
                for i in self.issues
            ],
        }
    
    def print_report(self):
        """打印验证报告"""
        if not self.issues:
            print_success("配置验证通过，未发现问题")
            return
        
        headers = ["级别", "位置", "问题"]
        rows = []
        
        level_styles = {
            ValidationLevel.ERROR: ("错误", "red"),
            ValidationLevel.WARNING: ("警告", "yellow"),
            ValidationLevel.INFO: ("信息", "blue"),
        }
        
        for issue in self.issues:
            level_str, color = level_styles[issue.level]
            rows.append([
                f"[{color}]{level_str}[/{color}]",
                issue.path or "-",
                issue.message,
            ])
        
        print_table("配置验证报告", headers, rows)
        
        # 显示建议
        suggestions = [i for i in self.issues if i.suggestion]
        if suggestions:
            console.print("\n修复建议:", style="bold yellow")
            for issue in suggestions:
                console.print(f"  [{issue.path or '-'}]", style="cyan")
                console.print(f"    {issue.suggestion}", style="dim")
        
        # 统计
        errors = len(self.errors)
        warnings = len(self.warnings)
        
        if errors > 0:
            console.print(f"\n✗ 发现 {errors} 个错误，必须修复", style="bold red")
        elif warnings > 0:
            console.print(f"\n⚠ 发现 {warnings} 个警告，建议修复", style="bold yellow")
        else:
            console.print(f"\n✓ 验证通过", style="bold green")


# ============================================================================
# 便捷函数
# ============================================================================

def validate_config(config: Optional[Dict] = None) -> bool:
    """
    验证配置文件。
    
    Args:
        config: 配置字典
    
    Returns:
        是否有效
    """
    validator = ConfigValidator(config)
    valid, _ = validator.validate_config()
    validator.print_report()
    return valid


def validate_scene(scene_name: str, config: Optional[Dict] = None) -> bool:
    """
    验证场景配置。
    
    Args:
        scene_name: 场景名称
        config: 配置字典
    
    Returns:
        是否有效
    """
    validator = ConfigValidator(config)
    valid, _ = validator.validate_scene(scene_name)
    validator.print_report()
    return valid
