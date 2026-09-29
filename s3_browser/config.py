"""Configuration, SSO profiles, and bookmarks management."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


APP_DIR = Path.home() / ".s3_browser"
PROFILES_FILE = APP_DIR / "profiles.json"
BOOKMARKS_FILE = APP_DIR / "bookmarks.json"
SETTINGS_FILE = APP_DIR / "settings.json"


@dataclass
class SSOProfile:
    """AWS SSO Profile definition with the 4 required fields."""
    profile_name: str
    start_url: str
    sso_region: str
    account_id: str
    role_name: str
    default_region: str = "us-east-1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SSOProfile:
        return cls(
            profile_name=data.get("profile_name", ""),
            start_url=data.get("start_url", ""),
            sso_region=data.get("sso_region", ""),
            account_id=data.get("account_id", ""),
            role_name=data.get("role_name", ""),
            default_region=data.get("default_region", "us-east-1"),
        )


@dataclass
class BookmarkItem:
    """Leaf bookmark pointing to profile and S3 URI."""
    name: str
    profile: str
    path: str  # e.g., "s3://bucket/folder/subfolder/"
    type: str = "bookmark"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BookmarkItem:
        return cls(
            name=data.get("name", "Untitled"),
            profile=data.get("profile", ""),
            path=data.get("path", ""),
            type="bookmark",
        )


@dataclass
class BookmarkFolder:
    """Cascading category folder containing bookmarks or sub-folders."""
    name: str
    children: List[BookmarkFolder | BookmarkItem] = field(default_factory=list)
    type: str = "folder"

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": "folder",
            "name": self.name,
            "children": [],
        }
        for child in self.children:
            res["children"].append(child.to_dict())
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BookmarkFolder:
        folder = cls(name=data.get("name", "New Folder"))
        for child_data in data.get("children", []):
            if child_data.get("type") == "folder":
                folder.children.append(BookmarkFolder.from_dict(child_data))
            else:
                folder.children.append(BookmarkItem.from_dict(child_data))
        return folder


@dataclass
class AppSettings:
    """Global user settings."""
    default_download_dir: str = field(
        default_factory=lambda: str(Path.home() / "Downloads")
    )
    max_concurrency: int = 30
    multipart_chunksize_mb: int = 16
    demo_mode: bool = False
    last_profile: str = ""
    last_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppSettings:
        default_dl = str(Path.home() / "Downloads")
        return cls(
            default_download_dir=data.get("default_download_dir", default_dl),
            max_concurrency=int(data.get("max_concurrency", 30)),
            multipart_chunksize_mb=int(data.get("multipart_chunksize_mb", 16)),
            demo_mode=bool(data.get("demo_mode", False)),
            last_profile=data.get("last_profile", ""),
            last_path=data.get("last_path", ""),
        )


class ConfigManager:
    """Manages loading and saving application state, profiles, and bookmarks."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or APP_DIR
        self.profiles_file = self.base_dir / "profiles.json"
        self.bookmarks_file = self.base_dir / "bookmarks.json"
        self.settings_file = self.base_dir / "settings.json"

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.profiles: Dict[str, SSOProfile] = {}
        self.bookmarks: List[BookmarkFolder | BookmarkItem] = []
        self.settings: AppSettings = AppSettings()

        self.load_all()

    def load_all(self) -> None:
        self.load_settings()
        self.load_profiles()
        self.load_bookmarks()

    def load_profiles(self) -> None:
        self.profiles.clear()
        if self.profiles_file.exists():
            try:
                with open(self.profiles_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        prof = SSOProfile.from_dict(item)
                        if prof.profile_name:
                            self.profiles[prof.profile_name] = prof
            except Exception as e:
                print(f"Error loading profiles: {e}")

    def save_profiles(self) -> None:
        try:
            with open(self.profiles_file, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in self.profiles.values()], f, indent=2)
        except Exception as e:
            print(f"Error saving profiles: {e}")

    def add_or_update_profile(self, profile: SSOProfile) -> None:
        self.profiles[profile.profile_name] = profile
        self.save_profiles()

    def delete_profile(self, profile_name: str) -> None:
        if profile_name in self.profiles:
            del self.profiles[profile_name]
            self.save_profiles()

    def load_bookmarks(self) -> None:
        self.bookmarks.clear()
        if self.bookmarks_file.exists():
            try:
                with open(self.bookmarks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        if item.get("type") == "folder":
                            self.bookmarks.append(BookmarkFolder.from_dict(item))
                        else:
                            self.bookmarks.append(BookmarkItem.from_dict(item))
            except Exception as e:
                print(f"Error loading bookmarks: {e}")
        else:
            # Seed default starter bookmarks structure
            self.bookmarks = [
                BookmarkFolder(
                    name="Examples",
                    children=[
                        BookmarkItem(
                            name="Sample Public Data",
                            profile="Default",
                            path="s3://commoncrawl/",
                        )
                    ],
                )
            ]
            self.save_bookmarks()

    def save_bookmarks(self) -> None:
        try:
            with open(self.bookmarks_file, "w", encoding="utf-8") as f:
                json.dump([b.to_dict() for b in self.bookmarks], f, indent=2)
        except Exception as e:
            print(f"Error saving bookmarks: {e}")

    def add_bookmark(self, item: BookmarkItem, folder_name: Optional[str] = None) -> None:
        if folder_name:
            # Find existing folder or create it
            target_folder = None
            for b in self.bookmarks:
                if isinstance(b, BookmarkFolder) and b.name.strip().lower() == folder_name.strip().lower():
                    target_folder = b
                    break
            if not target_folder:
                target_folder = BookmarkFolder(name=folder_name)
                self.bookmarks.append(target_folder)
            target_folder.children.append(item)
        else:
            self.bookmarks.append(item)
        self.save_bookmarks()

    def is_descendant(
        self, ancestor: BookmarkFolder, candidate: BookmarkFolder | BookmarkItem
    ) -> bool:
        """Checks if candidate is a direct or indirect child of ancestor."""
        for child in ancestor.children:
            if child is candidate:
                return True
            if isinstance(child, BookmarkFolder) and self.is_descendant(child, candidate):
                return True
        return False

    def remove_bookmark(self, item_or_folder: BookmarkFolder | BookmarkItem) -> bool:
        """Removes a bookmark or folder from wherever it is in the hierarchy."""
        def _remove(container: List[BookmarkFolder | BookmarkItem]) -> bool:
            if item_or_folder in container:
                container.remove(item_or_folder)
                return True
            for b in container:
                if isinstance(b, BookmarkFolder):
                    if _remove(b.children):
                        return True
            return False

        removed = _remove(self.bookmarks)
        if removed:
            self.save_bookmarks()
        return removed

    def move_bookmark(
        self,
        item: BookmarkFolder | BookmarkItem,
        target_folder: Optional[BookmarkFolder] = None,
    ) -> bool:
        """Moves a bookmark or folder to target_folder (or root if None)."""
        if isinstance(item, BookmarkFolder) and target_folder is not None:
            if item is target_folder or self.is_descendant(item, target_folder):
                return False

        if not self.remove_bookmark(item):
            return False

        if target_folder is None:
            self.bookmarks.append(item)
        else:
            target_folder.children.append(item)

        self.save_bookmarks()
        return True

    def load_settings(self) -> None:
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    self.settings = AppSettings.from_dict(json.load(f))
            except Exception as e:
                print(f"Error loading settings: {e}")
                self.settings = AppSettings()
        else:
            self.settings = AppSettings()
            self.save_settings()

    def save_settings(self) -> None:
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings.to_dict(), f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")
