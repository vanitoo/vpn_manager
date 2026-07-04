from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger(__name__)

SUPPORTED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


@dataclass(frozen=True)
class CoverProcessResult:
    cover_path: Path
    thumb_path: Path | None
    original_size: tuple[int, int]
    cover_size: tuple[int, int]
    thumb_size: tuple[int, int] | None


def _fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Center-crop image to exact target size."""
    return ImageOps.fit(img, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _prepare_rgb(img: Image.Image, background: str = 'white') -> Image.Image:
    """Convert source image to RGB. Transparent PNG/WEBP is composited over white."""
    img = ImageOps.exif_transpose(img)
    if img.mode in {'RGBA', 'LA'} or (img.mode == 'P' and 'transparency' in img.info):
        rgba = img.convert('RGBA')
        bg = Image.new('RGBA', rgba.size, background)
        return Image.alpha_composite(bg, rgba).convert('RGB')
    return img.convert('RGB')


def process_cover_image(
    source_path: str | Path,
    book_dir: str | Path,
    *,
    cover_width: int = 1200,
    cover_height: int = 1800,
    cover_quality: int = 90,
    generate_thumbnail: bool = True,
    thumb_width: int = 400,
    thumb_height: int = 600,
    min_width: int = 600,
    min_height: int = 900,
) -> CoverProcessResult:
    """
    Normalize a book cover:
    - accepts jpg/png/webp;
    - fixes EXIF rotation;
    - composites transparency over white;
    - center-crops to 2:3 target;
    - writes cover.jpg and cover_small.jpg.
    """
    source = Path(source_path)
    target_dir = Path(book_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(source) as opened:
            original_size = opened.size
            if original_size[0] < min_width or original_size[1] < min_height:
                log.warning(
                    'Cover source is small: %sx%s, recommended at least %sx%s',
                    original_size[0], original_size[1], min_width, min_height,
                )
            img = _prepare_rgb(opened)
            cover = _fit_cover(img, (cover_width, cover_height))
            cover_path = target_dir / 'cover.jpg'
            cover.save(cover_path, format='JPEG', quality=cover_quality, optimize=True, progressive=True)

            thumb_path: Path | None = None
            thumb_size: tuple[int, int] | None = None
            if generate_thumbnail:
                thumb = _fit_cover(img, (thumb_width, thumb_height))
                thumb_path = target_dir / 'cover_small.jpg'
                thumb.save(thumb_path, format='JPEG', quality=cover_quality, optimize=True, progressive=True)
                thumb_size = thumb.size

            return CoverProcessResult(
                cover_path=cover_path,
                thumb_path=thumb_path,
                original_size=original_size,
                cover_size=cover.size,
                thumb_size=thumb_size,
            )
    except UnidentifiedImageError as exc:
        raise ValueError('Не удалось прочитать изображение обложки. Поддерживаются JPG, PNG, WEBP.') from exc


def is_supported_image_filename(filename: str | None) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
