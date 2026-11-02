from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from pdf_studio.text_search import find_text


def test_text_search_positions_are_on_actual_pdf_page(tmp_path: Path) -> None:
    file = tmp_path / "search.pdf"
    canvas = Canvas(str(file), pagesize=(612, 792))
    canvas.drawString(72, 680, "Findable sample phrase")
    canvas.showPage()
    canvas.drawString(60, 640, "Second page sample")
    canvas.save()
    report = find_text(file, "sample")
    assert report["count"] == 2
    assert report["matches"][0]["page"] == 1
    assert report["matches"][1]["page"] == 2
    for row in report["matches"]:
        assert row["boxes_pdf"]
        assert all(0 <= box[0] < box[2] <= 612 for box in row["boxes_pdf"])
        assert all(0 <= box[1] < box[3] <= 792 for box in row["boxes_pdf"])
