import hashlib
import json
from pathlib import Path


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_digest(root):
    return digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted((root / "src/guiqa").rglob("*.py"))})
