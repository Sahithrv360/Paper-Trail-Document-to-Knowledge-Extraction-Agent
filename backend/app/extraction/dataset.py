"""Dataset access. Network/image libraries are imported lazily so that local-split
loading and unit tests work without the optional extraction dependencies."""

import json
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse


DATASET_ID = "katanaml-org/invoices-donut-data-v1"
ROWS_API = "https://datasets-server.huggingface.co/rows"
SPLITS_API = "https://datasets-server.huggingface.co/splits"


def dataset_revision() -> str:
    from huggingface_hub import HfApi

    return HfApi().dataset_info(DATASET_ID, revision="main").sha


def available_splits() -> list[str]:
    import requests

    response = requests.get(SPLITS_API, params={"dataset": DATASET_ID}, timeout=30)
    response.raise_for_status()
    return [split["split"] for split in response.json().get("splits", [])]


def fetch_rows(split: str, length: int) -> tuple[str, list[dict], int]:
    import requests

    if length < 1 or length > 100:
        raise ValueError("length must be between 1 and 100 rows")
    revision = dataset_revision()
    response = requests.get(
        ROWS_API,
        params={"dataset": DATASET_ID, "config": "default", "split": split,
                "offset": 0, "length": length},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("rows", [])
    if not rows:
        raise RuntimeError(f"Hugging Face returned no rows for split {split!r}")
    # Viewer image URLs embed the revision used to generate that page. Fail closed
    # rather than pairing labels and images from different dataset versions.
    image_url = rows[0].get("row", {}).get("image", {}).get("src", "")
    match = re.search(r"/--/([0-9a-f]{40})/--/", urlparse(image_url).path)
    if match and match.group(1) != revision:
        raise RuntimeError(
            "Dataset viewer cache revision differs from the dataset repository; retry later"
        )
    return revision, rows, int(payload.get("num_rows_total", len(rows)))


def fetch_image(image_info: dict):
    import requests
    from PIL import Image

    url = image_info.get("src", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "datasets-server.huggingface.co":
        raise ValueError("Refusing to fetch an image from an unexpected host")
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    with Image.open(BytesIO(response.content)) as image:
        return image.convert("RGB")


def load_local_split(data_root: Path, split: str) -> tuple[list[dict], Path]:
    """Load downloaded image/annotation pairs from data/raw, without network access."""
    if split not in {"train", "validation", "test"}:
        raise ValueError(f"Unsupported dataset split: {split}")
    split_dir = (Path(data_root) / split).resolve()
    manifest_path = split_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Dataset manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, list):
        raise ValueError(f"Dataset manifest must contain a list: {manifest_path}")
    examples = []
    for entry in manifest:
        if not isinstance(entry, dict) or "row_index" not in entry:
            raise ValueError(f"Invalid manifest entry in {manifest_path}")
        file_name = Path(str(entry.get("file", "")))
        if file_name.name != str(entry.get("file", "")):
            raise ValueError(f"Manifest contains an unsafe file path: {file_name}")
        image_path = (split_dir / file_name).resolve()
        if image_path.parent != split_dir or not image_path.is_file():
            raise FileNotFoundError(f"Dataset image missing or outside split folder: {image_path}")
        examples.append({
            "row_index": int(entry["row_index"]),
            "image_path": image_path,
            "ground_truth": entry.get("ground_truth"),
        })
    examples.sort(key=lambda example: example["row_index"])
    if len({example["row_index"] for example in examples}) != len(examples):
        raise ValueError(f"Duplicate row indices in dataset manifest: {manifest_path}")
    return examples, split_dir
