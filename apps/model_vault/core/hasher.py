import hashlib
import os

def calculate_sha256(file_path, buffer_size=65536):
    """
    Calculates the SHA256 hash of a file using a buffered approach to handle large files.
    """
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(buffer_size):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        print(f"Error calculating hash for {file_path}: {e}")
        return None

def calculate_autov1(file_path):
    """
    Calculates the AutoV1 hash (first 10KB of model data) used by some legacy tools.
    For Safetensors, this is roughly the start of the file after the header.
    """
    # Simple implementation: just the first 10KB of the file
    try:
        with open(file_path, "rb") as f:
            data = f.read(10240)
            return hashlib.sha256(data).hexdigest()
    except Exception as e:
        print(f"Error calculating AutoV1 for {file_path}: {e}")
        return None

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        print(f"Hashing: {path}")
        print(f"SHA256: {calculate_sha256(path)}")
