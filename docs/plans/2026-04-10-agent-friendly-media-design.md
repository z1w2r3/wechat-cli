# WeChat CLI Agent-Friendly Media Design

Date: 2026-04-10

## Goal

Enhance WeChat CLI to serve as a data layer for a "group secretary" agent. The CLI handles structured message extraction and media file resolution/transcoding. Analysis and summarization are left to the upper-layer agent.

## Design

### 1. Fix macOS Media Path Resolution

Current `_resolve_media_path()` uses old Windows-style paths (`msg/attach/<hash>/YYYY-MM/Img/*.dat`). macOS new WeChat stores media as plain files under:

```
Message/MessageTemp/<md5(username)>/
├── Image/    → {local_id}{timestamp}_.pic_thumb.jpg   (standard JPEG)
├── Audio/    → {hex}.aud.silk                          (Silk v3 encoded)
├── Video/    → {local_id}_{timestamp}.mp4              (standard MP4)
└── File/     → original filename (docx, pdf, etc.)
```

Changes:
- Detect macOS new-style `Message/MessageTemp/` directory first
- Match image/video files by `local_id` + `create_time` in filename
- Match audio files from `Audio/` directory
- Match file messages by `<title>` from XML content
- Keep old path logic as fallback for Windows/Linux

### 2. Voice Transcoding

Pipeline: `.aud.silk` → PCM (pilk) → `.wav` (ffmpeg)

- Output directory: `~/.wechat-cli/audio/`
- Cache: skip transcoding if wav exists and is newer than silk source
- Graceful degradation: if pilk or ffmpeg unavailable, output silk path only with warning
- Dependencies: `pilk` (optional Python package), `ffmpeg` (system command)

### 3. Structured JSON Output

New `--json` flag on `history` command. Each message becomes a structured object:

```json
{
  "local_id": 21,
  "type": "image",
  "type_code": 3,
  "sender": "Zhang San",
  "timestamp": 1749264115,
  "time": "2025-06-07 10:41:55",
  "text": "[Image]",
  "media": {
    "path": "/path/to/Image/211749264115_.pic_thumb.jpg",
    "exists": true
  }
}
```

Media field by type:
- text (1): no media field
- image (3): `media.path` → jpg path
- voice (34): `media.original` → silk path, `media.decoded` → wav path
- video (43): `media.path` → mp4 path
- file (49/6): `media.path` → file path, `media.filename` → original name
- link (49): no media, title in text
- others: current format preserved

### 4. Command Changes

No new commands. Enhanced `history`:

```bash
wechat-cli history "Group Name" \
  --start-time "2026-04-10" \
  --end-time "2026-04-10 23:59" \
  --type voice,image,file,text \
  --media \
  --decode-voice \
  --json
```

Typical agent call:
```bash
wechat-cli history "AI Group" --start-time "2026-04-10" --media --decode-voice --json
```

### 5. Dependencies & Compatibility

New dependencies:
- `pilk` — optional, in `pyproject.toml` extras `[voice]`
- `ffmpeg` — system command, runtime detection

Compatibility:
- macOS new path: new support
- Old path (`msg/attach/`): preserved as fallback
- `--decode-voice` without pilk/ffmpeg: outputs silk path + warning
- `--json`: new flag, default text output unchanged

### 6. Files Changed

- `wechat_cli/core/messages.py` — media path resolution + structured message building
- `wechat_cli/commands/history.py` — new params + JSON output
- New: `wechat_cli/core/voice.py` — voice transcoding logic
- `pyproject.toml` — optional dependency
