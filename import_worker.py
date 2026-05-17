#!/usr/bin/env python3
"""
import_worker.py — Imports clips from external folders into the publishing pipeline.

Monitors the `imports/` folder at the project root.
Each subfolder = a batch of clips. When detected:
  1. Creates a "virtual" entry in the database (status_cortes=concluido)
  2. Moves MP4s to lives/<video_id>/clips/
  3. Generates clips_manifest.json
  4. Removes the subfolder from imports/

Relevant settings in the database (config):
  import_gerar_descricao  true|false  — use AI to generate description when absent
  import_auto             true|false  — enable hourly check by the scheduler
"""

import os
import sys
import json
import shutil
import re
import subprocess
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
IMPORTS_DIR  = os.path.join(PROJECT_ROOT, 'imports')
LIVES_DIR    = os.environ.get('LIVES_DIR', os.path.join(PROJECT_ROOT, 'lives'))
CONFIG_DIR   = os.environ.get('GWS_CONFIG_DIR', os.path.join(PROJECT_ROOT, 'config'))

import db


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] [import] {msg}', file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(name):
    """Converts folder name to a safe video_id."""
    s = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    return s[:40].strip('_') or 'import'


def _title_from_filename(filename):
    """
    Extracts a readable title from the filename.
    Examples:
      clip_01_Como usar n8n.mp4        -> Como usar n8n
      03_Tutorial basico.mp4           -> Tutorial basico
      c0002-pascoa2026_quick_01.mp4    -> Pascoa2026 Quick 01
    """
    name = os.path.splitext(filename)[0]
    # If it has __ (prefix separator), take only the part after it
    if '__' in name:
        name = name.split('__', 1)[1]
    # Remove prefixes like clip_01_, 03_, c0002-, etc.
    name = re.sub(r'^clip_\d+_', '', name)
    name = re.sub(r'^\d+[_\-\s]+', '', name)
    name = re.sub(r'^[a-z]\d+[_\-]', '', name)  # c0002- style
    # Remove date/time suffixes (e.g.: _20260404_095306)
    name = re.sub(r'_\d{8}_\d{6}$', '', name)
    name = re.sub(r'_\d{8}$', '', name)
    # Replace separators with spaces
    name = name.replace('_', ' ').replace('-', ' ')
    # Remove purely numeric standalone words (e.g.: "01", "02")
    name = re.sub(r'\b\d+\b', '', name)
    # Normalize spaces and capitalize
    name = ' '.join(w for w in name.split() if w)
    return name.title() if name else filename


def _gerar_descricao_ia(title):
    """Uses Claude CLI to generate a short description from the title."""
    prompt = (
        f'Gere uma descricao curta (2-3 frases) em portugues para um video de YouTube '
        f'com o titulo: "{title}". Retorne apenas a descricao, sem introducao.'
    )
    try:
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)
        result = subprocess.run(
            ['claude', '-p', '--output-format', 'text', prompt],
            capture_output=True, text=True, timeout=60, env=env
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        log(f'  AI description error: {e}')
    return ''


def _collect_mp4s(folder_path):
    """
    Collects all MP4 files inside folder_path, recursively.
    Returns a list of (absolute_path, filename) sorted by name.
    """
    found = []
    for root, dirs, files in os.walk(folder_path):
        # Ignore the destination folder itself (clips_dir may be inside)
        dirs[:] = [d for d in sorted(dirs) if d != 'clips']
        for f in sorted(files):
            if f.lower().endswith('.mp4'):
                found.append((os.path.join(root, f), f))
    return found


def _build_manifest(clips_dir, folder_path, gerar_descricao):
    """
    Builds the clip list for the manifest.
    Scans folder_path recursively for MP4s.
    Uses manifest.json from the import folder if it exists for metadata.
    """
    manifest_src = os.path.join(folder_path, 'manifest.json')
    mp4_files = _collect_mp4s(folder_path)

    if not mp4_files:
        return []

    # Map filename->data from manual manifest (if it exists)
    manual = {}
    if os.path.exists(manifest_src):
        try:
            with open(manifest_src) as f:
                raw = json.load(f)
            # supports clip list or dict with "clips" key
            entries = raw if isinstance(raw, list) else raw.get('clips', [])
            for entry in entries:
                fname = os.path.basename(entry.get('file', entry.get('filename', '')))
                manual[fname] = entry
        except Exception as e:
            log(f'  manifest.json invalid: {e}, ignoring')

    clips = []
    for i, (src_path, fname) in enumerate(mp4_files, start=1):
        m = manual.get(fname, {})
        title = m.get('title') or _title_from_filename(fname)
        description = m.get('description', '')
        tags = m.get('tags', [])

        if not description and gerar_descricao:
            log(f'  Generating AI description for: {title[:50]}')
            description = _gerar_descricao_ia(title)

        dest_file = os.path.join(clips_dir, fname)
        clips.append({
            'index':       i,
            '_src_path':   src_path,   # actual source path (may be in a subdirectory)
            'file':        dest_file,
            'filename':    fname,
            'title':       title,
            'description': description,
            'tags':        tags,
            'duration':    0,
        })

    return clips


# ---------------------------------------------------------------------------
# Core: process a subfolder
# ---------------------------------------------------------------------------

def _read_folder_meta(folder_path):
    """
    Reads optional metadata from the folder's root manifest.json.
    Recognized fields at batch level:
      publish_at  — HH:MM time to publish (e.g.: "14:00")
                    if absent, follows the global schedule (pub_horarios)
      privacy     — public|unlisted|private (overrides global config)
      titulo      — batch name to display in the dashboard
    Returns dict (may be empty).
    """
    manifest_src = os.path.join(folder_path, 'manifest.json')
    if not os.path.exists(manifest_src):
        return {}
    try:
        with open(manifest_src) as f:
            data = json.load(f)
        # manifest can be a list (clips) or dict (meta + clips)
        if isinstance(data, dict):
            return {
                'publish_at': data.get('publish_at', ''),
                'privacy':    data.get('privacy', ''),
                'titulo':     data.get('titulo', ''),
            }
    except Exception:
        pass
    return {}


def _process_folder(folder_name, gerar_descricao, import_fila=True):
    """
    Processes a subfolder from imports/.
    import_fila=True  -> enters the normal queue (pub_horarios from global config)
    import_fila=False -> uses publish_at from manifest.json if available

    Returns (video_id, qtd_clips) on success, or None on error.
    """
    folder_path = os.path.join(IMPORTS_DIR, folder_name)
    if not os.path.isdir(folder_path):
        return None

    date_str  = datetime.now().strftime('%Y%m%d')
    video_id  = f'import_{date_str}_{_sanitize(folder_name)}'

    # Check if it already exists in the database
    if db.get_live(video_id):
        log(f'  {video_id} already exists in database, skipping')
        return None

    # Read batch metadata (publish_at, privacy, titulo)
    meta = _read_folder_meta(folder_path)
    titulo_lote = meta.get('titulo') or folder_name

    # publish_at: only used if import_fila=False
    publish_at = '' if import_fila else meta.get('publish_at', '')

    # Prepare destination directory
    job_dir   = os.path.join(LIVES_DIR, video_id)
    clips_dir = os.path.join(job_dir, 'clips')
    os.makedirs(clips_dir, exist_ok=True)

    # Build manifest (before moving, to read file list)
    clips = _build_manifest(clips_dir, folder_path, gerar_descricao)
    if not clips:
        log(f'  No MP4s in {folder_name}, skipping')
        shutil.rmtree(job_dir, ignore_errors=True)
        return None

    # Apply batch privacy to each clip (if specified)
    if meta.get('privacy'):
        for clip in clips:
            clip['privacy'] = meta['privacy']

    # Move MP4s (uses _src_path to support files in subdirectories)
    for clip in clips:
        src = clip.pop('_src_path', os.path.join(folder_path, clip['filename']))
        dst = clip['file']
        if os.path.exists(src):
            shutil.move(src, dst)
            log(f'  Moved: {clip["filename"]}')

    # Write clips_manifest.json
    manifest_path = os.path.join(job_dir, 'clips_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(clips, f, ensure_ascii=False, indent=2)

    # Notes include publish_at if defined
    obs_parts = [f'imported from {folder_name}']
    if publish_at:
        obs_parts.append(f'publish_at={publish_at}')
    if meta.get('privacy'):
        obs_parts.append(f'privacy={meta["privacy"]}')

    # Insert into database
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    db.add_lives([{
        'video_id':            video_id,
        'titulo':              titulo_lote,
        'data_live':           now[:10],
        'duracao_min':         '0',
        'url':                 '',
        'status_transcricao':  'concluido',
        'status_cortes':       'concluido',
        'qtd_clips':           str(len(clips)),
        'clips_publicados':    '0',
        'clips_pendentes':     str(len(clips)),
        'data_sync':           now,
        'observacoes':         ' | '.join(obs_parts),
        'data_corte':          now,
    }])

    log(f'  Created {video_id}: {len(clips)} clips | publish_at={publish_at or "global_queue"} | privacy={meta.get("privacy") or "global_config"}')

    # Remove subfolder from imports/
    shutil.rmtree(folder_path, ignore_errors=True)

    return video_id, len(clips)


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def process_imports(config=None):
    """
    Scans imports/ and processes all new subfolders.
    Returns a list of dicts with the result per folder.
    """
    if not os.path.isdir(IMPORTS_DIR):
        os.makedirs(IMPORTS_DIR, exist_ok=True)
        log('imports/ folder created (empty)')
        return []

    if config is None:
        config = db.load_config()

    gerar_descricao = config.get('import_gerar_descricao', 'false') == 'true'
    # import_fila_global=true  -> ignores publish_at from manifest, enters normal queue
    # import_fila_global=false -> respects publish_at from manifest if defined
    import_fila = config.get('import_fila_global', 'true') == 'true'

    folders = [
        f for f in os.listdir(IMPORTS_DIR)
        if os.path.isdir(os.path.join(IMPORTS_DIR, f))
        and not f.startswith('.')
    ]

    if not folders:
        log('imports/: no new folders found')
        return []

    log(f'imports/: {len(folders)} folder(s) found')
    results = []
    for folder_name in sorted(folders):
        log(f'  Processing: {folder_name}')
        try:
            res = _process_folder(folder_name, gerar_descricao, import_fila)
            if res:
                video_id, qtd = res
                results.append({'pasta': folder_name, 'video_id': video_id, 'clips': qtd, 'ok': True})
            else:
                results.append({'pasta': folder_name, 'ok': False, 'motivo': 'no clips or already exists'})
        except Exception as e:
            log(f'  ERROR processing {folder_name}: {e}')
            results.append({'pasta': folder_name, 'ok': False, 'motivo': str(e)})

    return results


def clean_imports():
    """
    Removes all content from imports/ (unprocessed folders or residual files).
    Returns the number of items removed.
    """
    if not os.path.isdir(IMPORTS_DIR):
        return 0
    items = [f for f in os.listdir(IMPORTS_DIR) if not f.startswith('.')]
    for item in items:
        path = os.path.join(IMPORTS_DIR, item)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    log(f'clean_imports: {len(items)} item(s) removed')
    return len(items)


def clean_clips(only_fully_published=True):
    """
    Removes the clips/ folder from lives that have had all clips published.
    If only_fully_published=False, removes clips/ from ALL lives (use with caution).
    Returns the number of lives cleaned.
    """
    lives = db.get_lives()
    cleaned = 0
    for live in lives:
        vid = live.get('video_id', '')
        if not vid:
            continue

        qtd      = int(live.get('qtd_clips', '0') or '0')
        pub      = int(live.get('clips_publicados', '0') or '0')
        is_done  = qtd > 0 and pub >= qtd

        if not only_fully_published or is_done:
            clips_path = os.path.join(LIVES_DIR, vid, 'clips')
            if os.path.isdir(clips_path) and os.listdir(clips_path):
                shutil.rmtree(clips_path)
                os.makedirs(clips_path)  # recreate empty so checks don't break
                log(f'  Clips cleaned: {vid}')
                cleaned += 1

    log(f'clean_clips: {cleaned} live(s) cleaned')
    return cleaned


# ---------------------------------------------------------------------------
# Distribution between instances
# ---------------------------------------------------------------------------

# Root where all instances live (e.g.: /home/nmaldaner/projetos/)
_INSTANCES_BASE = os.path.dirname(PROJECT_ROOT)
_INSTANCE_NAMES = [f'yt-pub-lives{i}' for i in range(1, 10)]

# Central distribution folder (outside the instances)
DIST_IMPORTS_DIR = '/home/nmaldaner/projetos/yt-pub-lives/imports'


def _collect_all_mp4s_flat(source_dir=None):
    """
    Collects all MP4s inside source_dir recursively (except .hidden).
    Returns a list of sorted absolute paths.
    """
    src = source_dir or IMPORTS_DIR
    found = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted([d for d in dirs if not d.startswith('.')])
        for f in sorted(files):
            if f.lower().endswith('.mp4'):
                found.append(os.path.join(root, f))
    return found


def distribute_imports(config=None):
    """
    Reads MP4s from DIST_IMPORTS_DIR (/home/nmaldaner/projetos/yt-pub-lives/imports/)
    and distributes round-robin to imports/dist_TIMESTAMP/ of each of the 7 instances.
    Each instance processes at its own time (scheduler import_auto or manual scan).
    Returns dict: { total, source, por_instancia: [{instancia, clips, ok}] }
    """
    if not os.path.isdir(DIST_IMPORTS_DIR):
        os.makedirs(DIST_IMPORTS_DIR, exist_ok=True)
        return {'total': 0, 'source': DIST_IMPORTS_DIR, 'por_instancia': []}

    all_mp4s = _collect_all_mp4s_flat(DIST_IMPORTS_DIR)
    if not all_mp4s:
        log(f'distribute_imports: no MP4s found in {DIST_IMPORTS_DIR}')
        return {'total': 0, 'source': DIST_IMPORTS_DIR, 'por_instancia': []}

    # Filter instances that exist on disk
    instances = [
        os.path.join(_INSTANCES_BASE, name)
        for name in _INSTANCE_NAMES
        if os.path.isdir(os.path.join(_INSTANCES_BASE, name))
    ]
    n = len(instances)
    log(f'distribute_imports: {len(all_mp4s)} MP4(s) -> {n} instances')

    # Round-robin: video i goes to instances[i % n]
    buckets = [[] for _ in range(n)]
    for i, mp4 in enumerate(all_mp4s):
        buckets[i % n].append(mp4)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results = []

    for inst_dir, bucket in zip(instances, buckets):
        inst_name = os.path.basename(inst_dir)
        if not bucket:
            results.append({'instancia': inst_name, 'clips': 0, 'ok': True})
            continue

        dist_folder = os.path.join(inst_dir, 'imports', f'dist_{timestamp}')
        os.makedirs(dist_folder, exist_ok=True)

        moved = []
        for src in bucket:
            fname = os.path.basename(src)
            dst = os.path.join(dist_folder, fname)
            counter = 1
            base, ext = os.path.splitext(fname)
            while os.path.exists(dst):
                dst = os.path.join(dist_folder, f'{base}_{counter}{ext}')
                counter += 1
            try:
                shutil.move(src, dst)
                moved.append(fname)
                log(f'  -> {inst_name}: {fname}')
            except Exception as e:
                log(f'  ERROR moving {fname} to {inst_name}: {e}')

        results.append({'instancia': inst_name, 'clips': len(moved), 'ok': True})

    # Remove empty directories left behind in imports/
    for root, dirs, files in os.walk(IMPORTS_DIR, topdown=False):
        if root != IMPORTS_DIR and not os.listdir(root):
            try:
                os.rmdir(root)
            except OSError:
                pass

    return {'total': len(all_mp4s), 'source': DIST_IMPORTS_DIR, 'por_instancia': results}


# ---------------------------------------------------------------------------
# Direct execution (test / CLI)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if action == 'scan':
        results = process_imports()
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif action == 'distribute':
        results = distribute_imports()
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif action == 'clean-imports':
        n = clean_imports()
        print(f'{n} items removed from imports/')
    elif action == 'clean-clips':
        n = clean_clips(only_fully_published='--all' not in sys.argv)
        print(f'{n} lives with clips cleaned')
    else:
        print(f'Usage: {sys.argv[0]} scan | distribute | clean-imports | clean-clips [--all]')
