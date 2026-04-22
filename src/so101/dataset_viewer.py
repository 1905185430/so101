"""
so101.dataset_viewer — 数据集网页可视化 & 管理
==============================================

功能:
  - 浏览所有本地 LeRobot 数据集
  - Episode 缩略图列表 + 视频逐帧回放
  - Action / State 数值图表
  - 数据集健康状态显示
  - 部署结果查看
  - 一键清理 / 修复

用法:
  so101 dataset view              # 默认 0.0.0.0:5555
  so101 dataset view --port 8080  # 自定义端口
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify, Response

# ============================================================================
# 配置
# ============================================================================

DATASET_ROOT = Path.home() / ".cache" / "huggingface" / "lerobot"
THUMB_ROOT = Path(tempfile.gettempdir()) / "so101_thumbnails"
THUMB_ROOT.mkdir(exist_ok=True)

# LeRobot 版本兼容性映射
_LEROBOT_COMPAT = {
    "v2.0": "lerobot <= 0.3.x",
    "v2.1": "lerobot 0.4.0 ~ 0.4.4",
    "v3.0": "lerobot 0.4.5 ~ 0.5.1",
    "v3.1": "lerobot >= 0.6.0",
}


def _lerobot_compat(version: str) -> str:
    """根据 codebase_version 推断兼容的 lerobot 版本。"""
    return _LEROBOT_COMPAT.get(version, "未知")

app = Flask(__name__)

try:
    import pyarrow.parquet as pq
    PARQUET_OK = True
except Exception:
    PARQUET_OK = False


# ============================================================================
# 工具函数
# ============================================================================


def get_video_info(path: str) -> dict:
    """用 ffprobe 读取视频信息。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                return {
                    "duration": float(s.get("duration", 0)),
                    "fps": eval(s.get("r_frame_rate", "30/1")) if "/" in s.get("r_frame_rate", "30") else 30,
                    "width": int(s.get("width", 0)),
                    "height": int(s.get("height", 0)),
                    "frames": int(s.get("nb_frames", 0)),
                }
    except Exception:
        pass
    return {"duration": 0, "fps": 30, "width": 0, "height": 0, "frames": 0}


def extract_frame(video_path: str, time_sec: float, output_path: str, size=320):
    """从视频中截取指定时间的帧。"""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(time_sec),
             "-i", str(video_path),
             "-vframes", "1", "-vf", f"scale={size}:-1",
             "-q:v", "2", str(output_path)],
            capture_output=True, timeout=15
        )
        return os.path.exists(output_path)
    except Exception:
        return False


def scan_datasets() -> list[dict]:
    """扫描所有本地数据集。"""
    results = []
    if not DATASET_ROOT.exists():
        return results

    for user_dir in sorted(DATASET_ROOT.iterdir()):
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

            # Parquet health
            parquet_ok = False
            if data_file.exists() and PARQUET_OK:
                try:
                    pq.ParquetFile(str(data_file))
                    parquet_ok = True
                except Exception:
                    pass

            # Videos
            videos = list(vid_dir.rglob("*.mp4")) if vid_dir.exists() else []
            video_cams = set()
            for v in videos:
                # videos/observation.images.top/chunk-000/file-000.mp4
                parts = v.parts
                for i, p in enumerate(parts):
                    if p == "videos" and i + 1 < len(parts):
                        video_cams.add(parts[i + 1])

            total_size = sum(f.stat().st_size for f in ds_dir.rglob("*") if f.is_file())

            results.append({
                "name": f"{user_dir.name}/{ds_dir.name}",
                "path": str(ds_dir),
                "episodes": info.get("total_episodes", 0),
                "frames": info.get("total_frames", 0),
                "fps": info.get("fps", 30),
                "robot_type": info.get("robot_type", ""),
                "codebase_version": info.get("codebase_version", ""),
                "total_tasks": info.get("total_tasks", 0),
                "chunks_size": info.get("chunks_size", 0),
                "compatible_lerobot": _lerobot_compat(info.get("codebase_version", "")),
                "size_mb": round(total_size / 1024 / 1024, 1),
                "parquet_ok": parquet_ok,
                "has_tasks": task_file.exists(),
                "video_cams": sorted(video_cams),
                "video_count": len(videos),
                "is_empty": total_size < 1024,
                "is_corrupt": data_file.exists() and not parquet_ok,
                "features": list(info.get("features", {}).keys()),
            })

    return results


def get_ds_dir(repo_id: str) -> Path | None:
    """获取数据集本地路径。"""
    p = DATASET_ROOT / repo_id
    return p if p.exists() else None


def read_episode_values(ds_dir: Path, ep_index: int, fps: int) -> dict | None:
    """读取 episode 的 action/state 数值。"""
    if not PARQUET_OK:
        return None
    try:
        data_file = ds_dir / "data" / "chunk-000" / "file-000.parquet"
        if not data_file.exists():
            return None
        table = pq.read_table(str(data_file))
        df = table.to_pydict()

        # Find episode frames
        ep_indices = df.get("episode_index", [])
        frame_indices = [i for i, ep in enumerate(ep_indices) if ep == ep_index]
        if not frame_indices:
            # Single-episode dataset
            frame_indices = list(range(len(df.get("frame_index", []))))

        if not frame_indices:
            return None

        result = {"frame_count": len(frame_indices)}

        # Extract action
        if "action" in df:
            actions = df["action"]
            if frame_indices and hasattr(actions[frame_indices[0]], '__len__'):
                n_joints = len(actions[frame_indices[0]])
                joint_names = [f"joint_{i}" for i in range(n_joints)]
                # Try to get names from meta
                info_file = ds_dir / "meta" / "info.json"
                if info_file.exists():
                    info = json.loads(info_file.read_text())
                    features = info.get("features", {})
                    action_info = features.get("action", {})
                    names = action_info.get("names", [])
                    if names:
                        joint_names = names

                result["joint_names"] = joint_names
                result["actions"] = [
                    [float(v) for v in actions[i]]
                    for i in frame_indices[::max(1, len(frame_indices)//200)]
                ]

        # Extract state
        if "observation.state" in df:
            states = df["observation.state"]
            if frame_indices and hasattr(states[frame_indices[0]], '__len__'):
                result["states"] = [
                    [float(v) for v in states[i]]
                    for i in frame_indices[::max(1, len(frame_indices)//200)]
                ]

        return result
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# API 路由
# ============================================================================


@app.route("/")
def index():
    """主页：数据集列表 + 健康状态。"""
    return APP_HTML


@app.route("/api/datasets")
def api_datasets():
    """返回所有数据集列表。"""
    ds_list = scan_datasets()
    return jsonify(ds_list)


@app.route("/api/env")
def api_env():
    """返回当前环境信息。"""
    lerobot_ver = "未知"
    try:
        import lerobot
        lerobot_ver = getattr(lerobot, "__version__", "未知")
    except Exception:
        pass
    import sys
    return jsonify({
        "lerobot_version": lerobot_ver,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "dataset_root": str(DATASET_ROOT),
    })


@app.route("/api/<path:repo_id>/episodes")
def api_episodes(repo_id: str):
    """返回数据集的 episode 列表和缩略图。"""
    ds_dir = get_ds_dir(repo_id)
    if ds_dir is None:
        return jsonify({"error": "not found"}), 404

    info_file = ds_dir / "meta" / "info.json"
    info = {}
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text())
        except Exception:
            pass

    fps = info.get("fps", 30)
    total_eps = info.get("total_episodes", 0)
    total_frames = info.get("total_frames", 0)

    # Find videos
    vid_dir = ds_dir / "videos"
    cam_videos = {}
    if vid_dir.exists():
        for mp4 in sorted(vid_dir.rglob("*.mp4")):
            parts = mp4.parts
            for i, p in enumerate(parts):
                if p == "videos" and i + 1 < len(parts):
                    cam = parts[i + 1]
                    cam_videos[cam] = str(mp4)
                    break

    # Read episodes index from ALL parquet files (支持追加的多文件)
    episodes = []
    if PARQUET_OK:
        ep_dir = ds_dir / "meta" / "episodes"
        for chunk_dir in sorted(ep_dir.glob("chunk-*")):
            for ep_file in sorted(chunk_dir.glob("*.parquet")):
                try:
                    tbl = pq.read_table(str(ep_file))
                    d = tbl.to_pydict()
                    lengths = d.get("length", [])
                    ep_indices = d.get("episode_index", [])
                    from_indices = d.get("dataset_from_index", [])
                    to_indices = d.get("dataset_to_index", [])
                    for i in range(tbl.num_rows):
                        ep_idx = ep_indices[i] if i < len(ep_indices) else i
                        length = lengths[i] if i < len(lengths) else 0
                        frame_start = from_indices[i] if i < len(from_indices) else 0
                        frame_stop = to_indices[i] if i < len(to_indices) else length
                        episodes.append({
                            "index": ep_idx,
                            "frame_start": frame_start,
                            "frame_stop": frame_stop,
                            "frames": length,
                            "duration_s": round(length / fps, 1) if fps > 0 else 0,
                            "mid_time": round((frame_start + frame_stop) / 2 / fps, 2) if fps > 0 else 0,
                        })
                except Exception:
                    pass

    # Fallback: if no episodes parquet, estimate from info.json
    if not episodes and total_eps > 0 and total_frames > 0:
        frames_per_ep = total_frames // total_eps
        for ep_idx in range(total_eps):
            mid_frame = ep_idx * frames_per_ep + frames_per_ep // 2
            mid_time = mid_frame / fps if fps > 0 else 0
            episodes.append({
                "index": ep_idx,
                "frame_start": ep_idx * frames_per_ep,
                "frame_stop": (ep_idx + 1) * frames_per_ep,
                "frames": frames_per_ep,
                "duration_s": round(frames_per_ep / fps, 1),
                "mid_time": round(mid_time, 2),
            })
    elif not episodes and total_frames > 0:
        episodes.append({
            "index": 0,
            "frame_start": 0,
            "frame_stop": total_frames,
            "frames": total_frames,
            "duration_s": round(total_frames / fps, 1),
            "mid_time": round(total_frames / fps / 2, 2),
        })

    return jsonify({
        "repo_id": repo_id,
        "info": info,
        "episodes": episodes,
        "cameras": cam_videos,
        "fps": fps,
    })


@app.route("/api/<path:repo_id>/ep/<int:ep_index>/frame/<int:frame_index>")
def api_frame(repo_id: str, ep_index: int, frame_index: int):
    """返回指定帧的图像。"""
    ds_dir = get_ds_dir(repo_id)
    if ds_dir is None:
        return "not found", 404

    cam = request.args.get("cam", "")

    # Find video path with chunk/file mapping from episodes parquet
    vid_path = None
    fps = 30

    # Read info
    info_file = ds_dir / "meta" / "info.json"
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text())
            fps = info.get("fps", 30)
        except Exception:
            pass

    # Find video mapping from episodes parquet
    if cam and PARQUET_OK:
        ep_dir = ds_dir / "meta" / "episodes"
        for chunk_dir in sorted(ep_dir.glob("chunk-*")):
            for ep_file in sorted(chunk_dir.glob("*.parquet")):
                try:
                    tbl = pq.read_table(str(ep_file))
                    d = tbl.to_pydict()
                    ep_indices = d.get("episode_index", [])
                    for i, ep_idx in enumerate(ep_indices):
                        if ep_idx == ep_index:
                            # Get video chunk/file for this camera
                            vid_chunk_key = f"videos/{cam}/chunk_index"
                            vid_file_key = f"videos/{cam}/file_index"
                            vid_from_key = f"videos/{cam}/from_timestamp"
                            vid_chunk = d.get(vid_chunk_key, [0])[i]
                            vid_file = d.get(vid_file_key, [0])[i]
                            vid_from = d.get(vid_from_key, [0.0])[i]

                            vid_path = ds_dir / "videos" / cam / f"chunk-{vid_chunk:03d}" / f"file-{vid_file:03d}.mp4"

                            # Calculate local timestamp within this video file
                            # frame_index is global, need to convert to local time
                            from_indices = d.get("dataset_from_index", [])
                            if from_indices:
                                local_frame = frame_index - from_indices[i]
                            else:
                                local_frame = frame_index
                            local_time = local_frame / fps
                            local_time = max(0, local_time)

                            break
                except Exception:
                    pass
            if vid_path:
                break

    # Fallback: find first video for this cam
    if not vid_path and cam:
        vid_dir = ds_dir / "videos" / cam
        if vid_dir.exists():
            mp4s = sorted(vid_dir.rglob("*.mp4"))
            if mp4s:
                vid_path = mp4s[0]
                local_time = frame_index / fps

    if not vid_path:
        # Try first camera
        vid_dir = ds_dir / "videos"
        if vid_dir.exists():
            mp4s = sorted(vid_dir.rglob("*.mp4"))
            if mp4s:
                vid_path = mp4s[0]
                local_time = frame_index / fps

    if not vid_path or not vid_path.exists():
        return "video not found", 404

    # Extract frame
    thumb_key = f"{repo_id.replace('/', '_')}_{ep_index}_{frame_index}_{cam}"
    thumb_path = THUMB_ROOT / f"{thumb_key}.jpg"

    if not thumb_path.exists():
        extract_frame(str(vid_path), local_time, str(thumb_path), size=480)

    if thumb_path.exists():
        from flask import send_file
        return send_file(str(thumb_path), mimetype="image/jpeg")
    return "frame extraction failed", 500


@app.route("/api/<path:repo_id>/ep/<int:ep_index>/values")
def api_ep_values(repo_id: str, ep_index: int):
    """返回 episode 的 action/state 数值。"""
    ds_dir = get_ds_dir(repo_id)
    if ds_dir is None:
        return jsonify({"error": "not found"}), 404

    info_file = ds_dir / "meta" / "info.json"
    fps = 30
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text())
            fps = info.get("fps", 30)
        except Exception:
            pass

    values = read_episode_values(ds_dir, ep_index, fps)
    if values is None:
        return jsonify({"error": "no data"})
    return jsonify(values)


@app.route("/api/<path:repo_id>/video/<path:vid_path>")
def api_video(repo_id: str, vid_path: str):
    """直接返回视频文件。"""
    ds_dir = get_ds_dir(repo_id)
    if ds_dir is None:
        return "not found", 404

    full_path = ds_dir / "videos" / vid_path
    if not full_path.exists():
        return "not found", 404

    from flask import send_file
    return send_file(str(full_path), mimetype="video/mp4")


@app.route("/api/batch_delete", methods=["POST"])
def api_batch_delete():
    """批量删除数据集。"""
    data = request.get_json()
    names = data.get("names", [])
    deleted = []
    errors = []
    for name in names:
        path = DATASET_ROOT / name
        if path.exists():
            try:
                shutil.rmtree(str(path))
                deleted.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")
        else:
            errors.append(f"{name}: 不存在")
    return jsonify({"deleted": deleted, "errors": errors})


@app.route("/api/batch_push", methods=["POST"])
def api_batch_push():
    """批量推送数据集到 HuggingFace（返回列表供确认）。"""
    data = request.get_json()
    names = data.get("names", [])
    results = []
    for name in names:
        ds_dir = DATASET_ROOT / name
        if not ds_dir.exists():
            results.append({"name": name, "status": "not_found"})
            continue
        info_file = ds_dir / "meta" / "info.json"
        data_file = ds_dir / "data" / "chunk-000" / "file-000.parquet"
        size = sum(f.stat().st_size for f in ds_dir.rglob("*") if f.is_file())
        info = {}
        if info_file.exists():
            try:
                info = json.loads(info_file.read_text())
            except Exception:
                pass
        results.append({
            "name": name,
            "status": "ready",
            "episodes": info.get("total_episodes", 0),
            "frames": info.get("total_frames", 0),
            "size_mb": round(size / 1024 / 1024, 1),
            "parquet_ok": data_file.exists(),
        })
    return jsonify({"datasets": results})


@app.route("/api/reload", methods=["POST"])
def api_reload():
    """清除缓存重新扫描。"""
    return jsonify({"ok": True})


@app.route("/api/rename", methods=["POST"])
def api_rename():
    """重命名数据集。"""
    data = request.get_json()
    old_name = data.get("old_name", "")
    new_name = data.get("new_name", "")

    if not old_name or not new_name:
        return jsonify({"error": "old_name and new_name required"}), 400

    if "/" not in new_name:
        return jsonify({"error": "new_name must be user/dataset format"}), 400

    old_path = DATASET_ROOT / old_name
    new_path = DATASET_ROOT / new_name

    if not old_path.exists():
        return jsonify({"error": f"源数据集不存在: {old_name}"}), 404

    if new_path.exists():
        return jsonify({"error": f"目标已存在: {new_name}"}), 409

    # Ensure user dir exists
    new_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.move(str(old_path), str(new_path))
        return jsonify({"ok": True, "old_name": old_name, "new_name": new_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete", methods=["POST"])
def api_delete():
    """删除数据集。"""
    data = request.get_json()
    name = data.get("name", "")

    if not name:
        return jsonify({"error": "name required"}), 400

    path = DATASET_ROOT / name
    if not path.exists():
        return jsonify({"error": f"数据集不存在: {name}"}), 404

    try:
        shutil.rmtree(str(path))
        return jsonify({"ok": True, "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# 前端 HTML（自包含，无需外部模板）
# ============================================================================

APP_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SO-101 数据集管理</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e1e4e8; }
.header { background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d; display: flex; align-items: center; gap: 16px; }
.header h1 { font-size: 20px; color: #58a6ff; }
.header .badge { background: #238636; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }

/* Dataset List */
.dataset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; margin-top: 16px; }
.ds-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; cursor: pointer; transition: all 0.2s; }
.ds-card:hover { border-color: #58a6ff; transform: translateY(-2px); }
.ds-card.corrupt { border-color: #f85149; }
.ds-card.empty { opacity: 0.5; }
.ds-card .name { font-size: 16px; font-weight: 600; color: #58a6ff; margin-bottom: 8px; }
.ds-card .meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; color: #8b949e; }
.ds-card .meta span { display: flex; align-items: center; gap: 4px; }
.ds-card .tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.tag.ok { background: #238636; color: #fff; }
.tag.corrupt { background: #da3633; color: #fff; }
.tag.empty { background: #484f58; }

/* Episode Viewer */
.viewer { display: none; }
.viewer.active { display: block; }
.viewer-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.viewer-header h2 { font-size: 18px; }
.btn { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn:hover { background: #30363d; border-color: #58a6ff; }
.btn.primary { background: #238636; border-color: #238636; color: #fff; }
.btn.sm { padding: 3px 8px; font-size: 11px; }
.btn.danger { border-color: #f85149; color: #f85149; }
.btn.danger:hover { background: #f85149; color: #fff; }
.card-actions { display: flex; gap: 6px; margin-top: 8px; }
.card-actions .chk { margin-right: 4px; accent-color: #58a6ff; }
.batch-bar { background: #1c2128; border: 1px solid #30363d; border-radius: 8px; padding: 10px 16px; margin-bottom: 16px; display: none; align-items: center; gap: 12px; }
.batch-bar.active { display: flex; }
.batch-bar .count { color: #58a6ff; font-weight: 600; }
.version-tag { display: inline-block; background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb44; padding: 0 6px; border-radius: 4px; font-size: 11px; margin-left: 6px; }
.compat-tag { display: inline-block; background: #23863622; color: #39d353; border: 1px solid #23863644; padding: 0 6px; border-radius: 4px; font-size: 11px; margin-left: 4px; }

.ep-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; margin-bottom: 16px; }
.ep-thumb { background: #161b22; border: 2px solid #30363d; border-radius: 6px; overflow: hidden; cursor: pointer; text-align: center; }
.ep-thumb.active { border-color: #58a6ff; }
.ep-thumb img { width: 100%; aspect-ratio: 4/3; object-fit: cover; display: block; }
.ep-thumb .ep-label { padding: 4px; font-size: 12px; color: #8b949e; }

.frame-viewer { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.frame-viewer .frames { display: flex; gap: 8px; overflow-x: auto; }
.frame-viewer .frames .cam-frame { flex: 0 0 auto; text-align: center; }
.frame-viewer .frames .cam-frame img { width: 320px; border-radius: 4px; }
.frame-viewer .frames .cam-label { font-size: 12px; color: #8b949e; margin-top: 4px; }

.controls { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.controls input[type=range] { flex: 1; }
.controls .frame-info { font-size: 13px; color: #8b949e; min-width: 120px; }

.chart-container { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.chart-container h3 { font-size: 14px; margin-bottom: 8px; color: #8b949e; }
canvas { width: 100%; height: 200px; }

/* Stats bar */
.stats-bar { display: flex; gap: 16px; padding: 12px 0; flex-wrap: wrap; }
.stat { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 8px 16px; }
.stat .val { font-size: 20px; font-weight: 700; color: #58a6ff; }
.stat .label { font-size: 12px; color: #8b949e; }

.loading { text-align: center; padding: 40px; color: #8b949e; }
</style>
</head>
<body>

<div class="header">
  <h1>SO-101 数据集管理</h1>
  <span class="badge" id="ds-count">-</span>
  <span class="version-tag" id="lerobot-ver" title="当前环境 lerobot 版本">lerobot ?</span>
  <button class="btn" onclick="loadDatasets()">刷新</button>
  <a href="https://huggingface.co" target="_blank" style="margin-left:auto; color:#58a6ff; font-size:13px;">HuggingFace Hub ↗</a>
</div>

<div class="container">
  <!-- Dataset List -->
  <div id="list-view">
    <div class="stats-bar" id="stats-bar"></div>
    <div class="batch-bar" id="batch-bar">
      <span>已选 <span class="count" id="batch-count">0</span> 个</span>
      <button class="btn" onclick="selectAll()">全选</button>
      <button class="btn" onclick="selectNone()">取消</button>
      <button class="btn danger" onclick="batchDelete()">批量删除</button>
      <button class="btn" onclick="batchPush()">批量推送</button>
    </div>
    <div class="dataset-grid" id="ds-grid">
      <div class="loading">加载中...</div>
    </div>
  </div>

  <!-- Episode Viewer -->
  <div id="viewer" class="viewer">
    <div class="viewer-header">
      <button class="btn" onclick="backToList()">← 返回</button>
      <h2 id="viewer-title">-</h2>
      <button class="btn" onclick="loadEpisodes(currentRepo)">刷新</button>
    </div>
    <div class="ep-grid" id="ep-grid"></div>
    <div class="frame-viewer">
      <div class="controls">
        <button class="btn" onclick="prevFrame()">◀</button>
        <input type="range" id="frame-slider" min="0" max="100" value="0" oninput="onSlider(this.value)">
        <button class="btn" onclick="nextFrame()">▶</button>
        <button class="btn" id="play-btn" onclick="togglePlay()">▶ 播放</button>
        <span class="frame-info" id="frame-info">-</span>
      </div>
      <div class="frames" id="frames-container"></div>
    </div>
    <div class="chart-container">
      <h3>Action / State</h3>
      <canvas id="chart"></canvas>
    </div>
  </div>
</div>

<script>
let datasets = [];
let currentRepo = '';
let currentEp = 0;
let currentFrame = 0;
let epData = null;
let playing = false;
let playTimer = null;
let values = null;

async function api(url) {
  const r = await fetch(url);
  return r.json();
}

async function loadDatasets() {
  // Load env info
  try {
    const env = await api('/api/env');
    document.getElementById('lerobot-ver').textContent = 'lerobot ' + env.lerobot_version;
    document.getElementById('lerobot-ver').title = 'Python ' + env.python_version + '\n' + env.dataset_root;
  } catch(e) {}

  datasets = await api('/api/datasets');
  document.getElementById('ds-count').textContent = datasets.length + ' 个数据集';

  const good = datasets.filter(d => d.parquet_ok && !d.is_empty);
  const corrupt = datasets.filter(d => d.is_corrupt);
  const empty = datasets.filter(d => d.is_empty);
  const totalSize = datasets.reduce((s, d) => s + d.size_mb, 0);

  document.getElementById('stats-bar').innerHTML =
    `<div class="stat"><div class="val">${good.length}</div><div class="label">完好</div></div>` +
    `<div class="stat"><div class="val" style="color:#f85149">${corrupt.length}</div><div class="label">损坏</div></div>` +
    `<div class="stat"><div class="val">${empty.length}</div><div class="label">空</div></div>` +
    `<div class="stat"><div class="val">${totalSize.toFixed(0)}MB</div><div class="label">总大小</div></div>`;

  const grid = document.getElementById('ds-grid');
  grid.innerHTML = datasets.map(d => {
    const cls = d.is_empty ? 'empty' : d.is_corrupt ? 'corrupt' : '';
    const tag = d.is_corrupt ? '<span class="tag corrupt">损坏</span>' :
                d.is_empty ? '<span class="tag empty">空</span>' :
                d.parquet_ok ? '<span class="tag ok">完好</span>' : '';
    const ver = d.codebase_version ? `<span class="version-tag" title="兼容: ${d.compatible_lerobot}">${d.codebase_version}</span>` : '';
    const compat = d.compatible_lerobot && d.compatible_lerobot !== '未知' ? `<span class="compat-tag">${d.compatible_lerobot}</span>` : '';
    return `<div class="ds-card ${cls}" data-name="${d.name}">
      <div class="name" onclick="openDataset('${d.name}')" style="cursor:pointer">${d.name} ${tag} ${ver}</div>
      <div class="meta">
        <span>📁 ${d.episodes} ep</span>
        <span>🎞 ${d.frames} 帧</span>
        <span>💾 ${d.size_mb}MB</span>
        <span>📷 ${d.video_cams.join(', ') || '无'}</span>
        ${compat ? '<span>🔧 ' + compat + '</span>' : ''}
      </div>
      <div class="card-actions">
        <input type="checkbox" class="chk" onchange="onCheck()" data-name="${d.name}">
        <button class="btn sm" onclick="renameDataset('${d.name}')">重命名</button>
        <button class="btn sm danger" onclick="deleteDataset('${d.name}')">删除</button>
      </div>
    </div>`;
  }).join('');
}

async function openDataset(repoId) {
  currentRepo = repoId;
  document.getElementById('list-view').style.display = 'none';
  document.getElementById('viewer').classList.add('active');
  document.getElementById('viewer-title').textContent = repoId;
  await loadEpisodes(repoId);
}

function backToList() {
  stopPlay();
  document.getElementById('list-view').style.display = '';
  document.getElementById('viewer').classList.remove('active');
}

async function loadEpisodes(repoId) {
  epData = await api(`/api/${repoId}/episodes`);
  const grid = document.getElementById('ep-grid');
  const camNames = Object.keys(epData.cameras);
  grid.innerHTML = epData.episodes.map((ep, i) =>
    `<div class="ep-thumb ${i===0?'active':''}" onclick="selectEp(${i})">
      <img src="/api/${repoId}/ep/${ep.index}/frame/${ep.frame_start + Math.floor(ep.frames/2)}?cam=${encodeURIComponent(camNames[0] || '')}" onerror="this.style.background='#161b22'; this.alt='Ep ${ep.index}'">
      <div class="ep-label">Ep ${ep.index} · ${ep.frames}帧 · ${ep.duration_s}s</div>
    </div>`
  ).join('');

  selectEp(0);
}

async function selectEp(idx) {
  stopPlay();
  currentEp = idx;
  currentFrame = 0;

  document.querySelectorAll('.ep-thumb').forEach((el, i) => el.classList.toggle('active', i === idx));

  const ep = epData.episodes[idx];
  if (!ep) return;

  const slider = document.getElementById('frame-slider');
  slider.max = ep.frames - 1;
  slider.value = 0;

  // Load values
  values = await api(`/api/${currentRepo}/ep/${ep.index}/values`);

  showFrame(0);
  drawChart();
}

async function showFrame(frameIdx) {
  currentFrame = frameIdx;
  const ep = epData.episodes[currentEp];
  const absFrame = ep.frame_start + frameIdx;

  document.getElementById('frame-slider').value = frameIdx;
  document.getElementById('frame-info').textContent = `帧 ${frameIdx}/${ep.frames-1} (${(frameIdx/epData.fps).toFixed(2)}s)`;

  const container = document.getElementById('frames-container');
  const cams = Object.keys(epData.cameras);
  if (container.children.length !== cams.length) {
    container.innerHTML = cams.map(cam =>
      `<div class="cam-frame">
        <img id="img-${cam}" src="" alt="${cam}">
        <div class="cam-label">${cam}</div>
      </div>`
    ).join('');
  }

  cams.forEach(cam => {
    document.getElementById(`img-${cam}`).src =
      `/api/${currentRepo}/ep/${ep.index}/frame/${absFrame}?cam=${cam}`;
  });
}

function onSlider(v) { showFrame(parseInt(v)); }
function prevFrame() { showFrame(Math.max(0, currentFrame - 1)); }
function nextFrame() { const ep = epData.episodes[currentEp]; showFrame(Math.min(ep.frames-1, currentFrame + 1)); }

function togglePlay() {
  if (playing) { stopPlay(); }
  else {
    playing = true;
    document.getElementById('play-btn').textContent = '⏸ 暂停';
    playTimer = setInterval(() => {
      const ep = epData.episodes[currentEp];
      if (currentFrame >= ep.frames - 1) { stopPlay(); return; }
      showFrame(currentFrame + 1);
    }, 1000 / epData.fps);
  }
}
function stopPlay() {
  playing = false;
  document.getElementById('play-btn').textContent = '▶ 播放';
  if (playTimer) { clearInterval(playTimer); playTimer = null; }
}

// Simple chart
function drawChart() {
  const canvas = document.getElementById('chart');
  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.parentElement.clientWidth - 32;
  const H = canvas.height = 200;
  ctx.clearRect(0, 0, W, H);

  if (!values || !values.actions) {
    ctx.fillStyle = '#8b949e';
    ctx.fillText('无数值数据（parquet 可能损坏）', 20, 100);
    return;
  }

  const data = values.actions;
  const names = values.joint_names || [];
  const colors = ['#58a6ff', '#f85149', '#238636', '#d29922', '#bc8cff', '#39d353'];

  // Find range
  let min = Infinity, max = -Infinity;
  data.forEach(row => row.forEach(v => { min = Math.min(min, v); max = Math.max(max, v); }));
  const range = max - min || 1;

  // Draw lines
  const nJoints = data[0].length;
  for (let j = 0; j < nJoints; j++) {
    ctx.beginPath();
    ctx.strokeStyle = colors[j % colors.length];
    ctx.lineWidth = 1.5;
    data.forEach((row, i) => {
      const x = (i / (data.length - 1)) * W;
      const y = H - ((row[j] - min) / range) * (H - 20) - 10;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // Legend
  ctx.font = '11px sans-serif';
  names.forEach((name, j) => {
    ctx.fillStyle = colors[j % colors.length];
    ctx.fillText(name || `j${j}`, 10 + j * 100, 15);
  });

  // Current frame indicator
  const ep = epData.episodes[currentEp];
  const markerX = (currentFrame / (ep.frames - 1)) * W;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(markerX, 0); ctx.lineTo(markerX, H); ctx.stroke();
  ctx.setLineDash([]);
}

// Batch selection
function getSelected() {
  return [...document.querySelectorAll('.chk:checked')].map(c => c.dataset.name);
}
function onCheck() {
  const sel = getSelected();
  document.getElementById('batch-count').textContent = sel.length;
  document.getElementById('batch-bar').classList.toggle('active', sel.length > 0);
}
function selectAll() {
  document.querySelectorAll('.chk').forEach(c => { c.checked = true; }); onCheck();
}
function selectNone() {
  document.querySelectorAll('.chk').forEach(c => { c.checked = false; }); onCheck();
}
async function batchDelete() {
  const sel = getSelected();
  if (!sel.length) return;
  if (!confirm('确定删除 ' + sel.length + ' 个数据集?\n\n' + sel.join('\n') + '\n\n不可恢复!')) return;
  const r = await fetch('/api/batch_delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({names: sel})
  });
  const d = await r.json();
  alert('已删除 ' + d.deleted.length + ' 个' + (d.errors.length ? '\n错误: ' + d.errors.join('; ') : ''));
  loadDatasets();
}
async function batchPush() {
  const sel = getSelected();
  if (!sel.length) return;
  const r = await fetch('/api/batch_push', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({names: sel})
  });
  const d = await r.json();
  const lines = d.datasets.map(ds => ds.name + ' (' + ds.episodes + 'ep, ' + ds.size_mb + 'MB)');
  alert('待推送 ' + d.datasets.length + ' 个:\n\n' + lines.join('\n') + '\n\n请使用 so101 dataset push 逐个推送');
}

// Rename
async function renameDataset(oldName) {
  const newName = prompt('重命名数据集\n\n当前: ' + oldName + '\n\n新名称 (user/dataset 格式):', oldName);
  if (!newName || newName === oldName) return;
  if (!newName.includes('/')) { alert('名称格式错误，需 user/dataset'); return; }
  const r = await fetch('/api/rename', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({old_name: oldName, new_name: newName})
  });
  const d = await r.json();
  if (d.ok) { alert('已重命名: ' + oldName + ' → ' + newName); loadDatasets(); }
  else alert('失败: ' + d.error);
}

// Delete
async function deleteDataset(name) {
  if (!confirm('确定删除数据集?\n\n' + name + '\n\n此操作不可恢复!')) return;
  const r = await fetch('/api/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name})
  });
  const d = await r.json();
  if (d.ok) { alert('已删除: ' + name); loadDatasets(); }
  else alert('失败: ' + d.error);
}

// Keyboard
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft') prevFrame();
  else if (e.key === 'ArrowRight') nextFrame();
  else if (e.key === ' ') { e.preventDefault(); togglePlay(); }
});

// Init
loadDatasets();
</script>
</body>
</html>
"""


# ============================================================================
# 启动入口
# ============================================================================


def run_viewer(host="0.0.0.0", port=5555, debug=False):
    """启动数据集可视化服务。"""
    print(f"SO-101 数据集管理界面")
    print(f"  地址: http://127.0.0.1:{port}")
    print(f"  数据集目录: {DATASET_ROOT}")
    print(f"  按 Ctrl+C 停止")
    print()
    app.run(host=host, port=port, debug=debug, threaded=True)
