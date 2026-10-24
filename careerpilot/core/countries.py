"""Country packs: countries/<code>/pack.yaml. Conventions (paper, photo, birth date, CV length, letter format)
follow the country of the JOB, not the person. Countries without a pack get the worldwide defaults."""
from functools import lru_cache
from pathlib import Path

from ruamel.yaml import YAML

PACKS = Path(__file__).parent.parent / "countries"
DEFAULT = {"paper": "a4", "photo": "avoid", "dob": "avoid", "cv_pages": 2, "letter": "modern"}
LETTER_PAPER = {"US", "CA", "MX", "PH", "CL", "CO", "VE", "GT", "PR"}  # countries that use US Letter


@lru_cache
def pack(code: str) -> dict:
    path = PACKS / (code or "").lower() / "pack.yaml"
    return YAML(typ="safe").load(path) if path.exists() else {}


def conventions(code: str) -> dict:
    code = (code or "").upper()
    conv = {**DEFAULT, "paper": "us-letter" if code in LETTER_PAPER else "a4"}
    if code in LETTER_PAPER or code in {"GB", "IE", "AU", "NZ"}:
        conv["cv_pages"] = 2
    return {**conv, **pack(code).get("conventions", {})}
