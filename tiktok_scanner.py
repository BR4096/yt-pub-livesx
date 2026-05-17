#!/usr/bin/env python3
"""
tiktok_scanner.py — TikTok channel scanner with persistent queue.

Architecture:
  - scan_channel_to_queue: lists metadata (flat-playlist) and populates tiktok_videos
    with status='pendente' (or 'pulado' for photo-posts). Does NOT download MP4.
  - download_pending_videos: consumes the queue (oldest-first by default), downloads
    via yt-dlp and creates a folder in imports/. Does not call flat-playlist.
  - process_all_channels: full flow (scan + download) for maintenance.

The scheduler only calls download_pending_videos at the scheduled time. Full
scans are triggered manually (dashboard) or via light scan.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
IMPORTS_DIR = os.path.join(PROJECT_ROOT, 'imports')

sys.path.insert(0, PROJECT_ROOT)
import db


def log(msg):
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {msg}', flush=True)


def _build_tiktok_url(handle):
    handle = handle.strip()
    if handle.startswith('http'):
        return handle
    if not handle.startswith('@'):
        handle = '@' + handle
    return f'https://www.tiktok.com/{handle}'


# ---------------------------------------------------------------------------
# SCAN: populate the queue (tiktok_videos) without downloading MP4
# ---------------------------------------------------------------------------

def scan_channel_to_queue(channel, playlist_end=5000, timeout=1200):
    """Lists channel metadata and populates the queue.

    Returns dict: {novos, pulados, ja_conhecidos, antes_data, total_scanned, erro}.
    """
    handle = channel['handle']
    url = _build_tiktok_url(handle)
    data_desde = channel.get('data_desde', '')

    log(f'  Scan -> queue: {handle} (since={data_desde}, limit={playlist_end})')

    cmd = ['yt-dlp', '--flat-playlist', '-j', '--no-warnings']
    if playlist_end and playlist_end > 0:
        cmd += ['--playlist-end', str(playlist_end)]
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or '')[:200]
            log(f'  yt-dlp error: {err}')
            return {'novos': 0, 'pulados': 0, 'ja_conhecidos': 0, 'antes_data': 0,
                    'total_scanned': 0, 'erro': err}
    except subprocess.TimeoutExpired:
        log(f'  yt-dlp timeout for {handle}')
        return {'novos': 0, 'pulados': 0, 'ja_conhecidos': 0, 'antes_data': 0,
                'total_scanned': 0, 'erro': 'timeout'}

    novos = pulados = ja_conhecidos = antes_data = total = 0

    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue

        vid_id = info.get('id', '')
        if not vid_id:
            continue

        total += 1

        upload_date = info.get('upload_date', '')  # YYYYMMDD
        if data_desde and upload_date:
            desde_fmt = data_desde.replace('-', '')
            if upload_date < desde_fmt:
                antes_data += 1
                continue

        if db.is_tiktok_known(vid_id):
            ja_conhecidos += 1
            continue

        duration = info.get('duration') or 0
        title = info.get('title', '')
        video_url = (info.get('url', '') or info.get('webpage_url', '')
                     or f'https://www.tiktok.com/@{handle.lstrip("@")}/video/{vid_id}')

        if not duration:
            # Photo-post / slideshow: mark as skipped immediately
            db.upsert_tiktok_video(vid_id, handle, title=title, url=video_url,
                                   upload_date=upload_date, duration=0,
                                   status='pulado', skip_reason='foto_post')
            pulados += 1
            continue

        db.upsert_tiktok_video(vid_id, handle, title=title, url=video_url,
                               upload_date=upload_date, duration=int(duration),
                               status='pendente')
        novos += 1

    log(f'  Scan: {total} scanned | {novos} new in queue | {ja_conhecidos} already known | '
        f'{antes_data} before {data_desde} | {pulados} skipped (photo-post)')

    db.update_tiktok_channel(channel['id'],
        ultimo_scan=datetime.now().strftime('%Y-%m-%d %H:%M'))

    return {'novos': novos, 'pulados': pulados, 'ja_conhecidos': ja_conhecidos,
            'antes_data': antes_data, 'total_scanned': total, 'erro': ''}


# ---------------------------------------------------------------------------
# FETCH NEW: incremental scan with early-break at cutoff
# ---------------------------------------------------------------------------

def fetch_new_videos_for_channel(channel, safety_cap=5000, timeout=1200):
    """Incremental scan: fetches only videos with upload_date > cutoff.

    Cutoff = MAX(upload_date) already known in the queue. Fallback: data_desde.
    yt-dlp returns newest-first; we iterate and stop as soon as upload_date < cutoff.
    """
    handle = channel['handle']
    url = _build_tiktok_url(handle)

    cutoff = db.get_tiktok_max_upload_date(handle)
    if not cutoff:
        cutoff = (channel.get('data_desde', '') or '').replace('-', '')

    data_desde_fmt = (channel.get('data_desde', '') or '').replace('-', '')

    log(f'  Fetch new: {handle} (cutoff={cutoff or "no cutoff"})')

    cmd = ['yt-dlp', '--flat-playlist', '-j', '--no-warnings',
           '--playlist-end', str(safety_cap), url]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or '')[:200]
            log(f'  yt-dlp error: {err}')
            return {'novos': 0, 'pulados': 0, 'ja_conhecidos': 0, 'antes_data': 0,
                    'parou_em_cutoff': False, 'erro': err}
    except subprocess.TimeoutExpired:
        log(f'  yt-dlp timeout for {handle}')
        return {'novos': 0, 'pulados': 0, 'ja_conhecidos': 0, 'antes_data': 0,
                'parou_em_cutoff': False, 'erro': 'timeout'}

    novos = pulados = ja_conhecidos = antes_data = 0
    parou_em_cutoff = False

    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue

        vid_id = info.get('id', '')
        if not vid_id:
            continue

        upload_date = info.get('upload_date', '')

        # Early break: yt-dlp returns newest-first; if we've fallen below the cutoff, stop here.
        if cutoff and upload_date and upload_date < cutoff:
            parou_em_cutoff = True
            break

        # Extra data_desde filter (doesn't break; just doesn't insert)
        if data_desde_fmt and upload_date and upload_date < data_desde_fmt:
            antes_data += 1
            continue

        if db.is_tiktok_known(vid_id):
            ja_conhecidos += 1
            continue

        duration = info.get('duration') or 0
        title = info.get('title', '')
        video_url = (info.get('url', '') or info.get('webpage_url', '')
                     or f'https://www.tiktok.com/@{handle.lstrip("@")}/video/{vid_id}')

        if not duration:
            db.upsert_tiktok_video(vid_id, handle, title=title, url=video_url,
                                   upload_date=upload_date, duration=0,
                                   status='pulado', skip_reason='foto_post')
            pulados += 1
            continue

        db.upsert_tiktok_video(vid_id, handle, title=title, url=video_url,
                               upload_date=upload_date, duration=int(duration),
                               status='pendente')
        novos += 1

    log(f'  Fetch: {novos} new in queue | {ja_conhecidos} already known | '
        f'{pulados} skipped | {antes_data} before {channel.get("data_desde","")} | '
        f'{"stopped at cutoff" if parou_em_cutoff else "iterated to end"}')

    db.update_tiktok_channel(channel['id'],
        ultimo_scan=datetime.now().strftime('%Y-%m-%d %H:%M'))

    return {'novos': novos, 'pulados': pulados, 'ja_conhecidos': ja_conhecidos,
            'antes_data': antes_data, 'parou_em_cutoff': parou_em_cutoff, 'erro': ''}


# ---------------------------------------------------------------------------
# DOWNLOAD: consume the queue (tiktok_videos status='pendente')
# ---------------------------------------------------------------------------

def _download_single(video, folder_path):
    """Downloads a video. Returns the mp4 filename or None."""
    vid_id = video['tiktok_id']
    video_url = video['url']
    out_template = os.path.join(folder_path, f'{vid_id}.%(ext)s')

    try:
        result = subprocess.run(
            ['yt-dlp', '-o', out_template, '--no-warnings',
             '--format', 'best[ext=mp4]/best',
             '--merge-output-format', 'mp4',
             video_url],
            capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        log(f'  Timeout downloading {vid_id}')
        return None

    if result.returncode != 0:
        log(f'  Download failed {vid_id}: {(result.stderr or "")[:200]}')
        return None

    for f in os.listdir(folder_path):
        if f.startswith(vid_id) and f.endswith('.mp4'):
            return f
    return None


def download_pending_for_channel(channel, max_por_scan=None):
    """Downloads N pending videos for a channel (oldest-first), creates manifest.

    Returns dict: {downloaded, errors, folder, handle}.
    """
    handle = channel['handle']
    if max_por_scan is None:
        max_por_scan = int(channel.get('max_por_scan', 2) or 2)

    pending = db.get_pending_tiktok_videos(handle, limit=max_por_scan, order='oldest')
    if not pending:
        return {'handle': handle, 'downloaded': 0, 'errors': 0, 'folder': '', 'ok': True}

    handle_clean = handle.strip().lstrip('@')
    today = datetime.now().strftime('%Y%m%d')
    folder_name = f'tiktok_{handle_clean}_{today}'
    folder_path = os.path.join(IMPORTS_DIR, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    manifest_path = os.path.join(folder_path, 'manifest.json')
    existing_clips = []
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                raw = json.load(f)
            existing_clips = raw.get('clips', []) if isinstance(raw, dict) else raw
        except Exception:
            existing_clips = []

    downloaded_new = []
    errors = 0

    for video in pending:
        vid_id = video['tiktok_id']
        title = video.get('title') or f'tiktok_{vid_id}'

        if any(vid_id in c.get('file', '') for c in existing_clips):
            db.mark_tiktok_video_status(vid_id, 'baixado')
            continue

        log(f'  Downloading: {title[:50]} ({vid_id})')
        mp4_file = _download_single(video, folder_path)

        if not mp4_file:
            errors += 1
            db.mark_tiktok_video_status(vid_id, 'erro', skip_reason='download_falhou')
            continue

        downloaded_new.append({
            'file': mp4_file,
            'title': title,
            'description': '',
            'tags': ['tiktok', handle_clean],
        })
        db.mark_tiktok_video_status(vid_id, 'baixado')

    if downloaded_new:
        all_clips = existing_clips + downloaded_new
        manifest = {
            'titulo': f'TikTok @{handle_clean}',
            'privacy': 'public',
            'clips': all_clips
        }
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log(f'  Manifest: {folder_name} ({len(downloaded_new)} new, {len(all_clips)} total)')
    else:
        # Remove empty folder if it was just created
        try:
            if not existing_clips:
                os.rmdir(folder_path)
        except OSError:
            pass

    # Update channel counter
    stats = db.get_tiktok_channel_stats(handle)
    db.update_tiktok_channel(channel['id'], total_baixados=stats['baixado'])

    return {'handle': handle, 'downloaded': len(downloaded_new), 'errors': errors,
            'folder': folder_name, 'ok': True}


def download_pending_videos(config=None):
    """Runs download_pending_for_channel on all active channels.

    Called by the scheduler at tiktok_horarios time.
    """
    channels = db.get_tiktok_channels()
    active = [c for c in channels if c.get('ativo', 0) == 1]
    if not active:
        log('  No active TikTok channels')
        return []

    log(f'  Queue download: {len(active)} active channel(s)')
    results = []
    for channel in active:
        try:
            r = download_pending_for_channel(channel)
            results.append(r)
        except Exception as e:
            log(f'  Error on channel {channel.get("handle")}: {e}')
            results.append({'handle': channel.get('handle'), 'downloaded': 0,
                           'errors': 1, 'ok': False, 'motivo': str(e)})

    total = sum(r.get('downloaded', 0) for r in results)
    log(f'  TikTok download complete: {total} video(s) downloaded')

    if total > 0:
        try:
            import import_worker
            import_results = import_worker.process_imports(config)
            processed = [r for r in import_results if r.get('ok')]
            if processed:
                log(f'  Import worker: {len(processed)} batch(es) processed')
        except Exception as e:
            log(f'  Import worker error: {e}')

    return results


# ---------------------------------------------------------------------------
# Compat: process_all_channels (scan + download)
# ---------------------------------------------------------------------------

def process_all_channels(config=None, playlist_end=5000):
    """Full flow (scan + download). Use: manual scan + immediate download."""
    channels = db.get_tiktok_channels()
    active = [c for c in channels if c.get('ativo', 0) == 1]
    if not active:
        log('  No active TikTok channels')
        return []

    log(f'  Scan+download: {len(active)} active channel(s)')
    for channel in active:
        try:
            scan_channel_to_queue(channel, playlist_end=playlist_end)
        except Exception as e:
            log(f'  Error scanning channel {channel.get("handle")}: {e}')

    return download_pending_videos(config)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if action == 'scan':
        # Full scan + immediate download
        results = process_all_channels()
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif action == 'scan-only':
        # Only populate queue, without downloading
        channels = [c for c in db.get_tiktok_channels() if c.get('ativo', 0) == 1]
        out = [scan_channel_to_queue(c) for c in channels]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif action == 'download':
        # Only consume queue
        results = download_pending_videos()
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif action == 'queue':
        channels = db.get_tiktok_channels()
        for c in channels:
            stats = db.get_tiktok_channel_stats(c['handle'])
            print(f'  [{c["id"]}] {c["handle"]}: pending={stats["pendente"]} '
                  f'downloaded={stats["baixado"]} skipped={stats["pulado"]} errors={stats["erro"]}')
    elif action == 'list':
        channels = db.get_tiktok_channels()
        for c in channels:
            print(f'  [{c["id"]}] {c["handle"]} ({"active" if c["ativo"] else "inactive"}) '
                  f'since={c["data_desde"]} max={c["max_por_scan"]} downloaded={c["total_baixados"]}')
    elif action == 'add':
        handle = sys.argv[2] if len(sys.argv) > 2 else ''
        if not handle:
            print('Usage: tiktok_scanner.py add @handle')
            sys.exit(1)
        row_id = db.add_tiktok_channel(handle)
        print(f'Channel added: {handle} (id={row_id})')
    else:
        print(f'Usage: {sys.argv[0]} scan | scan-only | download | queue | list | add @handle')
