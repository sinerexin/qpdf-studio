"""累计编辑副本的保存；不解析或重写 PDF，也不生成修改记录文件。"""
from pathlib import Path
import os
import shutil
import uuid


def save_working_copy(source, destination, original=None):
    """把当前完整结果原样保存；重复保存原子替换同一个副本，A 原稿不覆盖。"""
    src = Path(source).resolve(strict=True)
    dst = Path(destination).resolve()
    if original is not None and dst == Path(original).resolve():
        raise ValueError('请选择工作副本路径，A 原稿保持不变。')
    if src == dst:
        return dst
    temporary = dst.with_name('.' + dst.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        # 先写完整临时文件，再替换副本；写入失败不会把上一次已保存结果截断。
        with src.open('rb') as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, dst)
    finally:
        temporary.unlink(missing_ok=True)
    return dst
