"""Versioned prompt loader.

Looks up `prompts/{name}_v{version}.md`, supports `{PLACEHOLDER}`
interpolation. Kept deliberately small — the value here is *that* prompts
are versioned files, not how cleverly we load them.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PromptLibrary:
    def __init__(self, root: Path | str = PROMPTS_DIR) -> None:
        self.root = Path(root)

    def load(self, name: str, version: int = 1, **substitutions: str) -> str:
        """Return the prompt text, optionally with `{KEY}` substitutions.

        Substitution uses `string.Template`'s safe substitution so unknown
        placeholders are left intact instead of raising. That's the right
        default for prompts that may contain literal braces.
        """
        path = self.root / f"{name}_v{version}.md"
        if not path.is_file():
            raise FileNotFoundError(f"prompt not found: {path}")
        raw = path.read_text(encoding="utf-8")
        if not substitutions:
            return raw
        # Template uses $KEY; we use {KEY} in prompts. Translate.
        translated = raw
        for key, value in substitutions.items():
            translated = translated.replace("{" + key + "}", value)
        return translated
