"""Shopping list parser and Google Keep integration."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .config import ShoppingListConfig

LOGGER = logging.getLogger("pulse.shopping")

BASE_URL = "https://keep.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_LIST_TITLE = "Shopping list"
MAX_LIST_ITEMS = 1000

DEFAULT_COMPOUND_ITEMS: tuple[str, ...] = (
    "peanut butter",
    "almond butter",
    "corn flour",
    "powdered sugar",
    "brown sugar",
    "baking soda",
    "baking powder",
    "olive oil",
    "coconut oil",
    "maple syrup",
    "vanilla extract",
    "ice cream",
    "orange juice",
    "apple juice",
    "chicken broth",
    "vegetable broth",
    "tomato sauce",
    "salsa verde",
    "sour cream",
    "heavy cream",
    "bacon bits",
    "brioche bread",
    "toilet paper",
    "paper towels",
    "dish soap",
)

ITEM_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "then",
    "next",
    "please",
    "some",
    "more",
    "just",
}
ADJECTIVE_PREFIXES = {
    "sliced",
    "diced",
    "ground",
    "shredded",
    "frozen",
    "fresh",
    "organic",
    "smoked",
    "boneless",
    "skinless",
    "whole",
    "large",
    "small",
    "medium",
}
SHOPPING_KEYWORDS = ("shopping list", "grocery list")


class ShoppingListError(Exception):
    """Raised when shopping list operations fail."""


@dataclass(slots=True)
class ShoppingListEntry:
    label: str
    checked: bool = False


@dataclass(slots=True)
class ShoppingListCommand:
    action: str
    items: list[str]


@dataclass(slots=True)
class ShoppingListAddResult:
    added: list[str]
    reactivated: list[str]
    duplicates: list[str]
    entries: list[ShoppingListEntry] | None = None


@dataclass(slots=True)
class ShoppingListRemoveResult:
    removed: list[str]
    missing: list[str]
    entries: list[ShoppingListEntry] | None = None


@dataclass(slots=True)
class ShoppingListClearResult:
    cleared: int
    entries: list[ShoppingListEntry] | None = None


@dataclass(slots=True)
class ShoppingListView:
    items: list[ShoppingListEntry]

    @property
    def remaining(self) -> int:
        return sum(1 for item in self.items if not item.checked)


def _normalize_note_name(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if trimmed.startswith("notes/"):
        return trimmed
    return f"notes/{trimmed}"


def _normalize_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value.strip())
    return text


def _normalize_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", value.lower())
    tokens = [_singularize(token) for token in cleaned.split() if token and token not in ITEM_STOPWORDS]
    if not tokens:
        return ""
    return " ".join(tokens)


def _singularize(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ves"):
        return word[:-3] + "f"
    if len(word) > 3 and word.endswith("es") and not word.endswith(("ses", "xes", "zes")):
        return word[:-2]
    if len(word) > 2 and word.endswith("s") and word[-2] != "s":
        return word[:-1]
    return word


class ShoppingListParser:
    """Parse transcripts into actionable shopping list commands."""

    def __init__(self, compound_items: Iterable[str] | None = None) -> None:
        compounds = set(DEFAULT_COMPOUND_ITEMS)
        if compound_items:
            for item in compound_items:
                normalized = item.strip().lower()
                if normalized:
                    compounds.add(normalized)
        self._compound_items = tuple(sorted(compounds))

    def parse(self, transcript: str) -> ShoppingListCommand | None:
        lowered = transcript.lower()
        if not any(keyword in lowered for keyword in SHOPPING_KEYWORDS):
            return None

        add_match = re.search(
            r"(?:add|put|insert|include|throw)\s+(.+?)\s+(?:onto?|to)\s+(?:my\s+)?(?:shopping|grocery)\s+list",
            lowered,
            flags=re.IGNORECASE,
        )
        if add_match:
            span = add_match.span(1)
            items = self._split_items(transcript[slice(*span)])
            if items:
                return ShoppingListCommand("add", items)

        remove_match = re.search(
            r"(?:remove|delete|drop|take off)\s+(.+?)\s+from\s+(?:my\s+)?(?:shopping|grocery)\s+list",
            lowered,
            flags=re.IGNORECASE,
        )
        if remove_match:
            span = remove_match.span(1)
            items = self._split_items(transcript[slice(*span)])
            if items:
                return ShoppingListCommand("remove", items)

        if re.search(r"\b(erase|clear|clean|reset|start over)\b", lowered):
            if any(keyword in lowered for keyword in SHOPPING_KEYWORDS):
                return ShoppingListCommand("clear", [])

        if re.search(r"\b(show|what|list|display|read|tell me)\b", lowered):
            return ShoppingListCommand("show", [])
        if "on my shopping list" in lowered:
            return ShoppingListCommand("show", [])
        return None

    def _split_items(self, raw_items: str) -> list[str]:
        text = raw_items.strip()
        if not text:
            return []
        temp = re.sub(r"\bcomma\b", ",", text, flags=re.IGNORECASE)
        temp = re.sub(r"\bplus\b", " and ", temp, flags=re.IGNORECASE)
        chunks = re.split(r",|(?:\band\b)|(?:\balso\b)|(?:\bwith\b)", temp, flags=re.IGNORECASE)
        items = [_normalize_text(chunk) for chunk in chunks if chunk and chunk.strip()]
        if len(items) > 1:
            return items
        return self._split_by_space(temp)

    def _split_by_space(self, raw_text: str) -> list[str]:
        tokens = [token for token in raw_text.strip().split() if token]
        if len(tokens) <= 1:
            return [raw_text.strip()]
        compound_set = set(self._compound_items)
        idx = 0
        parsed: list[str] = []
        while idx < len(tokens):
            matched = False
            for size in range(3, 0, -1):
                end = idx + size
                if end > len(tokens):
                    continue
                candidate = " ".join(tokens[idx:end]).lower()
                if candidate in compound_set:
                    parsed.append(_normalize_text(" ".join(tokens[idx:end])))
                    idx = end
                    matched = True
                    break
            if matched:
                continue
            word = tokens[idx]
            if word.lower() in ADJECTIVE_PREFIXES and idx + 1 < len(tokens):
                parsed.append(_normalize_text(f"{word} {tokens[idx + 1]}"))
                idx += 2
                continue
            parsed.append(_normalize_text(word))
            idx += 1
        return parsed


class ShoppingListService:
    """Coordinate local parsing with the remote Google Keep API."""

    def __init__(
        self,
        config: ShoppingListConfig,
        *,
        logger: logging.Logger | None = None,
        http_timeout: float = 10.0,
    ) -> None:
        self.config = config
        self.logger = logger or LOGGER
        self._timeout = http_timeout
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._note_name: str | None = _normalize_note_name(config.note_id)
        compound_items = config.compound_items or ()
        self.parser = ShoppingListParser(compound_items=compound_items)
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def add_items(
        self,
        raw_items: Sequence[str],
        *,
        include_entries: bool = False,
    ) -> ShoppingListAddResult:
        items = [_normalize_text(item) for item in raw_items if item and item.strip()]
        if not items:
            raise ShoppingListError("No shopping items provided.")
        async with self._lock:
            note_name, entries = await self._load_entries()
            existing = {_normalize_key(entry.label): entry for entry in entries}
            added: list[str] = []
            reactivated: list[str] = []
            duplicates: list[str] = []
            for item in items:
                key = _normalize_key(item)
                if not key:
                    continue
                existing_entry = existing.get(key)
                if existing_entry:
                    if existing_entry.checked:
                        existing_entry.checked = False
                        reactivated.append(existing_entry.label)
                    else:
                        duplicates.append(existing_entry.label)
                    continue
                if len(entries) >= MAX_LIST_ITEMS:
                    raise ShoppingListError("Shopping list is full.")
                entry = ShoppingListEntry(label=item, checked=False)
                entries.append(entry)
                existing[key] = entry
                added.append(item)
            if added or reactivated:
                await self._save_entries(note_name, entries)
            result = ShoppingListAddResult(
                added=added,
                reactivated=reactivated,
                duplicates=duplicates,
                entries=list(entries) if include_entries else None,
            )
            return result

    async def remove_items(
        self,
        raw_items: Sequence[str],
        *,
        indexes: Sequence[int] | None = None,
        include_entries: bool = False,
    ) -> ShoppingListRemoveResult:
        items = [_normalize_text(item) for item in raw_items if item and item.strip()]
        async with self._lock:
            note_name, entries = await self._load_entries()
            index_targets: set[int] = set()
            if indexes:
                for idx in indexes:
                    try:
                        position = int(idx)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= position < len(entries):
                        index_targets.add(position)
            if not items and not index_targets:
                raise ShoppingListError("No shopping items provided.")
            removed: list[str] = []
            missing: list[str] = []
            remaining_entries: list[ShoppingListEntry] = []
            normalized_targets = {_normalize_key(item): item for item in items if _normalize_key(item)}
            for idx, entry in enumerate(entries):
                if idx in index_targets:
                    removed.append(entry.label)
                    continue
                key = _normalize_key(entry.label)
                if key in normalized_targets and normalized_targets[key] not in removed:
                    removed.append(entry.label)
                    continue
                remaining_entries.append(entry)
            removed_keys = {_normalize_key(label) for label in removed}
            for item in items:
                key = _normalize_key(item)
                if key not in removed_keys:
                    missing.append(item)
            if len(remaining_entries) != len(entries):
                await self._save_entries(note_name, remaining_entries)
                entries = remaining_entries
            result = ShoppingListRemoveResult(
                removed=removed,
                missing=missing,
                entries=list(entries) if include_entries else None,
            )
            return result

    async def clear(self, *, include_entries: bool = False) -> ShoppingListClearResult:
        async with self._lock:
            note_name, entries = await self._load_entries()
            cleared = len(entries)
            if cleared:
                await self._save_entries(note_name, [])
                entries = []
            return ShoppingListClearResult(cleared=cleared, entries=list(entries) if include_entries else None)

    async def list_items(self) -> ShoppingListView:
        _, entries = await self._load_entries()
        return ShoppingListView(items=list(entries))

    def build_card(self, view: ShoppingListView, *, subtitle: str | None = None) -> dict[str, Any]:
        total = len(view.items)
        remaining = view.remaining
        default_subtitle = (
            "Your shopping list is empty." if total == 0 else f"{remaining} item{'s' if remaining != 1 else ''} to buy."
        )
        payload = {
            "type": "shopping_list",
            "title": self.config.list_title or DEFAULT_LIST_TITLE,
            "text": subtitle or default_subtitle,
            "items": [
                {"label": entry.label, "checked": entry.checked, "index": idx} for idx, entry in enumerate(view.items)
            ],
        }
        return payload

    async def _load_entries(self) -> tuple[str, list[ShoppingListEntry]]:
        name = await self._resolve_note_name()
        note = await self._request("GET", name)
        body = (note or {}).get("body") or {}
        list_content = body.get("list")
        if list_content is None:
            if not body:
                list_content = {"listItems": []}
            else:
                raise ShoppingListError("Configured Keep note is not a list.")
        raw_items = list_content.get("listItems") or []
        entries: list[ShoppingListEntry] = []
        for item in raw_items:
            text_block = item.get("text") or {}
            label = str(text_block.get("text") or "").strip()
            if not label:
                continue
            entries.append(ShoppingListEntry(label=label, checked=bool(item.get("checked"))))
        return name, entries

    async def _save_entries(self, name: str, entries: list[ShoppingListEntry]) -> None:
        payload = {
            "body": {
                "list": {
                    "listItems": [
                        {
                            "text": {"text": entry.label},
                            "checked": entry.checked,
                        }
                        for entry in entries
                    ],
                }
            }
        }
        params = {"updateMask": "body.list"}
        await self._request("PATCH", name, params=params, json=payload)

    async def _resolve_note_name(self) -> str:
        cached = self._note_name
        if cached:
            return cached
        if not self.enabled:
            raise ShoppingListError("Shopping list is not configured.")
        if self.config.note_id:
            self._note_name = _normalize_note_name(self.config.note_id)
            if self._note_name:
                return self._note_name
        note = await self._find_note_by_title()
        if note:
            self._note_name = note
            return note
        created = await self._create_note()
        self._note_name = created
        return created

    async def _find_note_by_title(self) -> str | None:
        title = (self.config.list_title or DEFAULT_LIST_TITLE).strip().lower()
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 100, "filter": "trashed = false"}
            if page_token:
                params["pageToken"] = page_token
            payload = await self._request("GET", "notes", params=params)
            for note in (payload or {}).get("notes", []):
                candidate = str(note.get("title") or "").strip().lower()
                if candidate == title:
                    return note.get("name")
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return None

    async def _create_note(self) -> str:
        payload = {
            "title": self.config.list_title or DEFAULT_LIST_TITLE,
            "body": {"list": {"listItems": []}},
        }
        data = await self._request("POST", "notes", json=payload)
        name = (data or {}).get("name")
        if not name:
            raise ShoppingListError("Failed to create shopping list note.")
        return name

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise ShoppingListError("Shopping list is not configured.")
        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        url = f"{BASE_URL}/{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, params=params, json=json, headers=headers)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401 and retry:
                self._token = None
                return await self._request(method, path, params=params, json=json, retry=False)
            if status == 404:
                if path.startswith("notes/"):
                    self._note_name = None
                raise ShoppingListError("Shopping list note was not found.") from exc
            raise ShoppingListError(f"Keep API request failed: {status}") from exc
        except httpx.RequestError as exc:
            raise ShoppingListError("Unable to reach Google Keep.") from exc

    async def _get_access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        if not (self.config.keep_client_id and self.config.keep_client_secret and self.config.keep_refresh_token):
            raise ShoppingListError("Shopping list credentials are missing.")
        data = {
            "client_id": self.config.keep_client_id,
            "client_secret": self.config.keep_client_secret,
            "refresh_token": self.config.keep_refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(TOKEN_URL, data=data)
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token")
            expires_in = float(payload.get("expires_in", 3600))
            if not token:
                raise ShoppingListError("Google OAuth response missing access token.")
            self._token = token
            self._token_expiry = time.time() + max(30.0, expires_in)
            return token
        except httpx.HTTPStatusError as exc:
            raise ShoppingListError("Failed to refresh Google Keep access token.") from exc
        except httpx.RequestError as exc:
            raise ShoppingListError("Unable to reach Google OAuth endpoint.") from exc
