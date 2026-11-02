"""No automatically written edit logs; existing user files remain untouched.

Tests are authored here but not automatically executed per the user preference.
"""
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from pdf_studio.editing import edit_properties


def test_edit_does_not_create_or_overwrite_existing_change_record(tmp_path):
    source = tmp_path / 'original.pdf'
    c = Canvas(str(source))
    c.drawString(72, 720, 'Synthetic editable document')
    c.save()
    original = source.read_bytes()
    target = tmp_path / 'output.pdf'
    marker = target.with_suffix('.pdf.changes.json')
    marker.write_bytes(b'existing user file must stay unchanged')
    result = edit_properties(source, target, {'Title': 'New title'})
    assert target.is_file()
    assert source.read_bytes() == original
    assert marker.read_bytes() == b'existing user file must stay unchanged'
    assert result['properties_changed']['Title']['after'] == 'New title'
    second = tmp_path / 'second.pdf'
    edit_properties(source, second, {'Title': 'Another title'})
    assert not second.with_suffix('.pdf.changes.json').exists()


def test_no_change_record_file_paths_in_runtime_source():
    import pdf_studio
    source_directory = Path(pdf_studio.__file__).parent
    for path in source_directory.glob('*.py'):
        assert 'changes.json' not in path.read_text(encoding='utf-8'), path.name
