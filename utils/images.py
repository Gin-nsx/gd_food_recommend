# utils/images.py
# 商家上传的美食图片处理：校验类型与大小，居中裁成 4:3 并压缩后落盘。
# 统一输出 736×552 JPEG（和内置配图尺寸一致），避免前端卡片被拉伸变形。
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / 'static' / 'uploads' / 'foods'
UPLOAD_URL_PREFIX = 'uploads/foods'          # 存进数据库的 image_url 前缀

MAX_BYTES = 5 * 1024 * 1024                  # 单张最大 5MB
TARGET_SIZE = (736, 552)                     # 4:3
JPEG_QUALITY = 86
ALLOWED_FORMATS = ('JPEG', 'PNG', 'WEBP')


class ImageError(ValueError):
    """图片不合法，message 直接给用户看"""


def _normalize(file_storage):
    """读入内存 → 校验 → 转成 RGB 的 4:3 缩略图，返回 PIL Image"""
    raw = file_storage.read()
    if not raw:
        raise ImageError('图片内容为空')
    if len(raw) > MAX_BYTES:
        raise ImageError('图片不能超过 5MB')

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise ImageError('无法识别的图片文件，请上传 JPG / PNG / WebP')

    if img.format not in ALLOWED_FORMATS:
        raise ImageError('只支持 JPG / PNG / WebP 格式')

    if img.mode in ('RGBA', 'LA', 'P'):
        # 透明背景铺白底，否则转 RGB 会变黑
        img = img.convert('RGBA')
        canvas = Image.new('RGB', img.size, (255, 255, 255))
        canvas.paste(img, mask=img.split()[-1])
        img = canvas
    else:
        img = img.convert('RGB')

    # 居中裁剪成 4:3 再缩放，和内置配图的二次裁切保持一致的观感
    return ImageOps.fit(img, TARGET_SIZE, method=Image.LANCZOS, centering=(0.5, 0.5))


def save_food_image(file_storage) -> str:
    """保存上传的图片，返回可直接放进 foods.image_url 的相对路径"""
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        raise ImageError('请选择要上传的图片')

    img = _normalize(file_storage)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.jpg'
    img.save(UPLOAD_DIR / filename, 'JPEG', quality=JPEG_QUALITY, optimize=True)
    return f'{UPLOAD_URL_PREFIX}/{filename}'


def delete_food_image(image_url: str) -> None:
    """
    删除商家上传过的图片。只删 uploads/foods 下的文件，
    内置配图（images/foods/…）一律不动，防止误删。
    """
    if not image_url or not image_url.startswith(UPLOAD_URL_PREFIX + '/'):
        return
    name = image_url.rsplit('/', 1)[-1]
    # 只允许单纯的 uuid.jpg，挡掉 ../ 之类的路径穿越
    if name != Path(name).name:
        return
    target = UPLOAD_DIR / name
    try:
        target.unlink()
    except OSError:
        pass
