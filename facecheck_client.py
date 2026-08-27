from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

FACECHECK_BASE_URL = "https://facecheck.id"


class FaceCheckClient:
    """Cliente reservado para a futura integração com a API oficial do FaceCheck.ID.

    Nesta etapa o app ainda não chama este cliente. Quando o sistema de melhoria
    de imagem estiver pronto, a imagem melhorada será enviada por este módulo.
    """

    def __init__(self, api_token: str | None = None, testing_mode: bool = True) -> None:
        self.api_token = api_token or os.getenv("FACECHECK_API_TOKEN")
        self.testing_mode = testing_mode

    def _headers(self) -> dict[str, str]:
        if not self.api_token:
            raise RuntimeError(
                "FACECHECK_API_TOKEN não configurado. A integração com a busca ainda está desativada."
            )
        return {
            "accept": "application/json",
            "Authorization": self.api_token,
        }

    def upload_image(self, image_path: str | Path) -> dict[str, Any]:
        """Envia uma imagem para /api/upload_pic e retorna a resposta JSON.

        Método pronto para a próxima etapa; não é chamado pela interface atual.
        """
        image_path = Path(image_path)
        with image_path.open("rb") as image_file:
            response = requests.post(
                f"{FACECHECK_BASE_URL}/api/upload_pic",
                headers=self._headers(),
                files={"images": image_file},
                timeout=60,
            )
        response.raise_for_status()
        return response.json()

    def search(self, id_search: str) -> dict[str, Any]:
        """Inicia/consulta uma busca do FaceCheck para um id_search existente."""
        response = requests.post(
            f"{FACECHECK_BASE_URL}/api/search",
            headers=self._headers(),
            json={
                "id_search": id_search,
                "with_progress": True,
                "status_only": False,
                "demo": self.testing_mode,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()