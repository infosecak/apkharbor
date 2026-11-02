"""Private local sessions and explicit personal-account pairing."""

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from . import transport
from .transport import APKHarborError

ARCHES = ("arm64", "armv7", "x86", "x86_64", "tv")


def config_dir():
    if "APKHARBOR_CONFIG_DIR" in os.environ:
        return Path(os.environ["APKHARBOR_CONFIG_DIR"])
    # Reuse existing installations without copying account credentials or
    # changing logout semantics. The new override always takes precedence.
    if "DROIDTOOL_CONFIG_DIR" in os.environ:
        return Path(os.environ["DROIDTOOL_CONFIG_DIR"])
    current = Path.home() / ".config" / "apkharbor"
    legacy = Path.home() / ".config" / "droidtool"
    if not current.exists() and legacy.is_dir():
        return legacy
    return current


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise APKHarborError("Configuration directory must not be a symbolic link.")
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def session_path(arch):
    if arch not in ARCHES:
        raise APKHarborError("Unknown device architecture.")
    return config_dir() / ("session-" + arch + ".json")


def validate_session(data):
    if not isinstance(data, dict):
        raise APKHarborError("Session must be a JSON object.")
    token, device = data.get("authToken"), data.get("gsfId")
    if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
        raise APKHarborError("Session requires a nonempty authToken without whitespace.")
    if not isinstance(device, str) or not re.fullmatch(r"[0-9a-fA-F]{1,16}", device):
        raise APKHarborError("Session requires gsfId as a hexadecimal string (up to 16 digits).")
    result = {"authToken": token, "gsfId": device}
    for name in ("email", "dfeCookie", "deviceConfigToken", "deviceCheckInConsistencyToken"):
        value = data.get(name, "")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise APKHarborError("Invalid session field: " + name)
        result[name] = value
    info = data.get("deviceInfoProvider", {})
    if not isinstance(info, dict):
        raise APKHarborError("deviceInfoProvider must be an object.")
    result["deviceInfoProvider"] = {}
    for name in ("userAgentString", "mccMnc"):
        if name in info:
            value = info[name]
            if not isinstance(value, str) or "\r" in value or "\n" in value:
                raise APKHarborError("Invalid device information.")
            result["deviceInfoProvider"][name] = value
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise APKHarborError("Could not read JSON file: " + str(path)) from exc


def load_session(arch):
    path = session_path(arch)
    if not path.exists():
        raise APKHarborError("No session for " + arch + ". Run apkharbor auth login first.")
    return validate_session(read_json(path))


def save_session(data, arch):
    data = validate_session(data)
    private_write(session_path(arch), data)
    return data


def server_url(value):
    transport.https_url(value)
    parts = urlsplit(value)
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise APKHarborError("Use the token server's HTTPS origin without a path or query.")
    return value.rstrip("/")


def pair(server, code):
    server = server_url(server)
    response = transport.post(server + "/api/v1/pair/claim-cli", payload={"code": code, "label": "apkharbor"})
    if response.status_code != 200:
        raise APKHarborError("Pairing rejected (HTTP %s). Generate a fresh pairing code." % response.status_code)
    body = response.json()
    key = body.get("apiKey") if isinstance(body, dict) else None
    if not isinstance(key, str) or not key or any(c.isspace() for c in key):
        raise APKHarborError("Token server returned no valid pairing key.")
    return {"server": server, "apiKey": key}


def fetch_session(link, profile, email=None):
    server = server_url(link["server"])
    query = "?" + urlencode({"email": email}) if email else ""
    response = transport.post(server + "/api/auth" + query,
        headers={"X-Api-Key": link["apiKey"]}, payload=profile)
    if response.status_code != 200:
        raise APKHarborError("Account token request failed (HTTP %s). Check the linked account and device profile." % response.status_code)
    return validate_session(response.json())


def refresh(arch):
    path = config_dir() / ("link-" + arch + ".json")
    if not path.exists():
        raise APKHarborError("Play session expired. Run apkharbor auth login again, or import a fresh session.")
    link = read_json(path)
    return save_session(fetch_session(link, link["profile"], link.get("email")), arch)


def build_headers(auth):
    info = auth.get("deviceInfoProvider", {})
    headers = {
        "Authorization": "Bearer " + auth["authToken"],
        "User-Agent": info.get("userAgentString") or (
            "Android-Finsky/41.2.29-23 [0] [PR] 639844241 "
            "(api=3,versionCode=84122900,sdk=34,device=lynx,hardware=lynx,"
            "product=lynx,platformVersionRelease=14,model=Pixel%207a,"
            "buildId=UQ1A.231205.015,isWideScreen=0,supportedAbis=arm64-v8a;armeabi-v7a;armeabi)"
        ),
        "X-DFE-Device-Id": auth["gsfId"], "Accept-Language": "en-US",
        "X-DFE-Encoded-Targets": "CAESN/qigQYC2AMBFfUbyA7SM5Ij/CvfBoIDgxXrBPsDlQUdMfOLAfoFrwEHgAcBrQYhoA0cGt4MKK0Y2gI",
        "X-DFE-Client-Id": "am-android-google", "X-DFE-Network-Type": "4",
        "X-DFE-Content-Filters": "", "X-Limit-Ad-Tracking-Enabled": "true",
        "X-Ad-Id": "", "X-DFE-UserLanguages": "en_US",
        "X-DFE-Request-Params": "timeoutMs=4000", "X-DFE-Cookie": auth.get("dfeCookie", ""),
        "X-DFE-No-Prefetch": "true",
    }
    for key, header in (("deviceCheckInConsistencyToken", "X-DFE-Device-Checkin-Consistency-Token"),
                        ("deviceConfigToken", "X-DFE-Device-Config-Token")):
        if auth.get(key):
            headers[header] = auth[key]
    if info.get("mccMnc"):
        headers["X-DFE-MCCMNC"] = info["mccMnc"]
    return headers
