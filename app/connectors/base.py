from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.token_manager import get_valid_access_token


@dataclass
class RateLimitInfo:
    is_rate_limited: bool
    retry_after_seconds: int = 60


class ProviderAuthError(Exception):
    pass


class RateLimitError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limited — retry after {retry_after_seconds}s")


class BaseConnector(ABC):
    def __init__(self, integration, db: Session):
        self.integration = integration
        self.db = db

    def get_access_token(self) -> str:
        return get_valid_access_token(self.integration, self.db)

    @abstractmethod
    def execute(self, action: str, payload: dict) -> dict:
        """Execute a provider action. Returns normalized result dict."""

    @abstractmethod
    def handle_rate_limit(self, response) -> RateLimitInfo:
        """Inspect an HTTP response and return rate limit metadata."""

    def _raise_if_rate_limited(self, response) -> None:
        info = self.handle_rate_limit(response)
        if info.is_rate_limited:
            raise RateLimitError(info.retry_after_seconds)
