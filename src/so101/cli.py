"""
so101.cli — 命令行入口 (v0.3.0 增强版)
=======================================

用法：
    so101 --help
    so101 scan          探测并注册设备
    so101 list          列出系统当前设备（只读）
    so101 check         采前健康检查
    so101 calibrate     校准机械臂
    so101 teleop        遥操作
    so101 record        数据录制
    so101 capture       摄像头画面采集
    so101 dataset       数据集管理（ls/info/push/merge）
    so101 doctor        环境诊断
    so101 validate      配置验证
    so101 benchmark     性能测试
"""

import argparse
import sys
from pathlib import Path

from so101.logger import setup_logging, get_logger
from so101.console import console, print_error, print_info


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器"""
    parser = argparse.ArgumentParser(
        prog="so101",
        description="SO-101 机器人臂快速部署工具链 v0.3.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  so101 scan --all                    # 探测所有设备
  so101 check -s grab_redcube         # 检查场景就绪状态
  so101 record -s grab_redcube -n 50  # 录制 50 个 episodes
  so101 doctor                        # 环境诊断
  so101 validate -s grab_redcube      # 验证场景配置
        """,
    )
    
    # 全局参数
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="详细输出（DEBUG 级别）",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="安静模式（只显示警告和错误）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="so101 0.3.0",
    )
    
    sub = parser.add_subparsers(dest="command", required=False)
    
    # ---- so101 scan ----
    p_scan = sub.add_parser("scan", help="探测并注册设备到配置")
    p_scan.add_argument("--cameras", action="store_true", help="仅扫描摄像头")
    p_scan.add_argument("--arms", action="store_true", help="仅扫描机械臂")
    p_scan.add_argument("--all", action="store_true", help="扫描所有设备（默认）")
    
    # ---- so101 list ----
    p_list = sub.add_parser("list", help="列出系统当前设备（只读）")
    p_list.add_argument("--cameras", action="store_true", help="只列出摄像头")
    p_list.add_argument("--arms", action="store_true", help="只列出机械臂")
    p_list.add_argument("--scenes", action="store_true", help="只列出场景")
    
    # ---- so101 check ----
    p_check = sub.add_parser("check", help="采前健康检查")
    p_check.add_argument("--scene", "-s", type=str, default="", help="检查指定场景")
    
    # ---- so101 calibrate ----
    p_cal = sub.add_parser("calibrate", help="校准机械臂")
    p_cal.add_argument(
        "--arm", type=str, default="follower_left",
        choices=["follower_left", "follower_right", "leader_left", "leader_right"],
        help="指定要校准的臂（默认: follower_left）",
    )
    
    # ---- so101 teleop ----
    p_tele = sub.add_parser("teleop", help="遥操作")
    p_tele.add_argument(
        "--mode", type=str, default="1to1",
        choices=["1to1", "1toN", "dual"],
        help="遥操作模式（默认: 1to1）",
    )
    p_tele.add_argument(
        "--arm", type=str, default="left",
        choices=["left", "right"],
        help="1to1 模式下使用的臂（默认: left）",
    )
    p_tele.add_argument("--no-cam", action="store_true", help="禁用摄像头")
    
    # ---- so101 record ----
    p_rec = sub.add_parser("record", help="数据录制")
    p_rec.add_argument("--scene", "-s", type=str, required=True, help="场景名")
    p_rec.add_argument("--episodes", "-n", type=int, default=50, help="采集 episode 总数（默认: 50）")
    p_rec.add_argument("--episode-time", type=int, default=60, help="每个 episode 最长录制秒数（默认: 60）")
    p_rec.add_argument("--overwrite", action="store_true", help="覆盖已有数据集")
    p_rec.add_argument("--resume", action="store_true", help="追加到已有数据集")
    p_rec.add_argument("--name", type=str, default="", help="HuggingFace 仓库名（仅名称）")
    p_rec.add_argument("--dataset-repo-id", type=str, default="", help="完整的 HuggingFace repo_id")
    p_rec.add_argument(
        "--vcodec", type=str, default="h264",
        choices=["h264", "hevc", "libsvtav1", "auto"],
        help="视频编码器（默认: h264）",
    )
    
    # ---- so101 capture ----
    p_cap = sub.add_parser("capture", help="采集摄像头画面")
    p_cap.add_argument("--role", type=str, default="", help="按角色采集（需配置 role）")
    p_cap.add_argument("--filter", type=str, default="", help="按关键词过滤摄像头")
    p_cap.add_argument("--output", type=str, default="outputs", help="输出目录（默认: outputs）")
    
    # ---- so101 dataset ----
    from so101.dataset import build_parser as _build_dataset_parser
    _build_dataset_parser(sub)
    
    # ---- so101 deploy ----
    p_deploy = sub.add_parser("deploy", help="模型推理部署 v2（场景驱动，支持多策略）")
    p_deploy.add_argument("--policy", "-p", required=True, help="策略模型路径（HuggingFace repo 或本地目录）")
    p_deploy.add_argument("--dataset", "-d", required=True, help="训练数据集 repo_id（用于 metadata）")
    p_deploy.add_argument(
        "--policy_type", default="act",
        choices=["act", "diffusion", "smolvla", "pi0", "pi0_fast"],
        help="策略类型（默认: act）",
    )
    p_deploy.add_argument("--scene", "-s", type=str, required=True, help="场景名")
    p_deploy.add_argument("--episodes", "-n", type=int, default=10, help="推理 episode 数（默认: 10）")
    p_deploy.add_argument("--no-cam", action="store_true", help="禁用摄像头显示")
    p_deploy.add_argument("--smooth", type=float, default=1.0, help="动作平滑系数（默认: 1.0）")
    p_deploy.add_argument("--delta_threshold", type=float, default=50.0, help="跳变检测阈值（默认: 50.0）")
    p_deploy.add_argument("--home", action="store_true", default=True, help="推理前归位（默认: True）")
    p_deploy.add_argument("--no-home", action="store_false", dest="home", help="跳过归位")
    
    # ---- so101 doctor (新) ----
    p_doctor = sub.add_parser("doctor", help="环境诊断（检查依赖、权限、设备等）")
    p_doctor.add_argument("--quick", action="store_true", help="快速模式（跳过耗时检查）")
    p_doctor.add_argument("--fix", action="store_true", help="尝试自动修复问题")
    
    # ---- so101 validate (新) ----
    p_validate = sub.add_parser("validate", help="验证配置文件")
    p_validate.add_argument("--scene", "-s", type=str, default="", help="验证指定场景")
    p_validate.add_argument("--config", type=str, default="", help="指定配置文件路径")
    
    # ---- so101 benchmark (新) ----
    p_bench = sub.add_parser("benchmark", help="性能基准测试")
    p_bench.add_argument("--cameras", action="store_true", help="测试摄像头性能")
    p_bench.add_argument("--encoding", action="store_true", help="测试编码性能")
    p_bench.add_argument("--detection", action="store_true", help="测试设备检测性能")
    p_bench.add_argument("--all", action="store_true", help="运行所有测试")
    p_bench.add_argument("--iterations", "-n", type=int, default=10, help="测试迭代次数（默认: 10）")
    p_bench.add_argument("--output", "-o", type=str, default="", help="输出报告路径（JSON）")
    
    return parser


def main():
    """CLI 主入口"""
    parser = _build_parser()
    args = parser.parse_args()
    
    # 显示帮助
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    # 初始化日志
    setup_logging(
        verbose=getattr(args, 'verbose', False),
        quiet=getattr(args, 'quiet', False),
    )
    
    logger = get_logger(__name__)
    logger.debug(f"执行命令: {args.command}")
    
    # 命令分发
    try:
        if args.command == "scan":
            from so101.scan import run_scan
            run_scan(cameras=args.cameras, arms=args.arms, all=args.all)
        
        elif args.command == "list":
            from so101.config import print_system_cameras, print_system_arms, available_scenes
            if args.cameras:
                print_system_cameras()
            elif args.arms:
                print_system_arms()
            elif args.scenes:
                scenes = available_scenes()
                if scenes:
                    console.print("可用场景:", style="bold")
                    for scene in scenes:
                        console.print(f"  • {scene}")
                else:
                    print_info("未定义任何场景")
            else:
                print_system_cameras()
                print_system_arms()
        
        elif args.command == "check":
            from so101.check import run_check
            run_check(scene=args.scene)
        
        elif args.command == "calibrate":
            from so101.calibrate import run_calibrate
            run_calibrate(arm_name=args.arm)
        
        elif args.command == "teleop":
            from so101.teleop import run_teleop
            run_teleop(mode=args.mode, arm=args.arm, no_cam=args.no_cam)
        
        elif args.command == "record":
            from so101.record import run_record
            # run_record expects argv list, reconstruct from parsed args
            argv = ["-s", args.scene]
            if args.episodes:
                argv += ["-n", str(args.episodes)]
            if args.episode_time:
                argv += ["--episode-time", str(args.episode_time)]
            if args.overwrite:
                argv.append("--overwrite")
            if args.resume:
                argv.append("--resume")
            if args.name:
                argv += ["--name", args.name]
            if args.dataset_repo_id:
                argv += ["--dataset-repo-id", args.dataset_repo_id]
            if args.vcodec:
                argv += ["--vcodec", args.vcodec]
            run_record(argv=argv)
        
        elif args.command == "capture":
            from so101.capture import run_capture
            run_capture(role=args.role, filter_str=args.filter, output_dir=args.output)
        
        elif args.command == "dataset":
            from so101.dataset import main as dataset_main
            dataset_main(argv=sys.argv[2:])  # e.g. ["push", "--repo", "..."]
        
        elif args.command == "deploy":
            from so101.deploy import run_deploy
            deploy_argv = ["--policy", args.policy, "--dataset", args.dataset,
                           "--scene", args.scene, "--episodes", str(args.episodes),
                           "--smooth", str(args.smooth),
                           "--delta_threshold", str(args.delta_threshold)]
            if args.policy_type != "act":
                deploy_argv += ["--policy_type", args.policy_type]
            if args.no_cam:
                deploy_argv.append("--no-cam")
            if not args.home:
                deploy_argv.append("--no-home")
            run_deploy(argv=deploy_argv)
        
        elif args.command == "doctor":
            from so101.doctor import run_doctor
            success = run_doctor(quick=args.quick, fix=args.fix)
            sys.exit(0 if success else 1)
        
        elif args.command == "validate":
            from so101.validator import validate_config, validate_scene
            from so101.config import load_config
            
            config_path = args.config if args.config else None
            if config_path:
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f)
            else:
                config = load_config()
            
            if args.scene:
                success = validate_scene(args.scene, config)
            else:
                success = validate_config(config)
            
            sys.exit(0 if success else 1)
        
        elif args.command == "benchmark":
            from so101.benchmark import BenchmarkRunner
            
            runner = BenchmarkRunner(iterations=args.iterations)
            
            if args.all or args.cameras:
                console.print("测试摄像头性能...", style="bold")
                runner.benchmark_cameras()
            
            if args.all or args.encoding:
                console.print("测试编码性能...", style="bold")
                runner.benchmark_encoding()
            
            if args.all or args.detection:
                console.print("测试设备检测性能...", style="bold")
                runner.benchmark_device_detection()
            
            # 如果没有指定任何测试，默认运行检测测试
            if not (args.all or args.cameras or args.encoding or args.detection):
                console.print("运行设备检测性能测试...", style="bold")
                runner.benchmark_device_detection()
            
            # 输出报告
            runner.print_report()
            
            if args.output:
                from pathlib import Path
                runner.save_report(Path(args.output))
                console.print(f"\n报告已保存: {args.output}", style="green")
        
        else:
            print_error(f"未知命令: {args.command}")
            parser.print_help()
            sys.exit(1)
    
    except KeyboardInterrupt:
        console.print("\n\n操作已取消", style="yellow")
        sys.exit(130)
    except Exception as e:
        logger.error(f"命令执行失败: {e}", exc_info=True)
        print_error(f"执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
