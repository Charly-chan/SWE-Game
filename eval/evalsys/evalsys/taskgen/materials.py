
from __future__ import annotations
from pathlib import Path
MODEL_ASSET_SUFFIXES = frozenset({'.7z', '.ase', '.aseprite', '.blend', '.bmp', '.dae', '.exr', '.fbx', '.flac', '.gif', '.glb', '.gltf', '.gz', '.hdr', '.jpeg', '.jpg', '.kra', '.mp3', '.mtl', '.obj', '.ogg', '.otf', '.png', '.psd', '.rar', '.svg', '.tar', '.tga', '.ttf', '.wav', '.webp', '.woff', '.woff2', '.zip'})
MODEL_ASSET_SUPPORT_NAMES = frozenset({'copying', 'copying.md', 'copying.txt', 'credits.md', 'credits.txt', 'license', 'license.md', 'license.txt', 'source_rights.md'})
ARCHIVE_SUFFIXES = frozenset({'.7z', '.gz', '.rar', '.tar', '.zip'})
AUTHORING_SOURCE_SUFFIXES = frozenset({'.ase', '.aseprite', '.kra', '.psd'})
PACK_THUMBNAIL_STEMS = frozenset({'preview', 'sample', 'thumbnail', 'screenshot'})

def is_support_file(path: Path) -> bool:

    name = path.name.lower()
    return name in MODEL_ASSET_SUPPORT_NAMES or name.startswith('license.') or name.endswith(('_license.md', '_license.txt'))

def is_loadable_model_asset(path: Path) -> bool:


    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    if suffix not in MODEL_ASSET_SUFFIXES:
        return False
    if suffix in ARCHIVE_SUFFIXES or suffix in AUTHORING_SOURCE_SUFFIXES:
        return False
    if is_support_file(path):
        return False
    if path.stem.lower() in PACK_THUMBNAIL_STEMS:
        return False
    return True

def o6_denominator(asset_root: Path) -> list[Path]:


    if not asset_root.is_dir():
        return []
    files = [p for p in asset_root.rglob('*') if p.is_file()]
    loadable = sorted((p for p in files if is_loadable_model_asset(p)))
    if loadable:
        return loadable
    return sorted((p for p in files if p.suffix.lower() in ARCHIVE_SUFFIXES))
