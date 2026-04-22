#!/usr/bin/env python3
"""SO-101 数据集上传工具 — Gradio Web GUI"""
import os, sys, shutil, tempfile
from pathlib import Path

# 清代理，防止 SSL 握手失败
for k in ["HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"]:
    os.environ.pop(k, None)
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import gradio as gr
from huggingface_hub import HfApi

CACHE_ROOT = Path.home() / ".cache/huggingface/lerobot"

def list_local_datasets():
    """扫描本地数据集目录"""
    datasets = []
    if CACHE_ROOT.exists():
        for user_dir in CACHE_ROOT.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith("."):
                for ds_dir in user_dir.iterdir():
                    if ds_dir.is_dir() and (ds_dir / "meta" / "info.json").exists():
                        datasets.append(f"{user_dir.name}/{ds_dir.name}")
    return sorted(datasets)

def get_dataset_info(repo_id):
    """获取数据集基本信息"""
    ds_path = CACHE_ROOT / repo_id
    if not ds_path.exists():
        return f"数据集不存在: {ds_path}"
    
    info_file = ds_path / "meta" / "info.json"
    if info_file.exists():
        import json
        with open(info_file) as f:
            info = json.load(f)
        total_size = sum(f.stat().st_size for f in ds_path.rglob("*") if f.is_file())
        size_mb = total_size / (1024 * 1024)
        return (
            f"**{repo_id}**\n"
            f"- Episodes: {info.get('total_episodes', '?')}\n"
            f"- Frames: {info.get('total_frames', '?')}\n"
            f"- 大小: {size_mb:.1f} MB\n"
            f"- 路径: `{ds_path}`"
        )
    return f"路径: {ds_path}"

def upload_dataset(repo_id, hf_token, progress=gr.Progress()):
    """上传整个数据集到 HuggingFace Hub"""
    if not repo_id:
        return "请选择数据集"
    if not hf_token:
        return "请输入 HF Token"
    
    ds_path = CACHE_ROOT / repo_id
    if not ds_path.exists():
        return f"数据集不存在: {ds_path}"
    
    try:
        api = HfApi(token=hf_token)
        
        # 创建仓库
        progress(0.1, desc="创建仓库...")
        api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)
        
        # 上传文件夹
        progress(0.2, desc="上传中...")
        api.upload_folder(
            folder_path=str(ds_path),
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"Upload {repo_id} dataset",
        )
        
        return f"上传成功!\nhttps://huggingface.co/datasets/{repo_id}"
    except Exception as e:
        return f"上传失败: {e}"

def upload_custom_folder(folder_path, repo_id, hf_token, progress=gr.Progress()):
    """上传自定义文件夹"""
    if not folder_path or not os.path.isdir(folder_path):
        return "请选择有效文件夹"
    if not repo_id:
        return "请填写目标 repo_id (如 username/dataset_name)"
    if not hf_token:
        return "请输入 HF Token"
    
    try:
        api = HfApi(token=hf_token)
        progress(0.1, desc="创建仓库...")
        api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)
        progress(0.2, desc="上传中...")
        api.upload_folder(
            folder_path=folder_path,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"Upload {repo_id}",
        )
        return f"上传成功!\nhttps://huggingface.co/datasets/{repo_id}"
    except Exception as e:
        return f"上传失败: {e}"

# ============ Gradio UI ============
with gr.Blocks(title="SO-101 数据集上传工具", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# SO-101 数据集上传工具")
    gr.Markdown("将本地录制的数据集上传到 HuggingFace Hub")
    
    with gr.Tab("本地数据集"):
        with gr.Row():
            with gr.Column(scale=1):
                ds_dropdown = gr.Dropdown(
                    choices=list_local_datasets(),
                    label="选择数据集",
                    interactive=True,
                )
                refresh_btn = gr.Button("刷新列表", variant="secondary")
                ds_info = gr.Markdown("选择数据集后显示详情")
                hf_token = gr.Textbox(
                    label="HF Token",
                    type="password",
                    value=os.environ.get("HF_TOKEN", ""),
                    placeholder="hf_xxxxxxxxxxxxxxxx",
                )
                upload_btn = gr.Button("上传到 HuggingFace", variant="primary")
            with gr.Column(scale=1):
                upload_result = gr.Textbox(label="上传结果", lines=5, interactive=False)
        
        ds_dropdown.change(get_dataset_info, inputs=ds_dropdown, outputs=ds_info)
        refresh_btn.click(lambda: gr.update(choices=list_local_datasets()), outputs=ds_dropdown)
        upload_btn.click(upload_dataset, inputs=[ds_dropdown, hf_token], outputs=upload_result)
    
    with gr.Tab("自定义文件夹"):
        with gr.Row():
            with gr.Column():
                folder_input = gr.Textbox(label="文件夹路径", placeholder="/path/to/dataset")
                custom_repo = gr.Textbox(label="目标 repo_id", placeholder="Ready321/my_dataset")
                custom_token = gr.Textbox(label="HF Token", type="password", value=os.environ.get("HF_TOKEN", ""))
                custom_upload_btn = gr.Button("上传", variant="primary")
            with gr.Column():
                custom_result = gr.Textbox(label="上传结果", lines=5, interactive=False)
        
        custom_upload_btn.click(
            upload_custom_folder,
            inputs=[folder_input, custom_repo, custom_token],
            outputs=custom_result,
        )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
