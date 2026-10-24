"""Bounded direct package retrieval using the ordinary checked downloader."""
import hashlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

from .download import fetch_artifact

MAX_PACKAGE_BYTES = 64 * 1024 * 1024


def is_link(value):
    return urlsplit(str(value)).scheme in {'http', 'https'}


def retrieve_link(uri, cache, timeout=30):
    parts = urlsplit(uri)
    if not is_link(uri) or parts.username or parts.password:
        raise ValueError('Enter a direct HTTP(S) .rrpkg download URL without embedded credentials.')
    if parts.hostname in {'mega.nz', 'mega.co.nz'} or (parts.hostname in {'github.com', 'www.github.com'} and '/blob/' in parts.path):
        raise ValueError('This is a share/landing page, not a direct package. Download the .rrpkg in your browser and use Import .rrpkg, or obtain a direct download URL.')
    folder = Path(cache) / 'direct-packages'
    folder.mkdir(parents=True, exist_ok=True)
    artifact = {'filename': hashlib.sha256(uri.encode()).hexdigest() + '.rrpkg'}
    try:
        path, _, _ = fetch_artifact(artifact, uri, folder, timeout, max_bytes=MAX_PACKAGE_BYTES)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ValueError('Package download requires authentication or access was denied. Download it in your browser and use Import .rrpkg.') from error
        raise
    # Extraction/manifest validation happens in the common review path. A direct link
    # has no independent expected digest; never present its computed hash as trust.
    return path
