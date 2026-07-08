# Codebase map (onboarding 2026-07-08)

**E2HLS Server** — Enigma2 (OpenATV 7.6) plugin that live-streams TV channels
as **HLS** to any device (Roku, browser, VLC). Python, Twisted web server,
ffmpeg copy-remux via named pipe (no temp-file bloat).

## Layout (portability-aware — Kodi port intended)
- `src/config.py` — thin facade re-exporting the enigma2 platform config.
- `src/E2HLSServer/core/` — **platform-agnostic** logic: `stream_service.py`
  (stream lifecycle, `QUALITY_PRESETS`), `ffmpeg_service.py`. Keep Kodi-portable.
- `src/E2HLSServer/platform/enigma2/` — **enigma2-specific** glue:
  - `config.py` — `config.plugins.e2hlsserver.*` (ConfigSubsection). Defaults:
    **port 8003** (HTTP), stream_port 8001, stream_hw_port 8002. `Settings`
    accessors (`http_port()`, `hls_dir()`, …).
  - `http_server.py` — Twisted `Site`/`HlsRoot`. Routes: `/` `/web` (bouquet
    browser), `/player` (hls.js web player), `/stream?ref=` (legacy),
    **`/<service-ref>`** (OpenWebInterface-style root streaming), `/hls/*`
    (segments/playlist), `/status` `/logs` `/api/{bouquets,channels,start}`.
  - `ui.py` — enigma2 Setup screen; `plugin.py`-style entry.
- Build: `control/` (ipk CONTROL), ships to
  `/usr/lib/enigma2/python/Plugins/Extensions/E2HLSServer/`. `build/` is the
  staged ipk (gitignored artifact). Release via `.github/workflows/release.yml`.

## Streaming model
`get_or_create_stream(params)` (core) spawns ffmpeg reading the enigma2 stream
(`:stream_port/<ref>`) → HLS segments in `hls_dir` (`/tmp/fakehls`), served from
`/hls/`. Segments auto-expire (~30 s). Quality presets: hw_transcode,
low_latency, balanced (default), stable. Auth: `?user=&pass=` or Basic header.

## Conventions / gotchas
- Python 2/3-safe style (`from __future__ import absolute_import`), enigma2 runs
  Py3 on OpenATV 7.6 but keep imports defensive.
- Twisted `request.args`/`request.path` are **bytes** — decode explicitly.
- `_parse_basic_auth` needs `import base64` (was missing — fixed).
- Keep `core/` free of enigma2 imports so a `platform/kodi/` can be added.

## Recent change
Default port 8080→**8003**; added root-path streaming `http://<ip>:8003/<ref>`
(mirrors the STB streaming-port URL shape). See README "URL Format".
