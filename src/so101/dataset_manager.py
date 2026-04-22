"""
so101.dataset_manager — 数据集管理工具
=====================================

功能:
  1. 健康检查 — 检测损坏/空数据集
  2. 清理 — 删除空数据集释放空间
  3. 修复 — 从视频恢复损坏数据集的 episode 索引
  4. 合并 — 合并多个数据集
  5. 上传 — 断点续传推送到 HuggingFace
  6. GPU 加速 — NVENC 硬编码视频

用法:
  so101 dataset check         # 健康检查
  so101 dataset clean         # 清理空数据集
  so101 dataset repair <name> # 修复损坏数据集
  so101 dataset merge <a> <b> -o <out>  # 合并
  so101 dataset push <name>   # 推送到 HF
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import yaml

# ============================================================================
# 路径
# ============================================================================

LEROBOT_CACHE = Path.home() / ".cache" / "huggingface" / "lerobot"


def get_local_datasets() -> list[dict]:
    """扫描本地所有 LeRobot 数据集。"""
    datasets = []
    for user_dir in sorted(LEROBOT_CACHE.iterdir()):
        if not user_dir.is_dir() or user_dir.name in ("hub", "calibration"):
            continue
        for ds_dir in sorted(user_dir.iterdir()):
            if not ds_dir.is_dir():
                continue
            info_file = ds_dir / "meta" / "info.json"
            data_file = ds_dir / "data" / "chunk-000" / "file-000.parquet"
            task_file = ds_dir / "meta" / "tasks.parquet"
            vid_dir = ds_dir / "videos"

            info = {}
            if info_file.exists():
                try:
                    info = json.loads(info_file.read_text())
                except Exception:
                    pass

            # Check parquet health
            parquet_ok = False
            parquet_error = ""
            if data_file.exists():
                try:
                    import pyarrow.parquet as pq
                    pf = pq.ParquetFile(str(data_file))
                    parquet_ok = True
                except Exception as e:
                    parquet_error = str(e)[:80]

            # Check videos
            videos = list(vid_dir.rglob("*.mp4")) if vid_dir.exists() else []

            # Total size
            total_size = sum(
                f.stat().st_size for f in ds_dir.rglob("*") if f.is_file()
            )

            datasets.append({
                "name": f"{user_dir.name}/{ds_dir.name}",
                "path": str(ds_dir),
                "episodes": info.get("total_episodes", 0),
                "frames": info.get("total_frames", 0),
                "size_mb": total_size / 1024 / 1024,
                "parquet_ok": parquet_ok,
                "parquet_error": parquet_error,
                "has_tasks": task_file.exists(),
                "video_count": len(videos),
                "is_empty": total_size < 1024,  # < 1KB = empty
                "is_corrupt": data_file.exists() and not parquet_ok,
            })

    return datasets


# ============================================================================
# 健康检查
# ============================================================================


def run_check():
    """检查所有本地数据集健康状态。"""
    ds_list = get_local_datasets()

    print("=" * 70)
    print("  LeRobot 数据集健康检查")
    print("=" * 70)

    good = []
    corrupt = []
    empty = []

    for ds in ds_list:
        if ds["is_empty"]:
            empty.append(ds)
        elif ds["is_corrupt"]:
            corrupt.append(ds)
        elif ds["parquet_ok"]:
            good.append(ds)

    # Good datasets
    if good:
        print(f"\n[完好] {len(good)} 个数据集:")
        for ds in good:
            print(f"  {ds['name']}")
            print(f"    {ds['episodes']} episodes, {ds['frames']} frames, "
                  f"{ds['size_mb']:.1f}MB, {ds['video_count']} videos")

    # Corrupt datasets
    if corrupt:
        print(f"\n[损坏] {len(corrupt)} 个数据集 (parquet 损坏，视频完好):")
        for ds in corrupt:
            print(f"  {ds['name']}")
            print(f"    parquet: {ds['parquet_error']}")
            print(f"    {ds['size_mb']:.1f}MB, {ds['video_count']} videos")
            print(f"    → 可运行: so101 dataset repair {ds['name']}")

    # Empty datasets
    if empty:
        print(f"\n[空] {len(empty)} 个数据集 (可清理):")
        for ds in empty:
            print(f"  {ds['name']} ({ds['episodes']} eps)")
        print(f"  → 可运行: so101 dataset clean")

    # Summary
    total_size = sum(ds["size_mb"] for ds in ds_list)
    print(f"\n总计: {len(ds_list)} 个数据集, {total_size:.1f}MB")
    print(f"  完好: {len(good)}  损坏: {len(corrupt)}  空: {len(empty)}")
    print("=" * 70)


# ============================================================================
# 清理空数据集
# ============================================================================


def run_clean(dry_run: bool = True):
    """清理空数据集。"""
    ds_list = get_local_datasets()
    empty = [ds for ds in ds_list if ds["is_empty"]]

    if not empty:
        print("没有空数据集需要清理。")
        return

    print(f"找到 {len(empty)} 个空数据集:")
    total_size = 0
    for ds in empty:
        print(f"  {ds['name']} ({ds['size_mb']:.1f}MB)")
        total_size += ds["size_mb"]

    if dry_run:
        print(f"\n将释放 {total_size:.1f}MB。运行 `so101 dataset clean --yes` 执行清理。")
        return

    for ds in empty:
        path = Path(ds["path"])
        if path.exists():
            shutil.rmtree(path)
            print(f"  已删除: {ds['name']}")

    print(f"清理完成，释放 {total_size:.1f}MB。")


# ============================================================================
# 修复损坏数据集
# ============================================================================


def repair_dataset(ds_name: str):
    """
    修复 parquet 损坏的数据集。

    视频完好，但 parquet footer 缺失导致无法读取。
    修复方案：从视频帧数推断 episode 边界，重建 episodes.parquet 和 info.json。
    注意：joint state 数据无法恢复（在损坏的 parquet 里），但视频可以用。
    """
    import cv2
    import pyarrow as pa
    import pyarrow.parquet as pq
    import numpy as np

    # Find dataset
    ds_list = get_local_datasets()
    ds = None
    for d in ds_list:
        if d["name"] == ds_name or d["name"].endswith(ds_name):
            ds = d
            break

    if ds is None:
        print(f"数据集 '{ds_name}' 未找到。")
        return

    if not ds["is_corrupt"]:
        print(f"数据集 '{ds['name']}' 未损坏，无需修复。")
        return

    ds_path = Path(ds["path"])
    vid_dir = ds_path / "videos"
    meta_dir = ds_path / "meta"
    data_dir = ds_path / "data" / "chunk-000"

    print(f"修复: {ds['name']}")

    # 1. Analyze videos to get frame counts
    cam_videos = {}
    for mp4 in sorted(vid_dir.rglob("*.mp4")):
        cam_key = mp4.parent.parent.name  # observation.images.top
        cap = cv2.VideoCapture(str(mp4))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        cam_videos[cam_key] = {"path": str(mp4), "frames": frames, "fps": fps}

    if not cam_videos:
        print("  没有找到视频文件。")
        return

    total_frames = min(v["frames"] for v in cam_videos.values())
    fps = list(cam_videos.values())[0]["fps"]
    print(f"  视频: {total_frames} 帧, {fps:.0f} FPS, {total_frames/fps:.1f}s")

    # 2. Read task info
    task = "unknown"
    task_file = meta_dir / "tasks.parquet"
    if task_file.exists():
        try:
            import pyarrow.parquet as pq
            tasks = pq.read_table(str(task_file)).to_pydict()
            if "task" in tasks and tasks["task"]:
                task = tasks["task"][0]
        except Exception:
            pass

    # 3. Rebuild a minimal parquet with frame_index and timestamp
    #    (state/action data is lost, but we preserve the video structure)
    frame_indices = list(range(total_frames))
    timestamps = [i / fps for i in frame_indices]

    table = pa.table({
        "frame_index": pa.array(frame_indices, type=pa.int64()),
        "timestamp": pa.array(timestamps, type=pa.float32()),
        "task": pa.array([task] * total_frames, type=pa.string()),
    })

    repaired_file = data_dir / "file-000.parquet"
    pq.write_table(table, str(repaired_file))
    print(f"  重建 parquet: {total_frames} 行 (仅 frame_index + timestamp)")

    # 4. Rebuild info.json
    info = {
        "codebase_version": "v2.1",
        "robot_type": "so101_follower",
        "total_episodes": 1,  # Unknown, conservative estimate
        "total_frames": total_frames,
        "total_tasks": 1,
        "total_videos": len(cam_videos),
        "fps": fps,
        "splits": {"train": f"0:{total_frames}"},
        "chunks_size": total_frames,
    }
    info_file = meta_dir / "info.json"
    info_file.write_text(json.dumps(info, indent=2))
    print(f"  重建 info.json")

    # 5. Rebuild episodes index
    ep_table = pa.table({
        "episode_index": pa.array([0], type=pa.int64()),
        "chunk_index": pa.array([0], type=pa.int64()),
        "frame_start": pa.array([0], type=pa.int64()),
        "frame_stop": pa.array([total_frames], type=pa.int64()),
    })
    ep_dir = meta_dir / "episodes" / "chunk-000"
    ep_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(ep_table, str(ep_dir / "file-000.parquet"))
    print(f"  重建 episodes 索引")

    print(f"\n修复完成！视频数据完好，但 joint state 数据已丢失。")
    print(f"视频可直接用于: 视频回放、视觉分析、重新采集对比。")


# ============================================================================
# 合并数据集
# ============================================================================


def merge_datasets(ds_names: list[str], output_name: str):
    """合并多个数据集为一个。"""
    try:
        from lerobot.datasets import LeRobotDataset
        from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata
    except ImportError:
        print("需要在 lerobot 环境中运行: conda run -n lerobot so101 dataset merge ...")
        return

    print(f"合并 {len(ds_names)} 个数据集 → {output_name}")

    all_datasets = []
    for name in ds_names:
        print(f"  加载: {name}")
        try:
            ds = LeRobotDataset(name)
            all_datasets.append(ds)
            print(f"    {ds.meta.total_episodes} episodes, {len(ds)} frames")
        except Exception as e:
            print(f"    跳过: {e}")

    if len(all_datasets) < 2:
        print("至少需要 2 个有效数据集。")
        return

    # Use LeRobot's built-in concat if available
    print(f"\n合并中...")
    # TODO: Implement merge using LeRobot API or manual parquet concat
    print("合并功能需要手动实现或使用 LeRobot 的 concat_datasets API。")


# ============================================================================
# 推送到 HuggingFace
# ============================================================================


def run_push(ds_name: str, yes: bool = False):
    """推送数据集到 HuggingFace（断点续传）。"""
    from huggingface_hub import HfApi, list_repo_files

    api = HfApi()

    # Find local dataset
    ds_list = get_local_datasets()
    ds = None
    for d in ds_list:
        if d["name"] == ds_name or d["name"].endswith(ds_name):
            ds = d
            break

    if ds is None:
        print(f"数据集 '{ds_name}' 未找到。")
        return

    if ds["is_corrupt"]:
        print(f"数据集 '{ds['name']}' parquet 损坏，先运行 repair。")
        return

    # Determine repo_id
    repo_id = ds["name"]  # e.g., "Ready321/pickup_redcube_20260421_095438"

    # Check what's already uploaded
    try:
        remote_files = list_repo_files(repo_id, repo_type="dataset")
        remote_set = set(remote_files)
        print(f"远程已有 {len(remote_set)} 个文件")
    except Exception:
        remote_set = set()
        print("远程仓库为空或不存在")

    # List local files
    local_path = Path(ds["path"])
    local_files = []
    for f in sorted(local_path.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(local_path))
            local_files.append(rel)

    # Skip already uploaded
    to_upload = [f for f in local_files if f not in remote_set]
    skip_count = len(local_files) - len(to_upload)

    print(f"本地 {len(local_files)} 个文件, 已上传 {skip_count} 个, 待上传 {len(to_upload)} 个")

    if not to_upload:
        print("所有文件已上传，无需操作。")
        return

    if not yes:
        print(f"\n将上传 {len(to_upload)} 个文件到 {repo_id}")
        resp = input("确认? (y/N): ").strip().lower()
        if resp != "y":
            print("取消。")
            return

    # Upload files (skip proxy issues)
    old_http = os.environ.get("HTTP_PROXY", "")
    old_https = os.environ.get("HTTPS_PROXY", "")
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)

    try:
        for f in to_upload:
            local_file = local_path / f
            print(f"  上传: {f} ({local_file.stat().st_size / 1024:.0f}KB)")
            api.upload_file(
                path_or_fileobj=str(local_file),
                path_in_repo=f,
                repo_id=repo_id,
                repo_type="dataset",
            )
        print("上传完成!")
    except Exception as e:
        print(f"上传失败: {e}")
    finally:
        if old_http:
            os.environ["HTTP_PROXY"] = old_http
        if old_https:
            os.environ["HTTPS_PROXY"] = old_https


# ============================================================================
# GPU 加速视频编码
# ============================================================================


def check_gpu_encoding():
    """检查 GPU 是否支持 NVENC 硬编码。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        has_nvenc = "h264_nvenc" in result.stdout
        has_vaapi = "h264_vaapi" in result.stdout
        print("GPU 编码支持:")
        print(f"  NVENC (NVIDIA): {'✓' if has_nvenc else '✗'}")
        print(f"  VAAPI (Intel/AMD): {'✓' if has_vaapi else '✗'}")

        if has_nvenc:
            print("\nNVENC 可用于加速视频编码。")
            print("LeRobot 录制时可设置 vcodec=h264_nvenc")
            print("注意: 需要在 record 命令中指定，当前 LeRobot 默认用软件编码。")
        return has_nvenc
    except Exception as e:
        print(f"检查失败: {e}")
        return False


# ============================================================================
# CLI 入口
# ============================================================================


def build_parser(sub):
    """添加 dataset 子命令到 CLI。"""
    p_dataset = sub.add_parser("dataset", help="数据集管理")
    ds_sub = p_dataset.add_subparsers(dest="ds_command")

    # check
    ds_sub.add_parser("check", help="检查所有本地数据集健康状态")

    # clean
    p_clean = ds_sub.add_parser("clean", help="清理空数据集")
    p_clean.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    # repair
    p_repair = ds_sub.add_parser("repair", help="修复损坏的数据集")
    p_repair.add_argument("name", help="数据集名称")

    # merge
    p_merge = ds_sub.add_parser("merge", help="合并数据集")
    p_merge.add_argument("names", nargs="+", help="要合并的数据集名称")
    p_merge.add_argument("--output", "-o", required=True, help="输出数据集名称")

    # push
    p_push = ds_sub.add_parser("push", help="推送到 HuggingFace（断点续传）")
    p_push.add_argument("name", help="数据集名称")
    p_push.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    # gpu
    ds_sub.add_parser("gpu", help="检查 GPU 编码支持")


def run_dataset_command(args):
    """分发 dataset 子命令。"""
    if args.ds_command == "check":
        run_check()
    elif args.ds_command == "clean":
        run_clean(dry_run=not args.yes)
    elif args.ds_command == "repair":
        repair_dataset(args.name)
    elif args.ds_command == "merge":
        merge_datasets(args.names, args.output)
    elif args.ds_command == "push":
        run_push(args.name, yes=args.yes)
    elif args.ds_command == "gpu":
        check_gpu_encoding()
    else:
        print("用法: so101 dataset {check|clean|repair|merge|push|gpu}")
