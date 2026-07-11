# -*- coding: utf-8 -*-
from __future__ import absolute_import

import base64
import json
import os
import re
import urllib.parse

from twisted.internet import reactor
from twisted.web import static
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET, Site

from .config import HTML_TEMPLATE_FILE, get_favicon_path

try:
    from enigma import eServiceCenter, eServiceReference
except ImportError:  # not running inside enigma2 (dev machine, tests)
    eServiceCenter = None
    eServiceReference = None

# enigma2 stores the channel lists (bouquets) as plain text files here.
BOUQUET_DIR = "/etc/enigma2"

_WEB_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>E2HLS</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest/dist/hls.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d0d; color: #f0f0f0; font-family: sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
#bouquet-bar { display: none; gap: 0.4rem; padding: 0.5rem 1rem; background: #111; border-bottom: 1px solid #2a2a2a; flex-shrink: 0; flex-wrap: wrap; align-items: center; }
#bouquet-bar span { font-size: 0.8rem; color: #555; margin-right: 0.3rem; }
.bq-btn { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 3px; color: #aaa; padding: 0.3rem 0.8rem; font-size: 0.85rem; cursor: pointer; }
.bq-btn:hover { border-color: #00cc88; color: #00cc88; }
.bq-btn.active { background: #003322; border-color: #00cc88; color: #00cc88; }
#main { flex: 1; display: flex; overflow: hidden; }
#list { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; border-right: 1px solid #2a2a2a; background: #111; overflow: hidden; transition: width 0.25s ease, opacity 0.25s ease; }
#list.hidden { width: 0; opacity: 0; border-right: none; pointer-events: none; }
#search { background: #1a1a1a; border: none; border-bottom: 1px solid #2a2a2a; padding: 0.7rem 1rem; color: #f0f0f0; font-size: 0.9rem; outline: none; flex-shrink: 0; width: 100%; }
#channels { flex: 1; overflow-y: auto; }
.ch { padding: 0.65rem 1rem; cursor: pointer; border-bottom: 1px solid #1a1a1a; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ch:hover { background: #1e1e1e; }
.ch.active { background: #003322; color: #00cc88; border-left: 3px solid #00cc88; padding-left: calc(1rem - 3px); }
#right { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
/* min-height:0 lets the flex item shrink below the video's intrinsic size —
   without it the video pushes the URL bar out of the viewport. */
#video-area { flex: 1; min-height: 0; background: #000; display: flex; align-items: center; justify-content: center; position: relative; overflow: hidden; }
video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
#fullscreen-btn { position: absolute; bottom: 0.8rem; right: 0.8rem; z-index: 10; background: rgba(0,0,0,0.7); border: 1px solid #333; border-radius: 6px; color: #aaa; font-size: 1.1rem; width: 40px; height: 40px; cursor: pointer; display: none; align-items: center; justify-content: center; user-select: none; }
#fullscreen-btn:hover { background: rgba(0,204,136,0.2); color: #00cc88; border-color: #00cc88; }
#video-area.playing #fullscreen-btn { display: flex; }
#placeholder { color: #444; font-size: 1rem; text-align: center; line-height: 2.2; }
#toggle-list { position: absolute; top: 50%; left: 0; transform: translateY(-50%); z-index: 10; background: rgba(0,0,0,0.7); border: 1px solid #333; border-left: none; border-radius: 0 6px 6px 0; color: #aaa; font-size: 1.1rem; width: 22px; height: 56px; cursor: pointer; display: flex; align-items: center; justify-content: center; user-select: none; }
#toggle-list:hover { background: rgba(0,204,136,0.2); color: #00cc88; }
#now { position: absolute; top: 0.8rem; left: 0.8rem; background: rgba(0,0,0,0.75); border: 1px solid #00cc88; border-radius: 3px; padding: 0.3rem 0.7rem; font-size: 0.85rem; color: #00cc88; display: none; }
#loading { position: absolute; inset: 0; background: rgba(0,0,0,0.6); display: none; align-items: center; justify-content: center; flex-direction: column; gap: 1rem; }
#loading.show { display: flex; }
.spinner { width: 40px; height: 40px; border: 3px solid rgba(0,204,136,0.2); border-top-color: #00cc88; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
#msg { position: absolute; bottom: 0.8rem; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.75); border-radius: 3px; padding: 0.3rem 0.8rem; font-size: 0.85rem; color: #aaa; display: none; white-space: nowrap; }
#msg.show { display: block; }
#url-bar { flex-shrink: 0; display: none; align-items: center; gap: 0.5rem; padding: 0.4rem 1rem; background: #0d0d0d; border-top: 1px solid #1e1e1e; font-size: 0.8rem; }
#url-bar span { color: #444; white-space: nowrap; }
#url-text { flex: 1; background: #111; border: 1px solid #222; border-radius: 3px; padding: 0.3rem 0.6rem; color: #555; font-family: monospace; font-size: 0.78rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: text; user-select: all; }
#url-text:hover { color: #888; border-color: #333; }
#copy-btn { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 3px; color: #555; font-size: 0.78rem; padding: 0.3rem 0.7rem; cursor: pointer; }
#copy-btn:hover { color: #00cc88; border-color: #00cc88; }
</style>
</head>
<body>
<div id="top-bar" style="display:flex;gap:0.5rem;padding:0.5rem 1rem;background:#1a1a1a;border-bottom:1px solid #2a2a2a;flex-shrink:0;align-items:center;">
  <span style="font-size:0.8rem;color:#555;">Qualit&auml;t:</span>
  <select id="quality-select" onchange="onQualityChange()" style="background:#0d0d0d;border:1px solid #333;border-radius:4px;padding:0.4rem 0.7rem;color:#f0f0f0;font-size:0.85rem;outline:none;cursor:pointer;">
    <option value="hw_transcode">Hardware-Transcode (H.264)</option>
    <option value="low_latency">Original Niedrige Latenz (1s)</option>
    <option value="balanced" selected>Original Ausgewogen (2s)</option>
    <option value="stable">Original Stabil (4s)</option>
  </select>
</div>
<div id="bouquet-bar"><span>Bouquet:</span></div>
<div id="main">
  <div id="list">
    <input id="search" type="text" placeholder="Sender suchen&#8230;" oninput="filterChannels(this.value)" />
    <div id="channels"></div>
  </div>
  <div id="right">
    <div id="video-area">
      <button id="toggle-list" onclick="toggleList()">&#9664;</button>
      <div id="placeholder">Lade Senderliste&#8230;</div>
      <video id="v" controls style="display:none"></video>
      <button id="fullscreen-btn" onclick="toggleFullscreen()" title="Vollbild (Escape beendet)">&#x26F6;</button>
      <div id="now"></div>
      <div id="loading"><div class="spinner"></div><span id="loading-text" style="color:#aaa;font-size:0.9rem">Starte Stream&#8230;</span></div>
      <div id="msg"></div>
    </div>
    <div id="url-bar">
      <span>HLS-URL:</span>
      <div id="url-text"></div>
      <button id="copy-btn" onclick="copyUrl()">Kopieren</button>
    </div>
  </div>
</div>
<script>
let all = [], filtered = [], cur = -1, listVisible = true, hls = null, bufferTimer = null;
const PREBUFFER_SECONDS = 12;

// Hold playback until enough forward buffer exists. Starting immediately on a
// fresh stream means playing exactly as fast as ffmpeg produces — zero
// reserve, so every hiccup stalls. Waiting once up front buys smoothness.
function startWhenBuffered(v) {
  if (bufferTimer) clearInterval(bufferTimer);
  const t0 = Date.now();
  bufferTimer = setInterval(() => {
    let buf = 0;
    if (v.buffered.length) buf = v.buffered.end(v.buffered.length - 1) - v.currentTime;
    const waited = (Date.now() - t0) / 1000;
    document.getElementById('loading-text').textContent =
      'Puffere… ' + Math.floor(Math.min(buf, PREBUFFER_SECONDS)) + '/' + PREBUFFER_SECONDS + 's';
    if (buf >= PREBUFFER_SECONDS || waited > PREBUFFER_SECONDS + 15) {
      clearInterval(bufferTimer);
      bufferTimer = null;
      document.getElementById('loading').classList.remove('show');
      v.play().catch(() => {});
    }
  }, 250);
}

function onQualityChange() { if (cur >= 0) play(cur); }

function toggleList() {
  listVisible = !listVisible;
  document.getElementById('list').classList.toggle('hidden', !listVisible);
  document.getElementById('toggle-list').innerHTML = listVisible ? '&#9664;' : '&#9654;';
}

async function loadBouquets() {
  try {
    const r = await fetch('/api/bouquets');
    const bqs = await r.json();
    if (!bqs.length) { setPlaceholder('Keine Bouquets'); return; }
    renderBouquets(bqs);
    loadChannels(bqs[0].ref);
  } catch(e) { setPlaceholder('Fehler: ' + e.message); }
}

function renderBouquets(bqs) {
  const bar = document.getElementById('bouquet-bar');
  while (bar.children.length > 1) bar.removeChild(bar.lastChild);
  bqs.forEach((bq, i) => {
    const btn = document.createElement('button');
    btn.className = 'bq-btn' + (i === 0 ? ' active' : '');
    btn.textContent = bq.name;
    btn.onclick = () => {
      document.querySelectorAll('.bq-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadChannels(bq.ref);
    };
    bar.appendChild(btn);
  });
  bar.style.display = 'flex';
}

async function loadChannels(ref) {
  try {
    const r = await fetch('/api/channels?ref=' + encodeURIComponent(ref));
    const chs = await r.json();
    if (!chs.length) { setPlaceholder('Keine Sender'); return; }
    all = chs; filtered = [...chs]; cur = -1;
    document.getElementById('search').value = '';
    renderList();
    setPlaceholder(filtered.length + ' Sender — Antippen zum Starten');
  } catch(e) { showMsg('Fehler: ' + e.message); }
}

function renderList() {
  const div = document.getElementById('channels');
  div.innerHTML = '';
  filtered.forEach((ch, i) => {
    const el = document.createElement('div');
    el.className = 'ch' + (i === cur ? ' active' : '');
    el.textContent = (i+1) + '. ' + ch.name;
    el.onclick = () => play(i);
    div.appendChild(el);
  });
}

function filterChannels(q) {
  q = q.toLowerCase();
  filtered = q ? all.filter(c => c.name.toLowerCase().includes(q)) : [...all];
  cur = -1; renderList();
}

async function play(i) {
  cur = i;
  const ch = filtered[i];
  const v = document.getElementById('v');
  if (bufferTimer) { clearInterval(bufferTimer); bufferTimer = null; }
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('loading-text').textContent = 'Starte Stream…';
  document.getElementById('loading').classList.add('show');
  document.getElementById('video-area').classList.add('playing');
  v.style.display = 'block';
  if (hls) { hls.destroy(); hls = null; }
  v.pause(); v.removeAttribute('src'); v.load();
  try {
    const quality = document.getElementById('quality-select').value;
    // OpenWebInterface-style URL: /<service-ref> starts the stream and
    // redirects to its HLS playlist — same shape as the STB streaming port.
    const hlsUrl = '/' + ch.ref + '?quality=' + quality;
    document.getElementById('url-bar').style.display = 'flex';
    document.getElementById('url-text').textContent = window.location.origin + '/' + ch.ref;
    if (Hls.isSupported()) {
      hls = new Hls({
        // Sit 6 segments (~12s) behind the live edge; chasing it with only one
        // buffered segment causes a stall on every segment boundary.
        liveSyncDurationCount: 6,
        liveMaxLatencyDurationCount: 18,
        maxBufferLength: 30,
        backBufferLength: 30,
        // the root route holds the request until the playlist exists (up to 10s)
        manifestLoadingTimeOut: 20000,
        manifestLoadingMaxRetry: 10,
        manifestLoadingRetryDelay: 500,
        fragLoadingMaxRetry: 10,
        levelLoadingMaxRetry: 10,
        liveSyncOnStallEnabled: true,
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(v);
      hls.on(Hls.Events.MANIFEST_PARSED, () => startWhenBuffered(v));
      hls.on(Hls.Events.ERROR, (_, d) => {
        if (d.fatal) {
          document.getElementById('loading').classList.remove('show');
          showMsg('Stream-Fehler: ' + d.type);
        }
      });
    } else if (v.canPlayType('application/vnd.apple.mpegurl')) {
      v.src = hlsUrl;
      v.oncanplay = () => startWhenBuffered(v);
    }
  } catch(e) {
    document.getElementById('loading').classList.remove('show');
    showMsg('Fehler: ' + e.message);
  }
  document.getElementById('now').style.display = 'block';
  document.getElementById('now').textContent = ch.name;
  document.title = ch.name + ' — E2HLS';
  renderList();
  document.querySelectorAll('.ch')[i]?.scrollIntoView({block:'nearest'});
}

let lastFsExit = 0;
function toggleFullscreen() {
  const area = document.getElementById('video-area');
  if (document.fullscreenElement) document.exitFullscreen();
  else area.requestFullscreen().catch(() => {});
}
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement) lastFsExit = Date.now();
  const btn = document.getElementById('fullscreen-btn');
  btn.innerHTML = document.fullscreenElement ? '&#x2715;' : '&#x26F6;';
  btn.title = document.fullscreenElement ? 'Vollbild beenden (Escape)' : 'Vollbild (Escape beendet)';
});
document.getElementById('v').addEventListener('dblclick', toggleFullscreen);

function copyUrl() {
  const text = document.getElementById('url-text').textContent;
  const done = () => { const b = document.getElementById('copy-btn'); b.textContent='✓'; setTimeout(() => b.textContent='Kopieren', 2000); };
  // navigator.clipboard exists only in secure contexts (HTTPS/localhost);
  // this page is plain HTTP, so fall back to execCommand-based copying.
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { showMsg('Kopieren fehlgeschlagen'); }
  document.body.removeChild(ta);
}
function showMsg(t, ms=3000) {
  const el = document.getElementById('msg');
  el.textContent = t; el.classList.add('show');
  if (ms) setTimeout(() => el.classList.remove('show'), ms);
}
function setPlaceholder(t) {
  const el = document.getElementById('placeholder');
  el.style.display = 'block'; el.textContent = t;
}
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowDown') play(Math.min(cur+1, filtered.length-1));
  if (e.key === 'ArrowUp')   play(Math.max(cur-1, 0));
  if (e.key === 'f' && cur >= 0) toggleFullscreen();
  // Escape right after leaving fullscreen is the browser's exit key,
  // not a request to toggle the channel list.
  if (e.key === 'Escape' && Date.now() - lastFsExit < 500) return;
  if (e.key === 'Escape' || e.key === 'Tab') toggleList();
});
window.addEventListener('load', loadBouquets);
</script>
</body>
</html>"""


def load_html_template():
    try:
        if os.path.exists(HTML_TEMPLATE_FILE):
            with open(HTML_TEMPLATE_FILE, "r", encoding="utf-8") as handle:
                return handle.read()
        print("[E2HLSServer] ERROR: HTML template not found: " + HTML_TEMPLATE_FILE)
        return None
    except Exception as exc:
        print("[E2HLSServer] Error loading HTML template: " + str(exc))
        return None


class HlsRoot(Resource):
    def __init__(self, stream_service, logger, settings, local_ip_provider, html_template):
        Resource.__init__(self)
        self.stream_service = stream_service
        self.logger = logger
        self.settings = settings
        self.local_ip_provider = local_ip_provider
        self.html_template = html_template
        # Bouquet parsing triggers O(N) lamedb lookups per request on the
        # shared reactor; cache per file mtime. Name lookups are cached for
        # the process lifetime — lamedb only changes on a service scan,
        # which comes with a GUI restart anyway.
        self._channels_cache = {}
        self._name_cache = {}

    def getChild(self, name, request):
        # When the path contains colons (e.g. service refs like 1:0:19:EF11:…
        # or ports like :8003), Twisted splits on ':' for virtual hosts, so
        # render_GET never sees the full path. Force the root to handle
        # everything so our regex can match the full ref.
        return self

    def render_GET(self, request):
        path = request.path.decode()

        if path == "/" or path == "/web" or path == "/web/":
            return self.render_web(request)
        if path == "/player":
            return self.render_player(request)
        if path == "/favicon.ico" or path == "/res/favicon.png":
            return self.render_favicon(request)
        if path == "/stream":
            return self.render_stream(request)
        if path.startswith("/hls/"):
            return self.render_hls(request)
        if path == "/status":
            return self.render_status(request)
        if path == "/logs":
            return self.render_logs(request)
        if path == "/debug/bouquets":
            return self.render_debug_bouquets(request)
        if path == "/api/bouquets":
            return self.render_api_bouquets(request)
        if path == "/api/channels":
            return self.render_api_channels(request)
        if path == "/api/start":
            return self.render_api_start(request)

        # OpenWebInterface-style streaming: http://<box-ip>:<port>/<service-ref>
        # (same URL shape as the STB streaming port, just HLS on this port).
        # Unquote first so percent-encoded refs (e.g. %3A for ':') match too.
        ref_path = urllib.parse.unquote(path[1:])
        if re.match(r"^\d+:\d", ref_path):
            return self.render_root_stream(request, ref_path)

        request.setResponseCode(404)
        return b"Not Found"

    def _parse_basic_auth(self, request):
        header = request.getHeader("authorization")
        if not header or not header.startswith("Basic "):
            return None, None

        try:
            token = header[6:].strip()
            decoded = base64.b64decode(token).decode("utf-8")
            if ":" not in decoded:
                return None, None
            return decoded.split(":", 1)
        except Exception:
            self.logger.warning("Invalid Authorization header received")
            return None, None

    def _parse_params(self, request):
        args = request.args
        ref = args.get(b"ref", [None])[0]
        user = args.get(b"user", [None])[0]
        password = args.get(b"pass", [None])[0]

        if ref is None:
            return None

        query_user = urllib.parse.unquote(user.decode()) if user else None
        query_pass = urllib.parse.unquote(password.decode()) if password else None
        header_user, header_pass = self._parse_basic_auth(request)

        return {
            "ref": urllib.parse.unquote(ref.decode()),
            "user": query_user if query_user is not None else header_user,
            "password": query_pass if query_pass is not None else header_pass,
        }

    def _build_external_url(self, params, path):
        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        parts = ["ref=" + urllib.parse.quote(params["ref"])]
        if params.get("user"):
            parts.append("user=" + urllib.parse.quote(params["user"]))
        if params.get("password"):
            parts.append("pass=" + urllib.parse.quote(params["password"]))
        return "http://" + ip_addr + ":" + str(port) + path + "?" + "&".join(parts)

    def render_player(self, request):
        if self.html_template is None:
            return b"ERROR: HTML template not loaded"

        params = self._parse_params(request)
        if params is None:
            return b"Missing ref parameter"

        stream_id, _is_new = self.stream_service.get_or_create_stream(params)
        if stream_id is None:
            return b"Failed to start stream"

        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        hls_url = "http://" + ip_addr + ":" + str(port) + "/hls/live_" + stream_id + ".m3u8"
        ext_url = self._build_external_url(params, "/stream")

        try:
            html = self.html_template
            html = html.replace("{stream_id}", stream_id)
            html = html.replace("{service_ref}", params["ref"])
            html = html.replace("{external_url}", ext_url)
            html = html.replace("{hls_url}", hls_url)
            self.logger.log_request("GET", "/player", request.getClientIP(), 200, len(html))
            return html.encode()
        except Exception as exc:
            self.logger.error("HTML template error: " + str(exc))
            return ("Error generating player page: " + str(exc)).encode()

    def render_favicon(self, request):
        favicon_path = get_favicon_path()
        if not os.path.exists(favicon_path):
            request.setResponseCode(404)
            return b""

        try:
            with open(favicon_path, "rb") as handle:
                data = handle.read()
            request.setHeader(b"Content-Type", b"image/png")
            request.setHeader(b"Cache-Control", b"public, max-age=3600")
            self.logger.log_request("GET", "/res/favicon.png", request.getClientIP(), 200, len(data))
            return data
        except Exception as exc:
            self.logger.error("Error reading favicon: " + str(exc))
            request.setResponseCode(500)
            return b""

    def render_stream(self, request):
        params = self._parse_params(request)
        if params is None:
            request.setResponseCode(400)
            return b"Missing ref parameter"

        stream_id, _is_new = self.stream_service.get_or_create_stream(params)
        if stream_id is None:
            request.setResponseCode(500)
            return b"Failed to start stream"

        return self._redirect_when_playlist_ready(request, stream_id)

    def _redirect_when_playlist_ready(self, request, stream_id):
        """Redirect to the stream's playlist once it exists, without blocking.

        NEVER time.sleep() in a request handler here: the Twisted reactor
        shares enigma2's main event loop, which also drives the GUI and the
        port-8001 stream source ffmpeg reads from. Sleeping stalls that
        source, so the playlist we are waiting for can never appear.
        """
        playlist = os.path.join(self.settings.hls_dir(), "live_" + stream_id + ".m3u8")
        state = {"gone": False}
        request.notifyFinish().addErrback(lambda _f: state.update(gone=True))

        def poll(remaining):
            if state["gone"]:
                return
            if os.path.exists(playlist) or remaining <= 0:
                try:
                    request.redirect(("/hls/live_" + stream_id + ".m3u8").encode())
                    request.finish()
                except Exception:
                    pass
                return
            reactor.callLater(0.25, poll, remaining - 1)

        poll(40)  # up to 10 s
        return NOT_DONE_YET

    def render_root_stream(self, request, ref):
        """Stream a service given directly in the path (OpenWebInterface style).

        http://<box-ip>:<port>/<service-ref> starts (or reuses) the HLS stream
        for that reference and redirects to its playlist — mirroring the STB
        streaming port's URL shape on this port. Credentials may come as
        ?user=&pass= or a Basic auth header.
        """
        header_user, header_pass = self._parse_basic_auth(request)
        args = request.args
        q_user = args.get(b"user", [None])[0]
        q_pass = args.get(b"pass", [None])[0]
        q_quality = args.get(b"quality", [None])[0]
        from ...core.stream_service import QUALITY_PRESETS
        quality = q_quality.decode() if q_quality else "balanced"
        if quality not in QUALITY_PRESETS:
            quality = "balanced"
        params = {
            "ref": ref,
            "quality": quality,
            "user": urllib.parse.unquote(q_user.decode()) if q_user else header_user,
            "password": urllib.parse.unquote(q_pass.decode()) if q_pass else header_pass,
        }

        stream_id, _is_new = self.stream_service.get_or_create_stream(params)
        if stream_id is None:
            request.setResponseCode(500)
            return b"Failed to start stream"

        return self._redirect_when_playlist_ready(request, stream_id)

    def render_hls(self, request):
        path = request.path.decode()
        filename = path.split("/")[-1]
        filepath = os.path.join(self.settings.hls_dir(), filename)

        # Every fetch counts as activity — playlist AND segments. Some players
        # (VLC caches aggressively) fetch segments without re-reading the
        # playlist; counting only playlist hits kills streams mid-watch.
        if filename.startswith("live_") and filename.endswith(".m3u8"):
            self.stream_service.update_access(filename[5:-5])
        elif filename.endswith(".ts") and "_" in filename:
            self.stream_service.update_access(filename.split("_", 1)[0])

        if not os.path.exists(filepath):
            request.setResponseCode(404)
            return b""

        if filename.endswith(".ts"):
            # Segments are 2–5 MB. Reading them inline blocks the reactor —
            # which is enigma2's main loop (GUI + tuner stream) — for the
            # whole read. static.File streams via a producer instead, in
            # chunks, only when the socket can take more.
            try:
                client_ip = request.getClientIP()
                resource = static.File(filepath, defaultType="video/MP2T")
                resource.contentTypes = {".ts": "video/MP2T"}
                resource.isLeaf = True
                # Log when the response is done — static.File may answer
                # 206 (Range) or 304, and only then are code/size real.
                finished = request.notifyFinish()
                finished.addCallback(
                    lambda _: self.logger.log_request(
                        "GET", "/hls/" + filename, client_ip, request.code, request.sentLength))
                finished.addErrback(lambda _: None)
                return resource.render_GET(request)
            except Exception as exc:
                self.logger.error("Error serving HLS segment " + filename + ": " + str(exc))
                request.setResponseCode(500)
                return b""

        try:
            with open(filepath, "rb") as handle:
                data = handle.read()
        except Exception as exc:
            self.logger.error("Error reading HLS file " + filename + ": " + str(exc))
            request.setResponseCode(500)
            return b""

        if filename.endswith(".m3u8"):
            request.setHeader(b"Content-Type", b"application/vnd.apple.mpegurl")
            request.setHeader(b"Cache-Control", b"no-cache, no-store")

        self.logger.log_request("GET", "/hls/" + filename, request.getClientIP(), 200, len(data))
        return data

    def render_status(self, request):
        ip_addr = self.local_ip_provider()
        port = self.settings.http_port()
        status = self.stream_service.get_status()

        response = "HLS Plugin Status\n"
        response += "=" * 40 + "\n"
        response += "Server:   " + ip_addr + ":" + str(port) + "\n"
        response += "HLS dir:  " + self.settings.hls_dir() + "\n"
        response += "Streams:  " + str(len(status)) + "\n\n"

        for stream_id, info in status.items():
            response += "Stream " + stream_id + ":\n"
            response += "  Ref:      " + info["ref"] + "\n"
            response += "  Mode:     copy\n"
            response += "  Uptime:   " + str(info["uptime"]) + "s\n"
            response += "  Segments: " + str(info["segments"]) + "\n"
            response += "  Accesses: " + str(info["access_count"]) + "\n"
            response += "  Crashes:  " + str(info["crash_count"]) + "\n"
            response += "  HLS URL:  http://" + ip_addr + ":" + str(port) + info["hls_url"] + "\n\n"

        self.logger.log_request("GET", "/status", request.getClientIP(), 200)
        return response.encode()

    def render_logs(self, request):
        log_file = os.path.join(self.settings.hls_dir(), "logs", "plugin.log")
        try:
            if os.path.exists(log_file):
                file_size = os.path.getsize(log_file)
                # Read only the last 64 KB (covers ~100 lines) instead of
                # the whole file — the log is size-capped but still large.
                # Binary mode: seeking to an arbitrary offset is undefined
                # on text streams; decode after the bounded read instead.
                read_bytes = min(64 * 1024, file_size)
                with open(log_file, "rb") as handle:
                    handle.seek(file_size - read_bytes)
                    tail = handle.read()
                lines = tail.decode("utf-8", errors="ignore").splitlines()[-100:]
                content = "\n".join(lines)
                self.logger.log_request("GET", "/logs", request.getClientIP(), 200)
                return content.encode()
            return b"No logs available"
        except Exception as exc:
            return ("Error reading logs: " + str(exc)).encode()

    def render_debug_bouquets(self, request):
        bouquet_dir = BOUQUET_DIR
        lines = ["=== Bouquet Debug ===\n"]
        try:
            files = sorted(os.listdir(bouquet_dir))
            lines.append("Files in %s:\n" % bouquet_dir)
            for f in files:
                if "bouquet" in f.lower():
                    lines.append("  " + f + "\n")
            lines.append("\n")
        except Exception as exc:
            lines.append("Cannot list dir: " + str(exc) + "\n\n")

        top = os.path.join(bouquet_dir, "bouquets.tv")
        if os.path.exists(top):
            lines.append("=== bouquets.tv content ===\n")
            try:
                with open(top, "r", encoding="utf-8", errors="ignore") as handle:
                    lines.append(handle.read())
            except Exception as exc:
                lines.append("Read error: " + str(exc))
        else:
            lines.append("bouquets.tv NOT FOUND\n")

        lines.append("\n\n=== Parsed result ===\n")
        sub_bouquets = self._parse_top_bouquet_file(top) if os.path.exists(top) else []
        if not sub_bouquets:
            lines.append("_parse_top_bouquet_file returned EMPTY LIST\n")
        for name, filename in sub_bouquets:
            sub_path = os.path.join(bouquet_dir, filename)
            exists = os.path.exists(sub_path)
            try:
                channels = self._parse_channel_file(sub_path) if exists else []
            except Exception as exc:
                channels = []
                lines.append("Error parsing %s: %s\n" % (filename, exc))
            lines.append("  [%s] %s -> %d channels (file %s)\n" % (
                "OK" if exists else "MISSING", name, len(channels), filename))

        request.setHeader(b"Content-Type", b"text/plain; charset=utf-8")
        return "".join(lines).encode("utf-8")

    def _json_response(self, request, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request.setResponseCode(status)
        request.setHeader(b"Content-Type", b"application/json; charset=utf-8")
        request.setHeader(b"Access-Control-Allow-Origin", b"*")
        return body

    def render_api_bouquets(self, request):
        # Parse /etc/enigma2 directly — the plugin runs on the receiver, so no
        # OpenWebif round-trip is needed (and none may be installed).
        try:
            top_file = os.path.join(BOUQUET_DIR, "bouquets.tv")
            if not os.path.exists(top_file):
                self.logger.error("API bouquets: %s not found" % top_file)
                return self._json_response(
                    request, {"error": "bouquets.tv not found"}, 500)
            bouquets = [
                {"name": name, "ref": filename}
                for name, filename in self._parse_top_bouquet_file(top_file)
            ]
            return self._json_response(request, bouquets)
        except Exception as exc:
            self.logger.error("API bouquets error: " + str(exc))
            return self._json_response(request, {"error": str(exc)}, 500)

    def render_api_channels(self, request):
        ref_raw = request.args.get(b"ref", [None])[0]
        if not ref_raw:
            return self._json_response(request, {"error": "Missing ref"}, 400)
        ref = urllib.parse.unquote(ref_raw.decode())
        try:
            # Accept either a bouquet filename (as returned by /api/bouquets)
            # or a full '1:7:1:...FROM BOUQUET "file"...' service reference.
            match = re.search(r'FROM BOUQUET "([^"]+)"', ref)
            filename = match.group(1) if match else ref
            # basename() blocks path traversal via crafted refs
            path = os.path.join(BOUQUET_DIR, os.path.basename(filename))
            if not os.path.exists(path):
                return self._json_response(
                    request, {"error": "Bouquet not found: " + filename}, 404)
            return self._json_response(request, self._channels_for(path))
        except Exception as exc:
            self.logger.error("API channels error: " + str(exc))
            return self._json_response(request, {"error": str(exc)}, 500)

    def render_api_start(self, request):
        ref_raw = request.args.get(b"ref", [None])[0]
        quality_raw = request.args.get(b"quality", [b"balanced"])[0]
        if not ref_raw:
            request.setResponseCode(400)
            return b"Missing ref"
        ref = urllib.parse.unquote(ref_raw.decode())
        quality = quality_raw.decode() if quality_raw else "balanced"
        from ...core.stream_service import QUALITY_PRESETS
        if quality not in QUALITY_PRESETS:
            quality = "balanced"
        params = {"ref": ref, "quality": quality}
        stream_id, _is_new = self.stream_service.get_or_create_stream(params)
        if stream_id is None:
            return self._json_response(request, {"error": "Failed to start stream"}, 500)
        return self._json_response(request, {
            "id": stream_id,
            "playlist": "live_" + stream_id + ".m3u8",
        })

    def render_web(self, request):
        request.setHeader(b"Content-Type", b"text/html; charset=utf-8")
        self.logger.log_request("GET", "/web", request.getClientIP(), 200)
        return _WEB_HTML.encode("utf-8")

    def _load_bouquets(self):
        bouquets = []
        errors = []
        bouquet_dir = BOUQUET_DIR
        top_file = os.path.join(bouquet_dir, "bouquets.tv")

        try:
            if not os.path.exists(top_file):
                errors.append("bouquets.tv nicht gefunden in " + bouquet_dir)
                return bouquets, errors

            sub_bouquets = self._parse_top_bouquet_file(top_file)

            for name, filename in sub_bouquets:
                sub_path = os.path.join(bouquet_dir, filename)
                try:
                    channels = self._parse_channel_file(sub_path)
                    if channels:
                        bouquets.append({"name": name, "services": channels, "file": filename})
                except Exception as exc:
                    errors.append("Fehler beim Lesen von %s: %s" % (filename, exc))
        except Exception as exc:
            errors.append("Fehler beim Laden der Bouquets: " + str(exc))

        return bouquets, errors

    def _parse_top_bouquet_file(self, path):
        # collect (ref, description_or_None) pairs
        entries = []
        current_ref = None

        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("#SERVICE "):
                    if current_ref is not None:
                        entries.append((current_ref, None))
                    current_ref = line[9:].strip()
                elif line.startswith("#DESCRIPTION ") and current_ref is not None:
                    entries.append((current_ref, line[13:].strip() or None))
                    current_ref = None

        if current_ref is not None:
            entries.append((current_ref, None))

        result = []
        for ref, desc in entries:
            match = re.search(r'FROM BOUQUET "([^"]+)"', ref)
            if match:
                filename = match.group(1)
                # fall back to sub-file NAME or filename if no description
                name = desc or self._read_bouquet_name(os.path.join(os.path.dirname(path), filename)) or filename
                result.append((name, filename))

        return result

    def _read_bouquet_name(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw in handle:
                    line = raw.rstrip("\r\n")
                    if line.startswith("#NAME "):
                        return line[6:].strip() or None
        except Exception:
            pass
        return None

    def _channels_for(self, path):
        """Return cached channel list for *path*, keyed by (path, mtime)."""
        try:
            st = os.stat(path)
            key = (path, st.st_mtime)
        except OSError:
            return []

        cached = self._channels_cache.get(key)
        if cached is not None:
            return cached

        channels = self._parse_channel_file(path)
        self._channels_cache[key] = channels
        return channels

    def _parse_channel_file(self, path):
        channels = []
        current_service = None

        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("#SERVICE "):
                    service_ref = line[9:].strip()
                    parts = service_ref.split(":")
                    # skip sub-bouquet refs (type 7) and marker entries (type 64)
                    if len(parts) >= 2 and parts[1] in ("7", "64"):
                        current_service = None
                        continue
                    current_service = {"ref": service_ref, "name": ""}
                    channels.append(current_service)
                elif line.startswith("#DESCRIPTION ") and current_service is not None:
                    description = line[13:].strip()
                    if description:
                        current_service["name"] = description

        # Most bouquet files carry no #DESCRIPTION — channel names live in
        # enigma2's service database (lamedb), so resolve them there.
        for channel in channels:
            if not channel["name"]:
                channel["name"] = self._resolve_service_name(channel["ref"])

        # drop entries without a readable name
        return [ch for ch in channels if ch["name"]]

    def _resolve_service_name(self, ref):
        """Look up a channel name in enigma2's service database, with cache."""
        cached = self._name_cache.get(ref)
        if cached is not None:
            return cached

        if eServiceCenter is None or eServiceReference is None:
            self._name_cache[ref] = ""
            return ""
        try:
            service = eServiceReference(ref)
            info = eServiceCenter.getInstance().info(service)
            name = info.getName(service) if info else ""
            self._name_cache[ref] = name or ""
            return name or ""
        except Exception as exc:
            self.logger.debug("Name lookup failed for %s: %s" % (ref, exc))
            self._name_cache[ref] = ""
            return ""

    def _build_web_html(self, bouquets, errors):
        html_parts = ["""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>E2HLS &mdash; TV Bouquets</title>
<link rel="icon" type="image/png" href="/res/favicon.png">
<style>
:root{--bg:#0d0d12;--surface:#16161e;--surface2:#1e1e28;--border:#28283a;--text:#ddddf0;--dim:#7070a0;--accent:#6c8fff;--accent-h:#9ab0ff;--green:#4ade80}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:15px;line-height:1.5;min-height:100vh}
a{color:inherit;text-decoration:none}

/* header */
.hdr{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:50}
.hdr img{width:28px;height:28px;border-radius:6px}
.hdr-title{font-size:17px;font-weight:700;letter-spacing:-.2px}
.hdr-sub{margin-left:auto;font-size:12px;color:var(--dim);background:var(--surface2);border:1px solid var(--border);border-radius:20px;padding:3px 12px}

/* main */
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}

/* search */
.search{position:relative;margin-bottom:20px}
.search input{width:100%;padding:11px 16px 11px 42px;background:var(--surface);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:15px;outline:none;transition:border-color .15s}
.search input::placeholder{color:var(--dim)}
.search input:focus{border-color:var(--accent)}
.search-icon{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--dim);font-size:16px;pointer-events:none}

/* bouquet card */
.bq{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:10px;overflow:hidden;transition:border-color .15s}
.bq.open{border-color:#3a3a5a}
.bq-hdr{display:flex;align-items:center;padding:14px 18px;cursor:pointer;gap:12px;user-select:none;transition:background .12s}
.bq-hdr:hover{background:var(--surface2)}
.bq-arrow{color:var(--dim);font-size:11px;transition:transform .2s;flex-shrink:0}
.bq.open .bq-arrow{transform:rotate(90deg)}
.bq-name{font-weight:600;font-size:15px;flex:1}
.bq-badge{background:var(--surface2);color:var(--dim);border:1px solid var(--border);border-radius:20px;padding:2px 10px;font-size:12px;font-variant-numeric:tabular-nums}

/* channel list */
.ch-list{display:none;border-top:1px solid var(--border)}
.bq.open .ch-list{display:block}
.ch{display:flex;align-items:center;padding:10px 18px 10px 46px;gap:10px;border-bottom:1px solid var(--border);transition:background .1s}
.ch:last-child{border-bottom:none}
.ch:hover{background:var(--surface2)}
.ch-dot{width:6px;height:6px;border-radius:50%;background:var(--border);flex-shrink:0;transition:background .1s}
.ch:hover .ch-dot{background:var(--accent)}
.ch-name{flex:1;color:var(--text);transition:color .1s}
.ch:hover .ch-name{color:var(--accent-h)}
.ch-play{color:var(--accent);font-size:12px;opacity:0;transition:opacity .1s}
.ch:hover .ch-play{opacity:1}

/* empty / error */
.ch-empty{padding:12px 18px 12px 46px;color:var(--dim);font-size:13px}
.err-box{background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.25);color:#f87171;border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px}
.no-data{text-align:center;padding:60px 20px;color:var(--dim)}
.no-data svg{opacity:.3;margin-bottom:12px}

/* footer */
footer{text-align:center;color:var(--dim);font-size:12px;padding:20px;border-top:1px solid var(--border);margin-top:40px}
</style>
</head>
<body>
<header class="hdr">
  <img src="/res/favicon.png" alt="E2HLS">
  <span class="hdr-title">E2HLS Server</span>
  <span class="hdr-sub">TV Bouquets</span>
</header>
<main>"""]

        if errors:
            err_lines = "<br>".join(e.replace("<", "&lt;") for e in errors)
            html_parts.append('<div class="err-box"><strong>Fehler:</strong><br>%s</div>' % err_lines)

        # search box
        html_parts.append("""<div class="search">
  <span class="search-icon">&#128269;</span>
  <input type="search" id="q" placeholder="Sender suchen&hellip;" autocomplete="off" spellcheck="false">
</div>
<div id="list">""")

        if not bouquets:
            html_parts.append("""<div class="no-data">
  <div style="font-size:48px;opacity:.2">&#128250;</div>
  <div>Keine TV-Bouquets gefunden.</div>
  <div style="font-size:13px;margin-top:6px">Prüfe ob <code>/etc/enigma2/bouquets.tv</code> vorhanden ist.</div>
</div>""")
        else:
            for bouquet in bouquets:
                name = (bouquet["name"] or bouquet["file"]).replace("<", "&lt;").replace(">", "&gt;")
                count = len(bouquet["services"])
                html_parts.append(
                    '<div class="bq">'
                    '<div class="bq-hdr" onclick="toggle(this)">'
                    '<span class="bq-arrow">&#9654;</span>'
                    '<span class="bq-name">%s</span>'
                    '<span class="bq-badge">%d</span>'
                    '</div>'
                    '<div class="ch-list">' % (name, count)
                )
                if count == 0:
                    html_parts.append('<div class="ch-empty">Keine Sender in diesem Bouquet.</div>')
                else:
                    for service in bouquet["services"]:
                        encoded_ref = urllib.parse.quote(service["ref"], safe="")
                        display = service["name"].replace("<", "&lt;").replace(">", "&gt;")
                        html_parts.append(
                            '<a class="ch" href="/player?ref=%s">'
                            '<span class="ch-dot"></span>'
                            '<span class="ch-name">%s</span>'
                            '<span class="ch-play">&#9654;</span>'
                            '</a>' % (encoded_ref, display)
                        )
                html_parts.append('</div></div>')

        html_parts.append("""</div>
</main>
<footer>E2HLS Server &bull; Sender anklicken zum Starten des Streams</footer>
<script>
function toggle(hdr){hdr.closest('.bq').classList.toggle('open')}

const inp=document.getElementById('q');
inp.addEventListener('input',function(){
  const q=this.value.toLowerCase();
  document.querySelectorAll('.bq').forEach(function(bq){
    let n=0;
    bq.querySelectorAll('.ch').forEach(function(ch){
      const m=!q||ch.querySelector('.ch-name').textContent.toLowerCase().includes(q);
      ch.style.display=m?'':'none';
      if(m)n++;
    });
    bq.style.display=(n>0||!q)?'':'none';
    if(q&&n>0)bq.classList.add('open');
    else if(!q)bq.classList.remove('open');
  });
});
</script>
</body>
</html>""")

        return "".join(html_parts)


class HlsHttpServer(object):
    def __init__(self, stream_service, logger, settings, local_ip_provider):
        self.stream_service = stream_service
        self.logger = logger
        self.settings = settings
        self.local_ip_provider = local_ip_provider
        self._listener = None
        self._html_template = load_html_template()

    def is_running(self):
        return self._listener is not None

    def start(self):
        if self._listener:
            self.logger.info("HTTP server already running")
            return True

        try:
            from twisted.internet import reactor

            root = HlsRoot(
                stream_service=self.stream_service,
                logger=self.logger,
                settings=self.settings,
                local_ip_provider=self.local_ip_provider,
                html_template=self._html_template,
            )
            site = Site(root)
            port = self.settings.http_port()
            self.logger.info("Starting HTTP server on port " + str(port))
            self._listener = reactor.listenTCP(port, site)
            self.logger.info("HTTP server started on port " + str(port))
            return True
        except Exception as exc:
            self.logger.error("Failed to start HTTP server: " + str(exc))
            self._listener = None
            return False

    def stop(self):
        if not self._listener:
            return

        try:
            self._listener.stopListening()
            self._listener = None
            self.logger.info("HTTP server stopped")
        except Exception as exc:
            self.logger.error("Error stopping HTTP server: " + str(exc))
            self._listener = None

    def restart(self):
        self.stream_service.stop_all()
        self.stop()

        import time

        time.sleep(1)
        self.start()
        self.logger.info("Server restarted")
