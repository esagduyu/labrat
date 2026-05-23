"""Banner rendering for LabRat.

Renders the LabRat banner via rich-pyfiglet by default; falls back to the
override file at assets/banner_{variant}.txt when provided.
"""

from pathlib import Path

from rich.console import Console, RenderableType
from rich.text import Text
from rich_pyfiglet import RichFiglet

ASSETS_DIR = Path(__file__).parent / "assets"

# font, colors per variant
_FIGLET_CONFIG: dict[str, tuple[str, list[str]]] = {
    "splash": ("ansi_shadow", ["cyan"]),
    "header": ("slant", ["cyan"]),
    "status": ("small", ["cyan"]),
}


def get_banner_renderable(
    variant: str = "splash",
    *,
    assets_dir: Path | None = None,
) -> RenderableType:
    """Return a rich renderable for the given banner variant.

    Checks assets_dir for a banner_{variant}.txt override file first.
    Falls back to rich-pyfiglet rendering.
    """
    resolved_dir = assets_dir if assets_dir is not None else ASSETS_DIR
    override_path = resolved_dir / f"banner_{variant}.txt"
    if override_path.exists():
        return Text(override_path.read_text())
    return _make_figlet_renderable(variant)


def render_banner(
    console: Console,
    variant: str = "splash",
    *,
    assets_dir: Path | None = None,
) -> None:
    """Render the LabRat banner to the given console."""
    console.print(get_banner_renderable(variant, assets_dir=assets_dir))


def _make_figlet_renderable(variant: str) -> RenderableType:
    font, colors = _FIGLET_CONFIG.get(variant, ("slant", ["cyan"]))
    return RichFiglet("LabRat", font=font, colors=colors)  # type: ignore[return-value]
