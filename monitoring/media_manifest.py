"""Validate and compile the event's two-root image / video-frame folder tree.

This module is identical for DEMO and PRODUCTION. A video folder is atomic:
its frames are encoded into one native video, never assigned separately.
"""
from collections import defaultdict
from hashlib import sha256
from pathlib import PurePosixPath, Path
import json
import re
import shutil
import subprocess
import tempfile

from .service import WorkflowError

IMAGE_EXT = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'})
MAX_FILES = 100000
MAX_VIDEOS = 2000
MAX_FRAMES_PER_VIDEO = 20000


def _natural(name):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r'(\d+)', name)]


def validate_tree(relative_paths, require_both=True):
    """Return canonical image paths and video sequences. No remote mutations."""
    if not isinstance(relative_paths, list) or not relative_paths:
        raise WorkflowError('Select the dataset folder containing images/ and videos/.')
    if len(relative_paths) > MAX_FILES:
        raise WorkflowError('The selected dataset contains too many files.')
    parsed = []
    for candidate in relative_paths:
        if not isinstance(candidate, str) or not candidate or '\x00' in candidate:
            raise WorkflowError('Every dataset file needs a valid relative path.')
        raw = candidate.replace('\\', '/')
        parts = PurePosixPath(raw).parts
        if (raw.startswith('/') or raw.endswith('/') or
                any(p in ('', '.', '..') for p in raw.split('/')) or
                not parts or ':' in parts[0]):
            raise WorkflowError('Dataset paths must stay inside the chosen folder.')
        parsed.append(parts)
    # Browser folder selection includes the chosen root folder in every file.
    # Support direct images/ and videos/ paths as well.
    if parsed and all(p[0].casefold() not in ('images', 'videos') for p in parsed):
        selected = {p[0].casefold() for p in parsed}
        if len(selected) != 1:
            raise WorkflowError('Select exactly one dataset root folder.')
        parsed = [p[1:] for p in parsed]
    images = []
    videos = defaultdict(list)
    normalized = set()
    roots = set()
    for parts in parsed:
        if not parts or parts[0].casefold() not in ('images', 'videos'):
            raise WorkflowError('Dataset must contain only images/ and videos/ roots.')
        root = parts[0].casefold()
        roots.add(root)
        if (root == 'images' and len(parts) != 3) or (
                root == 'videos' and len(parts) != 4):
            raise WorkflowError(
                'Use images/<category>/<image> and '
                'videos/<category>/<video-title>/<frame> folder layouts.'
            )
        if PurePosixPath(parts[-1]).suffix.casefold() not in IMAGE_EXT:
            raise WorkflowError('Only image files are accepted; video folders contain frames.')
        path = '/'.join((root, *parts[1:]))
        folded = path.casefold()
        if folded in normalized:
            raise WorkflowError('Dataset contains duplicated file paths.')
        normalized.add(folded)
        if root == 'images':
            images.append({'path': path, 'category': parts[1],
                           'filename': parts[2]})
        else:
            videos[(parts[1], parts[2])].append(path)
    if require_both and roots != {'images', 'videos'}:
        raise WorkflowError('Dataset must contain both images/ and videos/ folders.')
    if len(videos) > MAX_VIDEOS:
        raise WorkflowError('Too many source videos.')
    sequences = []
    for (category, title), frames in sorted(
            videos.items(), key=lambda pair: (pair[0][0].casefold(), pair[0][1].casefold())):
        if not 2 <= len(frames) <= MAX_FRAMES_PER_VIDEO:
            raise WorkflowError(f'Video {title} must have 2 to {MAX_FRAMES_PER_VIDEO} frames.')
        frames.sort(key=lambda p: _natural(PurePosixPath(p).stem))
        keys = [PurePosixPath(p).stem for p in frames]
        if len(set(k.casefold() for k in keys)) != len(keys):
            raise WorkflowError(f'Video {title} has ambiguous frame names.')
        sequences.append({
            'category': category, 'title': title, 'frames': frames,
            'frame_count': len(frames),
            'source_folder': f'videos/{category}/{title}',
        })
    images.sort(key=lambda p: _natural(p['path']))
    signature = sha256(json.dumps(
        {'images': [r['path'] for r in images], 'videos': sequences},
        sort_keys=True, ensure_ascii=False
    ).encode()).hexdigest()
    return {'images': images, 'videos': sequences,
            'image_count': len(images), 'video_count': len(sequences),
            'frame_count': sum(r['frame_count'] for r in sequences),
            'category_count': len({
                (r['category'] if isinstance(r, dict) else r)
                for r in images + sequences
            }), 'hash': signature}


def encode_video(frame_paths, destination, fps=12):
    """Encode sorted source frames into one video suitable for native annotation.

    This requires ffmpeg and ffprobe in the environment. The original relative
    frame-path manifest is retained independently by the caller.
    """
    try:
        fps = int(fps)
    except (ValueError, TypeError):
        raise WorkflowError('Set video FPS to an integer between 1 and 60.') from None
    if not 1 <= fps <= 60:
        raise WorkflowError('Set video FPS to an integer between 1 and 60.')
    ffmpeg, probe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not probe:
        raise WorkflowError(
            'ffmpeg and ffprobe must be installed before frame-folder video import.'
        )
    if len(frame_paths) < 2 or not all(Path(p).is_file() for p in frame_paths):
        raise WorkflowError('Video frames are missing or incomplete.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nightjar-frames-') as temp:
        concat = Path(temp) / 'frames.txt'
        def quote(path):
            return str(Path(path).resolve()).replace("'", "'\\''")
        concat.write_text(''.join(f"file '{quote(p)}'\n" for p in frame_paths),
                          encoding='utf-8')
        cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-y', '-f', 'concat', '-safe', '0', '-i', str(concat),
               '-vf', f'setpts=N/({fps}*TB),scale=trunc(iw/2)*2:trunc(ih/2)*2',
               '-fps_mode', 'passthrough', '-c:v', 'libx264',
               '-crf', '18', '-pix_fmt', 'yuv420p', str(destination)]
        try:
            completed = subprocess.run(cmd, check=False, timeout=1800,
                                       capture_output=True, text=True)
        except (OSError, subprocess.TimeoutExpired):
            raise WorkflowError('Video frame encoding failed or timed out.') from None
        if completed.returncode != 0 or not destination.exists():
            raise WorkflowError(
                'Could not encode a frame folder. Check frame dimensions and formats.'
            )
        check = subprocess.run(
            [probe, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
             '-show_entries', 'stream=nb_read_frames', '-of',
             'default=noprint_wrappers=1:nokey=1', str(destination)],
            check=False, capture_output=True, text=True, timeout=120,
        )
        if (check.returncode != 0 or
                check.stdout.strip() != str(len(frame_paths))):
            destination.unlink(missing_ok=True)
            raise WorkflowError(
                'Encoded video frame count differs from the original folder. '
                'No remote upload was attempted.'
            )
    return destination
