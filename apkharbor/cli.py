"""APKHarbor command-line entry point."""

import argparse
import getpass
import json
import sys
import zlib
import warnings
import webbrowser
from http.client import HTTPException
from dataclasses import asdict
from pathlib import Path

from . import __version__, auth, packages
from ._vendor import play
from .transport import APKHarborError


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parser():
    root = argparse.ArgumentParser(prog="apkharbor", description="Search Google Play and download APKs using your own authenticated account.")
    root.add_argument("--version", action="version", version="apkharbor " + __version__)
    commands = root.add_subparsers(dest="command", required=True)
    login_group = commands.add_parser("auth", help="Manage personal account sessions")
    actions = login_group.add_subparsers(dest="action", required=True)
    login = actions.add_parser("login", help="Sign in directly with Google, import a Play session, or explicitly pair with a server")
    source = login.add_mutually_exclusive_group()
    source.add_argument("--google", action="store_true", help="Direct experimental Google sign-in (default); no token server")
    source.add_argument("--token-file", type=Path, help="Existing Play session JSON; passwords are never requested")
    source.add_argument("--server", help="Explicit HTTPS origin of your chosen gplaydl-compatible personal account token server")
    login.add_argument("--profile-file", type=Path, help="Device properties JSON for Google login or pairing; default is an ARM64 Pixel profile")
    login.add_argument("--email", help="Google account email, or account selection for explicit server pairing")
    login.add_argument("--no-browser", action="store_true", help="Print the Google sign-in URL without opening a browser")
    for item in (login, actions.add_parser("status", help="Show local session presence; does not check expiry"), actions.add_parser("logout", help="Delete the local session and pairing key")):
        item.add_argument("--arch", choices=auth.ARCHES, default="arm64")
    for name in ("search", "info", "download"):
        cmd = commands.add_parser(name, help={"search": "Search for apps", "info": "Show app metadata", "download": "Download base APK, splits and additional files"}[name])
        cmd.add_argument("query" if name == "search" else "package")
        cmd.add_argument("--arch", choices=auth.ARCHES, default="arm64", help="Architecture of your authenticated device profile")
        cmd.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        if name == "search":
            cmd.add_argument("--limit", type=positive, default=10)
        if name == "download":
            cmd.add_argument("--output", "-o", type=Path, default=Path("downloads"))
            cmd.add_argument("--version-code", type=positive, help="Request a specific version if Play still serves it")
            cmd.add_argument("--locale", action="append", help="Language tag; repeat for multiple languages (default en-US)")
    install = commands.add_parser("install", help="Verify a APKHarbor manifest and install its APK set with ADB")
    install.add_argument("manifest", type=Path, help="manifest.json or its containing directory")
    install.add_argument("--adb", required=True, type=Path, help="Absolute path to your trusted Android SDK Platform Tools adb executable; PATH is never searched")
    install.add_argument("--serial", help="ADB device serial")
    return root


def authenticated(arch, operation):
    session = auth.load_session(arch)
    try:
        return operation(session)
    except play.AuthExpiredError:
        session = auth.refresh(arch)
        try:
            return operation(session)
        except play.AuthExpiredError as exc:
            raise APKHarborError("Google Play rejected the refreshed session. Run apkharbor auth login again.") from exc


def hidden_prompt(label):
    # Refuse getpass's fallback to echoed input when terminal control fails.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(label).strip()
        except getpass.GetPassWarning as exc:
            raise APKHarborError("A terminal with hidden input is required. Token entry was cancelled.") from exc


def auth_command(args):
    path = auth.session_path(args.arch)
    link_path = auth.config_dir() / ("link-" + args.arch + ".json")
    if args.action == "status":
        if path.exists():
            auth.load_session(args.arch)
            print("Session stored for %s (expiry not checked)." % args.arch)
        else:
            print("No session stored for " + args.arch + ".")
        return
    if args.action == "logout":
        was_paired = link_path.exists()
        path.unlink(missing_ok=True)
        link_path.unlink(missing_ok=True)
        print("Local session removed.")
        if was_paired:
            print("Pairing key removed locally. Revoke the machine in your chosen token server to invalidate its key remotely.")
        return
    link = None
    if args.no_browser and (args.token_file or args.server):
        raise APKHarborError("--no-browser applies only to direct Google sign-in.")
    if args.token_file:
        if args.profile_file or args.email:
            raise APKHarborError("--profile-file and --email apply only to server pairing.")
        session = auth.validate_session(auth.read_json(args.token_file))
    elif args.server:
        server = auth.server_url(args.server)
        if args.arch != "arm64" and not args.profile_file:
            raise APKHarborError("Pairing for this architecture requires --profile-file with matching device properties.")
        profile = auth.read_json(args.profile_file or Path(__file__).with_name("default-device.json"))
        if not isinstance(profile, dict) or not profile:
            raise APKHarborError("Device profile must be a nonempty JSON object.")
        print("Pairing with " + server + ".", file=sys.stderr)
        print("Add your Google account in that server's Android authenticator, then generate a CLI pairing code. The server receives and manages your account's Play tokens.", file=sys.stderr)
        if not sys.stdin.isatty():
            raise APKHarborError("Pairing needs an interactive terminal. Use --token-file for automation.")
        code = hidden_prompt("Pairing code: ")
        if not code:
            raise APKHarborError("Pairing code cannot be empty.")
        link = auth.pair(server, code)
        session = auth.fetch_session(link, profile, args.email)
        link.update(profile=profile, email=args.email)
    else:
        from . import google_auth
        if not sys.stdin.isatty():
            raise APKHarborError("Direct Google sign-in requires an interactive terminal. Use --token-file for automation.")
        if args.arch != "arm64" and not args.profile_file:
            raise APKHarborError("This architecture requires --profile-file with matching device properties.")
        profile = auth.read_json(args.profile_file or Path(__file__).with_name("default-device.json"))
        email = (args.email or input("Google account email: ")).strip()
        print("Sign in on Google's website: " + google_auth.SIGN_IN_URL, file=sys.stderr)
        print("Open browser Developer Tools > Network before signing in. After sign-in, inspect an accounts.google.com response's Cookies and copy oauth_token (starts oauth2_4/). Paste it only at the hidden prompt below.", file=sys.stderr)
        print("This is experimental Google Play authentication. No token server is used, and the long-lived account token is not saved.", file=sys.stderr)
        if not args.no_browser:
            try:
                webbrowser.open(google_auth.SIGN_IN_URL)
            except (webbrowser.Error, OSError):
                print("Open the Google URL above manually.", file=sys.stderr)
        session = google_auth.login(email, hidden_prompt("One-time Google oauth_token (hidden): "), profile, args.arch)
    # Verify against Google Play before replacing the existing session.
    play.search_apps("android", session, limit=1)
    auth.save_session(session, args.arch)
    if link:
        auth.private_write(link_path, link)
    else:
        link_path.unlink(missing_ok=True)
    print("Authenticated for " + args.arch + ".")


def run(args):
    if args.command == "auth":
        return auth_command(args)
    if args.command == "install":
        packages.install(args.manifest, args.serial, adb_path=args.adb)
        print("Installed successfully.")
        return
    if args.command == "search":
        if not args.query.strip():
            raise APKHarborError("Search query cannot be empty.")
        if args.limit > 100:
            raise APKHarborError("Search limit must be between 1 and 100.")
        result = authenticated(args.arch, lambda session: play.search_apps(args.query, session, limit=args.limit))
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for item in result:
                print(item["package"] + "  " + item["title"])
            if not result:
                print("No results found.")
        return
    package = packages.package_name(args.package)
    if args.command == "info":
        details = authenticated(args.arch, lambda session: play.get_details(package, session))
        if args.json:
            print(json.dumps(asdict(details), indent=2))
        else:
            print(details.title + " (" + details.package + ")")
            print("Version: %s (%s)" % (details.version_string, details.version_code))
            print("Developer: " + details.developer)
            print(details.play_url)
        return
    locales = args.locale or ["en-US"]
    import re
    if any(not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", locale) for locale in locales):
        raise APKHarborError("Use locale tags such as en-US or de-DE.")

    def acquire(session):
        details = play.get_details(package, session)
        version = args.version_code or details.version_code
        if version <= 0:
            raise APKHarborError("Google Play returned no valid version code.")
        token = play.purchase(package, version, session)
        return version, play.get_delivery(package, version, session, delivery_token=token, locales=locales)

    version, delivery = authenticated(args.arch, acquire)
    manifest = packages.download_set(package, version, args.arch, delivery, args.output, locales)
    if args.json:
        print(json.dumps({"manifest": str(manifest.resolve())}))
    else:
        print("Downloaded. Manifest: " + str(manifest.resolve()))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        run(args)
        return 0
    except (APKHarborError, play.PlayAPIError) as exc:
        print("apkharbor: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, RecursionError, EOFError, zlib.error, HTTPException) as exc:
        # Untrusted protocol data and low-level errors must never dump tokens or URLs.
        print("apkharbor: Operation failed (%s). Check files, permissions and server responses." % type(exc).__name__, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\napkharbor: Cancelled.", file=sys.stderr)
        return 130
