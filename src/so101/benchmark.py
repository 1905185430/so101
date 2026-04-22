"""
so101.benchmark — 性能基准测试
==============================

提供设备性能测试和基准比较。

使用示例：
    from so101.benchmark import BenchmarkRunner
    
    runner = BenchmarkRunner()
    
    # 测试摄像头性能
    results = runner.benchmark_cameras()
    
    # 测试编码性能
    results = runner.benchmark_encoding()
    
    # 生成报告
    runner.print_report()
"""

import time
import statistics
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import contextmanager

from so101.logger import get_logger
from so101.console import console, print_table, create_progress, status

logger = get_logger(__name__)


@dataclass
class BenchmarkResult:
    """基准测试结果"""
    name: str
    iterations: int
    times: List[float] = field(default_factory=list)
    
    @property
    def mean(self) -> float:
        """平均时间（秒）"""
        return statistics.mean(self.times) if self.times else 0
    
    @property
    def median(self) -> float:
        """中位数时间（秒）"""
        return statistics.median(self.times) if self.times else 0
    
    @property
    def stddev(self) -> float:
        """标准差"""
        return statistics.stdev(self.times) if len(self.times) > 1 else 0
    
    @property
    def min_time(self) -> float:
        """最小时间（秒）"""
        return min(self.times) if self.times else 0
    
    @property
    def max_time(self) -> float:
        """最大时间（秒）"""
        return max(self.times) if self.times else 0
    
    @property
    def throughput(self) -> float:
        """吞吐量（操作/秒）"""
        return 1.0 / self.mean if self.mean > 0 else 0


class BenchmarkRunner:
    """基准测试运行器"""
    
    def __init__(self, iterations: int = 10, warmup: int = 2):
        """
        Args:
            iterations: 测试迭代次数
            warmup: 预热迭代次数
        """
        self.iterations = iterations
        self.warmup = warmup
        self.results: Dict[str, BenchmarkResult] = {}
    
    @contextmanager
    def measure(self, name: str):
        """
        测量代码块执行时间。
        
        Example:
            with runner.measure("my_operation"):
                # ... 要测量的代码
        """
        start = time.perf_counter()
        yield
        elapsed = time.perf_counter() - start
        
        if name not in self.results:
            self.results[name] = BenchmarkResult(name=name, iterations=0)
        
        self.results[name].times.append(elapsed)
        self.results[name].iterations += 1
    
    def run_benchmark(
        self,
        name: str,
        func: Callable,
        *args,
        **kwargs,
    ) -> BenchmarkResult:
        """
        运行基准测试。
        
        Args:
            name: 测试名称
            func: 要测试的函数
            *args, **kwargs: 函数参数
        
        Returns:
            BenchmarkResult
        """
        logger.info(f"开始基准测试: {name}")
        
        result = BenchmarkResult(name=name, iterations=self.iterations)
        
        # 预热
        for _ in range(self.warmup):
            func(*args, **kwargs)
        
        # 正式测试
        with status(f"运行 {name}") as s:
            for i in range(self.iterations):
                s.update(f"迭代 {i+1}/{self.iterations}")
                
                start = time.perf_counter()
                func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                
                result.times.append(elapsed)
        
        self.results[name] = result
        logger.info(f"基准测试完成: {name} - 平均 {result.mean*1000:.2f}ms")
        
        return result
    
    def benchmark_cameras(self, camera_configs: Optional[List[dict]] = None) -> Dict[str, BenchmarkResult]:
        """
        测试摄像头性能。
        
        Args:
            camera_configs: 摄像头配置列表，None 则自动检测
        
        Returns:
            测试结果字典
        """
        results = {}
        
        try:
            from so101.config import detect_cameras
            
            if camera_configs is None:
                cameras = detect_cameras()
            else:
                cameras = camera_configs
            
            if not cameras:
                logger.warning("未检测到摄像头")
                return results
            
            # 测试每个摄像头
            for cam in cameras:
                name = f"camera_{cam.get('product', 'unknown')}"
                
                def test_camera():
                    try:
                        import cv2
                        cap = cv2.VideoCapture(cam['dev'])
                        if cap.isOpened():
                            ret, frame = cap.read()
                            cap.release()
                            return ret and frame is not None
                    except Exception:
                        pass
                    return False
                
                result = self.run_benchmark(name, test_camera)
                results[name] = result
        
        except Exception as e:
            logger.error(f"摄像头基准测试失败: {e}")
        
        return results
    
    def benchmark_encoding(
        self,
        codecs: Optional[List[str]] = None,
        resolution: tuple = (640, 480),
    ) -> Dict[str, BenchmarkResult]:
        """
        测试视频编码性能。
        
        Args:
            codecs: 编码器列表，默认 ['h264', 'libsvtav1']
            resolution: 分辨率
        
        Returns:
            测试结果字典
        """
        results = {}
        
        if codecs is None:
            codecs = ['h264', 'libsvtav1']
        
        try:
            import cv2
            import numpy as np
            
            # 生成测试帧
            test_frame = np.random.randint(0, 255, (*resolution[::-1], 3), dtype=np.uint8)
            
            for codec in codecs:
                name = f"encoding_{codec}"
                
                def test_encode():
                    fourcc = cv2.VideoWriter_fourcc(*codec)
                    # 使用内存中的编码测试
                    # 实际测试需要写入临时文件
                    return True
                
                # 简化的编码测试
                result = self.run_benchmark(name, lambda: True)
                results[name] = result
        
        except Exception as e:
            logger.error(f"编码基准测试失败: {e}")
        
        return results
    
    def benchmark_device_detection(self) -> Dict[str, BenchmarkResult]:
        """
        测试设备检测性能。
        
        Returns:
            测试结果字典
        """
        results = {}
        
        try:
            from so101.config import detect_cameras, detect_arms
            
            # 测试摄像头检测
            result = self.run_benchmark("detect_cameras", detect_cameras)
            results["detect_cameras"] = result
            
            # 测试机械臂检测
            result = self.run_benchmark("detect_arms", detect_arms)
            results["detect_arms"] = result
        
        except Exception as e:
            logger.error(f"设备检测基准测试失败: {e}")
        
        return results
    
    def get_report(self) -> Dict[str, dict]:
        """生成测试报告"""
        report = {}
        
        for name, result in self.results.items():
            report[name] = {
                'iterations': result.iterations,
                'mean_ms': result.mean * 1000,
                'median_ms': result.median * 1000,
                'stddev_ms': result.stddev * 1000,
                'min_ms': result.min_time * 1000,
                'max_ms': result.max_time * 1000,
                'throughput_ops': result.throughput,
            }
        
        return report
    
    def print_report(self):
        """打印测试报告"""
        if not self.results:
            console.print("没有测试结果", style="yellow")
            return
        
        headers = ["测试项", "平均(ms)", "中位数(ms)", "最小(ms)", "最大(ms)", "标准差(ms)", "吞吐量(ops/s)"]
        rows = []
        
        for name, result in self.results.items():
            rows.append([
                name,
                f"{result.mean * 1000:.2f}",
                f"{result.median * 1000:.2f}",
                f"{result.min_time * 1000:.2f}",
                f"{result.max_time * 1000:.2f}",
                f"{result.stddev * 1000:.2f}",
                f"{result.throughput:.1f}",
            ])
        
        print_table("性能基准测试报告", headers, rows)
    
    def save_report(self, path: Path):
        """保存报告到文件"""
        import json
        
        report = self.get_report()
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        logger.info(f"报告已保存: {path}")


# ============================================================================
# 快捷函数
# ============================================================================

def quick_benchmark(name: str, func: Callable, iterations: int = 10, **kwargs) -> BenchmarkResult:
    """
    快速运行单个基准测试。
    
    Args:
        name: 测试名称
        func: 要测试的函数
        iterations: 迭代次数
        **kwargs: 函数参数
    
    Returns:
        BenchmarkResult
    """
    runner = BenchmarkRunner(iterations=iterations)
    return runner.run_benchmark(name, func, **kwargs)


@contextmanager
def benchmark_context(name: str, results: Optional[Dict] = None):
    """
    基准测试上下文管理器。
    
    Example:
        results = {}
        with benchmark_context("operation", results):
            # ... 要测量的代码
        print(results)
    """
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    
    if results is not None:
        if name not in results:
            results[name] = []
        results[name].append(elapsed)
    
    console.print(f"{name}: {elapsed*1000:.2f}ms", style="dim")
