# -*- coding: utf-8 -*-
"""Softcam stream-relay whitelist support.

OpenATV descrambles some services (ICAM) through a local softcam stream
relay instead of the plain streaming port — pulling such a service from
port 8001/8002 yields a scrambled, broken transport stream (visible as
dropouts). Services whose reference is listed in the receiver's
``whitelist_streamrelay`` file must be fetched from the relay port.
"""
from __future__ import absolute_import

import os

DEFAULT_STREAMRELAY_PORT = 17999
WHITELIST_PATH = "/etc/enigma2/whitelist_streamrelay"


def normalize_service_ref(ref):
    """Canonical form for comparing service references.

    Whitelist entries and URL-supplied refs differ in case and in the
    presence of a trailing colon; both are irrelevant to identity.
    """
    return ref.strip().upper().rstrip(":")


class StreamRelayWhitelist(object):
    """The receiver's stream-relay whitelist, reloaded when the file changes."""

    def __init__(self, path=WHITELIST_PATH):
        self._path = path
        self._cache_key = None
        self._refs = frozenset()

    def refs(self):
        """Current set of normalized whitelisted refs (cached per file state)."""
        try:
            stat = os.stat(self._path)
            # mtime alone can miss two writes within one timestamp tick on
            # coarse-granularity flash filesystems; size closes that gap.
            cache_key = (stat.st_mtime, stat.st_size)
        except OSError:  # no whitelist on this receiver
            self._cache_key = None
            self._refs = frozenset()
            return self._refs

        if cache_key != self._cache_key:
            loaded = self._load()
            if loaded is None:
                # Transient read failure (file being replaced): keep the old
                # refs and retry on the next call instead of caching a miss —
                # a wrongly empty whitelist routes ICAM refs to the plain
                # port and the picture breaks silently.
                return self._refs
            self._refs = loaded
            self._cache_key = cache_key
        return self._refs

    def contains(self, ref):
        return normalize_service_ref(ref) in self.refs()

    def _load(self):
        refs = set()
        try:
            with open(self._path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        refs.add(normalize_service_ref(line))
        except Exception as exc:
            print("[E2HLSServer] WARNING: cannot read stream-relay whitelist "
                  + self._path + ": " + str(exc))
            return None
        return frozenset(refs)
