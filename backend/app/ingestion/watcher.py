"""Polling folder scanner with stability checks and startup reconciliation."""

import time
from pathlib import Path

from .registry import DocumentRegistry


class FolderWatcher:
    def __init__(self, folder: Path, registry: DocumentRegistry, max_file_bytes: int,
                 supported_suffixes=frozenset({".pdf", ".jpg", ".jpeg", ".png"})):
        self.folder = Path(folder).resolve()
        self.registry = registry
        self.max_file_bytes = max_file_bytes
        self.supported_suffixes = {suffix.lower() for suffix in supported_suffixes}
        self._signatures = {}
        self._hash_cache = {}
        self._observed = {}

    def scan_once(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        live_paths = set()
        current_signatures = {}
        for path in self.folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in self.supported_suffixes:
                continue
            try:
                resolved = path.resolve(strict=True)
                # Reject symlinks that escape the configured input folder.
                resolved.relative_to(self.folder)
                before = resolved.stat()
                canonical = str(resolved)
                live_paths.add(canonical)
                if before.st_size > self.max_file_bytes:
                    continue
                signature = (before.st_size, before.st_mtime_ns)
                current_signatures[canonical] = signature
                if self._signatures.get(canonical) != signature:
                    continue
                if self._observed.get(canonical) == signature:
                    continue
                cached = self._hash_cache.get(canonical)
                content_hash = (cached[1] if cached and cached[0] == signature
                                else self.registry.hash_file(resolved))
                after = resolved.stat()
                if (after.st_size, after.st_mtime_ns) != signature:
                    current_signatures.pop(canonical, None)
                    self._hash_cache.pop(canonical, None)
                    self._observed.pop(canonical, None)
                    continue
                self.registry.observe(resolved, content_hash)
                self._hash_cache[canonical] = (signature, content_hash)
                self._observed[canonical] = signature
            except (OSError, ValueError):
                # The file may still be moving, writing, or may be an escaping link.
                continue
        self.registry.mark_missing(live_paths)
        self._signatures = current_signatures
        self._hash_cache = {path: value for path, value in self._hash_cache.items()
                            if current_signatures.get(path) == value[0]}
        self._observed = {path: signature for path, signature in self._observed.items()
                          if current_signatures.get(path) == signature}

    def run_forever(self, interval_seconds: float) -> None:
        while True:
            self.scan_once()
            time.sleep(interval_seconds)
