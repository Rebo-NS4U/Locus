# SPDX-License-Identifier: GPL-3.0-or-later
"""A bounded browser HTTP cache for tiles actually viewed by the user."""
import os
from pathlib import Path

from PySide6.QtWebEngineCore import QWebEngineProfile


def create_map_profile(parent, *, demo=False):
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    directory = root / "locus-linux" / ("demo-map" if demo else "map")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile = QWebEngineProfile("locus-demo-map" if demo else "locus-map", parent)
    profile.setCachePath(str(directory / "http"))
    profile.setPersistentStoragePath(str(directory / "storage"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    profile.setHttpCacheMaximumSize(256 * 1024 * 1024)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.StoreInMemory)
    profile.setHttpUserAgent(profile.httpUserAgent() + " LocusLinux/0.4 (+https://github.com/Rebo-NS4U/Locus)")
    return profile
