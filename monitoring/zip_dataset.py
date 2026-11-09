"""Safe ZIP ingestion for the same two-root folder layout as direct folder uploads.

Accepted ZIP layout:
  Some Dataset/<species-or-morphology>/<video title>/<frame.ext>
  <species-or-morphology>/<video title>/<frame.ext>
Archives may also start directly with category folders. All accepted frames
are mapped to canonical paths; extraction never trusts archive paths.
"""
import hashlib
import os
import shutil
import stat
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .media_manifest import MAX_FILES, validate_tree
from .service import WorkflowError

MAX_ZIP_BYTES = int(os.getenv('EVENT_MAX_ZIP_GB', '10')) * 1024**3
MAX_UNPACKED_BYTES = int(os.getenv('EVENT_MAX_UNPACKED_GB', '30')) * 1024**3
MAX_SINGLE_FILE_BYTES = int(os.getenv('EVENT_MAX_ENTRY_GB', '2')) * 1024**3
MAX_EXPANSION = 400

def archive_sha256(file_path):
    hash_ = hashlib.sha256()
    with Path(file_path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            hash_.update(block)
    return hash_.hexdigest()

def _metadata(name):
    """Ignore harmless Finder metadata, never actual dataset contents."""
    parts = name.replace('\\', '/').split('/')
    return (parts[0] == '__MACOSX'
            or parts[-1] in ('.DS_Store', 'Thumbs.db')
            or parts[-1].startswith('._'))

def _entries(file_path):
    file_path = Path(file_path)
    if not file_path.is_file() or file_path.stat().st_size > MAX_ZIP_BYTES:
        raise WorkflowError('ZIP archive is missing or exceeds the configured maximum size.')
    try:
        archive = ZipFile(file_path, 'r')
    except (BadZipFile, OSError):
        raise WorkflowError('The selected file is not a readable ZIP archive.') from None
    try:
        members = archive.infolist()
        if len(members) > MAX_FILES + 10000:
            raise WorkflowError('ZIP contains too many entries.')
        files = []
        total = 0
        for entry in members:
            name = entry.filename
            if ('\\' in name or name.startswith('/') or '\x00' in name or
                    len(name) > 1200 or ':' in name.split('/')[0] or
                    any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))):
                raise WorkflowError('ZIP contains unsafe or ambiguous paths.')
            flags = (entry.external_attr >> 16)
            if stat.S_ISLNK(flags):
                raise WorkflowError('ZIP symbolic links are not permitted.')
            if entry.flag_bits & 1:
                raise WorkflowError('Password-protected ZIP entries are not supported.')
            if entry.is_dir():
                continue
            if _metadata(name):
                continue
            if not entry.file_size or entry.file_size > MAX_SINGLE_FILE_BYTES:
                raise WorkflowError('ZIP contains an empty or oversized video frame.')
            if entry.compress_size == 0 or entry.file_size / entry.compress_size > MAX_EXPANSION:
                raise WorkflowError('ZIP contains an excessively compressed entry.')
            total += entry.file_size
            if total > MAX_UNPACKED_BYTES:
                raise WorkflowError('ZIP uncompressed content exceeds the configured limit.')
            files.append(entry)
        if not files:
            raise WorkflowError('ZIP has no video frames.')
        if len(files) > MAX_FILES:
            raise WorkflowError('ZIP contains too many dataset files.')
        manifest = validate_tree([entry.filename for entry in files])
        # A video path is category/video/frame; a named outer folder is optional.
        widths = {len(entry.filename.split('/')) for entry in files}
        if len(widths) != 1 or not widths.issubset({3, 4}):
            raise WorkflowError('ZIP paths must use a consistent category/video/frame hierarchy.')
        wrapped = widths == {4}
        indexed = {}
        folded = set()
        for entry in files:
            segments = entry.filename.split('/')
            canonical = '/'.join(segments[1:] if wrapped else segments)
            if canonical.casefold() in folded:
                raise WorkflowError('ZIP contains case-conflicting or duplicated video frame paths.')
            folded.add(canonical.casefold())
            indexed[canonical] = entry
        return archive, manifest, indexed, total
    except Exception:
        archive.close()
        raise

def inspect_zip(file_path):
    archive, manifest, entries, unpacked = _entries(file_path)
    archive.close()
    return {
        'manifest': manifest,
        'archive_sha256': archive_sha256(file_path),
        'zip_entries': len(entries),
        'unpacked_bytes': unpacked,
    }

def extract_zip(file_path, target_folder, expected_archive_sha256, expected_manifest_hash):
    """Extract into generated filenames, never using archive path as filesystem path."""
    if archive_sha256(file_path) != expected_archive_sha256:
        raise WorkflowError('ZIP contents changed after preview. Inspect it again.')
    archive, manifest, entries, unpacked = _entries(file_path)
    try:
        if manifest['hash'] != expected_manifest_hash:
            raise WorkflowError('ZIP folder structure changed after preview.')
        target = Path(target_folder)
        target.mkdir(parents=True, exist_ok=True)
        mapping = {}
        for index, (canonical, entry) in enumerate(sorted(entries.items())):
            output = target / f'{index:08d}{Path(canonical).suffix.casefold()}'
            size = 0
            try:
                with archive.open(entry, 'r') as src, output.open('xb') as dst:
                    while block := src.read(2 * 1024 * 1024):
                        size += len(block)
                        if size > entry.file_size or size > MAX_SINGLE_FILE_BYTES:
                            raise WorkflowError('ZIP entry exceeded its advertised size.')
                        dst.write(block)
            except (BadZipFile, RuntimeError, OSError) as exc:
                raise WorkflowError(
                    'ZIP entry failed CRC verification or could not be extracted.'
                ) from exc
            if size != entry.file_size:
                raise WorkflowError('ZIP entry is truncated.')
            mapping[canonical] = output
        return manifest, mapping
    finally:
        archive.close()
