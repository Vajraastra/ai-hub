import requests
import time

class CivitaiClient:
    """
    Async-ready (though sync for now) client for Civitai API.
    Handles rate limiting and basic model fetching.
    """
    BASE_URL = "https://civitai.com/api/v1"

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.headers = {"User-Agent": "AI-Hub-Model-Vault/1.0"}
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    def _request(self, method, url, max_retries=3, timeout=30, **kwargs):
        """Internal helper for robust requests with exponential backoff."""
        for attempt in range(max_retries):
            try:
                response = requests.request(method, url, headers=self.headers, timeout=timeout, **kwargs)
                
                if response.status_code == 200:
                    return response
                elif response.status_code == 404:
                    return response # Handle 404 in caller
                elif response.status_code in (429, 502, 503, 504):
                    wait_time = (attempt + 1) * 5
                    if response.status_code == 429:
                        status_msg = "Límite de peticiones alcanzado (Rate Limit)"
                    else:
                        status_msg = "Civitai está en mantenimiento o bajo carga pesada"
                    
                    print(f"[{response.status_code}] {status_msg}. Reintento {attempt+1}/{max_retries} en {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Error del servidor Civitai (Código: {response.status_code})")
                    return response
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                wait_time = (attempt + 1) * 5
                print(f"Error de conexión ({e}). Reintento {attempt+1}/{max_retries} en {wait_time}s...")
                time.sleep(wait_time)
        
        return None

    def get_model_version_by_hash(self, file_hash):
        """Fetches model version info using a SHA256 hash."""
        url = f"{self.BASE_URL}/model-versions/by-hash/{file_hash}"
        response = self._request("GET", url)
        
        if response and response.status_code == 200:
            return response.json()
        elif response and response.status_code == 404:
            print(f"Model not found for hash: {file_hash}")
        
        return None

    def get_model_info(self, model_id):
        """Fetches general model info."""
        url = f"{self.BASE_URL}/models/{model_id}"
        response = self._request("GET", url)
        if response and response.status_code == 200:
            return response.json()
        return None

    def get_latest_version_for_arch(self, model_id, target_base_model):
        """
        Fetches all versions for a model and returns the latest one 
        that matches the target base architecture (e.g., 'SDXL 1.0').
        """
        model_info = self.get_model_info(model_id)
        if not model_info or "modelVersions" not in model_info:
            return None

        matching_versions = []
        for version in model_info["modelVersions"]:
            if version.get("baseModel") == target_base_model:
                matching_versions.append(version)

        if not matching_versions:
            return None

        # Sort by creation date or ID (latest first)
        matching_versions.sort(key=lambda x: x.get("id", 0), reverse=True)
        return matching_versions[0]

    def extract_metadata(self, version_data):
        """Extracts triggers, base model, and model type from version/model data."""
        model_data = version_data.get("model", {})
        return {
            "triggers": version_data.get("trainedWords", []),
            "baseModel": version_data.get("baseModel", "Unknown"),
            "modelType": model_data.get("type", "Unknown"),
            "versionName": version_data.get("name", "Unknown"),
            "modelVersionId": version_data.get("id"),
            "description": version_data.get("description") or model_data.get("description", "")
        }

    def download_image(self, url, save_path):
        """Downloads a preview image, filtering for real images only."""
        # Visual check for common video extensions
        if any(url.lower().endswith(ext) for ext in [".mp4", ".webm", ".mov"]):
            return False

        response = self._request("GET", url, stream=True)
        if response and response.status_code == 200:
            # Extra check of content type
            ctype = response.headers.get("Content-Type", "").lower()
            if "image" not in ctype:
                return False

            try:
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(4096):
                        f.write(chunk)
                return True
            except Exception as e:
                print(f"Error escribiendo imagen {save_path}: {e}")
        return False
