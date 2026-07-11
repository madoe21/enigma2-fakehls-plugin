# Project aim

**Goal:** Stream live Enigma2 TV channels as HLS to any device (Roku, browser, VLC). Python/Twisted server + ffmpeg copy-remux via named pipe, no temp-file bloat. Runs on Enigma2 boxes (OpenATV 7.6), Kodi-portable.

**Target architecture:** `src/E2HLSServer/` — platform-agnostic core (`core/` for stream lifecycle, ffmpeg) + platform adapters (`platform/enigma2/` for config/UI, `platform/kodi/` planned). Build → `.ipk` via Makefile. Deploy via SSH. Box at `192.168.1.4`.
