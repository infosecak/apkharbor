"""Atomic downloads, integrity manifests, and manifest-driven ADB installs."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path
from urllib.error import HTTPError

from . import transport
from .transport import APKHarborError


def package_name(value):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", value):
        raise APKHarborError("Use an Android package name, for example com.example.app.")
    return value


def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value) or ".." in value:
        raise APKHarborError("Server returned an unsafe filename.")
    return value


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_file(url, destination, *, cookies=None, expected="", algorithm="sha256", gzipped=False, size=0):
    transport.https_url(url)
    headers = {"Accept-Encoding": "identity"}
    if cookies:
        parts = []
        for cookie in cookies:
            name, value = cookie["name"], cookie["value"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or any(c in value for c in "\r\n;"):
                raise APKHarborError("Invalid download cookie.")
            parts.append(name + "=" + value)
        headers["Cookie"] = "; ".join(parts)
    fd, temporary = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
    hasher = hashlib.new(algorithm)
    total = 0
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if gzipped else None
    try:
        with os.fdopen(fd, "wb") as target:
            try:
                response = transport.open_url(url, headers=headers, download_redirects=True)
            except HTTPError as exc:
                raise APKHarborError("Download refused (HTTP %s). Request a fresh delivery." % exc.code) from exc
            with response:
                for chunk in iter(lambda: response.read(128 * 1024), b""):
                    if decoder:
                        chunk = decoder.decompress(chunk)
                    hasher.update(chunk)
                    target.write(chunk)
                    total += len(chunk)
            if decoder:
                tail = decoder.flush()
                hasher.update(tail)
                target.write(tail)
                total += len(tail)
                if not decoder.eof:
                    raise APKHarborError("Compressed download was truncated.")
        if size and total != size:
            raise APKHarborError("Download size does not match delivery metadata.")
        actual = base64.urlsafe_b64encode(hasher.digest()).decode().rstrip("=")
        if expected and actual != expected.rstrip("="):
            raise APKHarborError("Download checksum does not match Google Play metadata.")
        if destination.suffix == ".apk":
            try:
                with zipfile.ZipFile(temporary) as apk:
                    if "AndroidManifest.xml" not in apk.namelist() or apk.testzip() is not None:
                        raise APKHarborError("Downloaded APK is invalid or corrupted.")
            except zipfile.BadZipFile as exc:
                raise APKHarborError("Downloaded file is not a valid APK.") from exc
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"name": destination.name, "size": total, "sha256": digest(destination),
            "upstream_verified": bool(expected)}


def download_set(package, version, arch, delivery, output, locales=None):
    package_name(package)
    root = Path(output) / package / str(version) / arch
    root.mkdir(parents=True, exist_ok=True)
    if (root / "package").exists():
        raise APKHarborError("This download already has a manifest. Use a new output directory to download again.")
    # A fresh staging directory prevents failed or simultaneous runs from mixing APKs.
    items = [("base.apk", "base", delivery.download_url, delivery.cookies,
              delivery.sha256 or delivery.sha1, "sha256" if delivery.sha256 else "sha1",
              False, delivery.download_size)]
    names = {"base.apk"}
    for split in delivery.splits:
        name = "split-" + safe_name(split.name) + ".apk"
        if name in names:
            raise APKHarborError("Delivery contained duplicate split names.")
        names.add(name)
        items.append((name, "split", split.url, delivery.cookies, split.sha256, "sha256", False, split.size))
    for index, extra in enumerate(delivery.additional_files):
        name = ("asset-%s.apk" % index if extra.is_asset_pack else
                "%s.%s.%s.obb" % (extra.type_label, extra.version_code, package))
        items.append((name, "asset" if extra.is_asset_pack else "obb", extra.url,
                      extra.cookies, "", "sha256", extra.gzipped, 0 if extra.gzipped else extra.size))
    if len({item[0] for item in items}) != len(items):
        raise APKHarborError("Delivery contained duplicate filenames.")
    stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        files = []
        for name, kind, url, cookies, expected, algorithm, gzipped, size in items:
            print("Downloading " + name, file=__import__("sys").stderr)
            record = download_file(url, stage / name, cookies=cookies, expected=expected,
                                   algorithm=algorithm, gzipped=gzipped, size=size)
            record["kind"] = kind
            files.append(record)
        manifest = {"schema": 1, "package": package, "version_code": version,
                    "arch": arch, "locales": locales or ["en-US"], "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # Publish the entire download as one directory, never individual partial files.
        published = root / "package"
        if published.exists():
            raise APKHarborError("Download destination exists. Use a new output directory.")
        os.rename(stage, published)
        return published / "manifest.json"
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def resolve_adb(adb_path):
    """Use only an operator-selected absolute executable; never search PATH."""
    if adb_path is None:
        raise APKHarborError("ADB requires --adb with the absolute path to your trusted Platform Tools executable.")
    candidate = Path(adb_path)
    # Check before resolving: resolve() would silently turn a relative input
    # into an absolute path under a possibly untrusted working directory.
    if not candidate.is_absolute():
        raise APKHarborError("--adb must be an absolute path to your trusted Platform Tools executable.")
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise APKHarborError("The selected ADB executable could not be resolved.") from exc
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise APKHarborError("--adb must point to an existing executable file.")
    if os.name == "nt" and candidate.suffix.lower() != ".exe":
        raise APKHarborError("On Windows, --adb must select the trusted adb.exe executable, not a script.")
    return str(candidate)


def install(manifest_path, serial=None, *, adb_path=None):
    manifest_path = Path(manifest_path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise APKHarborError("Could not read the download manifest.") from exc
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("files"), list):
        raise APKHarborError("Unsupported download manifest.")
    package_name(data.get("package", ""))
    paths, names, base_count = [], set(), 0
    for entry in data["files"]:
        if not isinstance(entry, dict):
            raise APKHarborError("Invalid manifest file record.")
        name = safe_name(entry.get("name"))
        if name in names:
            raise APKHarborError("Duplicate manifest filename.")
        names.add(name)
        path = manifest_path.parent / name
        if path.is_symlink() or not path.is_file() or digest(path) != entry.get("sha256"):
            raise APKHarborError("Missing or changed download: " + name)
        kind = entry.get("kind")
        if kind not in ("base", "split", "obb", "asset"):
            raise APKHarborError("Invalid file kind in manifest.")
        if kind in ("base", "split"):
            paths.append(str(path.resolve()))
            base_count += kind == "base"
    if base_count != 1:
        raise APKHarborError("Manifest must contain exactly one base APK.")
    if any(entry["kind"] in ("obb", "asset") for entry in data["files"]):
        raise APKHarborError("This app has OBB or asset packs. Files were downloaded, but v1 supports automatic installation of base and split APKs only.")
    adb = resolve_adb(adb_path)
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["install-multiple", "-r"] + paths
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise APKHarborError("ADB installation failed (exit %s). Check the device, ABI and Android version." % completed.returncode)
