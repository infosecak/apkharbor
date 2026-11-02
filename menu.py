"""Small Mac launcher for the bundled APKHarbor CLI; no extra dependencies."""

import contextlib
import io
import json
import subprocess
from pathlib import Path

from apkharbor import __version__, cli

BUNDLE = Path(__file__).resolve().parent
DOWNLOADS = BUNDLE / "Downloads"


def run_cli(arguments):
    try:
        return cli.main(arguments)
    except SystemExit as exc:
        return exc.code


def download(package):
    print("\nDownloading " + package + " ...")
    if run_cli(["download", package, "--output", str(DOWNLOADS)]) == 0:
        print("\nSaved inside the Downloads folder beside Start APKHarbor.command.")


def search():
    query = input("\nApp name to search for: ").strip()
    if not query:
        return
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = run_cli(["search", "--json", "--limit", "10", "--", query])
    if status != 0:
        return
    results = json.loads(output.getvalue())
    if not results:
        print("No matching apps were returned.")
        return
    print()
    for index, result in enumerate(results, 1):
        print("%s. %s\n   %s" % (index, result["title"], result["package"]))
    selection = input("\nEnter a result number to download it, or press Enter to return: ").strip()
    if not selection:
        return
    if not selection.isascii() or not selection.isdigit() or not 1 <= int(selection) <= len(results):
        print("Choose one of the numbered results.")
        return
    download(results[int(selection) - 1]["package"])


def open_path(path):
    subprocess.run(["/usr/bin/open", str(path)], check=False)


def main():
    print("\nAPKHarbor for Mac " + __version__)
    print("Experimental Google Play client. Direct Google login needs a one-time browser token.")
    print("No dispenser service or Python package installation is required.")
    while True:
        print("\n1. Sign in with Google\n2. Search for an app and download it\n3. Download using an Android package name\n4. Show local login status\n5. Open the Downloads folder\n6. Open the usage guide\n7. Log out locally\n0. Quit")
        try:
            choice = input("\nChoose a number: ").strip()
            if choice == "0":
                return 0
            if choice == "1":
                run_cli(["auth", "login", "--google"])
            elif choice == "2":
                search()
            elif choice == "3":
                package = input("Android package name (for example org.videolan.vlc): ").strip()
                if package:
                    download(package)
            elif choice == "4":
                run_cli(["auth", "status"])
            elif choice == "5":
                DOWNLOADS.mkdir(parents=True, exist_ok=True)
                open_path(DOWNLOADS)
            elif choice == "6":
                open_path(BUNDLE / "START HERE.txt")
            elif choice == "7":
                run_cli(["auth", "logout"])
            else:
                print("Enter one of the menu numbers.")
        except (EOFError, KeyboardInterrupt):
            print("\nReturning from input. Close this window or choose 0 to quit.")
            return 0
        except (OSError, ValueError, KeyError, TypeError):
            print("The operation could not finish. Check the usage guide and try again.")


if __name__ == "__main__":
    raise SystemExit(main())
