#!/usr/bin/env python3
"""
Scheduler for the yt-pub-lives pipeline.
Runs in a loop, checking every minute whether it is time to cut or publish.
Reads configuration from the local SQLite database.
"""

import json
import os
import shutil
import sys
import time
import subprocess
import base64
import tempfile
import urllib.request
import urllib.parse
import threading
from datetime import datetime

# Config
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get('GWS_CONFIG_DIR', os.path.join(PROJECT_ROOT, 'config'))
ENV_FILE = os.path.join(CONFIG_DIR, '.env')
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard', 'scheduler_status.json')

# Load env (before reading env-dependent vars)
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key] = val

LIVES_DIR = os.environ.get('LIVES_DIR', os.path.join(PROJECT_ROOT, 'lives'))

import db


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr, flush=True)


def update_status(state, detail='', video_id='', step='', clip_id='', clip_title=''):
    """Writes the current scheduler status to JSON for the dashboard to read."""
    data = {
        'state': state,        # idle | cutting | publishing | error
        'detail': detail,
        'video_id': video_id,
        'clip_id': clip_id,
        'clip_title': clip_title,
        'step': step,          # current step: transcription | analysis | download | cut | thumbnail | upload
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def get_access_token():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with open(os.path.join(CONFIG_DIR, '.encryption_key'), 'r') as f:
        key = base64.b64decode(f.read().strip())

    with open(os.path.join(CONFIG_DIR, 'credentials.enc'), 'rb') as f:
        data = f.read()

    aesgcm = AESGCM(key)
    creds = json.loads(aesgcm.decrypt(data[:12], data[12:], None))

    token_data = urllib.parse.urlencode({
        'client_id': os.environ['CLIENT_ID'],
        'client_secret': os.environ['CLIENT_SECRET'],
        'refresh_token': creds['refresh_token'],
        'grant_type': 'refresh_token'
    }).encode()

    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=token_data)
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp['access_token']


def load_config():
    """Reads CONFIG from the local database."""
    return db.load_config()


def get_pending_lives():
    """Returns lives (oldest first)."""
    return db.get_lives()


def get_matching_schedule(horarios_str):
    """Returns the scheduled time that matches now, or None.
    Supports HH:00 (top of hour) and HH:MM (exact minute)."""
    if not horarios_str:
        return None
    now_hm = datetime.now().strftime('%H:%M')
    now_hour = datetime.now().strftime('%H:00')
    for h in horarios_str.split(','):
        h = h.strip()
        if h == now_hm:
            return h
        if h == now_hour:
            return h
    return None


def run_corte(video_id, config=None):
    """Runs yt-clip for a live, updating status per step."""
    log(f'  Running cut: {video_id}')
    update_status('cutting', f'Downloading transcript...', video_id, step='transcription')
    script = os.path.join(SCRIPTS_DIR, 'yt-clip')
    env = os.environ.copy()
    env['LIVES_DIR'] = LIVES_DIR
    env['PATH'] = f"{os.path.expanduser('~/.deno/bin')}:/usr/bin:{os.path.expanduser('~/.local/bin')}:{SCRIPTS_DIR}:{env.get('PATH', '')}"

    # Analysis mode: claude-api | anthropic-api | openrouter-api | piramyd-api
    ai_mode = 'claude-api'
    if config:
        ai_mode = config.get('ai_mode', 'claude-api')
        ai_model = config.get('ai_model', '')
        if ai_model:
            env['AI_MODEL'] = ai_model
        if ai_mode == 'anthropic-api':
            key = config.get('anthropic_api_key', '')
            if key:
                env['ANTHROPIC_API_KEY'] = key
        elif ai_mode == 'openrouter-api':
            key = config.get('openrouter_api_key', '')
            if key:
                env['OPENROUTER_API_KEY'] = key
        elif ai_mode == 'piramyd-api':
            key = config.get('thumb_api_key', '')
            if key:
                env['PIRAMYD_API_KEY'] = key

    proc = subprocess.Popen(
        [script, video_id, '--ai', ai_mode],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env
    )

    output_lines = []
    last_logged_pct = -10
    step_map = {
        '[1/5]': ('transcription', 'Downloading transcript...'),
        '[2/5]': ('transcript_analysis', 'Processing transcript...'),
        '[3/5]': ('analysis', 'Analyzing topics with AI...'),
        '[4/5]': ('cut', 'Downloading video and cutting clips...'),
        '[5/5]': ('publication', 'Finishing...'),
    }

    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        output_lines.append(line)

        # Filter yt-dlp download lines (log only every 10%)
        if '[download]' in line and '%' in line:
            try:
                pct = float(line.split('%')[0].split()[-1])
                if pct - last_logged_pct < 10:
                    continue
                last_logged_pct = pct
            except (ValueError, IndexError):
                pass

        log(f'    | {line}')
        # Detect step from [N/5] marker
        for marker, (step, label) in step_map.items():
            if marker in line:
                update_status('cutting', label, video_id, step=step)
                break

    proc.wait()

    if proc.returncode == 0:
        log(f'  Cut completed: {video_id}')
        update_status('idle', f'Cut completed: {video_id}', video_id)
        return True
    else:
        last_output = '\n'.join(output_lines[-5:]) if output_lines else 'no output'
        log(f'  Cut error: {last_output}')
        update_status('error', f'Cut error: {video_id}', video_id)
        return False


def refine_pub_with_ai(title, description, config, video_id=''):
    """Uses Claude CLI (OAuth) to refine title and description before publishing."""
    prompt_file = os.path.join(CONFIG_DIR, 'prompt_pub.txt')
    if not os.path.exists(prompt_file):
        return title, description

    with open(prompt_file) as f:
        system_prompt = f.read().strip()
    if not system_prompt:
        return title, description

    user_msg = f'Original title: "{title}"\nOriginal description: "{description}"\nOriginal live video ID: {video_id}'
    full_prompt = f'{system_prompt}\n\n---\n\n{user_msg}'

    try:
        log(f'  Refining title/description with Claude CLI...')
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)
        result = subprocess.run(
            ['claude', '-p', '--output-format', 'json', full_prompt],
            capture_output=True, text=True, timeout=120, env=env
        )
        if result.returncode != 0:
            log(f'  Claude CLI error (code {result.returncode}): {result.stderr[:200]}, using originals')
            return title, description

        data = json.loads(result.stdout)
        content = data.get('result', '')

        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            refined = json.loads(json_match.group())
            new_title = refined.get('title', title)
            new_desc = refined.get('description', description)
            log(f'  Refined title: {new_title[:60]}')
            return new_title, new_desc
        else:
            log(f'  AI did not return valid JSON, using originals')
            return title, description
    except Exception as e:
        log(f'  Error refining with AI: {e}, using originals')
        return title, description


def run_publicacao(video_id, clip_file, title, description, tags, privacy):
    """Runs yt-publish for a clip."""
    log(f'  Publishing: {title[:60]}')
    log(f'  File: {clip_file} ({os.path.getsize(clip_file) / 1024 / 1024:.1f} MB)')
    update_status('publishing', f'Publishing: {title[:50]}', video_id, step='upload')
    script = os.path.join(SCRIPTS_DIR, 'yt-publish')
    env = os.environ.copy()
    env['PATH'] = f"{os.path.expanduser('~/.deno/bin')}:/usr/bin:{os.path.expanduser('~/.local/bin')}:{SCRIPTS_DIR}:{env.get('PATH', '')}"

    cmd = [script, clip_file, '--title', title, '--description', description, '--privacy', privacy]
    if tags:
        cmd += ['--tags', tags]

    log(f'  CMD: {" ".join(cmd[:3])} ...')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    output_lines = []
    video_id_result = None
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            output_lines.append(line)
            log(f'    | {line}')
            if 'Video ID:' in line:
                video_id_result = line.split('Video ID:')[1].strip()

        proc.wait(timeout=600)  # 10 min max per upload
    except subprocess.TimeoutExpired:
        log(f'  TIMEOUT: publication exceeded 10 min, killing process')
        proc.kill()
        proc.wait()
        return None

    if proc.returncode == 0:
        if video_id_result:
            return video_id_result
        log(f'  Published but no video ID in output')
        return 'unknown'
    else:
        last_output = '\n'.join(output_lines[-5:]) if output_lines else 'no output'
        log(f'  Publication error: {last_output}')
        return None


def upload_thumbnail(video_id, thumb_path):
    """Upload thumbnail to YouTube using thumbnails.set API."""
    token = get_access_token()
    url = f'https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}&uploadType=media'

    with open(thumb_path, 'rb') as f:
        img_data = f.read()

    req = urllib.request.Request(url, data=img_data, method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'image/jpeg')
    req.add_header('Content-Length', str(len(img_data)))

    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read())
    log(f'  Thumbnail uploaded for {video_id}')
    return result


def _add_pending_thumb(video_id, title):
    """Add a thumbnail to the pending list for later upload."""
    pending_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lives', 'pending_thumbs.json')
    pending = []
    if os.path.exists(pending_file):
        try:
            with open(pending_file) as f:
                pending = json.load(f)
        except Exception:
            pending = []
    # Avoid duplicates
    if not any(p['id'] == video_id for p in pending):
        pending.append({'id': video_id, 'title': title})
        with open(pending_file, 'w') as f:
            json.dump(pending, f, indent=2, ensure_ascii=False)


def _apply_saved_preset(preset_name, config, yt_thumb):
    """Applies a saved (custom) or hardcoded preset."""
    saved = config.get(f'preset_{preset_name}', '')
    if saved:
        try:
            preset_data = json.loads(saved)
            # Map JS fields to env vars
            field_map = {
                'font': 'DESIGN_FONT', 'fontSize': 'DESIGN_FONT_SIZE', 'lastLineScale': 'DESIGN_LAST_LINE_SCALE',
                'lineHeight': 'DESIGN_LINE_HEIGHT', 'tracking': 'DESIGN_TRACKING', 'case': 'DESIGN_CASE',
                'textColor': 'DESIGN_TEXT_COLOR', 'highlightColor': 'DESIGN_HIGHLIGHT_COLOR',
                'highlightEnabled': 'DESIGN_HIGHLIGHT_ENABLED', 'accentColor': 'DESIGN_ACCENT_COLOR',
                'accentWidth': 'DESIGN_ACCENT_WIDTH', 'accentHeight': 'DESIGN_ACCENT_HEIGHT',
                'accentGap': 'DESIGN_ACCENT_GAP', 'accentEnabled': 'DESIGN_ACCENT_ENABLED',
                'strokeEnabled': 'DESIGN_STROKE_ENABLED', 'strokeColor': 'DESIGN_STROKE_COLOR',
                'strokeSize': 'DESIGN_STROKE_SIZE', 'shadowType': 'DESIGN_SHADOW_TYPE',
                'shadowColor': 'DESIGN_SHADOW_COLOR', 'shadowSize': 'DESIGN_SHADOW_SIZE',
                'shadowOpacity': 'DESIGN_SHADOW_OPACITY', 'gradient': 'DESIGN_GRADIENT',
                'gradientOpacity': 'DESIGN_GRADIENT_OPACITY', 'gradientCoverage': 'DESIGN_GRADIENT_COVERAGE',
                'brand': 'DESIGN_BRAND', 'brandFont': 'DESIGN_BRAND_FONT', 'brandSize': 'DESIGN_BRAND_SIZE',
                'brandColor': 'DESIGN_BRAND_COLOR', 'brandPosition': 'DESIGN_BRAND_POSITION',
                'position': 'DESIGN_POSITION'
            }
            for js_key, env_key in field_map.items():
                if js_key in preset_data:
                    os.environ[env_key] = str(preset_data[js_key])
            log(f'  Custom preset: {preset_name}')
            return
        except Exception:
            pass
    # Fallback to hardcoded preset
    if preset_name in yt_thumb.PRESETS:
        for k, v in yt_thumb.PRESETS[preset_name].items():
            os.environ[k] = v
    log(f'  [thumb] Preset ativo: {preset_name}')


def handle_thumbnail(video_id, title, description, config):
    """Generate and upload thumbnail based on config thumb_mode."""
    thumb_mode = config.get('thumb_mode', 'none')
    if thumb_mode == 'none':
        return

    thumb_path = f'/tmp/yt_thumb_{video_id}.jpg'

    try:
        if thumb_mode == 'api':
            # Set API key, model and visual config before importing
            api_key = config.get('thumb_api_key', '')
            model = config.get('thumb_model', 'dreamshaper')
            if api_key:
                os.environ['PIRAMYD_API_KEY'] = api_key
            os.environ['THUMB_MODEL'] = model
            # Image provider
            img_provider = config.get('thumb_image_provider', 'piramyd')
            os.environ['THUMB_IMAGE_PROVIDER'] = img_provider
            kie_key = config.get('kie_api_key', '')
            if kie_key:
                os.environ['KIE_API_KEY'] = kie_key
            minimax_key = config.get('minimax_api_key', '')
            if minimax_key:
                os.environ['MINIMAX_API_KEY'] = minimax_key
            google_img_key = config.get('google_image_api_key', '')
            if google_img_key:
                os.environ['GOOGLE_IMAGE_API_KEY'] = google_img_key
            inemaimg_url = config.get('inemaimg_url', '')
            if inemaimg_url:
                os.environ['INEMAIMG_URL'] = inemaimg_url
            google_img_model = config.get('google_image_model', '')
            if google_img_model:
                os.environ['GOOGLE_IMAGE_MODEL'] = google_img_model
            # API keys for providers
            or_key = config.get('openrouter_api_key', '')
            if or_key:
                os.environ['OPENROUTER_API_KEY'] = or_key
            ant_key = config.get('anthropic_api_key', '')
            if ant_key:
                os.environ['ANTHROPIC_API_KEY'] = ant_key
            # LLM chain (3 attempts)
            for i in range(1, 4):
                p = config.get(f'thumb_llm_{i}_provider', '')
                m = config.get(f'thumb_llm_{i}_model', '')
                if p:
                    os.environ[f'THUMB_LLM_{i}_PROVIDER'] = p
                if m:
                    os.environ[f'THUMB_LLM_{i}_MODEL'] = m
            # Visual settings
            for key in ('thumb_font_size', 'thumb_text_color', 'thumb_accent_color',
                        'thumb_brand_color', 'thumb_text_position', 'thumb_brand'):
                val = config.get(key, '')
                if val:
                    os.environ[key.upper()] = val
            # Design config
            for key in ('design_font', 'design_font_size', 'design_last_line_scale',
                        'design_line_height', 'design_tracking', 'design_case',
                        'design_text_color', 'design_highlight_color', 'design_highlight_enabled',
                        'design_accent_color', 'design_accent_width',
                        'design_accent_height', 'design_accent_gap', 'design_accent_enabled',
                        'design_stroke_enabled', 'design_stroke_color', 'design_stroke_size',
                        'design_shadow_type', 'design_shadow_color', 'design_shadow_size',
                        'design_shadow_opacity', 'design_gradient', 'design_gradient_opacity',
                        'design_gradient_coverage', 'design_brand', 'design_brand_font',
                        'design_brand_size', 'design_brand_color', 'design_brand_position',
                        'design_position'):
                val = config.get(key, '')
                if val:
                    os.environ[key.upper()] = val

            # Random preset: if presets are selected, pick one randomly
            import random
            random_presets_str = config.get('design_random_presets', '')
            if random_presets_str:
                random_list = [p.strip() for p in random_presets_str.split(',') if p.strip()]
                if random_list:
                    chosen = random.choice(random_list)
                    os.environ['DESIGN_RANDOM_PRESET'] = chosen
                    # Load custom preset if it exists
                    saved = config.get(f'preset_{chosen}', '')
                    if saved:
                        os.environ['DESIGN_SAVED_PRESET'] = saved
                    log(f'  Random preset: {chosen} (from {len(random_list)} options)')

            # Import generate_thumbnail from scripts/yt-thumbnail
            import types
            script_path = os.path.join(SCRIPTS_DIR, 'yt-thumbnail')
            yt_thumb = types.ModuleType('yt_thumbnail')
            yt_thumb.__file__ = script_path
            with open(script_path) as _f:
                exec(compile(_f.read(), script_path, 'exec'), yt_thumb.__dict__)

            log(f'  Generating API thumbnail for {video_id} (model: {model})')
            try:
                yt_thumb.generate_thumbnail(title, description, thumb_path)
            except Exception as api_err:
                log(f'  API thumbnail failed: {api_err}, using fallback')
                _apply_saved_preset('fallback', config, yt_thumb)
                bg = yt_thumb.create_gradient_bg()
                yt_thumb.compose_thumbnail(bg, title[:70], '', thumb_path)

        elif thumb_mode == 'local':
            # Local Pillow-based: extract frame from video + overlay text
            log(f'  Generating local thumbnail for {video_id}')
            from PIL import Image, ImageDraw, ImageFont

            # Create simple gradient background with text overlay
            img = Image.new('RGB', (1280, 720))
            for y in range(720):
                r = int(10 + 10 * y / 720)
                g = int(10 + 5 * y / 720)
                b = int(30 + 20 * y / 720)
                for x in range(1280):
                    img.putpixel((x, y), (r, g, b))

            draw = ImageDraw.Draw(img)
            font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
            try:
                font = ImageFont.truetype(font_path, 64)
            except Exception:
                font = ImageFont.load_default()

            # Wrap and draw title text
            words = title[:70].upper().split()
            lines, current = [], ''
            for word in words:
                test = (current + ' ' + word).strip()
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] > 1000 and current:
                    lines.append(current)
                    current = word
                else:
                    current = test
            if current:
                lines.append(current)

            y_pos = 80
            for line in lines[:3]:
                # Shadow
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        draw.text((130 + dx, y_pos + dy), line, font=font, fill=(0, 0, 0))
                draw.text((130, y_pos), line, font=font, fill=(255, 255, 255))
                y_pos += 80

            img.save(thumb_path, 'JPEG', quality=92)

        elif thumb_mode == 'fallback':
            # Fallback: sempre usa preset "fallback"
            log(f'  Generating fallback thumbnail for {video_id}')

            import types
            script_path = os.path.join(SCRIPTS_DIR, 'yt-thumbnail')
            yt_thumb = types.ModuleType('yt_thumbnail')
            yt_thumb.__file__ = script_path
            with open(script_path) as _f:
                exec(compile(_f.read(), script_path, 'exec'), yt_thumb.__dict__)

            _apply_saved_preset('fallback', config, yt_thumb)

            bg = yt_thumb.create_gradient_bg()
            yt_thumb.compose_thumbnail(bg, title[:70], '', thumb_path)

        else:
            log(f'  Unknown thumb_mode: {thumb_mode}, skipping')
            return

        # Save a copy to lives/thumbs/ for future reference
        if os.path.exists(thumb_path):
            thumbs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lives', 'thumbs')
            os.makedirs(thumbs_dir, exist_ok=True)
            saved_path = os.path.join(thumbs_dir, f'{video_id}.jpg')
            shutil.copy2(thumb_path, saved_path)

            # Upload to YouTube
            try:
                upload_thumbnail(video_id, thumb_path)
            except Exception as upload_err:
                log(f'  Thumbnail upload failed, saved as pending: {upload_err}')
                _add_pending_thumb(video_id, title)
            # Clean up temp file
            try:
                os.remove(thumb_path)
            except OSError:
                pass

    except Exception as e:
        log(f'  Thumbnail error (non-fatal): {e}')


def update_video_metadata(video_id, title, description):
    """Update video title and description on YouTube via Data API v3."""
    token = get_access_token()
    body = {
        'id': video_id,
        'snippet': {
            'title': title,
            'description': description,
            'categoryId': '22'
        }
    }
    url = 'https://www.googleapis.com/youtube/v3/videos?part=snippet'
    req_data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=req_data, method='PUT')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/json')

    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read())
    log(f'  YouTube metadata updated for {video_id}: {title[:60]}')
    return result


def generate_enrich_thumbnail(title, config):
    """Generate thumbnail with default background + text overlay using Design Thumb settings."""
    import types
    import random
    from PIL import Image

    default_bg_path = os.path.join(CONFIG_DIR, 'thumb_default.jpg')

    # Load or generate default background
    if os.path.exists(default_bg_path):
        bg = Image.open(default_bg_path).resize((1280, 720), Image.LANCZOS).convert('RGB')
    else:
        bg = Image.new('RGB', (1280, 720))
        for y in range(720):
            r = int(26 - 11 * y / 720)
            g = int(26 - 11 * y / 720)
            b = int(46 - 11 * y / 720)
            for x in range(1280):
                bg.putpixel((x, y), (max(r, 0), max(g, 0), max(b, 0)))
        bg.save(default_bg_path, 'JPEG', quality=92)
        log(f'  Generated default thumbnail background: {default_bg_path}')

    # Apply Design Thumb config from database (same as handle_thumbnail)
    for key in ('design_font', 'design_font_size', 'design_last_line_scale',
                'design_line_height', 'design_tracking', 'design_case',
                'design_text_color', 'design_highlight_color', 'design_highlight_enabled',
                'design_accent_color', 'design_accent_width',
                'design_accent_height', 'design_accent_gap', 'design_accent_enabled',
                'design_stroke_enabled', 'design_stroke_color', 'design_stroke_size',
                'design_shadow_type', 'design_shadow_color', 'design_shadow_size',
                'design_shadow_opacity', 'design_gradient', 'design_gradient_opacity',
                'design_gradient_coverage', 'design_brand', 'design_brand_font',
                'design_brand_size', 'design_brand_color', 'design_brand_position',
                'design_position'):
        val = config.get(key, '')
        if val:
            os.environ[key.upper()] = val

    # Random preset: if configured, pick one
    random_presets_str = config.get('design_random_presets', '')
    if random_presets_str:
        random_list = [p.strip() for p in random_presets_str.split(',') if p.strip()]
        if random_list:
            chosen = random.choice(random_list)
            os.environ['DESIGN_RANDOM_PRESET'] = chosen
            saved = config.get(f'preset_{chosen}', '')
            if saved:
                os.environ['DESIGN_SAVED_PRESET'] = saved
            log(f'  Enrich thumb preset: {chosen}')

    # Load yt-thumbnail module
    script_path = os.path.join(SCRIPTS_DIR, 'yt-thumbnail')
    yt_thumb = types.ModuleType('yt_thumbnail')
    yt_thumb.__file__ = script_path
    with open(script_path) as _f:
        exec(compile(_f.read(), script_path, 'exec'), yt_thumb.__dict__)

    # If no random preset was set, apply fallback
    if not random_presets_str:
        _apply_saved_preset('fallback', config, yt_thumb)

    # Compose thumbnail with title text
    thumb_path = f'/tmp/yt_enrich_{int(time.time())}.jpg'
    yt_thumb.compose_thumbnail(bg, title[:70], '', thumb_path)
    return thumb_path


def enrich_live_with_ai(video_id, data_live, duracao_min, transcript_text, config):
    """Use AI to generate title and description based on the live's transcript."""
    prompt_file = os.path.join(CONFIG_DIR, 'prompt_enrich.txt')
    if not os.path.exists(prompt_file):
        log(f'  prompt_enrich.txt not found, skipping AI enrichment')
        return None, None

    with open(prompt_file) as f:
        system_prompt = f.read().strip()
    if not system_prompt:
        return None, None

    user_msg = (
        f'Video ID: {video_id}\n'
        f'Live date: {data_live}\n'
        f'Duration: {duracao_min} minutes\n\n'
        f'=== LIVE TRANSCRIPT ===\n{transcript_text}'
    )
    full_prompt = f'{system_prompt}\n\n---\n\n{user_msg}'

    try:
        log(f'  Generating title/description with AI for {video_id}...')
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)
        result = subprocess.run(
            ['claude', '-p', '--output-format', 'json', full_prompt],
            capture_output=True, text=True, timeout=180, env=env
        )
        if result.returncode != 0:
            log(f'  Claude CLI error (code {result.returncode}): {result.stderr[:200]}')
            return None, None

        data = json.loads(result.stdout)
        content = data.get('result', '')

        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            refined = json.loads(json_match.group())
            new_title = refined.get('title', '')
            new_desc = refined.get('description', '')
            if new_title:
                log(f'  Generated title: {new_title[:60]}')
                return new_title, new_desc
        log(f'  AI did not return valid JSON')
        return None, None
    except Exception as e:
        log(f'  Error generating with AI: {e}')
        return None, None


def _enrich_single_live(vid, live, config):
    """Enrich a single live: read transcript → AI title/desc → thumbnail → YouTube update.
    Returns True on success, False on error."""
    data_live = live.get('data_live', '')
    duracao = live.get('duracao_min', '0')

    # Read transcript
    condensed_file = os.path.join(LIVES_DIR, vid, 'condensed.txt')
    if not os.path.exists(condensed_file):
        log(f'  Enrich: transcript not found for {vid}')
        return False

    with open(condensed_file) as f:
        transcript = f.read()
    if not transcript:
        log(f'  Enrich: empty transcript for {vid}')
        return False

    # Generate title + description with AI
    update_status('enriching', f'Generating title/description: {vid}', vid, step='analysis')
    new_title, new_desc = enrich_live_with_ai(vid, data_live, duracao, transcript, config)
    if not new_title:
        log(f'  Enrich: AI did not generate title for {vid}')
        return False

    # Update on YouTube (title + description)
    try:
        update_video_metadata(vid, new_title, new_desc or '')
    except Exception as yt_err:
        log(f'  Enrich: error updating YouTube for {vid}: {yt_err}')
        # Still update local DB even if YouTube fails (e.g. wrong channel)

    # Generate and upload thumbnail
    try:
        update_status('enriching', f'Thumbnail: {vid}', vid, step='thumbnail')
        thumb_path = generate_enrich_thumbnail(new_title, config)
        if thumb_path and os.path.exists(thumb_path):
            thumbs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lives', 'thumbs')
            os.makedirs(thumbs_dir, exist_ok=True)
            shutil.copy2(thumb_path, os.path.join(thumbs_dir, f'{vid}.jpg'))
            try:
                upload_thumbnail(vid, thumb_path)
            except Exception as upload_err:
                log(f'  Enrich: thumbnail upload failed (non-fatal): {upload_err}')
            try:
                os.remove(thumb_path)
            except OSError:
                pass
    except Exception as thumb_err:
        log(f'  Enrich: thumbnail error for {vid} (non-fatal): {thumb_err}')

    # Update local database
    db.update_live(vid, titulo=new_title, observacoes='enriquecida')
    log(f'  Enrich OK: {vid} -> {new_title[:60]}')
    return True


def process_enrich(config):
    """Manual enrich: if no cuts exist, run cuts first, then enrich."""
    max_por_vez = int(config.get('enrich_max_por_vez', '3'))
    lives = get_pending_lives()

    # Filter: lives with generic title OR marked for re-enrichment
    genericas = [
        l for l in lives
        if not (l.get('video_id', '') or '').startswith('import_')
        and (
            (l.get('titulo', '').strip().upper() == 'INEMA' and l.get('observacoes', '') != 'enriquecida')
            or l.get('observacoes', '') == 'refazer_enrich'
        )
    ]

    if not genericas:
        log('  No lives to enrich')
        return {'enriched': 0, 'errors': 0}

    log(f'  {len(genericas)} lives to enrich, processing up to {max_por_vez}')
    update_status('enriching', f'Enriching {min(len(genericas), max_por_vez)} lives...')

    enriched = 0
    errors = 0

    for live in genericas[:max_por_vez]:
        vid = live.get('video_id', '')

        try:
            # If no transcript yet, run full cut process first
            condensed_file = os.path.join(LIVES_DIR, vid, 'condensed.txt')
            if not os.path.exists(condensed_file):
                log(f'  No transcript for {vid}, running cut first...')
                update_status('enriching', f'Cutting: {vid}', vid, step='cut')
                success = run_corte(vid, config)
                if success:
                    job_dir = os.path.join(LIVES_DIR, vid)
                    topics_file = os.path.join(job_dir, 'topics.json')
                    clips_dir = os.path.join(job_dir, 'clips')
                    qtd_clips = 0
                    if os.path.exists(topics_file):
                        with open(topics_file) as f:
                            topics = json.load(f)
                        qtd_clips = len(topics.get('topics', []))
                    has_clips = os.path.isdir(clips_dir) and len(os.listdir(clips_dir)) > 0
                    update_live_status(vid, 'status_transcricao', 'transcricao', {
                        'status_cortes': 'concluido' if has_clips else 'pendente',
                        'qtd_clips': qtd_clips,
                        'data_corte': datetime.now().strftime('%Y-%m-%d %H:%M')
                    })
                else:
                    update_live_status(vid, 'status_cortes', 'erro')
                    log(f'  Cut failed for {vid}, skipping enrich')
                    errors += 1
                    continue

            # Now enrich using the transcript
            if _enrich_single_live(vid, live, config):
                enriched += 1
            else:
                errors += 1

        except Exception as e:
            log(f'  Error enriching {vid}: {e}')
            errors += 1

    update_status('idle', f'Enrich completed: {enriched} OK, {errors} errors')
    return {'enriched': enriched, 'errors': errors}


def update_live_status(video_id, status_field, new_status, extra=None):
    """Updates the status of a live in the local database."""
    fields = {status_field: new_status}
    if extra:
        fields.update(extra)
    db.update_live(video_id, **fields)


def _check_disk_space(config):
    threshold_gb = float(config.get('disk_min_gb', '5'))
    try:
        check_path = LIVES_DIR if os.path.exists(LIVES_DIR) else PROJECT_ROOT
        free_gb = round(shutil.disk_usage(check_path).free / (1024 ** 3), 1)
        if free_gb < threshold_gb:
            log(f'  DISK WARNING: {free_gb} GB free (minimum: {threshold_gb} GB)')
            return False, free_gb
        return True, free_gb
    except Exception as e:
        log(f'  Disk check error (non-fatal): {e}')
        return True, -1.0


def process_cortes(config):
    """Processes cuts for pending lives."""
    max_per_run = int(config.get('corte_max_por_dia', '3'))
    lives = get_pending_lives()

    pendentes = [l for l in lives if l.get('status_cortes') not in ('concluido', 'erro')]
    if not pendentes:
        log('  No pending lives to cut')
        return

    log(f'  {len(pendentes)} pending lives, processing up to {max_per_run}')

    for live in pendentes[:max_per_run]:
        vid = live.get('video_id', '')
        if not vid:
            continue

        success = run_corte(vid, config)
        if success:
            job_dir = os.path.join(LIVES_DIR, vid)
            topics_file = os.path.join(job_dir, 'topics.json')
            clips_dir = os.path.join(job_dir, 'clips')

            qtd_clips = 0
            if os.path.exists(topics_file):
                with open(topics_file) as f:
                    topics = json.load(f)
                qtd_clips = len(topics.get('topics', []))

            has_clips = os.path.isdir(clips_dir) and len(os.listdir(clips_dir)) > 0

            update_live_status(vid, 'status_transcricao', 'transcricao', {
                'status_cortes': 'concluido' if has_clips else 'pendente',
                'qtd_clips': qtd_clips,
                'data_corte': datetime.now().strftime('%Y-%m-%d %H:%M')
            })

            # If enrich is enabled, enrich after cutting
            enrich_auto = config.get('enrich_auto', 'false') == 'true'
            enrich_paused = config.get('pipeline_enrich_paused', 'false') == 'true'
            needs_enrich = (
                live.get('titulo', '').strip().upper() == 'INEMA'
                and live.get('observacoes', '') != 'enriquecida'
            )
            if enrich_auto and not enrich_paused and needs_enrich:
                log(f'  Auto enrich: enriching {vid} after cut...')
                _enrich_single_live(vid, live, config)
        else:
            update_live_status(vid, 'status_cortes', 'erro')


_pub_lock = threading.Lock()

_import_pub_lock = threading.Lock()

def process_publicacao(config):
    """Publishes clips from lives (excludes imports)."""
    if not _pub_lock.acquire(blocking=False):
        log('  Publication already in progress, skipping')
        return
    try:
        _process_publicacao_inner(config)
    finally:
        _pub_lock.release()

_tiktok_pub_lock = threading.Lock()

def process_publicacao_imports(config):
    """Publishes clips from normal imports (not TikTok)."""
    if not _import_pub_lock.acquire(blocking=False):
        log('  Import publication already in progress, skipping')
        return
    try:
        privacy = config.get('import_privacy', '') or config.get('privacy_padrao', 'unlisted')
        max_por_vez = int(config.get('import_pub_max_por_vez', '1') or '1')
        all_imports = [l for l in get_pending_lives() if l.get('video_id', '').startswith('import_')]
        lives = [l for l in all_imports if not (l.get('titulo', '') or '').startswith('TikTok @')]
        _publish_import_list('import', lives, max_por_vez, privacy, config)
    finally:
        _import_pub_lock.release()

def process_publicacao_tiktok(config):
    """Publishes TikTok clips."""
    if not _tiktok_pub_lock.acquire(blocking=False):
        log('  TikTok publication already in progress, skipping')
        return
    try:
        privacy = config.get('tiktok_privacy', '') or config.get('privacy_padrao', 'unlisted')
        max_por_vez = int(config.get('tiktok_pub_max_por_vez', '1') or '1')
        all_imports = [l for l in get_pending_lives() if l.get('video_id', '').startswith('import_')]
        lives = [l for l in all_imports if (l.get('titulo', '') or '').startswith('TikTok @')]
        _publish_import_list('tiktok', lives, max_por_vez, privacy, config)
    finally:
        _tiktok_pub_lock.release()

def _publish_import_list(label, lives, max_por_vez, privacy, config):
    found_any = False
    global_count = 0
    for live in lives:
        if global_count >= max_por_vez:
            break
        vid = live.get('video_id', '')
        qtd_clips = int(live.get('qtd_clips', '0') or '0')
        publicados_count = int(live.get('clips_publicados', '0') or '0')

        if live.get('status_cortes') != 'concluido' or not vid:
            continue
        if publicados_count >= qtd_clips or qtd_clips == 0:
            continue

        obs = live.get('observacoes', '')
        import re as _re
        pa_match = _re.search(r'publish_at=(\d{2}:\d{2})', obs)
        if pa_match:
            publish_at = pa_match.group(1)
            now_hm = datetime.now().strftime('%H:%M')
            if now_hm < publish_at:
                log(f'  [{label}] {vid}: waiting for publish_at={publish_at} (now={now_hm}), skipping')
                continue

        found_any = True
        log(f'  [{label}] {vid}: {qtd_clips} clips, {publicados_count} publicados')

        job_dir = os.path.join(LIVES_DIR, vid)
        manifest_file = os.path.join(job_dir, 'clips_manifest.json')
        if not os.path.exists(manifest_file):
            log(f'  No manifest for {vid}, skipping')
            continue

        with open(manifest_file) as f:
            clips = json.load(f)

        pub_records = db.get_publicados(live_video_id=vid)
        published_ids = set()
        pub_ok = 0
        pub_erro = 0
        for p in pub_records:
            vid_status = p.get('clip_video_id', '')
            cid = p.get('filename', '')
            if vid_status in ('erro_upload', 'publicando', ''):
                pub_erro += 1
            else:
                pub_ok += 1
            if cid:
                published_ids.add(cid)

        count = 0
        for clip in clips:
            if global_count >= max_por_vez:
                break

            clip_id = f'{vid}_{clip.get("index", 0)}'
            if clip_id in published_ids:
                continue
            if clip.get('paused', False):
                continue
            if not os.path.exists(clip['file']):
                log(f'  File not found: {clip["file"]}')
                continue

            clip_title = clip['title']
            clip_desc = clip.get('description', '') or clip.get('title', '')
            clip_privacy = clip.get('privacy', privacy)

            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            lock_row_id = db.add_publicado({
                'clip_video_id': 'publicando',
                'clip_titulo': clip_title,
                'clip_url': '',
                'live_video_id': vid,
                'live_titulo': live.get('titulo', ''),
                'data_publicacao': now,
                'privacy': clip_privacy,
                'duracao': str(clip.get('duration', '')),
                'tags': ','.join(clip.get('tags', [])) if isinstance(clip.get('tags'), list) else clip.get('tags', ''),
                'categoria': '27',
                'filename': clip_id
            })

            update_status('publishing', f'[{label}] Uploading: {clip_title[:50]}', vid, step='upload', clip_title=clip_title[:50])
            new_vid = run_publicacao(vid, clip['file'], clip_title, clip_desc,
                                     ','.join(clip.get('tags', [])) if isinstance(clip.get('tags'), list) else clip.get('tags', ''),
                                     clip_privacy)

            if new_vid:
                update_status('publishing', f'[{label}] Thumbnail...', vid, step='thumbnail', clip_id=new_vid)
                handle_thumbnail(new_vid, clip_title, clip_desc, config)
                db.update_publicado(lock_row_id,
                    clip_video_id=new_vid,
                    clip_titulo=clip_title,
                    clip_url=f'https://www.youtube.com/watch?v={new_vid}'
                )
                count += 1
                global_count += 1
                pub_ok += 1
                published_ids.add(clip_id)
                log(f'  [{label}] Published: {clip_title[:50]} -> {new_vid}')
            else:
                db.update_publicado(lock_row_id, clip_video_id='erro_upload')
                count += 1
                global_count += 1
                pub_erro += 1
                published_ids.add(clip_id)
                log(f'  [{label}] Failed: {clip_title[:50]}')

        if pub_ok != publicados_count:
            pend = max(0, qtd_clips - pub_ok)
            update_live_status(vid, 'clips_publicados', str(pub_ok), {'clips_pendentes': str(pend)})

    if not found_any:
        log(f'  [{label}] No pending clips to publish')

def _process_publicacao_inner(config):
    privacy = config.get('privacy_padrao', 'unlisted')
    max_por_vez = int(config.get('pub_max_por_vez', '2') or '2')
    log(f'  Looking for lives to publish (privacy={privacy}, max={max_por_vez})...')
    lives = get_pending_lives()
    log(f'  {len(lives)} lives found')

    # Find lives with clips but not all published
    found_any = False
    for live in lives:
        vid = live.get('video_id', '')
        if live.get('status_cortes') != 'concluido' or not vid:
            continue

        qtd_clips = int(live.get('qtd_clips', '0') or '0')
        publicados_count = int(live.get('clips_publicados', '0') or '0')

        if publicados_count >= qtd_clips or qtd_clips == 0:
            continue

        # Imports have their own queue — exclude here
        if vid.startswith('import_'):
            continue

        found_any = True
        log(f'  Live {vid}: {qtd_clips} clips, {publicados_count} published')

        job_dir = os.path.join(LIVES_DIR, vid)
        manifest_file = os.path.join(job_dir, 'clips_manifest.json')

        if not os.path.exists(manifest_file):
            log(f'  No manifest for {vid}, skipping')
            continue

        with open(manifest_file) as f:
            clips = json.load(f)

        # Build set of clip_ids already in the DB (by filename field)
        pub_records = db.get_publicados(live_video_id=vid)
        published_ids = set()
        pub_ok = 0
        pub_erro = 0
        for p in pub_records:
            vid_status = p.get('clip_video_id', '')
            cid = p.get('filename', '')
            if vid_status in ('erro_upload', 'publicando', ''):
                pub_erro += 1
            else:
                pub_ok += 1
            if cid:
                published_ids.add(cid)

        count = 0
        log(f'  {len(clips)} clips in manifest, {pub_ok} published OK, {pub_erro} with error')
        for clip in clips:
            if count >= max_por_vez:
                log(f'  Limit of {max_por_vez} clips per run reached')
                break

            # Unique clip_id: live_video_id + index
            clip_id = f'{vid}_{clip.get("index", 0)}'

            # Skip if already published or in error
            if clip_id in published_ids:
                continue

            if clip.get('paused', False):
                log(f'  Pausado: {clip["title"][:50]}')
                continue

            if not os.path.exists(clip['file']):
                log(f'  File not found: {clip["file"]}')
                continue

            clip_title = clip['title']
            clip_desc = clip.get('description', '') or clip.get('title', '')

            # DB lock: mark as "publicando" BEFORE starting
            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            lock_row_id = db.add_publicado({
                'clip_video_id': 'publicando',
                'clip_titulo': clip_title,
                'clip_url': '',
                'live_video_id': vid,
                'live_titulo': live.get('titulo', ''),
                'data_publicacao': now,
                'privacy': privacy,
                'duracao': str(clip.get('duration', '')),
                'tags': ','.join(clip.get('tags', [])),
                'categoria': '27',
                'filename': clip_id
            })
            log(f'  Reserved in DB: {clip_title[:50]} (id: {clip_id})')

            # Refine title and description with AI
            update_status('publishing', f'Refining with AI: {clip_title[:50]}', vid, step='refine', clip_title=clip_title[:50])
            clip_title, clip_desc = refine_pub_with_ai(clip_title, clip_desc, config, video_id=vid)

            # Append original live link (if enabled in config)
            if config.get('pub_link_live', 'true') == 'true':
                clip_desc += f'\n\nLive original: https://www.youtube.com/watch?v={vid}'

            update_status('publishing', f'Uploading: {clip_title[:50]}', vid, step='upload', clip_title=clip_title[:50])
            new_vid = run_publicacao(
                vid, clip['file'], clip_title,
                clip_desc, ','.join(clip.get('tags', [])),
                privacy
            )

            if new_vid:
                update_status('publishing', f'Generating thumbnail...', vid, step='thumbnail', clip_id=new_vid, clip_title=clip_title[:50])
                handle_thumbnail(
                    new_vid, clip_title,
                    clip.get('description', ''), config
                )

                db.update_publicado(lock_row_id,
                    clip_video_id=new_vid,
                    clip_titulo=clip_title,
                    clip_url=f'https://www.youtube.com/watch?v={new_vid}'
                )

                count += 1
                pub_ok += 1
                published_ids.add(clip_id)
                log(f'  Published: {clip_title[:50]} -> {new_vid}')
            else:
                db.update_publicado(lock_row_id, clip_video_id='erro_upload')
                count += 1
                pub_erro += 1
                published_ids.add(clip_id)
                log(f'  Failed to publish: {clip_title[:50]}')

        # Update counter
        new_total = pub_ok
        if new_total != publicados_count:
            pend = max(0, qtd_clips - new_total)
            update_live_status(vid, 'clips_publicados', str(new_total), {'clips_pendentes': str(pend)})
            log(f'  Updated clips_publicados: {publicados_count} -> {new_total} for {vid}')

        if count > 0:
            log(f'  {count} attempt(s) for {vid}')
            update_status('idle', f'Publication completed for {vid}')
            break

    if not found_any:
        log('  No pending clips to publish')
        update_status('idle', 'No clips to publish')


def main():
    log('Scheduler started')
    log(f'  Scripts: {SCRIPTS_DIR}')
    log(f'  Lives: {LIVES_DIR}')
    log(f'  Config: {CONFIG_DIR}')
    update_status('idle', 'Scheduler started')

    config = None
    corte_running = threading.Event()
    try:
        config = load_config()
    except Exception as e:
        log(f'ERROR loading config: {e}')

    # Track which scheduled time has already been executed (avoids repeating)
    startup_corte = get_matching_schedule(config.get('corte_horarios', '')) if config else None
    startup_pub = get_matching_schedule(config.get('pub_horarios', '')) if config else None
    startup_import_pub = get_matching_schedule(config.get('import_pub_horarios', '')) if config else None
    last_executed = {'cortes': startup_corte, 'pub': startup_pub, 'import_pub': startup_import_pub}
    log(f'  Schedule: cuts={startup_corte or "none now"}, pub={startup_pub or "none now"}, import_pub={startup_import_pub or "none now"}')

    def run_cortes_thread(cfg):
        """Runs cuts in a separate thread so it does not block publication."""
        try:
            corte_running.set()
            process_cortes(cfg)
        except Exception as e:
            log(f'ERROR in cut (thread): {e}')
        finally:
            corte_running.clear()

    while True:
        try:
            config = load_config()

            # --- Cuts (runs in a separate thread) ---
            cortes_paused = config.get('pipeline_cortes_paused', 'false') == 'true'
            corte_auto = config.get('corte_auto', 'true') == 'true'
            corte_horarios = config.get('corte_horarios', '')
            corte_match = get_matching_schedule(corte_horarios)

            if not cortes_paused and corte_auto and corte_match:
                if last_executed['cortes'] != corte_match:
                    if not corte_running.is_set():
                        disk_ok, disk_free = _check_disk_space(config)
                        if not disk_ok:
                            log(f'  Scheduled cut ({corte_match}) blocked: disk full ({disk_free} GB free)')
                        else:
                            last_executed['cortes'] = corte_match
                            log(f'==> Time to cut! (scheduled: {corte_match})')
                            threading.Thread(target=run_cortes_thread, args=(config,), daemon=True).start()
                    else:
                        log(f'==> Scheduled cut ({corte_match}) but another cut is still running, skipping')

            if not corte_match and last_executed['cortes']:
                last_executed['cortes'] = None

            # --- Publication (runs in main loop, non-blocking) ---
            pub_paused = config.get('pipeline_pub_paused', 'false') == 'true'
            pub_horarios = config.get('pub_horarios', '')
            pub_match = get_matching_schedule(pub_horarios)

            if not pub_paused and pub_match:
                if last_executed['pub'] != pub_match:
                    last_executed['pub'] = pub_match
                    log(f'==> Time to publish! (scheduled: {pub_match})')
                    process_publicacao(config)

            if not pub_match and last_executed['pub']:
                last_executed['pub'] = None

            # --- Import publication (own queue via import_pub_horarios) ---
            import_pub_horarios = config.get('import_pub_horarios', '')
            import_pub_paused = config.get('pipeline_imports_paused', 'false') == 'true'
            import_pub_match = get_matching_schedule(import_pub_horarios) if import_pub_horarios else None

            if not import_pub_paused and import_pub_match:
                if last_executed['import_pub'] != import_pub_match:
                    last_executed['import_pub'] = import_pub_match
                    log(f'==> Time to publish imports! (scheduled: {import_pub_match})')
                    process_publicacao_imports(config)

            if not import_pub_match and last_executed.get('import_pub'):
                last_executed['import_pub'] = None

            # --- TikTok publication (own queue via tiktok_pub_horarios) ---
            tiktok_pub_horarios = config.get('tiktok_pub_horarios', '')
            tiktok_pub_paused = config.get('pipeline_tiktok_paused', 'false') == 'true'
            tiktok_pub_match = get_matching_schedule(tiktok_pub_horarios) if tiktok_pub_horarios else None

            if not tiktok_pub_paused and tiktok_pub_match:
                if last_executed.get('tiktok_pub') != tiktok_pub_match:
                    last_executed['tiktok_pub'] = tiktok_pub_match
                    log(f'==> Time to publish TikTok! (scheduled: {tiktok_pub_match})')
                    process_publicacao_tiktok(config)

            if not tiktok_pub_match and last_executed.get('tiktok_pub'):
                last_executed['tiktok_pub'] = None

            # --- Enrich lives (generic title INEMA → title + desc + thumb) ---
            enrich_paused = config.get('pipeline_enrich_paused', 'false') == 'true'
            enrich_auto = config.get('enrich_auto', 'false') == 'true'
            enrich_horarios = config.get('enrich_horarios', '')
            enrich_match = get_matching_schedule(enrich_horarios)

            if not enrich_paused and enrich_auto and enrich_match:
                if last_executed.get('enrich') != enrich_match:
                    last_executed['enrich'] = enrich_match
                    log(f'==> Time to enrich lives! (scheduled: {enrich_match})')
                    process_enrich(config)

            if not enrich_match and last_executed.get('enrich'):
                last_executed['enrich'] = None

            # --- TikTok Scanner ---
            tiktok_paused = config.get('pipeline_tiktok_paused', 'false') == 'true'
            tiktok_auto = config.get('tiktok_auto', 'false') == 'true'
            tiktok_horarios = config.get('tiktok_horarios', '')
            tiktok_match = get_matching_schedule(tiktok_horarios)

            if not tiktok_paused and tiktok_auto and tiktok_match:
                if last_executed.get('tiktok') != tiktok_match:
                    last_executed['tiktok'] = tiktok_match
                    log(f'==> TikTok download from queue! (scheduled: {tiktok_match})')
                    try:
                        import tiktok_scanner
                        results = tiktok_scanner.download_pending_videos(config)
                        total = sum(r.get('downloaded', 0) for r in results)
                        if total:
                            log(f'==> TikTok: {total} video(s) downloaded')
                    except Exception as e:
                        log(f'ERROR in tiktok_scanner: {e}')

            if not tiktok_match and last_executed.get('tiktok'):
                last_executed['tiktok'] = None

            # --- Import worker (checks every hour or if import_auto=true) ---
            now_hm = datetime.now().strftime('%H:%M')
            import_auto = config.get('import_auto', 'false') == 'true'
            if import_auto:
                now_h = datetime.now().strftime('%H')
                if last_executed.get('import') != now_h:
                    last_executed['import'] = now_h
                    try:
                        import import_worker
                        results = import_worker.process_imports(config)
                        novos = [r for r in results if r.get('ok')]
                        if novos:
                            log(f'==> Import: {len(novos)} batch(es) imported for publication')
                    except Exception as e:
                        log(f'ERROR in import_worker: {e}')

            # --- Auto-sync (midnight) ---
            sync_auto = config.get('sync_auto', 'false') == 'true'
            if sync_auto and now_hm == '00:00':
                if last_executed.get('sync') != '00:00':
                    last_executed['sync'] = '00:00'
                    log('==> Auto-sync: syncing lives from source channel...')
                    update_status('syncing', 'Auto-sync in progress...')
                    # Derive dashboard port from INSTANCE_NAME (yt-pub-livesN -> 8090+N)
                    instance_name = os.environ.get('INSTANCE_NAME', '')
                    _inst_num = ''.join(c for c in instance_name if c.isdigit())
                    dashboard_port = str(8090 + int(_inst_num)) if _inst_num else config.get('dashboard_port', '8091')
                    sync_payload = {'mode': 'novas', 'max_lives': 1000}
                    sync_date_from = config.get('sync_auto_date_from', '').strip()
                    if sync_date_from:
                        sync_payload['date_from'] = sync_date_from
                        log(f'  Auto-sync com filtro date_from={sync_date_from}')
                    payload = json.dumps(sync_payload).encode()
                    sync_ok = False
                    for attempt in range(1, 4):
                        try:
                            req = urllib.request.Request(
                                f'http://localhost:{dashboard_port}/api/sync',
                                data=payload
                            )
                            req.add_header('Content-Type', 'application/json')
                            resp = urllib.request.urlopen(req, timeout=120)
                            result = json.loads(resp.read())
                            novas = result.get('novas_lives', 0)
                            log(f'  Auto-sync completed: {novas} new lives')
                            update_status('idle', f'Auto-sync OK: {novas} new lives')
                            sync_ok = True
                            break
                        except Exception as e:
                            log(f'  ERROR in auto-sync (attempt {attempt}/3): {e}')
                            if attempt < 3:
                                time.sleep(30)
                    if not sync_ok:
                        log('  Auto-sync failed after 3 attempts')
                        update_status('error', 'Auto-sync failed after 3 attempts (connection refused)')
            if now_hm != '00:00' and last_executed.get('sync'):
                last_executed['sync'] = None

        except Exception as e:
            log(f'ERROR: {e}')

        # Check every 60 seconds
        time.sleep(60)


def acquire_lock():
    """Ensures only 1 scheduler instance runs per project.

    Opens with 'r+' (no truncate) to avoid clearing the PID before validating the flock.
    Only truncates and writes the PID AFTER acquiring the flock. Removes the previous
    "stale detected" flow that had a race condition causing multiple instances.
    """
    import fcntl
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.scheduler.lock')
    # Open (or create) without truncating
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    lock_file = os.fdopen(fd, 'r+')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another process holds the flock. Read PID (without modifying file) and report.
        lock_file.seek(0)
        raw = lock_file.read().strip()
        try:
            old_pid = int(raw)
            os.kill(old_pid, 0)
            print(f'[ERROR] Another scheduler is already running (PID {old_pid}, lock: {lock_path}). Exiting.', file=sys.stderr)
        except (ValueError, ProcessLookupError, OSError):
            print(f'[ERROR] flock held but PID is empty/dead (inconsistent lock). '
                  f'Remove {lock_path} manually and restart.', file=sys.stderr)
        lock_file.close()
        sys.exit(1)
    # flock OK: now safe to truncate and write the PID
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


if __name__ == '__main__':
    _lock = acquire_lock()
    main()
