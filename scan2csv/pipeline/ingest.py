from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from scan2csv.pipeline.io_util import copy_in


def normalize_to_png(src: Path, dest_png: Path, thumb_jpg: Path, *, thumb_side: int = 1280) -> tuple[Path, Path]:
    dest_png.parent.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            rgb = im.convert("RGB")
            rgb.save(dest_png, format="PNG")
            th = rgb.copy()
            th.thumbnail((thumb_side, thumb_side))
            th.save(thumb_jpg, format="JPEG", quality=85, optimize=True)
    elif suffix == ".pdf":
        # best-effort via pdftoppm if present
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out_prefix = Path(td) / "page"
            cmd = ["pdftoppm", "-png", "-r", "200", "-f", "1", "-l", "1", str(src), str(out_prefix)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"pdftoppm failed: {r.stderr[:500]}")
            pages = sorted(Path(td).glob("page*.png"))
            if not pages:
                raise RuntimeError("pdftoppm produced no pages")
            return normalize_to_png(pages[0], dest_png, thumb_jpg, thumb_side=thumb_side)
    else:
        # try PIL anyway
        copy_in(src, dest_png.with_suffix(src.suffix))
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            rgb = im.convert("RGB")
            rgb.save(dest_png, format="PNG")
            th = rgb.copy()
            th.thumbnail((thumb_side, thumb_side))
            th.save(thumb_jpg, format="JPEG", quality=85, optimize=True)
    return dest_png, thumb_jpg
