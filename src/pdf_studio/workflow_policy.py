"""File availability and independent output-path checks. No product workflow gates."""
from pathlib import Path


def signing_block_reason(*, busy, b_accessible):
    if busy:
        return '后台任务处理中，请等待完成。'
    if not b_accessible:
        return '请先打开或导入要处理的 PDF。'
    return ''


def signing_output_block_reason(target, sources):
    """Single output rule shared by preparation, selection and execution UI."""
    path = Path(target)
    try:
        if path.suffix.lower() != '.pdf':
            return '请选择 .pdf 输出文件。'
        if path.exists() or path.resolve() in {Path(p).resolve() for p in sources if p}:
            return '不能覆盖 A / B 或已有文件；请选择新的输出文件名。'  # 限制：签署输出不能覆盖 A、B 或已有文件
        if not path.parent.is_dir():
            return '输出目录不存在，请重新选择。'
    except (OSError, ValueError) as exc:
        return '无法访问输出位置：' + str(exc)
    return ''
