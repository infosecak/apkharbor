"""Direct, experimental Google Play sign-in. No token broker or saved AAS token.

Interoperability references: EFForg/rs-google-play and simon-weber/gpsoauth.
Google's consumer Play protocol is unofficial and can change independently.
"""

import re
import ssl
from urllib.parse import quote, urlencode

from . import auth, transport
from ._vendor.protobuf import ProtoDecoder
from .transport import APKHarborError

SIGN_IN_URL = "https://accounts.google.com/EmbeddedSetup"
GOOGLE_BASE = "https://android.clients.google.com"
CLIENT_SIGNATURE = "38918a453d07199354f8b19af05ec6562ced5788"


def varint(value):
    if not isinstance(value, int) or value < 0 or value >= 2**64:
        raise APKHarborError("Invalid unsigned protobuf value.")
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def number(field, value):
    return varint(field << 3) + varint(int(value))


def blob(field, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return varint((field << 3) | 2) + varint(len(value)) + value


def fields(data):
    try:
        return ProtoDecoder(data).read_all_ordered()
    except (ValueError, EOFError, RecursionError) as exc:
        raise APKHarborError("Google returned an unrecognized device-registration response.") from exc


def first(data, field, wire=2):
    return next((value for fn, wt, value in fields(data) if fn == field and wt == wire), None)


def text(data, field):
    value = first(data, field)
    if value is None:
        return ""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise APKHarborError("Google returned invalid registration text.") from exc


def nested(data, *path):
    for field in path:
        data = first(data, field)
        if data is None:
            raise APKHarborError("Google Play did not return the expected registration data.")
    return data


def validate_inputs(email, token, profile, arch):
    if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise APKHarborError("Enter the email address used for Google sign-in.")
    if not isinstance(token, str) or len(token) > 16384 or not re.fullmatch(r"oauth2_4/[A-Za-z0-9._~+/=-]+", token):
        raise APKHarborError("Paste the one-time oauth_token value beginning oauth2_4/ at the hidden prompt.")
    if not isinstance(profile, dict) or not profile or any(
        not isinstance(key, str) or not isinstance(value, str) or len(value) > 32768 or "\r" in value or "\n" in value
        for key, value in profile.items()
    ):
        raise APKHarborError("Direct Google login requires a device-properties object with string values.")
    required = ("Build.FINGERPRINT", "Build.HARDWARE", "Build.BRAND", "Build.DEVICE", "Build.MODEL",
                "Build.MANUFACTURER", "Build.PRODUCT", "Build.ID", "Build.VERSION.SDK_INT", "Build.VERSION.RELEASE",
                "GSF.version", "Vending.version", "Vending.versionString", "Platforms")
    if any(not profile.get(key) for key in required):
        raise APKHarborError("Device profile is incomplete. Start with apkharbor/default-device.json.")
    abi = {"arm64": "arm64-v8a", "armv7": "armeabi-v7a", "x86": "x86", "x86_64": "x86_64"}.get(arch)
    if arch not in auth.ARCHES or (abi and abi not in profile["Platforms"].split(",")):
        raise APKHarborError("The selected device profile does not support this architecture.")
    if arch == "tv" and "android.software.leanback" not in profile.get("Features", "").split(","):
        raise APKHarborError("Android TV login requires a compatible TV profile.")
    for key in ("Build.VERSION.SDK_INT", "GSF.version", "Vending.version", "Screen.Density", "Screen.Width", "Screen.Height", "GL.Version"):
        value = profile.get(key, "1")
        if not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**31:
            raise APKHarborError("Device profile has an invalid numeric property: " + key)


def device_configuration(profile):
    # Android DeviceConfigurationProto wire fields, including repeated ABI,
    # library, feature, locale and GLES strings. No identifiers from the Mac.
    result = b"".join(number(fn, value) for fn, value in (
        (1, 3), (2, 1), (3, 1), (4, 2), (5, 0), (6, 0),
        (7, profile.get("Screen.Density", "440")), (8, profile.get("GL.Version", "196610")),
        (12, profile.get("Screen.Width", "1080")), (13, profile.get("Screen.Height", "2340"))))
    for fn, key in ((9, "SharedLibraries"), (10, "Features"), (11, "Platforms"), (14, "Locales"), (15, "GL.Extensions")):
        for value in profile.get(key, "").split(","):
            if value:
                result += blob(fn, value)
    return result


def checkin_request(profile, configuration):
    build = b""
    for fn, key in ((1, "Build.FINGERPRINT"), (2, "Build.HARDWARE"), (3, "Build.BRAND"), (4, "Build.RADIO"),
                    (5, "Build.BOOTLOADER"), (6, "Client"), (9, "Build.DEVICE"), (11, "Build.MODEL"),
                    (12, "Build.MANUFACTURER"), (13, "Build.PRODUCT")):
        build += blob(fn, profile.get(key, ""))
    build += number(8, profile["GSF.version"]) + number(10, profile["Build.VERSION.SDK_INT"]) + number(14, 0)
    checkin = blob(1, build) + number(2, 0) + number(9, 0)
    for fn, key in ((6, "CellOperator"), (7, "SimOperator"), (8, "Roaming")):
        checkin += blob(fn, profile.get(key, ""))
    return (number(2, 0) + blob(4, checkin) + blob(6, "en_US") + blob(12, "UTC")
            + number(14, 3) + blob(18, configuration) + number(20, 0))


def auth_context():
    # Explicit context avoids urllib's automatic ALPN advertisement, matching
    # the GoogleAuth client handshake. Certificate and hostname checks stay on.
    context = ssl.create_default_context()
    context.options &= ~ssl.OP_NO_TICKET
    return context


def form_request(values, profile):
    response = transport.post(GOOGLE_BASE + "/auth", content=urlencode(values), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "identity",
        "User-Agent": "GoogleAuth/1.4 (%s %s)" % (profile["Build.DEVICE"], profile["Build.ID"]),
        "app": "com.google.android.gms",
    }, tls_context=auth_context())
    if response.status_code != 200:
        raise APKHarborError("Google sign-in request was rejected (HTTP %s). No token server was contacted." % response.status_code)
    try:
        result = dict(line.split("=", 1) for line in response.content.decode("utf-8").splitlines() if "=" in line)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APKHarborError("Google returned an unrecognized sign-in response.") from exc
    if result.get("Error"):
        # Never reflect arbitrary Google responses or secret-bearing URLs.
        known = result["Error"]
        if known not in ("BadAuthentication", "NeedsBrowser", "CaptchaRequired", "ServiceDisabled", "DeviceManagementRequired"):
            known = "sign-in rejected"
        raise APKHarborError("Google sign-in failed: %s. Sign in again and obtain a fresh one-time token." % known)
    return result


def user_agent(profile):
    value = lambda key: quote(profile[key], safe="")
    return ("Android-Finsky/%s (api=3,versionCode=%s,sdk=%s,device=%s,hardware=%s,product=%s,"
            "platformVersionRelease=%s,model=%s,buildId=%s,isWideScreen=0,supportedAbis=%s)" % (
                profile["Vending.versionString"], profile["Vending.version"], profile["Build.VERSION.SDK_INT"],
                value("Build.DEVICE"), value("Build.HARDWARE"), value("Build.PRODUCT"),
                value("Build.VERSION.RELEASE"), value("Build.MODEL"), value("Build.ID"),
                profile["Platforms"].replace(",", ";")))


def login(email, one_time_token, profile, arch="arm64"):
    """Return only a short-lived Play session; account/AAS tokens never persist."""
    validate_inputs(email, one_time_token, profile, arch)
    configuration = device_configuration(profile)
    headers = {"Content-Type": "application/x-protobuf", "User-Agent": "GoogleAuth/1.4"}
    response = transport.post(GOOGLE_BASE + "/checkin", content=checkin_request(profile, configuration), headers=headers)
    if response.status_code != 200:
        raise APKHarborError("Google device registration failed (HTTP %s)." % response.status_code)
    identifier = first(response.content, 7, wire=1)
    if not isinstance(identifier, int) or not identifier:
        raise APKHarborError("Google device registration returned no Android device ID.")
    common = {"Email": email, "sdk_version": profile["Build.VERSION.SDK_INT"], "lang": "en",
              "device_country": "us", "google_play_services_version": profile["GSF.version"], "callerSig": CLIENT_SIGNATURE}
    exchange = form_request({**common, "Token": one_time_token, "service": "ac2dm", "ACCESS_TOKEN": "1",
                             "add_account": "1", "get_accountid": "1", "callerPkg": "com.google.android.gms",
                             "droidguard_results": "null"}, profile)
    account_token = exchange.get("Token")
    if not account_token or any(c.isspace() for c in account_token):
        raise APKHarborError("Google did not exchange the one-time token. Obtain a fresh oauth_token and try again.")
    token = form_request({**common, "Token": account_token, "androidId": format(identifier, "x"),
                          "service": "oauth2:https://www.googleapis.com/auth/googleplay", "app": "com.android.vending",
                          "client_sig": CLIENT_SIGNATURE, "callerPkg": "com.google.android.gms", "check_email": "1",
                          "oauth2_foreground": "1", "token_request_options": "CAA4AVAB", "system_partition": "1"}, profile)
    session = auth.validate_session({"email": email, "authToken": token.get("Auth"), "gsfId": format(identifier, "x"),
              "deviceCheckInConsistencyToken": text(response.content, 12),
              "deviceInfoProvider": {"userAgentString": user_agent(profile), "mccMnc": profile.get("SimOperator", "")}})
    response = transport.post(GOOGLE_BASE + "/fdfe/uploadDeviceConfig", content=blob(1, configuration),
                              headers={**auth.build_headers(session), "Content-Type": "application/x-protobuf"})
    if response.status_code != 200:
        raise APKHarborError("Google Play rejected device configuration (HTTP %s)." % response.status_code)
    session["deviceConfigToken"] = text(nested(response.content, 1, 28), 1)
    if not session["deviceConfigToken"]:
        raise APKHarborError("Google Play returned no device-configuration token.")
    response = transport.get(GOOGLE_BASE + "/fdfe/toc", headers=auth.build_headers(session))
    if response.status_code != 200:
        raise APKHarborError("Google Play initialization failed (HTTP %s)." % response.status_code)
    toc = nested(response.content, 1, 6)
    if text(toc, 3) or text(toc, 7):
        raise APKHarborError("Google Play requires terms-of-service review for this account. Review them through Google's official services before retrying. APKHarbor does not accept terms automatically.")
    session["dfeCookie"] = text(toc, 22)
    if not session["dfeCookie"]:
        raise APKHarborError("Google Play returned no initialized session cookie.")
    return auth.validate_session(session)
