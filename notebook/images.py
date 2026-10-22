import hashlib
import io
import warnings
from pathlib import Path

import fitz
from PIL import Image, ImageOps, UnidentifiedImageError
import pillow_heif

pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 30_000_000
MAX_UPLOAD = 100 * 1024 * 1024


def raster(data):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img = ImageOps.exif_transpose(img)
                if img.mode in ('RGBA', 'LA') or 'transparency' in img.info:
                    rgba = img.convert('RGBA')
                    background = Image.new('RGBA', rgba.size, 'white')
                    img = Image.alpha_composite(background, rgba)
                return img.convert('RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError, ValueError) as e:
            raise ValueError('Изображение повреждено, слишком велико или имеет неподдерживаемый формат') from e


def png(img):
    stream = io.BytesIO()
    img.save(stream, 'PNG')
    return stream.getvalue()


def fingerprint(img):
    gray = img.convert('L').resize((9, 8))
    vals = list(gray.getdata())
    bits = sum((vals[y*9+x] > vals[y*9+x+1]) << (y*8+x) for y in range(8) for x in range(8))
    return f'{bits:016x}'


def decode_upload(data, filename):
    ext = Path(filename).suffix.lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.pdf'):
        raise ValueError('Допустимы JPG, PNG, HEIC и PDF')
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError('Файл пуст или превышает 100 МБ')
    if ext == '.pdf':
        try:
            with fitz.open(stream=data, filetype='pdf') as pdf:
                if pdf.needs_pass:
                    raise ValueError('PDF защищён паролем')
                if not 1 <= len(pdf) <= 50:
                    raise ValueError('В одном PDF должно быть от 1 до 50 страниц')
                results = []
                for page in pdf:
                    if page.rect.width < 1 or page.rect.height < 1:
                        raise ValueError('Неверный размер страницы PDF')
                    scale = min(2, 2400 / max(page.rect.width, page.rect.height))
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
                    results.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
                return results
        except (fitz.FileDataError, RuntimeError) as e:
            raise ValueError('Повреждённый PDF') from e
    return [raster(data)]


def crop(store, page, bbox):
    with Image.open(store.path(page['image'])) as img:
        if bbox:
            x, y, w, h = bbox
            box = (int(x*img.width), int(y*img.height), min(img.width, max(int((x+w)*img.width), int(x*img.width)+1)), min(img.height, max(int((y+h)*img.height), int(y*img.height)+1)))
            img = img.crop(box)
        return png(img.convert('RGB'))


def digest(data):
    return hashlib.sha256(data).hexdigest()
