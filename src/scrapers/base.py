"""Base scraper interface."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List

import httpx

from ..models import ContentItem


class BaseScraper(ABC):
    """Abstract base class for all scrapers."""

    def __init__(self, http_client: httpx.AsyncClient):
        self.client = http_client

    @abstractmethod
    async def fetch(self, since: datetime) -> List[ContentItem]:
        """Fetch content items published after `since`."""
        ...

    @staticmethod
    def _make_id(source_type: str, subtype: str, native_id: str) -> str:
        return f"{source_type}:{subtype}:{native_id}"
