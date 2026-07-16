#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree


OWNER = "SuperMonster002"
REPO = "Auto.js-SDK-Plugins-Garage"
BRANCH = "main"
OUTPUT_FILE = "plugins.generated.json"
IGNORED_TOP_LEVEL_DIRS = {".git", ".idea", ".github", "tools"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the AutoJs6 third-party plugin index.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    root = args.root.resolve()
    output = (args.output or root / OUTPUT_FILE).resolve()

    items = []
    for package_dir in sorted(iter_plugin_dirs(root), key=lambda path: path.name.lower()):
        entry = build_entry(root, package_dir)
        if entry is not None:
            items.append(entry)

    payload = {
        "schemaVersion": 1,
        "source": "THIRD_PARTY",
        "repository": {
            "owner": OWNER,
            "repo": REPO,
            "branch": BRANCH,
        },
        "items": items,
    }

    output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {output} with {len(items)} plugin entries.")
    return 0


def iter_plugin_dirs(root: Path):
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in IGNORED_TOP_LEVEL_DIRS:
            continue
        yield child


def build_entry(root: Path, package_dir: Path) -> dict | None:
    candidates = []
    for apk_path in package_dir.rglob("*.apk"):
        parsed = parse_garage_apk_file_name(apk_path.name)
        if parsed is None:
            continue
        original_name, version_name, version_code = parsed
        candidates.append(
            {
                "path": apk_path,
                "fileName": apk_path.name,
                "originalName": original_name,
                "versionName": version_name,
                "versionCode": version_code,
                "size": apk_path.stat().st_size,
            }
        )
    if not candidates:
        return None

    latest = max(candidates, key=lambda item: (item["versionCode"], item["size"], item["fileName"]))
    index_config = read_index_config(package_dir / "index.json", latest["versionCode"])
    localized_descriptions = read_localized_descriptions(package_dir)
    instruction_markdown_urls = read_instruction_markdown_urls(root, package_dir)

    icon_path = resolve_icon_path(package_dir)
    night_icon_path = resolve_night_icon_path(package_dir)
    release = {
        "versionName": select_non_blank(index_config.get("versionName"), latest["versionName"]),
        "versionCode": latest["versionCode"],
        "versionDate": index_config.get("versionDate"),
        "apkUrl": github_raw_blob_url(root_relative_path(root, latest["path"])),
        "apkSha256": sha256_file(latest["path"]),
        "apkSizeBytes": latest["size"],
        "changelogUrl": None,
        "changelogText": "No release notes",
    }

    entry = {
        "packageName": package_dir.name,
        "iconUrl": raw_github_url(root_relative_path(root, icon_path)) if icon_path else None,
        "nightIconUrl": raw_github_url(root_relative_path(root, night_icon_path)) if night_icon_path else None,
        "forceIgnoreLocalIcon": bool(index_config.get("forceIgnoreLocalIcon", False)),
        "title": select_non_blank(index_config.get("name"), latest["originalName"], package_dir.name),
        "description": choose_default_localized(localized_descriptions) or index_config.get("description"),
        "localizedDescriptions": localized_descriptions,
        "instructionHardCoded": index_config.get("instruction"),
        "instructionMarkdownUrl": choose_default_localized(instruction_markdown_urls),
        "localizedInstructionMarkdownUrls": instruction_markdown_urls,
        "author": index_config.get("author"),
        "collaborators": [],
        "engine": index_config.get("engine"),
        "variant": index_config.get("variant"),
        "engineId": index_config.get("engineId"),
        "releases": [release],
        "supportedAbis": index_config.get("supportedAbis"),
        "tags": ["third-party"],
        "requiresHostVersion": index_config.get("requiresHostVersion"),
        "source": "THIRD_PARTY",
    }
    return prune_nulls(entry)


def parse_garage_apk_file_name(file_name: str):
    if not file_name.lower().endswith(".apk"):
        return None
    base = file_name[:-4]
    suffix = re.search(r"-(?:build|buid)-(\d+)$", base, flags=re.IGNORECASE)
    if suffix is None:
        return None
    version_code = int(suffix.group(1))
    without_suffix = base[: suffix.start()] + base[suffix.end() :]
    split = without_suffix.rfind("-")
    if split <= 0 or split >= len(without_suffix) - 1:
        return None
    original_name = without_suffix[:split]
    version_name = without_suffix[split + 1 :]
    if not original_name or not version_name:
        return None
    return original_name, version_name, version_code


def read_index_config(path: Path, version_code: int) -> dict:
    if not path.exists():
        return {}
    try:
        root = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: failed to read {path}: {exc}", file=sys.stderr)
        return {}

    by_version = root.get(str(version_code)) if isinstance(root.get(str(version_code)), dict) else {}
    defaults = root.get("0") if isinstance(root.get("0"), dict) else {}
    capabilities = first_dict(by_version.get("capabilities"), defaults.get("capabilities"))

    return prune_nulls(
        {
            "name": select_string(by_version, defaults, "name"),
            "author": select_string(by_version, defaults, "author"),
            "description": select_string(by_version, defaults, "description"),
            "instruction": select_string(by_version, defaults, "instruction"),
            "versionName": select_string(by_version, defaults, "versionName"),
            "versionDate": select_string(by_version, defaults, "versionDate"),
            "requiresHostVersion": coerce_positive_int(capabilities.get("requiresHostVersion") if capabilities else None),
            "forceIgnoreLocalIcon": select_bool(by_version, defaults, "forceIgnoreLocalIcon")
            if select_bool(by_version, defaults, "forceIgnoreLocalIcon") is not None
            else bool(select_bool(by_version, defaults, "preferRemoteIcon") or False),
            "supportedAbis": resolve_supported_abis(by_version, defaults, capabilities),
            "engine": select_string(by_version, defaults, "engine"),
            "variant": select_string(by_version, defaults, "variant"),
            "engineId": select_string(by_version, defaults, "engineId"),
        }
    )


def select_string(primary: dict, fallback: dict, key: str):
    for source in (primary, fallback):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def select_bool(primary: dict, fallback: dict, key: str):
    for source in (primary, fallback):
        if key not in source:
            continue
        parsed = parse_bool(source.get(key))
        if parsed is not None:
            return parsed
    return None


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def resolve_supported_abis(by_version: dict, defaults: dict, capabilities: dict | None):
    for source in (by_version, defaults, capabilities or {}):
        for key in ("supportedAbis", "abis", "abi"):
            if key not in source:
                continue
            resolved = normalize_abi_value(source.get(key))
            if resolved is not None:
                return resolved
    return None


def normalize_abi_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;|\s]+", value) if part.strip()]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        return None
    return parts if parts else None


def read_localized_descriptions(package_dir: Path) -> dict:
    result = {}
    res_dir = package_dir / "res"
    if not res_dir.exists():
        return result
    for strings_path in sorted(res_dir.glob("values*/strings.xml")):
        description = parse_string_resource(strings_path, "plugin_description")
        if description:
            result[strings_path.parent.name] = description
    return result


def parse_string_resource(path: Path, name: str):
    try:
        root = ElementTree.fromstring(path.read_text(encoding="utf-8-sig"))
    except (OSError, ElementTree.ParseError) as exc:
        print(f"Warning: failed to parse {path}: {exc}", file=sys.stderr)
        return None
    for element in root.findall("string"):
        if element.attrib.get("name") != name:
            continue
        text = "".join(element.itertext()).strip()
        return html.unescape(text) or None
    return None


def read_instruction_markdown_urls(root: Path, package_dir: Path) -> dict:
    result = {}
    res_dir = package_dir / "res"
    if not res_dir.exists():
        return result
    for markdown_path in sorted(res_dir.glob("raw*/plugin_instruction.md")):
        result[markdown_path.parent.name] = raw_github_url(root_relative_path(root, markdown_path))
    return result


def resolve_icon_path(package_dir: Path):
    names = ("ic_launcher", "ic_launcher_round", "plugin_icon")
    exts = ("png", "webp", "jpg", "jpeg")
    dirs = (
        "res/mipmap",
        "res/mipmap-anydpi-v26",
        "res/mipmap-xxxhdpi",
        "res/mipmap-xxhdpi",
        "res/mipmap-xhdpi",
        "res/mipmap-hdpi",
        "res/mipmap-mdpi",
        "res/drawable",
        "res/drawable-anydpi-v26",
        "res/drawable-xxxhdpi",
        "res/drawable-xxhdpi",
        "res/drawable-xhdpi",
        "res/drawable-hdpi",
        "res/drawable-mdpi",
    )
    return first_existing_named_asset(package_dir, dirs, names, exts)


def resolve_night_icon_path(package_dir: Path):
    names = ("ic_launcher", "ic_launcher_round", "plugin_icon")
    exts = ("png", "webp", "jpg", "jpeg")
    dirs = ("res/mipmap-night", "res/drawable-night")
    return first_existing_named_asset(package_dir, dirs, names, exts)


def first_existing_named_asset(package_dir: Path, dirs, names, exts):
    for directory in dirs:
        for name in names:
            for ext in exts:
                candidate = package_dir / directory / f"{name}.{ext}"
                if candidate.exists():
                    return candidate
    return None


def raw_github_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/{OWNER}/{REPO}/refs/heads/{BRANCH}/{quote(path, safe='/')}"


def github_raw_blob_url(path: str) -> str:
    return f"https://github.com/{OWNER}/{REPO}/raw/refs/heads/{BRANCH}/{quote(path, safe='/')}"


def root_relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_default_localized(values: dict):
    for key in ("values-en", "values", "en", "default"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return next((value for value in values.values() if isinstance(value, str) and value.strip()), None)


def select_non_blank(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def first_dict(*values):
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def coerce_positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def prune_nulls(value):
    if isinstance(value, dict):
        return {key: prune_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [prune_nulls(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
