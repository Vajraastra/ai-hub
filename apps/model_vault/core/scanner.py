import os
import json
import stat

MODEL_EXTENSIONS = {".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".gguf", ".bin"}


def _is_directory_link(path):
    """Do not descend into Windows junctions or directory symlinks."""
    try:
        info = os.lstat(path)
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except OSError:
        return True

def scan_models(root_dir):
    """
    Recursively scans for model files and their associated JSON sidecars.
    Returns a list of dictionaries with model info.
    """
    models = []
    for root, dirs, files in os.walk(root_dir, followlinks=False):
        dirs[:] = [name for name in dirs if not _is_directory_link(os.path.join(root, name))]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in MODEL_EXTENSIONS:
                full_path = os.path.join(root, file)
                base_name = os.path.splitext(full_path)[0]
                
                # Check for sidecars
                cm_info = base_name + ".cm-info.json"
                metadata = base_name + ".metadata.json"
                preview = base_name + ".preview.jpeg"
                
                models.append({
                    "name": file,
                    "path": full_path,
                    "has_cm_info": os.path.exists(cm_info),
                    "has_metadata": os.path.exists(metadata),
                    "has_preview": os.path.exists(preview),
                    "cm_info_path": cm_info if os.path.exists(cm_info) else None,
                    "metadata_path": metadata if os.path.exists(metadata) else None,
                    "preview_path": preview if os.path.exists(preview) else None,
                })
    return models

def get_model_metadata(metadata_path):
    """Reads the metadata.json file if it exists."""
    if metadata_path and os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}
