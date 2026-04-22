"""
so101.doctor — 环境诊断工具
============================

检查 SO-101 运行环境，诊断常见问题。

使用示例：
    from so101.doctor import Doctor
    
    doctor = Doctor()
    doctor.run_all_checks()
    doctor.print_report()
    doctor.fix_permissions()  # 尝试自动修复
"""

import os
import sys
import subprocess
import platform
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

from so101.logger import get_logger
from so101.console import (
    console,
    print_success,
    print_error,
    print_warning,
    print_info,
    print_table,
    status,
)

logger = get_logger(__name__)


class CheckStatus(Enum):
    """检查状态"""
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIP = "skip"


@dataclass
class CheckResult:
    """检查结果"""
    name: str
    status: CheckStatus
    message: str
    details: str = ""
    fix_command: str = ""


class Doctor:
    """环境诊断器"""
    
    def __init__(self, quick: bool = False):
        """
        Args:
            quick: 快速模式，跳过耗时检查
        """
        self.quick = quick
        self.results: List[CheckResult] = []
        self._fix_commands: List[str] = []
    
    def add_result(
        self,
        name: str,
        status: CheckStatus,
        message: str,
        details: str = "",
        fix_command: str = "",
    ):
        """添加检查结果"""
        result = CheckResult(
            name=name,
            status=status,
            message=message,
            details=details,
            fix_command=fix_command,
        )
        self.results.append(result)
        
        if fix_command:
            self._fix_commands.append(fix_command)
        
        # 日志
        if status == CheckStatus.OK:
            logger.debug(f"✓ {name}: {message}")
        elif status == CheckStatus.WARNING:
            logger.warning(f"⚠ {name}: {message}")
        elif status == CheckStatus.ERROR:
            logger.error(f"✗ {name}: {message}")
    
    # ========================================================================
    # 检查项
    # ========================================================================
    
    def check_python_version(self):
        """检查 Python 版本"""
        version = sys.version_info
        version_str = f"{version.major}.{version.minor}.{version.micro}"
        
        if version >= (3, 10):
            self.add_result(
                "Python 版本",
                CheckStatus.OK,
                f"Python {version_str} (>= 3.10)",
            )
        elif version >= (3, 8):
            self.add_result(
                "Python 版本",
                CheckStatus.WARNING,
                f"Python {version_str} (建议 >= 3.10)",
                details="某些功能可能不兼容",
            )
        else:
            self.add_result(
                "Python 版本",
                CheckStatus.ERROR,
                f"Python {version_str} (需要 >= 3.8)",
                fix_command="请升级 Python 到 3.10 或更高版本",
            )
    
    def check_dependencies(self):
        """检查核心依赖"""
        required = {
            'yaml': 'pyyaml',
            'cv2': 'opencv-python',
        }
        
        optional = {
            'lerobot': 'lerobot',
            'torch': 'torch',
            'transformers': 'transformers',
        }
        
        missing_required = []
        missing_optional = []
        
        for module, package in required.items():
            try:
                __import__(module)
            except ImportError:
                missing_required.append(package)
        
        for module, package in optional.items():
            try:
                __import__(module)
            except ImportError:
                missing_optional.append(package)
        
        if not missing_required:
            self.add_result(
                "核心依赖",
                CheckStatus.OK,
                "pyyaml, opencv-python 已安装",
            )
        else:
            self.add_result(
                "核心依赖",
                CheckStatus.ERROR,
                f"缺少: {', '.join(missing_required)}",
                fix_command=f"pip install {' '.join(missing_required)}",
            )
        
        if not missing_optional:
            self.add_result(
                "可选依赖",
                CheckStatus.OK,
                "lerobot, torch, transformers 已安装",
            )
        elif missing_optional:
            self.add_result(
                "可选依赖",
                CheckStatus.WARNING,
                f"未安装: {', '.join(missing_optional)}",
                details="录制/部署功能需要这些依赖",
                fix_command=f"pip install {' '.join(missing_optional)}",
            )
    
    def check_permissions(self):
        """检查串口权限"""
        serial_dir = Path('/dev/serial/by-id')
        
        if not serial_dir.exists():
            self.add_result(
                "串口权限",
                CheckStatus.WARNING,
                "未检测到串口设备",
            )
            return
        
        # 检查用户是否在 dialout 组
        try:
            groups = subprocess.check_output(['groups'], text=True).strip()
            if 'dialout' in groups:
                self.add_result(
                    "串口权限 (组)",
                    CheckStatus.OK,
                    "用户在 dialout 组中",
                )
            else:
                self.add_result(
                    "串口权限 (组)",
                    CheckStatus.ERROR,
                    "用户不在 dialout 组",
                    details="可能导致串口访问被拒绝",
                    fix_command="sudo usermod -aG dialout $USER && newgrp dialout",
                )
        except Exception as e:
            self.add_result(
                "串口权限 (组)",
                CheckStatus.WARNING,
                f"无法检查组权限: {e}",
            )
        
        # 检查串口设备权限
        inaccessible = []
        for device in serial_dir.iterdir():
            if not os.access(str(device), os.R_OK | os.W_OK):
                inaccessible.append(str(device))
        
        if not inaccessible:
            self.add_result(
                "串口权限 (设备)",
                CheckStatus.OK,
                "所有串口设备可访问",
            )
        else:
            self.add_result(
                "串口权限 (设备)",
                CheckStatus.ERROR,
                f"{len(inaccessible)} 个设备不可访问",
                details='\n'.join(inaccessible[:3]),
                fix_command="sudo chmod 666 /dev/ttyACM*",
            )
    
    def check_cameras(self):
        """检查摄像头"""
        try:
            from so101.config import detect_cameras
            
            cameras = detect_cameras()
            
            if cameras:
                self.add_result(
                    "摄像头",
                    CheckStatus.OK,
                    f"检测到 {len(cameras)} 个彩色摄像头",
                    details='\n'.join([
                        f"  {c['dev']}: {c['product']}"
                        for c in cameras[:5]
                    ]),
                )
            else:
                self.add_result(
                    "摄像头",
                    CheckStatus.WARNING,
                    "未检测到彩色摄像头",
                    details="请检查摄像头连接",
                )
        except Exception as e:
            self.add_result(
                "摄像头",
                CheckStatus.ERROR,
                f"检测失败: {e}",
            )
    
    def check_arms(self):
        """检查机械臂"""
        try:
            from so101.config import detect_arms
            
            arms = detect_arms()
            
            if arms:
                self.add_result(
                    "机械臂",
                    CheckStatus.OK,
                    f"检测到 {len(arms)} 个 SO-101 机械臂",
                    details='\n'.join([
                        f"  Serial {a['serial']}: {a['port']}"
                        for a in arms[:4]
                    ]),
                )
            else:
                self.add_result(
                    "机械臂",
                    CheckStatus.WARNING,
                    "未检测到 SO-101 机械臂",
                    details="请检查 USB 连接",
                )
        except Exception as e:
            self.add_result(
                "机械臂",
                CheckStatus.ERROR,
                f"检测失败: {e}",
            )
    
    def check_config(self):
        """检查配置文件"""
        try:
            from so101.config import CONFIG_FILE, load_config
            
            if not CONFIG_FILE.exists():
                self.add_result(
                    "配置文件",
                    CheckStatus.WARNING,
                    f"配置文件不存在: {CONFIG_FILE}",
                    details="运行 'so101 scan' 创建配置",
                    fix_command="so101 scan --all",
                )
                return
            
            cfg = load_config()
            
            cameras_count = len(cfg.get('cameras', {}))
            arms_count = len(cfg.get('arms', {}))
            scenes_count = len(cfg.get('scenes', {}))
            
            if cameras_count > 0 or arms_count > 0:
                self.add_result(
                    "配置文件",
                    CheckStatus.OK,
                    f"配置有效 ({cameras_count} 摄像头, {arms_count} 机械臂, {scenes_count} 场景)",
                )
            else:
                self.add_result(
                    "配置文件",
                    CheckStatus.WARNING,
                    "配置文件存在但无设备定义",
                    fix_command="so101 scan --all",
                )
        except Exception as e:
            self.add_result(
                "配置文件",
                CheckStatus.ERROR,
                f"配置加载失败: {e}",
                fix_command="so101 scan --all",
            )
    
    def check_disk_space(self):
        """检查磁盘空间"""
        try:
            import shutil
            
            # 检查用户主目录
            stat = shutil.disk_usage(Path.home())
            free_gb = stat.free / (1024**3)
            
            # 估算可录制的 episode 数量
            # 假设每个 episode 约 200MB
            estimated_episodes = int(free_gb / 0.2)
            
            if free_gb >= 50:
                self.add_result(
                    "磁盘空间",
                    CheckStatus.OK,
                    f"剩余 {free_gb:.1f}GB (~{estimated_episodes} episodes)",
                )
            elif free_gb >= 10:
                self.add_result(
                    "磁盘空间",
                    CheckStatus.WARNING,
                    f"剩余 {free_gb:.1f}GB (~{estimated_episodes} episodes)",
                    details="建议清理空间",
                )
            else:
                self.add_result(
                    "磁盘空间",
                    CheckStatus.ERROR,
                    f"剩余 {free_gb:.1f}GB (不足)",
                    details="磁盘空间严重不足",
                )
        except Exception as e:
            self.add_result(
                "磁盘空间",
                CheckStatus.WARNING,
                f"无法检查: {e}",
            )
    
    def check_ffmpeg(self):
        """检查 ffmpeg"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                capture_output=True,
                text=True,
            )
            
            if result.returncode == 0:
                version_line = result.stdout.split('\n')[0]
                self.add_result(
                    "ffmpeg",
                    CheckStatus.OK,
                    version_line,
                )
            else:
                self.add_result(
                    "ffmpeg",
                    CheckStatus.ERROR,
                    "ffmpeg 命令执行失败",
                    fix_command="sudo apt install ffmpeg",
                )
        except FileNotFoundError:
            self.add_result(
                "ffmpeg",
                CheckStatus.ERROR,
                "ffmpeg 未安装",
                details="视频编码需要 ffmpeg",
                fix_command="sudo apt install ffmpeg",
            )
    
    def check_v4l2_utils(self):
        """检查 v4l2-ctl"""
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--version'],
                capture_output=True,
                text=True,
            )
            
            if result.returncode == 0:
                self.add_result(
                    "v4l2-ctl",
                    CheckStatus.OK,
                    "v4l-utils 已安装",
                )
            else:
                self.add_result(
                    "v4l2-ctl",
                    CheckStatus.WARNING,
                    "v4l2-ctl 命令执行失败",
                    fix_command="sudo apt install v4l-utils",
                )
        except FileNotFoundError:
            self.add_result(
                "v4l2-ctl",
                CheckStatus.WARNING,
                "v4l2-ctl 未安装",
                details="摄像头检测可能不准确",
                fix_command="sudo apt install v4l-utils",
            )
    
    def check_system_load(self):
        """检查系统负载"""
        try:
            import psutil
            
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            
            issues = []
            
            if cpu_percent > 80:
                issues.append(f"CPU 使用率 {cpu_percent:.0f}%")
            
            if memory.percent > 90:
                issues.append(f"内存使用率 {memory.percent:.0f}%")
            
            if issues:
                self.add_result(
                    "系统负载",
                    CheckStatus.WARNING,
                    "系统负载较高",
                    details='\n'.join(issues),
                )
            else:
                self.add_result(
                    "系统负载",
                    CheckStatus.OK,
                    f"CPU {cpu_percent:.0f}%, 内存 {memory.percent:.0f}%",
                )
        except ImportError:
            # psutil 不可用，跳过
            pass
        except Exception as e:
            logger.debug(f"系统负载检查失败: {e}")
    
    def check_desktop_environment(self):
        """检查桌面环境"""
        try:
            de = os.environ.get('XDG_CURRENT_DESKTOP', '')
            de_session = os.environ.get('DESKTOP_SESSION', '')
            
            if 'KDE' in de or 'kde' in de_session.lower():
                # 检查 KDE 服务
                kde_services = ['baloo_file_extractor', 'kdeconnectd', 'plasmashell']
                running = []
                
                for svc in kde_services:
                    result = subprocess.run(
                        ['pgrep', '-f', svc],
                        capture_output=True,
                    )
                    if result.returncode == 0:
                        running.append(svc)
                
                if running:
                    self.add_result(
                        "桌面环境 (KDE)",
                        CheckStatus.WARNING,
                        f"检测到 KDE Plasma",
                        details=f"运行中的服务: {', '.join(running)}\n"
                                "这些服务可能影响串口通信实时性",
                    )
                else:
                    self.add_result(
                        "桌面环境",
                        CheckStatus.OK,
                        f"{de or de_session}",
                    )
            else:
                self.add_result(
                    "桌面环境",
                    CheckStatus.OK,
                    f"{de or de_session or '未知'}",
                )
        except Exception as e:
            logger.debug(f"桌面环境检查失败: {e}")
    
    # ========================================================================
    # 运行检查
    # ========================================================================
    
    def run_all_checks(self):
        """运行所有检查"""
        self.results = []
        
        checks = [
            ("Python", self.check_python_version),
            ("依赖", self.check_dependencies),
            ("配置", self.check_config),
            ("权限", self.check_permissions),
            ("摄像头", self.check_cameras),
            ("机械臂", self.check_arms),
            ("磁盘", self.check_disk_space),
            ("ffmpeg", self.check_ffmpeg),
            ("v4l2-ctl", self.check_v4l2_utils),
        ]
        
        if not self.quick:
            checks.extend([
                ("系统负载", self.check_system_load),
                ("桌面环境", self.check_desktop_environment),
            ])
        
        with status("运行环境诊断") as s:
            for name, check_func in checks:
                s.update(f"检查 {name}...")
                try:
                    check_func()
                except Exception as e:
                    logger.error(f"检查 {name} 失败: {e}")
                    self.add_result(
                        name,
                        CheckStatus.ERROR,
                        f"检查异常: {e}",
                    )
    
    def print_report(self):
        """打印诊断报告"""
        if not self.results:
            console.print("没有诊断结果", style="yellow")
            return
        
        headers = ["检查项", "状态", "结果"]
        rows = []
        
        status_symbols = {
            CheckStatus.OK: ("✓", "green"),
            CheckStatus.WARNING: ("⚠", "yellow"),
            CheckStatus.ERROR: ("✗", "red"),
            CheckStatus.SKIP: ("⊘", "dim"),
        }
        
        for result in self.results:
            symbol, color = status_symbols[result.status]
            rows.append([
                result.name,
                f"[{color}]{symbol}[/{color}]",
                result.message,
            ])
        
        print_table("环境诊断报告", headers, rows)
        
        # 显示详细信息
        for result in self.results:
            if result.details:
                console.print(f"\n{result.name}:", style="bold")
                console.print(f"  {result.details}", style="dim")
        
        # 显示修复建议
        if self._fix_commands:
            console.print("\n建议的修复命令:", style="bold yellow")
            for cmd in self._fix_commands:
                console.print(f"  {cmd}", style="cyan")
    
    def get_summary(self) -> Tuple[int, int, int]:
        """获取统计摘要"""
        ok = sum(1 for r in self.results if r.status == CheckStatus.OK)
        warning = sum(1 for r in self.results if r.status == CheckStatus.WARNING)
        error = sum(1 for r in self.results if r.status == CheckStatus.ERROR)
        return ok, warning, error
    
    def fix_permissions(self):
        """尝试自动修复权限问题"""
        import getpass
        
        username = getpass.getuser()
        
        commands = [
            f"sudo usermod -aG dialout {username}",
            "sudo chmod 666 /dev/ttyACM* 2>/dev/null || true",
        ]
        
        console.print("尝试修复权限...", style="bold")
        
        for cmd in commands:
            console.print(f"  执行: {cmd}", style="cyan")
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    print_success(f"成功: {cmd}")
                else:
                    print_error(f"失败: {result.stderr}")
            except Exception as e:
                print_error(f"执行异常: {e}")
        
        console.print("\n注意: 需要注销并重新登录使组权限生效", style="yellow")


# ============================================================================
# 便捷函数
# ============================================================================

def run_doctor(quick: bool = False, fix: bool = False):
    """
    运行环境诊断。
    
    Args:
        quick: 快速模式
        fix: 尝试自动修复
    """
    doctor = Doctor(quick=quick)
    doctor.run_all_checks()
    doctor.print_report()
    
    ok, warning, error = doctor.get_summary()
    
    console.print(f"\n总计: {ok} 正常, {warning} 警告, {error} 错误", style="bold")
    
    if fix and error > 0:
        console.print("\n是否尝试自动修复? (需要 sudo 权限)", style="yellow")
        try:
            response = input("继续? [y/N] ").strip().lower()
            if response in ('y', 'yes'):
                doctor.fix_permissions()
        except (KeyboardInterrupt, EOFError):
            console.print("\n已取消", style="dim")
    
    return error == 0
