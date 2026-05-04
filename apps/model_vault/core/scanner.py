import os
import json

MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt"}

def scan_models(root_dir):
    """
    Recursively scans for model files and their associated JSON sidecars.
    Returns a list of dictionaries with model info.
    """
    models = []
    for root, _, files in os.walk(root_dir):
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
