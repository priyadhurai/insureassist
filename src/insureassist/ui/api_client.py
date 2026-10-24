"""The web UI's only door to the data: the InsureAssist API, called over HTTP.

The UI never reads the database or the vector store itself (except the admin-only
developer page), so every permission check happens in the API, for every user.
"""

from typing import Any

import httpx

from insureassist.config import settings


class ApiClientError(Exception):
    """An API error, with the API's own safe message and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ApiClient:
    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.token = token
        self._http = httpx.Client(
            base_url=f"{(base_url or settings.api_base_url).rstrip('/')}/api/v1", timeout=90
        )

    # --- calls -------------------------------------------------------------------------

    def login(self, email: str, password: str) -> str:
        data = self._call("POST", "/auth/token", data={"username": email, "password": password})
        self.token = str(data["access_token"])
        return self.token

    def me(self) -> dict[str, Any]:
        return self._call("GET", "/auth/me")

    def documents(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self._call("GET", "/documents?limit=100")["items"]
        return items

    def upload(self, filename: str, content: bytes, share_with_viewers: bool) -> dict[str, Any]:
        form = {"shared_with_roles": ["viewer"]} if share_with_viewers else {}
        return self._call(
            "POST", "/documents", data=form, files={"file": (filename, content, "application/pdf")}
        )

    def ask(self, question: str, top_k: int) -> dict[str, Any]:
        return self._call("POST", "/chat/query", json={"question": question, "top_k": top_k})

    def feedback(self, query_id: str, rating: str) -> dict[str, Any]:
        return self._call("POST", "/feedback", json={"query_id": query_id, "rating": rating})

    # --- plumbing ----------------------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise ApiClientError(
                0, "api_unreachable", "The API is not reachable. Is `insureassist serve` running?"
            ) from None
        if response.status_code >= 400:
            try:
                error = response.json()["error"]
                raise ApiClientError(response.status_code, error["code"], error["message"])
            except (ValueError, KeyError, TypeError):
                raise ApiClientError(
                    response.status_code, "error", f"Request failed ({response.status_code})."
                ) from None
        result: dict[str, Any] = response.json()
        return result
