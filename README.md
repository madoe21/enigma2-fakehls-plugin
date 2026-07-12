# E2HLS Server

[![Built with aiflow](https://img.shields.io/badge/built%20with-aiflow-6b46c1)](https://github.com/cyber93de/aiflow)

📖 [Project page & install instructions](https://madoe21.github.io/enigma2-fakehls-plugin/)

Enigma2 plugin that streams live TV channels as HLS to any device (Roku, browser, VLC, etc.).

## Features

- Live HLS streaming from Enigma2 via named pipe (no temp file bloat)
- Copy-only streaming (no re-encoding profiles)
- Configurable via Enigma2 settings menu
- Configurable internal Enigma2 stream port (4-digit)
- Segments auto-deleted after 30 seconds
- Roku, VLC, browser compatible

## URL Format

The server listens on port **8003** by default (configurable in the plugin
settings). Streaming mirrors the OpenWebInterface URL shape — just HLS on this
port:

```
# Direct HLS, OpenWebInterface style (Roku, VLC): service ref straight in the path
http://<box-ip>:8003/<service-ref>

# Web player (browser)
http://<box-ip>:8003/player?ref=<service-ref>&user=<user>&pass=<pass>

# Legacy query form (still supported)
http://<box-ip>:8003/stream?ref=<service-ref>&user=<user>&pass=<pass>
```

Example: `http://192.168.1.10:8003/1:0:19:283D:3FB:1:C00000:0:0:0:`

Special characters in password must be URL-encoded: `&` → `%26`, `$` → `%24`

Authentication can be provided either as query params (`user`, `pass`) or as `Authorization: Basic <base64(user:pass)>` header.
If neither query params nor Basic Auth header is provided, stream access is treated as unauthenticated.

## Project Structure

```
E2HLSServer/
├── src/                    # Python source files
│   ├── plugin.py           # Thin Enigma2 entry point (compat wrapper)
│   ├── E2HLSServer/        # New package layout (OpenLiga-style)
│   │   ├── plugin.py       # Real plugin entry / app singleton
│   │   ├── app.py          # AppContext wiring
│   │   ├── logger.py       # Logging
│   │   ├── locale/
│   │   │   └── de/LC_MESSAGES/
│   │   │       └── E2HLSServer.po
│   │   ├── core/           # Platform-agnostic streaming core
│   │   │   ├── ffmpeg_service.py
│   │   │   └── stream_service.py
│   │   ├── res/            # Plugin icons, favicon, QR assets
│   │   │   ├── plugin.png
│   │   │   ├── plugin_1x1.png
│   │   │   ├── plugin_4x3.png
│   │   │   ├── plugin_16x10.png
│   │   │   ├── plugin_16x9.png
│   │   │   └── favicon.png
│   │   └── platform/
│   │       ├── enigma2/    # Enigma2 adapter layer
│   │       │   ├── config.py
│   │       │   ├── http_server.py
│   │       │   └── ui.py
│   │       └── kodi/       # Kodi adapter placeholder
│   └── player_template.html
├── test.http
├── control/
│   ├── control
│   ├── postinst
│   └── prerm
├── build/                  # Generated - not in git
├── .env                    # Local box config - not in git
├── .env.example            # Template for .env
├── .gitignore
├── Makefile
└── README.md
```

## Setup

```bash
# Copy .env.example and fill in your box details
cp .env.example .env
```

## Build & Deploy

```bash
# Fast development deploy (direct file copy, no IPK)
make deploy

# Build IPK and install via opkg
make install

# Clean build directory
make clean
```

## API Smoke Tests

Use the included `test.http` to quickly verify:

- `/status`
- `/player`
- `/stream`
- `/logs`

## Box Utilities

```bash
# View last 50 lines of plugin log
make logs

# Follow plugin log live
make logs-follow

# Show box status (FFmpeg, HLS files, disk usage)
make status

# Open SSH shell to box
make shell
```

## Requirements

- Enigma2 with Python 3
- FFmpeg (`/usr/bin/ffmpeg`)

## Plugin Icons and Favicon

The transparent icon variants and favicon are stored in `src/E2HLSServer/res/`.

---

## Portability Note

The `core` package is intentionally decoupled from Enigma2 imports.
Kodi reuse is prepared by the layered structure and the `platform/kodi/` placeholder.
For Kodi, add adapter implementations for config, HTTP endpoints and UI wiring while reusing `core/` unchanged.

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🤝 Contributing

Found a bug or have a suggestion for improvement? Please create an issue or pull request.

I appreciate everyone who supports me and the project! For any requests and suggestions, feel free to provide feedback.

<p>
  <a href="https://www.buymeacoffee.com/madoe21">
    <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" height="50" alt="Buy Me a Coffee">
  </a>

  <a href="https://ko-fi.com/madoe21">
    <img src="https://storage.ko-fi.com/cdn/kofi3.png?v=3" height="50" alt="Ko-fi">
  </a>

  <a href="https://paypal.me/MartinD809">
    <img src="https://www.paypalobjects.com/webstatic/mktg/logo/pp_cc_mark_111x69.jpg" height="50" alt="PayPal">
  </a>
</p>

---

## Built with aiflow

This project was built with support from **[aiflow](https://cyber93de.github.io/aiflow/)** — *built with aiflow*.
