"""Fetch pinned compiled models; requires a completed Modalix release manifest."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def load_manifest(path):
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Expected a schema_version 1 manifest")
    if manifest.get("target") != "modalix":
        raise ValueError("The release must target Modalix")
    for field in ("release", "neat_version", "platform_version"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip() or value.lower() == "latest":
            raise ValueError(f"Maintainer must fill {field} with the validated release version")
    models = manifest.get("models")
    if not isinstance(models, dict) or set(models) != {"people", "ppe"}:
        raise ValueError("The manifest must contain exactly the people and ppe models")
    filenames = set()
    for role, model in models.items():
        if not isinstance(model, dict):
            raise ValueError(f"Invalid {role} model entry")
        filename = model.get("filename", "")
        if not isinstance(filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*\.tar\.gz", filename
        ):
            raise ValueError(f"{role}: filename must be a compiled .tar.gz package basename")
        if filename in filenames:
            raise ValueError("Model filenames must be distinct")
        filenames.add(filename)
        checksum = model.get("sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError(f"{role}: maintainer must supply the artifact's SHA-256")
        url = model.get("url")
        if not isinstance(url, str):
            raise ValueError(f"{role}: maintainer must supply the published artifact URL")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{role}: expected a credential-free HTTPS artifact URL")
        if role == "people":
            valid = parsed.netloc == "docs.sima.ai" and re.fullmatch(
                r"/pkg_downloads/SDK[0-9][A-Za-z0-9_.-]*/models/modalix/yolo26-detection/"
                + re.escape(filename),
                parsed.path,
            )
        else:
            match = re.fullmatch(
                r"/[^/]+/[^/]+/resolve/([0-9a-f]{40})/" + re.escape(filename), parsed.path
            )
            valid = parsed.netloc == "huggingface.co" and match
        if not valid:
            raise ValueError(
                f"{role}: use a versioned {'SiMa Model Zoo' if role == 'people' else 'Hugging Face commit-pinned'} URL"
            )
    return manifest


def fetch(model, destination, verify_only=False):
    path = destination / model["filename"]
    if path.is_file() and digest(path) == model["sha256"]:
        return path
    if verify_only:
        raise ValueError(f"Missing or checksum mismatch: {path}")
    destination.mkdir(parents=True, exist_ok=True)
    # Stage beside the final file so replacement is atomic. Never extract archives.
    with tempfile.TemporaryDirectory(prefix=".model-download-", dir=destination) as directory:
        stage = Path(directory) / model["filename"]
        if urlsplit(model["url"]).netloc == "docs.sima.ai":
            if not shutil.which("sima-cli"):
                raise ValueError(
                    "Install sima-cli and log in before downloading the Model Zoo package"
                )
            subprocess.run(
                ["sima-cli", "download", model["url"], "--dest", directory],
                check=True,
                timeout=1800,
                env={**os.environ, "SIMA_CLI_CHECK_FOR_UPDATE": "0"},
            )
        else:
            with urlopen(model["url"], timeout=60) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Refusing a non-HTTPS release redirect")
                with stage.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
        if not stage.is_file() or digest(stage) != model["sha256"]:
            raise ValueError(
                f"Checksum verification failed for {model['filename']}; existing file retained"
            )
        stage.replace(path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "models/modalix-release.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-manifest", action="store_true", help="Check metadata without downloading"
    )
    mode.add_argument(
        "--verify-only", action="store_true", help="Verify local artifacts without network access"
    )
    args = parser.parse_args(argv)
    try:
        # Validate the complete manifest before downloading either artifact.
        manifest = load_manifest(args.manifest)
        if args.check_manifest:
            print(f"Manifest valid: {manifest['release']} (artifact contents not checked)")
            return 0
        for role, model in manifest["models"].items():
            path = fetch(model, args.output_dir, args.verify_only)
            print(f"Verified {role}: {path}")
        print("Both checksums verified. Device execution and accuracy require separate validation.")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Model preparation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
