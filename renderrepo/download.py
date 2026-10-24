"""Resolve declared/manual sources and download files after explicit consent."""

import hashlib
import json
import http.client
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import URLError, HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, url2pathname


def source_uri(value):
    value = value.strip()
    parts = urlsplit(value)
    if not parts.scheme:
        if not value:
            raise ValueError("source must be a URL or local file path")
        return Path(value).expanduser().resolve().as_uri()
    if parts.scheme in {"http", "https"} and parts.hostname:
        if parts.username is not None or parts.password is not None:
            raise ValueError("URLs with embedded credentials are not supported")
        return value
    if parts.scheme == "file" and parts.netloc in {"", "localhost"} and parts.path.startswith("/"):
        return value
    raise ValueError("source must use http://, https://, file://, or a local file path")


class DownloadRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if urlsplit(newurl).scheme not in {"http", "https"}:
            raise ValueError("HTTP downloads may only redirect to HTTP(S)")
        return super().redirect_request(request, fp, code, message, headers, newurl)


class TransferCancelled(Exception):
    pass


def fetch_artifact(artifact, uri, destination, timeout, control_file=None, rate_limit=5, max_bytes=None):
    """Keep resumable partials; publish only after full size and hash verification."""
    import fcntl
    import re
    key = hashlib.sha256(json.dumps([uri, artifact], sort_keys=True).encode()).hexdigest()
    partial = destination / ('.renderrepo-' + key + '.part')
    lock_path = destination / ('.renderrepo-' + key + '.lock')
    size = 0
    total = artifact.get('size_bytes')
    started = time.monotonic()
    transferred = 0
    last_report = 0
    state = 'connecting'
    rate = rate_limit

    def report(complete=False):
        nonlocal last_report
        elapsed = max(time.monotonic() - started, .001)
        speed = transferred / elapsed
        progress = {'filename': artifact['filename'],
            'bytes': size, 'total_bytes': total, 'complete': complete,
            'state': state, 'bytes_per_second': speed,
            'eta_seconds': (total-size)/speed if total and speed else None}
        if control_file:
            print('RR_TRANSFER ' + json.dumps(progress), flush=True)
        else:
            percent = min(100, size/total*100) if total else None
            bar = '[' + '=' * int((percent or 0)/5) + ' ' * (20-int((percent or 0)/5)) + ']'
            label = f'{percent:.1f}%' if percent is not None else 'size unknown'
            text = f'{bar} {label} {size/1e9:.2f} GB — {speed/1e6:.2f} MB/s — {state}: {artifact["filename"]}'
            print(('\033[36m' + text + '\033[0m') if sys.stdout.isatty() else text, flush=True)
        last_report = time.monotonic()

    def controls():
        nonlocal state, rate
        while True:
            settings = {}
            if control_file:
                try:
                    settings = json.loads(Path(control_file).read_text())
                except (OSError, ValueError):
                    pass
            rate = settings.get('rate_mbps', rate_limit)
            if settings.get('action') == 'cancel':
                raise TransferCancelled('Cancelled; partial downloads retained for the next installation.')
            if settings.get('action') != 'pause':
                state = 'transferring'
                return
            state = 'paused'
            if time.monotonic() - last_report >= 1:
                report()
            time.sleep(.1)

    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('This artifact is already being downloaded by another RR process')
        # A package checksum is mandatory for safe byte-range resumption.
        offset = partial.stat().st_size if partial.exists() and artifact.get('sha256') else 0
        if total is not None and offset > total:
            offset = 0
        size = offset
        controls()
        state = 'connecting'
        report()
        parts = urlsplit(uri)
        stream = None
        if parts.scheme == 'file':
            stream = Path(url2pathname(parts.path)).open('rb')
            total = os.fstat(stream.fileno()).st_size
            stream.seek(offset)
        elif total is None or offset != total:
            headers = {'User-Agent': 'RenderRepo/0.1', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            stream = build_opener(DownloadRedirects()).open(Request(uri, headers=headers), timeout=timeout)
            if stream.headers.get_content_type() == 'text/html':
                stream.close()
                raise ValueError('Server returned an HTML page; supply a direct download URL')
            if stream.status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', stream.headers.get('Content-Range', ''))
                if not offset or not match or int(match[1]) != offset or int(match[2]) != int(match[3])-1:
                    stream.close()
                    raise ValueError('Invalid resume response; partial file retained')
                response_total = int(match[3])
            elif stream.status == 200:
                if offset:
                    print('Server does not support resume; restarting this file.', flush=True)
                offset = 0
                length = stream.headers.get('Content-Length')
                response_total = int(length) if length is not None else None
            else:
                stream.close()
                raise ValueError('Server did not return a complete file or valid byte range')
            if total is not None and response_total is not None and total != response_total:
                stream.close()
                raise ValueError('Remote size differs from package; partial file retained')
            total = total if total is not None else response_total
        if artifact.get('size_bytes') is not None and total != artifact['size_bytes']:
            if stream: stream.close()
            raise ValueError('Source size differs from package')
        if max_bytes is not None and total is not None and total > max_bytes:
            if stream: stream.close()
            raise ValueError('Package archive exceeds the 64 MiB inspection limit; use a smaller package.')
        size = offset
        report()
        try:
            with partial.open('ab' if offset else 'wb') as output:
                if stream:
                    with stream:
                        while True:
                            controls()
                            tick = time.monotonic()
                            chunk = stream.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if max_bytes is not None and size > max_bytes:
                                raise ValueError('Package archive exceeds the 64 MiB inspection limit.')
                            transferred += len(chunk)
                            if total is not None and size > total:
                                raise ValueError('Transfer exceeds expected size')
                            output.write(chunk)
                            output.flush()
                            # Chunk pacing keeps the configured cap responsive to edits.
                            while rate and time.monotonic()-tick < len(chunk)/(rate*1_000_000):
                                controls()
                                if not rate:
                                    break
                                time.sleep(min(.05, max(0, len(chunk)/(rate*1_000_000) - (time.monotonic()-tick))))
                            if time.monotonic()-last_report >= 1:
                                report()
                output.flush()
                os.fsync(output.fileno())
            if total is not None and size != total:
                raise OSError(f'Interrupted transfer: {size} of {total} bytes; partial retained')
            state = 'verifying'
            report()
            digest = hashlib.sha256()
            with partial.open('rb') as data:
                while chunk := data.read(1024*1024):
                    controls()
                    state = 'verifying'
                    digest.update(chunk)
                    if time.monotonic()-last_report >= 1:
                        report()
            actual_hash = digest.hexdigest()
            if artifact.get('sha256') and actual_hash != artifact['sha256'].lower():
                partial.unlink(missing_ok=True)
                raise ValueError('sha256 mismatch; corrupt partial discarded')
            target = destination / artifact['filename']
            os.replace(partial, target)
            state = 'saved'
            report(complete=True)
            return target, size, actual_hash
        except Exception:
            size = partial.stat().st_size if partial.exists() else 0
            state = 'stopped'
            report()
            raise
        finally:
            if stream and not stream.closed:
                stream.close()
            if not artifact.get('sha256'):
                partial.unlink(missing_ok=True)


def artifact_destination(artifact, args):
    if getattr(args, "comfy_targets", None) is not None:
        return args.comfy_targets[artifact["filename"]].parent
    return args.destination.expanduser().resolve()


def install_artifacts(manifest, results, args):
    names = {artifact["filename"] for artifact in manifest["artifacts"]}
    overrides = {}
    for item in args.source or []:
        name, separator, uri = item.partition("=")
        if not separator or name not in names or name in overrides:
            raise ValueError("--source must be a unique declared FILENAME=URL or FILENAME=PATH")
        overrides[name] = source_uri(uri)
    destination = args.destination.expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"destination is not a directory: {destination}")
    if args.timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if not 0 <= getattr(args, 'rate_limit', 5) <= 10000:
        raise ValueError('rate limit must be between 0 and 10000 MB/s')
    plan = []
    unresolved = []
    for artifact, result in zip(manifest["artifacts"], results):
        if result["status"] == "FOUND":
            continue
        name = artifact["filename"]
        sources = ([overrides[name]] if name in overrides else
                   [resolve_source(source["uri"], args) for source in artifact.get("sources", [])])
        if not sources:
            if args.yes:
                unresolved.append(name)
                continue
            try:
                uri = input(f"Source URL or local file path for {name} (blank cancels): ").strip()
            except EOFError:
                uri = ""
            if not uri:
                unresolved.append(name)
                continue
            sources = [source_uri(uri)]
        target = artifact_destination(artifact, args) / name
        if target.is_dir():
            raise ValueError(f"download target is a directory: {target}")
        plan.append((artifact, sources))
    if unresolved:
        for name in unresolved:
            print(f"NO SOURCE: {name}; add sources to the manifest or use --source", file=sys.stderr)
        print("No files downloaded: resolve all missing sources first.")
        return 1
    if not plan:
        print("Artifacts satisfied; nothing to download.")
        return 0
    print(f"Download destination: {destination}")
    known_total = 0
    unknown_count = 0
    for artifact, sources in plan:
        name = artifact["filename"]
        if "size_bytes" in artifact:
            known_total += artifact["size_bytes"]
            size = f"{artifact['size_bytes']:,} bytes"
        else:
            unknown_count += 1
            size = "size unknown"
        replacing = " (replace existing destination file)" if (artifact_destination(artifact, args) / name).exists() else ""
        checks = "SHA-256 required" if "sha256" in artifact else "no expected SHA-256"
        print(f"DOWNLOAD: {name} — {size}; {checks}{replacing}")
        for source in sources:
            print(f"  source: {source}")
    print(f"Total: {known_total:,} known bytes ({known_total / 1_000_000_000:.2f} GB)"
          f" + {unknown_count} files with unknown size.")
    if not args.yes:
        try:
            confirmed = input(f"Download {len(plan)} artifacts? [y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Cancelled; no files downloaded.")
            return 1
    destination.mkdir(parents=True, exist_ok=True)
    failed = False
    for artifact, sources in plan:
        attempts = list(sources)
        counts = {}
        for index, source in enumerate(attempts):
            counts[source] = counts.get(source, 0) + 1
            print(f"DOWNLOADING: {artifact['filename']}", flush=True)
            try:
                folder = artifact_destination(artifact, args)
                folder.mkdir(parents=True, exist_ok=True)
                target, size, digest = fetch_artifact(artifact, source, folder, args.timeout,
                    getattr(args, 'download_control', None), getattr(args, 'rate_limit', 5))
            except TransferCancelled as error:
                print(str(error), flush=True)
                return 1
            except (OSError, ValueError, URLError, http.client.HTTPException) as error:
                print(f"FAILED SOURCE: {artifact['filename']}: {source}: {error}", file=sys.stderr)
                transient = (error.code in {408, 429, 500, 502, 503, 504} if isinstance(error, HTTPError)
                    else isinstance(error, (OSError, URLError, http.client.HTTPException)))
                if transient and urlsplit(source).scheme in {'http', 'https'} and counts[source] < 3:
                    attempts.insert(index + 1, source)
                    print(f'Retrying interrupted source (attempt {counts[source] + 1}/3).', flush=True)
                    time.sleep(.25 * counts[source])
                continue
            if hasattr(args, 'acquired'):
                args.acquired[artifact['filename']] = {'sha256': digest, 'size_bytes': size}
            print(f"DOWNLOADED: {artifact['filename']} -> {target} ({size:,} bytes; sha256 {digest})")
            break
        else:
            failed = True
    print("Artifacts not satisfied; rerun to retry failed files." if failed else "Artifacts satisfied.")
    return 1 if failed else 0


def resolve_source(uri, args):
    if uri.startswith("package:"):
        from .pack import relative_path
        relative = relative_path(uri[len("package:"):])
        root = args.package_root
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"bundled source missing or outside package: {relative}")
        return path.as_uri()
    return source_uri(uri)

