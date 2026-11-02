# Third-party code

APKHarbor includes Google Play FDFE response parsing and a protobuf decoder adapted from [rehmatworks/gplaydl](https://github.com/rehmatworks/gplaydl), pinned at commit `b21b18916f4bfd20cfdd85214c39f80ae58b7a61`.

- `apkharbor/_vendor/play.py`: adapted from `gplaydl/api.py`. Uses APKHarbor's standard-library transport and header construction, encodes search queries and delivery tokens, and requires structured delivery metadata instead of falling back to URL discovery.
- `apkharbor/_vendor/protobuf.py`: vendored protobuf decoder, with corrected length-delimited group skipping and bounds checks.
- `apkharbor/auth.py`: header constants and personal-account pairing protocol adapted from `gplaydl/auth.py` and `gplaydl/onboarding.py`.
- `apkharbor/default-device.json`: fallback device properties adapted from `gplaydl/profiles.py`. Aurora profile collections and anonymous token access are not bundled.

Upstream is MIT licensed, copyright (c) 2021 Rehmat Alam. The full notice is included in `apkharbor/_vendor/GPLAYDL_LICENSE.txt` and packaged in distributions.

Direct Google authentication in `apkharbor/google_auth.py` is a Python implementation of the interoperability flow documented by [EFForg/apkeep](https://github.com/EFForg/apkeep/blob/master/USAGE-google-play.md), [EFForg/rs-google-play](https://github.com/EFForg/rs-google-play) (reference commit `eb423a3edfc653a1d533d4b7f6c9148d5bc144a8`), and [simon-weber/gpsoauth](https://github.com/simon-weber/gpsoauth). It uses the existing protobuf decoder, Google's wire field numbers and public Android client identifiers. It does not bundle those clients, automate browser cookie extraction, or depend on their token servers. Their documentation does not establish live compatibility of this implementation.
