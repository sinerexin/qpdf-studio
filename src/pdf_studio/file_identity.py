"""界面与后台共用的文件版本标识；这不是密码学摘要或验签结果。"""
from pathlib import Path


def file_identity(path):
    """用同一格式关联实际文件，避免调用方之间三项/六项比较导致误报变化。"""
    source = Path(path).resolve(strict=True)
    stat = source.stat()
    return (source, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_dev, stat.st_ino)
