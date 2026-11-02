# Advanced usage and authentication

APKHarbor is the new name for Droidtool. The command is now `apkharbor`, and the Mac launcher is **Start APKHarbor.command**.

An authenticated Google Play downloader for macOS, Linux and Windows. Python 3.9+; no runtime dependencies.

Version 0.3.0 supports Google's HTTPS CDN redirects during downloads, alongside experimental direct Google sign-in, app search, base/split APK downloads, local sessions and ADB installation. Aurora anonymous authentication remains planned for v2.

## Run

From this directory, run without installing anything:

```sh
python3 -m apkharbor --help
```

Or install the command in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/apkharbor --help
```

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\apkharbor.exe`. In the examples below, replace `apkharbor` with `python3 -m apkharbor` if running directly from source.

## Authenticate

APKHarbor uses your own Google Play account. Direct Google sign-in is the default; no Android phone or token-dispenser service is required for that mode. This uses an unofficial consumer Play protocol, not Google's supported third-party OAuth authorization flow. Compatibility varies by account and device profile; broader live-account validation is ongoing.

### Direct Google sign-in (default)

```sh
apkharbor auth login --email you@gmail.com
```

Your normal browser opens Google's [EmbeddedSetup sign-in page](https://accounts.google.com/EmbeddedSetup). APKHarbor never asks for your Google password. Password and two-factor entry take place on Google's website.

1. Open your browser's Developer Tools and select **Network**. In Chrome on Mac, press **Option + Command + I**. Enable **Preserve log**, then reload the Google sign-in page so the requests are recorded.
2. Complete Google sign-in. If Google shows terms, read them and decide whether to accept them on Google's page.
3. Select a recent request to `accounts.google.com`. In its **Cookies** view, find the response cookie named `oauth_token`; its value should start with `oauth2_4/`.
4. Copy only that cookie value and paste it at APKHarbor's hidden terminal prompt. Input is not echoed; press Enter. Do not paste tokens into chat or put them in command arguments.
5. APKHarbor registers the configured Android device profile with Google, exchanges the one-time token directly with Google, requests a Play-scoped token and initializes the Play session. A successful login saves the Play session locally.

This manual token step follows the approach documented by [EFF's apkeep](https://github.com/EFForg/apkeep/blob/master/USAGE-google-play.md). It is not an automatic browser callback. If Google does not issue that cookie or rejects this unofficial flow, APKHarbor cannot finish sign-in; it will not fall back to an external server. Do not send us your cookies or tokens for troubleshooting.

The one-time browser token and the resulting long-lived account/AAS token are used only in memory during login and are not saved. Only the Play session is persisted. When that session expires, repeat Google sign-in; direct mode deliberately does not save a refresh credential. Successful direct sign-in also removes any old pairing link for the selected architecture. A failed login preserves your previous local state.

`--google` explicitly selects the same default flow. Add `--no-browser` to print the Google URL and open it yourself. Noninteractive input is refused so the token cannot accidentally be echoed. For automation, use an existing short-lived session file instead.

Direct mode defaults to the packaged ARM64 Android profile, regardless of whether the Mac itself is Intel or Apple Silicon. `--arch` describes the target Android device. Other architectures require `--profile-file` with compatible string-valued device properties; start with `apkharbor/default-device.json`. Google decides whether the profile is accepted. Accounts requiring additional Google Play terms review are stopped with an error; APKHarbor never accepts those terms automatically.

### Import an existing authenticated Play session

If you already have a session exported from a compatible Google Play client, import its JSON locally:

```sh
apkharbor auth login --token-file /path/to/private-session.json --arch arm64
```

Expected shape (placeholder values below are not working credentials):

```json
{
  "authToken": "YOUR_PLAY_AUTH_TOKEN",
  "gsfId": "0123456789abcdef",
  "email": "you@example.com",
  "deviceInfoProvider": {
    "userAgentString": "YOUR_MATCHING_DEVICE_USER_AGENT"
  }
}
```

`authToken` and a hexadecimal string `gsfId` are required. Preserve matching `deviceInfoProvider`, `dfeCookie`, `deviceConfigToken`, and `deviceCheckInConsistencyToken` fields when present. Choose the architecture corresponding to that session's device. Importing a token does not register a new device or convert it to a different architecture. Imported sessions need manual replacement when they expire.

### Optional explicit server pairing

Existing gplaydl-compatible personal-account token servers remain supported only when you explicitly pass `--server`. This is an alternative mode, not a requirement or fallback for Google sign-in:

```sh
apkharbor auth login --server https://your-private-token-server.example
```

This method requires that server's Android authenticator and a CLI pairing code. Your selected server manages account Play tokens; choose it only if you trust its operator. Paired sessions can refresh through that server. Use `--email` to select an account and `--profile-file` for a custom device profile. No server is contacted by default.

Login probes Google Play before replacing the local session. APKHarbor never asks for or stores your Google password. Session files and pairing keys are plaintext credentials protected by owner-only file permissions (`0600`, directory `0700` on POSIX). Keep the original import file private too. These permissions do not provide encryption at rest; Windows users should protect the configuration directory with their user ACLs.

```sh
apkharbor auth status --arch arm64
apkharbor auth logout --arch arm64
```

Status checks local presence and format, not server validity. Logout removes local credentials for the selected architecture. Revoke the machine on your token server to invalidate its pairing key remotely. Configuration defaults to `~/.config/apkharbor`; set `APKHARBOR_CONFIG_DIR` to use another location.

Existing installations reuse `~/.config/droidtool` when it exists and the new configuration directory does not. The old `DROIDTOOL_CONFIG_DIR` override remains supported, with `APKHARBOR_CONFIG_DIR` taking precedence. Credentials are not copied, and an existing Play session can continue until it expires. Logout affects whichever configuration directory is in use.

## Search, inspect and download

```sh
apkharbor search "Signal" --limit 5
apkharbor info org.thoughtcrime.securesms --json
apkharbor download org.thoughtcrime.securesms --output ./downloads
apkharbor download org.videolan.vlc --locale en-US --locale de-DE
apkharbor download com.example.app --version-code 123 --arch arm64 --json
```

Downloads request the base APK, splits delivered for your authenticated device, and OBB or asset files returned by Play. A specific version works only if Google still serves it to that account and device. Acquisition registers free app entitlement through Play; this command does not perform a paid checkout. Already purchased apps still depend on the account's entitlement and Play's delivery behavior.

Output layout:

```text
downloads/<package>/<version-code>/<arch>/package/
  base.apk
  split-config.arm64_v8a.apk
  ...
  manifest.json
```

Downloads stream into a staging directory. The complete directory is published only after every file passes validation. Existing completed downloads are preserved; use a different `--output` directory to fetch the same version again. V1 does not resume incomplete transfers or retry them automatically.

Downloads can follow HTTPS redirects between Google download domains (`google.com`, `googleapis.com`, `googleusercontent.com`, `gvt1.com`, `gvt2.com`, including their subdomains) on port 443. On each change of host, request cookies and authentication/device headers are removed. Account and API requests retain the stricter same-origin redirect rule. HTTPS-to-HTTP redirects and unrelated download domains are rejected.

Each manifest records local SHA-256 digests and sizes. APKs are checked as ZIP archives with an Android manifest; upstream SHA-256/SHA-1 and sizes are checked when delivery metadata supplies them. `upstream_verified: false` means Play did not supply a digest for that file. Local hashes detect later changes; they are not APK signing-certificate verification. No credentials, cookies or download URLs are written into manifests.

## Install

Install Android SDK Platform Tools, enable USB debugging and authorize the connected device. Pass the manifest printed by `download` and the absolute path to your trusted SDK executable:

```sh
apkharbor install ./downloads/com.example.app/123/arm64/package/manifest.json \
  --adb /absolute/path/to/android-sdk/platform-tools/adb \
  --serial YOUR_ADB_DEVICE_SERIAL
```

On Windows (PowerShell), for example:

```powershell
apkharbor install .\downloads\com.example.app\123\arm64\package\manifest.json --adb "C:\Android\Sdk\platform-tools\adb.exe" --serial YOUR_ADB_DEVICE_SERIAL
```

Starting with 0.1.1, `--adb` is required. APKHarbor does not search PATH or the current directory for ADB, and rejects relative paths. Windows requires an `.exe` file, so batch and command scripts are not accepted. Choose an SDK installation you trust, outside untrusted package bundles; an explicit path does not authenticate the binary or protect an attacker-writable SDK directory. Existing scripts must add `--adb` when upgrading from 0.1.0.

Installation verifies manifest hashes and passes exactly its base and split APKs to `adb install-multiple -r`. It preserves the original APK signatures. The account's selected profile must be compatible with the target device. OBB and asset files are downloaded, but v1 refuses automatic installation when they are present: their placement and delivery require separate handling. ADB installation does not automatically install on-device feature modules that Play did not return.

## Development and validation

```sh
python3 -m unittest discover -s tests -v
```

The offline suite covers direct Google device registration and token exchange, Google-only destinations, token non-persistence, secure hidden prompts, Google failure/terms handling, session import/pairing, credential permissions, downloads, manifests and ADB selection. Network responses and ADB are mocked. No live Google account or physical device was available during development; Google sign-in, real package downloads and device installation remain to be verified with your account and device.

Google Play access here uses an unofficial protocol. Endpoint changes, account restrictions, region rules, device targeting and account entitlements can affect availability. The protocol implementation derives from MIT-licensed gplaydl; see `THIRD_PARTY.md`.

## Roadmap

- Follow-up: live-account compatibility testing, encrypted OS credential storage, device discovery and download resume.
- v2: Aurora anonymous authentication as an explicit alternative provider, subject to service availability; retain personal-account authentication.

This project has not been published to a package registry. `apkharbor-cli` is the local distribution name; registry name availability has not been checked.
