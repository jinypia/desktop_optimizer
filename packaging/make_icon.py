"""Generate assets/app.ico from the app's own tray-icon artwork.

Renders the same rounded tile + status dot the tray uses (healthy green)
at several sizes with Qt, then packs them into a multi-resolution .ico
with Pillow. Run from the project root:
    python packaging/make_icon.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image                                    # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from app.ui.tray import status_icon                      # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "app.ico")


def render(size: int, tmpdir: str) -> Image.Image:
    pixmap = status_icon("good", size=size).pixmap(size, size)
    path = os.path.join(tmpdir, f"icon_{size}.png")
    if not pixmap.save(path, "PNG"):
        raise RuntimeError(f"Qt failed to write {path}")
    with Image.open(path) as img:
        return img.convert("RGBA")


def main() -> int:
    app = QApplication(sys.argv)                         # noqa: F841
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        images = [render(s, tmp) for s in SIZES]
        # Pillow writes a proper multi-size ICO from the largest image
        images[-1].save(OUT, format="ICO",
                        sizes=[(s, s) for s in SIZES],
                        append_images=images[:-1])
    print(f"wrote {OUT} ({os.path.getsize(OUT):,} bytes, sizes {SIZES})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
