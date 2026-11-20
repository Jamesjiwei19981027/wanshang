"""Verify the container's actual native dependencies and required image codecs.

Run directly during the image build and from the isolated Linux smoke check.
This module does not load Django settings or read application credentials.
"""

import importlib.util
import io
import json
from pathlib import Path
import ssl
import sys
import zlib

import PIL
from PIL import Image, features
from psycopg import pq


EXPECTED_APK_VERSIONS = {
    "libpq": "18.7-r0",
    "libcrypto3": "3.5.9-r1",
    "libssl3": "3.5.9-r1",
    # Upstream zlib 1.4 is later than the CVE-2026-85091 fix that 1.3.2-r1 backported.
    "zlib": "1.4-r0",
}
FORBIDDEN_PACKAGES = {
    "glibc", "glibc-bin", "glibc-utils", "gcompat",
    "gcc", "g++", "binutils", "build-base", "libstdc++", "libstdc++-dev",
    "krb5", "krb5-libs", "krb5-server", "libgssapi", "libgssapi-krb5",
    "acl", "libacl", "libacl1", "util-linux", "util-linux-misc", "libmount",
    "perl", "perl-base", "perl-modules",
    "tiff", "libtiff", "openjpeg", "libavif", "libxcb",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def installed_apk_packages():
    packages = {}
    for record in Path("/lib/apk/db/installed").read_text().split("\n\n"):
        fields = dict(
            line.split(":", 1) for line in record.splitlines() if ":" in line
        )
        if "P" in fields and "V" in fields:
            packages[fields["P"]] = fields["V"]
    return packages


def verify_runtime():
    require(sys.platform == "linux", "Container verification requires Linux.")
    require(sys.version_info[:3] == (3, 13, 18), "Unexpected Python runtime.")
    require(
        Path("/etc/alpine-release").read_text().strip().startswith("3.24."),
        "The container must use the supported Alpine 3.24 branch.",
    )
    packages = installed_apk_packages()
    for name, version in EXPECTED_APK_VERSIONS.items():
        require(packages.get(name) == version, f"Unexpected native package: {name}.")
    unexpected = sorted(FORBIDDEN_PACKAGES.intersection(packages))
    require(not unexpected, f"Unneeded native/toolchain packages present: {unexpected}.")
    require(pq.__impl__ == "c", "Use the locally compiled psycopg C binding.")
    require(pq.version() == 180007, "Unexpected system libpq version.")
    require(
        importlib.util.find_spec("psycopg_binary") is None,
        "A private binary-wheel libpq/OpenSSL must not be installed.",
    )
    require(
        not (Path(PIL.__file__).parent.parent / "pillow.libs").exists(),
        "Use the source-built Pillow instead of bundled native wheel libraries.",
    )
    for feature in ("jpg", "zlib", "webp"):
        require(features.check(feature), f"Required image codec missing: {feature}.")
    for feature in ("libtiff", "jpg_2000", "littlecms2", "freetype2", "raqm", "xcb", "avif"):
        require(not features.check(feature), f"Unneeded image codec enabled: {feature}.")

    # Exercise the actual codec implementations and all registered plugins, so
    # missing libraries or accidentally reintroduced C++ dependencies fail here.
    Image.init()
    for image_format in ("PNG", "JPEG", "WEBP"):
        encoded = io.BytesIO()
        Image.new("RGB", (2, 2), color="white").save(encoded, format=image_format)
        encoded.seek(0)
        with Image.open(encoded, formats=[image_format]) as image:
            image.load()
            require(image.size == (2, 2), f"Image codec round-trip failed: {image_format}.")

    loaded_libraries = sorted({
        line.split()[-1]
        for line in Path("/proc/self/maps").read_text().splitlines()
        if ".so" in line and "/" in line
    })
    forbidden_libraries = (
        "libstdc++", "libkrb5", "libgssapi", "libacl", "libmount",
        "libtiff", "libopenjp2", "libavif", "libxcb",
    )
    require(
        not any(marker in library for library in loaded_libraries for marker in forbidden_libraries),
        "Unexpected optional native library loaded by the application.",
    )
    return {
        "python": sys.version.split()[0],
        "alpine": Path("/etc/alpine-release").read_text().strip(),
        "native_packages": packages,
        "openssl": ssl.OPENSSL_VERSION,
        "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION,
        "psycopg_implementation": pq.__impl__,
        "libpq": pq.version(),
        "pillow": PIL.__version__,
        "image_roundtrips": ["PNG", "JPEG", "WEBP"],
        "loaded_libraries": loaded_libraries,
    }


if __name__ == "__main__":
    print(json.dumps(verify_runtime(), sort_keys=True))
