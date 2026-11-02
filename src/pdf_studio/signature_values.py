"""解析结果的纯值快照；不复制 reader、文件句柄或解析器的内部引用。"""
from datetime import date, datetime
from decimal import Decimal

from pyhanko.pdf_utils import generic


def detached_values(value):
    """逐项取值，不用 deepcopy/pickle，也不遍历对象的 __dict__ 或 container_ref。"""
    if value is None or isinstance(value, generic.NullObject):
        return None
    if isinstance(value, (bool, generic.BooleanObject)):
        return bool(value)
    if isinstance(value, int):
        # NumberObject 是 int 的子类，但附带 container_ref；int() 只提取数字本身。
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        # NameObject/TextStringObject 同样可能携带解析器引用，必须转成真正的 str。
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, Decimal):
        # 保留 PDF 小数的十进制精度，同时去掉 FloatObject 子类的运行时属性。
        return Decimal(str(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, generic.IndirectObject):
        # 间接引用只保留对象号/代号，不解引用，也不复制它指向的 PDF reader。
        return f'{int(value.idnum)} {int(value.generation)} R'
    if isinstance(value, dict):
        return {str(key): detached_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [detached_values(child) for child in value]
    if isinstance(value, tuple):
        return tuple(detached_values(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return [detached_values(child) for child in value]
    # 意外出现的运行时对象不能冒充原始 PDF 数据；明确标明类型，不复制也不读取它。
    return {'runtime_object_not_copied': type(value).__module__ + '.' + type(value).__qualname__,
            'note': '运行时对象，非解析后的数据值；未复制、未读取其内部内容。'}
