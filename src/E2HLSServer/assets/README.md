# filler.ts

Bundled placeholder HLS segment: 2 s of black video + silent audio, muxed as a
self-contained MPEG-TS. Served immediately when a stream starts (and re-served
on a 2 s cadence) so the very first client request gets *something* playable
right away instead of waiting on ffmpeg's cold start — see
`Segmenter._maybe_emit_filler` in `core/stream_service.py`.

Regenerate with:

```bash
ffmpeg -y -f lavfi -i color=c=black:s=640x360:r=25 \
  -f lavfi -i anullsrc=r=48000:cl=stereo -t 2 \
  -c:v libx264 -profile:v baseline -level 3.0 -pix_fmt yuv420p \
  -x264-params keyint=25:scenecut=0 \
  -c:a aac -b:a 64k -ac 2 \
  -f mpegts -muxdelay 0 -muxpreload 0 filler.ts
```

If you change the duration, update `_FILLER_DURATION` in `core/stream_service.py`
to match — it drives both the EXTINF value and the re-emit cadence.
