import requests
import time
import os
import tempfile
from urllib.parse import urlsplit


class CivitaiError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code

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
        headers = {"User-Agent": self.headers["User-Agent"]}
        target = urlsplit(url)
        if target.scheme == "https" and target.hostname == "civitai.com" and target.path.startswith("/api/"):
            headers.update(self.headers)
        failure = "No se pudo conectar con Civitai"
        status_code = None
        for attempt in range(max_retries):
            try:
                response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
                
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
                    failure = f"Civitai no está disponible (HTTP {response.status_code})"
                    status_code = response.status_code
                    response.close()
                    if attempt + 1 < max_retries:
                        time.sleep(wait_time)
                else:
                    print(f"Error del servidor Civitai (Código: {response.status_code})")
                    return response
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                wait_time = (attempt + 1) * 5
                failure = "No se pudo conectar con Civitai; revisa la conexión e inténtalo de nuevo"
                status_code = None
                if attempt + 1 < max_retries:
                    time.sleep(wait_time)
        raise CivitaiError(failure, status_code)

    @staticmethod
    def _json(response):
        if response is None:
            raise CivitaiError("No se recibió una respuesta de Civitai")
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise CivitaiError("Civitai rechazó el acceso. Revisa la clave API en Ajustes.", response.status_code)
        if response.status_code != 200:
            raise CivitaiError(f"Civitai devolvió un error (HTTP {response.status_code})", response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise CivitaiError("Civitai devolvió una respuesta inválida") from exc
        if not isinstance(data, dict):
            raise CivitaiError("Civitai devolvió una respuesta inválida")
        return data

    def get_model_version_by_hash(self, file_hash):
        """Fetches model version info using a SHA256 hash."""
        url = f"{self.BASE_URL}/model-versions/by-hash/{file_hash}"
        response = self._request("GET", url)
        
        return self._json(response)

    def get_model_info(self, model_id):
        """Fetches general model info."""
        url = f"{self.BASE_URL}/models/{model_id}"
        response = self._request("GET", url)
        return self._json(response)

    def verify_api_key(self):
        """Checks if the API key is valid by fetching 1 model."""
        url = f"{self.BASE_URL}/models?limit=1"
        try:
            return bool(self._json(self._request("GET", url)))
        except CivitaiError:
            return False

    def get_models(self, query=None, username=None, model_type=None, base_model=None, sort=None, period=None, tag=None, cursor=None, limit=50):
        """Fetches models based on query parameters. Returns (items, nextCursor)."""
        params = {key: value for key, value in {
            "limit": limit, "query": query, "username": username, "types": model_type,
            "baseModel": base_model, "tag": tag, "sort": sort, "period": period, "cursor": cursor,
        }.items() if value is not None and value != ""}
        data = self._json(self._request("GET", f"{self.BASE_URL}/models", params=params))
        return (data.get("items", []), data.get("metadata", {}).get("nextCursor")) if data else ([], None)

    def get_models_by_creator(self, username, limit=50):
        """Fetches models published by a specific creator."""
        return self.get_models(username=username, limit=limit)

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
        """Extracts triggers, base model, model type, name, creator and tags from data."""
        model_data = version_data.get("model", {})
        tags = model_data.get("tags", [])
        if not tags:
            # Sometimes tags are directly in version_data or need a full model info fetch
            tags = version_data.get("tags", [])
            
        return {
            "modelName": model_data.get("name", "Unknown"),
            "creator": model_data.get("creator", {}).get("username", "Unknown"),
            "triggers": version_data.get("trainedWords", []),
            "tags": tags,
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

        response = None
        temporary_path = None
        try:
            response = self._request("GET", url, stream=True)
            if response is None or response.status_code != 200:
                return False
            if "image" not in response.headers.get("Content-Type", "").lower():
                return False
            directory = os.path.dirname(os.path.abspath(save_path))
            with tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=".vault-preview-",
                                             suffix=".download", delete=False) as stream:
                temporary_path = stream.name
                written = 0
                for chunk in response.iter_content(4096):
                    if chunk:
                        written += stream.write(chunk)
            if not written:
                return False
            os.replace(temporary_path, save_path)
            temporary_path = None
            return True
        except (CivitaiError, requests.exceptions.RequestException, OSError):
            return False
        finally:
            if response is not None:
                response.close()
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
