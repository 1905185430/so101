"""
so101.dataset — 数据集管理
============================

so101 dataset ls                  列出本地所有数据集
so101 dataset info --repo ID      查看数据集详情
so101 dataset push --repo ID      推送到 HuggingFace Hub
so101 dataset merge --repos ID1 ID2 ...  合并多个数据集
"""

import argparse
import shutil
import sys
from pathlib import Path

from so101 import config


def _iter_local_datasets():
    """遍历本地所有数据集目录，返回 [(repo_id, path), ...]。"""
    root = config.LEROBOT_CACHE_ROOT
    if not root.exists():
        return []
    results = []
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir():
            continue
        for ds_dir in sorted(user_dir.iterdir()):
            if not ds_dir.is_dir():
                continue
            if (ds_dir / "meta").exists() or (ds_dir / "data").exists():
                repo_id = f"{user_dir.name}/{ds_dir.name}"
                results.append((repo_id, ds_dir))
    return results


def _get_dataset_info(repo_id: str) -> dict:
    """获取数据集基本信息。"""
    ds_path = config.LEROBOT_CACHE_ROOT / repo_id
    if not ds_path.exists():
        return {"error": f"数据集不存在: {ds_path}"}

    info = {"repo_id": repo_id, "path": str(ds_path)}

    meta_file = ds_path / "meta" / "info.json"
    if meta_file.exists():
        import json
        with open(meta_file) as f:
            meta = json.load(f)
        info["fps"] = meta.get("fps", "?")
        info["robot_type"] = meta.get("robot_type", "?")
        info["total_episodes"] = meta.get("total_episodes", "?")
        info["total_frames"] = meta.get("total_frames", "?")
        info["features"] = list(meta.get("features", {}).keys())
        info["videos"] = sorted(meta.get("videos", {}).keys())

    total_size = sum(f.stat().st_size for f in ds_path.rglob("*") if f.is_file())
    info["size_mb"] = round(total_size / (1024 * 1024), 1)

    data_dir = ds_path / "data"
    if data_dir.exists():
        parquet_files = list(data_dir.glob("*.parquet"))
        info["parquet_count"] = len(parquet_files)
        bad = []
        for pf in parquet_files:
            with open(pf, "rb") as f:
                f.seek(-4, 2)
                if f.read(4) != b"PAR1":
                    bad.append(pf.name)
        if bad:
            info["corrupted_parquets"] = bad

    video_dir = ds_path / "videos"
    if video_dir.exists():
        info["video_files"] = sorted(v.name for v in video_dir.rglob("*.mp4"))

    return info


# ============================================================================
# 子命令实现
# ============================================================================

def cmd_ls(args):
    """列出本地所有数据集。"""
    datasets = _iter_local_datasets()
    if not datasets:
        print("本地没有 LeRobot 数据集。")
        print(f"目录: {config.LEROBOT_CACHE_ROOT}")
        return

    print(f"本地数据集 ({len(datasets)} 个)：\n")
    print(f"  {'Repo ID':<45} {'Episodes':<10} {'Frames':<10} {'Size':<10}")
    print(f"  {'-'*45} {'-'*10} {'-'*10} {'-'*10}")

    for repo_id, ds_path in datasets:
        info = _get_dataset_info(repo_id)
        eps = str(info.get("total_episodes", "?"))
        frames = str(info.get("total_frames", "?"))
        size = f"{info.get('size_mb', '?')}MB"
        corrupted = info.get("corrupted_parquets", [])
        tag = " [CORRUPTED]" if corrupted else ""
        print(f"  {repo_id:<45} {eps:<10} {frames:<10} {size:<10}{tag}")

    print()


def cmd_info(args):
    """查看数据集详情。"""
    info = _get_dataset_info(args.repo)
    if "error" in info:
        print(info["error"])
        return

    print(f"\n数据集: {info['repo_id']}")
    print(f"路径:   {info['path']}")
    print(f"大小:   {info.get('size_mb', '?')} MB")
    print(f"FPS:    {info.get('fps', '?')}")
    print(f"机器人: {info.get('robot_type', '?')}")
    print(f"Episodes: {info.get('total_episodes', '?')}")
    print(f"Frames:   {info.get('total_frames', '?')}")

    features = info.get("features", [])
    if features:
        print(f"\nFeatures ({len(features)}):")
        for f in features[:10]:
            print(f"  - {f}")
        if len(features) > 10:
            print(f"  ... 及其他 {len(features) - 10} 个")

    videos = info.get("videos", [])
    if videos:
        print(f"\n视频流 ({len(videos)}):")
        for v in videos:
            print(f"  - {v}")

    corrupted = info.get("corrupted_parquets", [])
    if corrupted:
        print(f"\n[警告] 损坏的 Parquet 文件: {corrupted}")
        print("  原因：录制中断导致 footer 未写入，数据不可恢复")

    print()


def cmd_push(args):
    """推送到 HuggingFace Hub。"""
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    repo_id = args.repo
    ds_path = config.LEROBOT_CACHE_ROOT / repo_id
    if not ds_path.exists():
        print(f"[错误] 数据集不存在: {ds_path}")
        sys.exit(1)

    print(f"[上传] 加载数据集: {repo_id}")
    dataset = LeRobotDataset(repo_id=repo_id, root=str(ds_path), download_videos=False)
    print(f"[上传] Episodes: {dataset.num_episodes}, Frames: {dataset.num_frames}")

    push = input(f"确认上传 {repo_id} 到 HuggingFace Hub? [y/N]: ").strip().lower()
    if push == "y":
        print("[上传] push_to_hub() ...")
        dataset.push_to_hub()
        print("[上传] 完成!")
    else:
        print("[跳过]")


def cmd_merge(args):
    """合并多个数据集。"""
    print("[合并] 功能开发中...")
    print("  提示：使用 lerobot-edit-dataset 或手动合并")
    print("  确保所有数据集使用相同的 feature schema")


# ============================================================================
# CLI 入口
# ============================================================================

def build_parser(subparsers):
    """构建 dataset 子命令解析器（注册到顶层 subparsers）。"""
    p_ds = subparsers.add_parser("dataset", help="数据集管理")
    _add_dataset_subparsers(p_ds)


def _add_dataset_subparsers(p_ds):
    """给 dataset parser 添加子命令（供 build_parser 和 main 共用）。"""
    ds_sub = p_ds.add_subparsers(dest="dataset_command")

    # ls（避免和顶层 list 冲突）
    p_ls = ds_sub.add_parser("ls", help="列出本地所有数据集")
    p_ls.set_defaults(func=cmd_ls)

    # info
    p_info = ds_sub.add_parser("info", help="查看数据集详情")
    p_info.add_argument("--repo", "-r", required=True, help="repo_id，如 Ready321/my_dataset")
    p_info.set_defaults(func=cmd_info)

    # push
    p_push = ds_sub.add_parser("push", help="推送到 HuggingFace Hub")
    p_push.add_argument("--repo", "-r", required=True, help="repo_id")
    p_push.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    p_push.set_defaults(func=cmd_push)

    # merge
    p_merge = ds_sub.add_parser("merge", help="合并多个数据集")
    p_merge.add_argument("--repos", "-r", nargs="+", help="要合并的 repo_id 列表")
    p_merge.set_defaults(func=cmd_merge)

    # --- 新增: check / clean / repair / gpu ---
    from so101.dataset_manager import (
        run_check as _dm_check,
        run_clean as _dm_clean,
        repair_dataset as _dm_repair,
        check_gpu_encoding as _dm_gpu,
    )

    p_check = ds_sub.add_parser("check", help="健康检查（检测损坏/空数据集）")
    p_check.set_defaults(func=lambda a: _dm_check())

    p_clean = ds_sub.add_parser("clean", help="清理空数据集")
    p_clean.add_argument("--yes", "-y", action="store_true", help="跳过确认直接删除")
    p_clean.set_defaults(func=lambda a: _dm_clean(dry_run=not a.yes))

    p_repair = ds_sub.add_parser("repair", help="修复 parquet 损坏的数据集（从视频恢复）")
    p_repair.add_argument("name", help="数据集名称")
    p_repair.set_defaults(func=lambda a: _dm_repair(a.name))

    p_gpu = ds_sub.add_parser("gpu", help="检查 GPU 硬编码支持")
    p_gpu.set_defaults(func=lambda a: _dm_gpu())

    # view — 网页可视化
    p_view = ds_sub.add_parser("view", help="启动网页可视化管理界面")
    p_view.add_argument("--port", type=int, default=5555, help="端口号（默认 5555）")
    p_view.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    def _run_view(a):
        from so101.dataset_viewer import run_viewer
        run_viewer(host=a.host, port=a.port)
    p_view.set_defaults(func=_run_view)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="so101 dataset", description="数据集管理")
    _add_dataset_subparsers(parser)
    args = parser.parse_args(argv)
    args.func(args)
