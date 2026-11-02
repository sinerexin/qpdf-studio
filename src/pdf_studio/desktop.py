"""Local desktop reader for the PDF Studio qpdf extension."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
from queue import Empty, SimpleQueue
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from PIL import Image, ImageTk
import pypdfium2 as pdfium

from .core import compare, inspect_pdf
from .signing import sign_document, verify_document
from .visual import render_page, visual_diff
from .deep import semantic_inventory
from .pages import transform_pages, combine_documents
from .bundle import analyze_complete


class PdfStudioDesktop(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        from . import APP_NAME
        self.title(APP_NAME)
        self.geometry("1360x920")
        self.minsize(900, 640)
        logo = Path(__file__).resolve().parents[2] / "assets" / "qpdf-studio.ico"
        if logo.exists():
            try:
                self.iconbitmap(default=str(logo))
            except tk.TclError:
                pass
        self._scratch = tempfile.TemporaryDirectory(prefix="qpdf-studio-")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pdf-studio")
        self._events: SimpleQueue = SimpleQueue()
        self._busy = False
        self._closed = False
        self._render_sequence = 0
        self._render_after: str | None = None
        self.documents: dict[str, Path | None] = {"A": None, "B": None}
        self.manifests: dict[str, dict] = {}
        self.detail_reports: dict[str, dict] = {}
        self.images: dict[str, ImageTk.PhotoImage] = {}
        self.page_number = tk.IntVar(value=1)
        self.zoom = tk.DoubleVar(value=1.0)
        self.status = tk.StringVar(value="就绪 · 本地只读解析；签署时输出为新文件")
        self.report: dict | None = None
        self._diff_image: ImageTk.PhotoImage | None = None
        self._diff_files: list[Path] = []
        self._diff_current = tk.IntVar(value=1)
        ttk.Style(self).theme_use("clam")
        self._build()
        self.after(80, self._poll_events)

    def _work(self, title: str, action: Callable[[], Any],
              finished: Callable[[Any], None]) -> None:
        """Execute long-running PDF operations without blocking the Tk main thread."""
        if self._busy:
            self.status.set("当前任务执行中，请完成后再操作")
            return
        self._busy = True
        self.configure(cursor="watch")
        self.status.set(f"{title} · 正在处理…")
        future = self._executor.submit(action)
        future.add_done_callback(lambda result: self._events.put(("work", title, finished, result)))

    def _poll_events(self) -> None:
        if self._closed:
            return
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "work":
                    _, title, finished, future = event
                    self._busy = False
                    self.configure(cursor="")
                    try:
                        finished(future.result())
                    except Exception as exc:
                        self.status.set(f"{title}失败：{exc}")
                        messagebox.showerror(f"{title}失败", str(exc))
                else:
                    _, sequence, slot, future = event
                    if sequence != self._render_sequence:
                        continue
                    canvas = self.__dict__[f"canvas_{slot}"]
                    try:
                        picture = future.result()
                        image = ImageTk.PhotoImage(picture)
                        self.images[slot] = image
                        canvas.delete("all")
                        canvas.create_image(8, 8, image=image, anchor="nw")
                        canvas.configure(scrollregion=(0, 0, image.width() + 16, image.height() + 16))
                    except IndexError:
                        canvas.delete("all")
                        canvas.create_text(100, 80, text="该文件没有此页", fill="#4b5563")
                    except Exception as exc:
                        self.status.set(f"页面渲染失败：{exc}")
        except Empty:
            pass
        self.after(80, self._poll_events)

    def _build(self) -> None:
        heading = ttk.Frame(self, padding=(14, 10, 14, 5))
        heading.pack(fill="x")
        ttk.Label(heading, text="qpdf Studio", font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(heading, text="文件结构 · 内容语义 · 签名 · 双文件对比",
                  foreground="#64748b", padding=(18, 6)).pack(side="left")

        toolbar = ttk.Frame(self, padding=(10, 5, 10, 4))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="文件分析", foreground="#64748b").pack(side="left", padx=(3, 9))
        for slot in ("A", "B"):
            ttk.Button(toolbar, text=f"打开 {slot}", command=lambda s=slot: self.open_pdf(s)).pack(side="left", padx=2)
            ttk.Button(toolbar, text=f"解析 {slot}", command=lambda s=slot: self.inspect(s)).pack(side="left", padx=2)
        ttk.Button(toolbar, text="完整解析导出", command=self.analyze_all).pack(side="left", padx=3)
        ttk.Button(toolbar, text="对比 A/B", command=self.compare_documents).pack(side="left", padx=3)
        ttk.Button(toolbar, text="深度解析", command=self.deep_inspect).pack(side="left", padx=3)

        operations = ttk.Frame(self, padding=(10, 3, 10, 4))
        operations.pack(fill="x")
        ttk.Label(operations, text="文件操作", foreground="#64748b").pack(side="left", padx=(3, 9))
        ttk.Button(operations, text="导出报告", command=self.export_report).pack(side="left", padx=3)
        ttk.Button(operations, text="页面整理", command=self.transform_selected_pages).pack(side="left", padx=3)
        ttk.Button(operations, text="合并 A/B", command=self.merge_selected).pack(side="left", padx=3)
        ttk.Separator(operations, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Label(operations, text="签名", foreground="#64748b").pack(side="left", padx=(0, 7))
        ttk.Button(operations, text="验证签名", command=self.verify_selected).pack(side="left", padx=3)
        ttk.Button(operations, text="证书签署", command=self.sign_selected).pack(side="left", padx=3)
        navigation = ttk.Frame(self, padding=(10, 2, 10, 6))
        navigation.pack(fill="x")
        ttk.Button(navigation, text="上一页", command=lambda: self.change_page(-1)).pack(side="left")
        ttk.Label(navigation, text="页码").pack(side="left", padx=(12, 3))
        ttk.Spinbox(navigation, from_=1, to=9999, textvariable=self.page_number, width=5,
                    command=self.refresh_images).pack(side="left")
        ttk.Button(navigation, text="下一页", command=lambda: self.change_page(1)).pack(side="left", padx=(4, 16))
        ttk.Label(navigation, text="缩放").pack(side="left")
        ttk.Scale(navigation, from_=0.6, to=2.5, variable=self.zoom,
                  command=lambda _: self.refresh_images()).pack(side="left", padx=4)
        ttk.Label(navigation, text="A=参考文件    B=待对比文件").pack(side="right")

        splitter = ttk.Panedwindow(self, orient="vertical")
        splitter.pack(fill="both", expand=True, padx=8, pady=4)
        visual = ttk.Frame(splitter)
        splitter.add(visual, weight=3)
        for column, label in enumerate(("A — 原件/参考", "B — 对比文件")):
            frame = ttk.LabelFrame(visual, text=label, padding=6)
            frame.grid(row=0, column=column, sticky="nsew", padx=4)
            self.__dict__[f"label_{label[0]}"] = ttk.Label(frame, text="尚未选择 PDF", anchor="center")
            self.__dict__[f"label_{label[0]}"].pack(fill="x")
            viewer = ttk.Frame(frame)
            viewer.pack(fill="both", expand=True)
            canvas = tk.Canvas(viewer, background="#e9edf2", highlightthickness=0)
            yscroll = ttk.Scrollbar(viewer, orient="vertical", command=canvas.yview)
            xscroll = ttk.Scrollbar(viewer, orient="horizontal", command=canvas.xview)
            canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll.grid(row=1, column=0, sticky="ew")
            viewer.columnconfigure(0, weight=1)
            viewer.rowconfigure(0, weight=1)
            self.__dict__[f"canvas_{label[0]}"] = canvas
        visual.columnconfigure(0, weight=1)
        visual.columnconfigure(1, weight=1)
        visual.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(splitter)
        splitter.add(notebook, weight=2)
        self.notebook = notebook
        self.tabs: dict[str, tk.Text] = {}
        for name in ("功能总览", "A structure", "B structure", "A details", "B details", "Comparison", "Signatures"):
            frame = ttk.Frame(notebook)
            text = tk.Text(frame, wrap="none", font=("Consolas", 10))
            ybar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            xbar = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
            text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
            text.grid(row=0, column=0, sticky="nsew")
            ybar.grid(row=0, column=1, sticky="ns")
            xbar.grid(row=1, column=0, sticky="ew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            notebook.add(frame, text=name)
            self.tabs[name] = text
        diff_frame = ttk.Frame(notebook)
        notebook.add(diff_frame, text="视觉差异图")
        diff_toolbar = ttk.Frame(diff_frame, padding=5)
        diff_toolbar.pack(fill="x")
        ttk.Label(diff_toolbar, text="差异图 · 红色区域表示不同像素").pack(side="left", padx=6)
        ttk.Button(diff_toolbar, text="上一差异页", command=lambda: self._change_diff_page(-1)).pack(side="right", padx=3)
        ttk.Button(diff_toolbar, text="下一差异页", command=lambda: self._change_diff_page(1)).pack(side="right", padx=3)
        self.diff_page_label = ttk.Label(diff_toolbar, text="未进行对比")
        self.diff_page_label.pack(side="right", padx=14)
        diff_canvas_box = ttk.Frame(diff_frame)
        diff_canvas_box.pack(fill="both", expand=True)
        self.diff_canvas = tk.Canvas(diff_canvas_box, background="#e9edf2", highlightthickness=0)
        diff_vbar = ttk.Scrollbar(diff_canvas_box, orient="vertical", command=self.diff_canvas.yview)
        diff_hbar = ttk.Scrollbar(diff_canvas_box, orient="horizontal", command=self.diff_canvas.xview)
        self.diff_canvas.configure(xscrollcommand=diff_hbar.set, yscrollcommand=diff_vbar.set)
        self.diff_canvas.grid(row=0, column=0, sticky="nsew")
        diff_vbar.grid(row=0, column=1, sticky="ns")
        diff_hbar.grid(row=1, column=0, sticky="ew")
        diff_canvas_box.columnconfigure(0, weight=1)
        diff_canvas_box.rowconfigure(0, weight=1)
        self.tabs["功能总览"].insert("1.0",
            "qpdf Studio · PDF 多层解析与文件处理\n\n"
            "① 打开 A：原件或参考文件；打开 B：待比较文件。\n"
            "② 解析 A/B：对象、字典、内容流、图片、字体、签名字段。\n"
            "   完整解析导出：所有已支持的结构、语义、签名、逐页图像集中存入一个目录。\n"
            "③ 深度解析：PDFium 字符框、表单、书签、注释、附件、元数据。\n"
            "④ 对比 A/B：文本差异、对象差异与逐页像素差异。\n"
            "⑤ 页面整理、合并：生成新 PDF，写出操作清单，保留原文件。\n"
            "⑥ 数字签名：分别核对数学完整性与签署者证书信任状态。\n\n"
            "不会因为 PDF 正常打开或签名完整，就将文档自动认定为真实来源。\n"  # 限制：签名完整不等于来源真实
            "尚未完成：OCR、可视化正文排版编辑、完整历史修订恢复。")  # 限制：没有 OCR、可视化排版编辑和完整修订回放
        self.tabs["Comparison"].insert("1.0", "打开 A/B 文件并点击「对比 A/B」。\n"
                         "签名完整性与证书信任是两项不同的检查。")  # 限制：签名完整和证书信任分开检查
        self.tabs["Signatures"].insert("1.0", "选择 PDF 并点击「验证签名」。\n"
                         "如果证书未经可信认证，不能因为签名完整就确认文件来源。")  # 限制：证书未经可信认证时，不能凭签名完整确认来源
        self.tabs["A details"].insert("1.0", "点击「深度解析」查看 PDF A 的字符位置、表单、注释、附件和书签。")
        self.tabs["B details"].insert("1.0", "打开 B 文件后点击「深度解析」，优先分析 B。")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  padding=(12, 5)).pack(fill="x", side="bottom")

    def _close(self) -> None:
        if self._busy:
            messagebox.showinfo("后台任务运行中", "当前 PDF 任务正在执行，完成后再关闭窗口。")
            return
        self._closed = True
        if self._render_after is not None:
            self.after_cancel(self._render_after)
            self._render_after = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._scratch.cleanup()
        self.destroy()

    def open_pdf(self, slot: str) -> None:
        selected = filedialog.askopenfilename(filetypes=[("PDF documents", "*.pdf")])
        if not selected:
            return
        try:
            doc = pdfium.PdfDocument(selected)
            count = len(doc)
            doc.close()
            self.documents[slot] = Path(selected)
            self.manifests.pop(slot, None)
            self.detail_reports.pop(slot, None)
            self.report = None
            self.page_number.set(1)
            self.__dict__[f"label_{slot}"].configure(text=f"{Path(selected).name}  ·  {count} 页")
            self.status.set(f"已加载 {slot}：{Path(selected).name}，{count} 页")
            self.refresh_images()
        except Exception as exc:
            messagebox.showerror("无法打开 PDF", str(exc))

    def _show(self, key: str, obj: object) -> None:
        target = self.tabs[key]
        target.delete("1.0", "end")
        # Avoid building a huge JSON string on Tk's main thread.
        chunks = []
        total = 0
        display_limit = 600_000
        clipped = False
        for chunk in json.JSONEncoder(ensure_ascii=False, indent=2, default=str).iterencode(obj):
            remaining = display_limit - total
            if len(chunk) >= remaining:
                chunks.append(chunk[:remaining])
                clipped = True
                break
            chunks.append(chunk)
            total += len(chunk)
        target.insert("1.0", "".join(chunks))
        if clipped:
            target.insert("end", "\n\n[窗口预览已截断；请通过 JSON 导出查看完整数据。]")

    def _change_diff_page(self, step: int) -> None:
        if not self._diff_files:
            return
        self._diff_current.set(max(1, min(len(self._diff_files), self._diff_current.get() + step)))
        self._show_diff_page()

    def _show_diff_page(self) -> None:
        if not self._diff_files:
            return
        index = self._diff_current.get() - 1
        path = self._diff_files[index]
        if not path.is_file():
            self.status.set(f"对比图片不存在：{path.name}")
            return
        with Image.open(path) as source:
            image = source.convert("RGB")
        self._diff_image = ImageTk.PhotoImage(image)
        self.diff_canvas.delete("all")
        self.diff_canvas.create_image(8, 8, image=self._diff_image, anchor="nw")
        self.diff_canvas.configure(scrollregion=(0, 0, image.width + 16, image.height + 16))
        self.diff_page_label.configure(text=f"{self._diff_current.get()} / {len(self._diff_files)}")

    def refresh_images(self) -> None:
        # Debounce slider changes; the actual rendering runs off the UI thread.
        if self._render_after is not None:
            self.after_cancel(self._render_after)
        self._render_after = self.after(110, self._render_async)

    def _render_async(self) -> None:
        self._render_after = None
        try:
            page = max(1, self.page_number.get()) - 1
        except tk.TclError:
            return
        self._render_sequence += 1
        sequence = self._render_sequence
        scale = 1.25 * self.zoom.get()
        for slot, path in self.documents.items():
            if path is None:
                continue
            canvas = self.__dict__[f"canvas_{slot}"]
            canvas.delete("all")
            canvas.create_text(100, 80, text="正在渲染…", fill="#4b5563")
            future = self._executor.submit(render_page, path, page, scale)
            future.add_done_callback(lambda f, s=slot, g=sequence:
                                     self._events.put(("render", g, s, f)))

    def change_page(self, offset: int) -> None:
        try:
            self.page_number.set(max(1, self.page_number.get() + offset))
        except tk.TclError:
            self.page_number.set(1)
        self.refresh_images()

    def inspect(self, slot: str) -> None:
        path = self.documents[slot]
        if path is None:
            messagebox.showinfo("Select a file", f"Open PDF {slot} first.")
            return
        def finished(manifest: dict) -> None:
            if self.documents[slot] != path:
                self.status.set("文件已更换，已忽略过期的分析结果")
                return
            self.manifests[slot] = manifest
            self._show(f"{slot} structure", manifest)
            self.status.set(f"{slot} 解析完成 · {manifest['page_count']} 页 · "
                            f"{manifest['object_count']} 个对象 · "
                            f"{len(manifest['signatures'])} 个签名字段")
        self._work(f"{slot} 文件解析",
                   lambda: inspect_pdf(path, Path(self._scratch.name) / slot,
                                       operators=True, text_geometry=True),
                   finished)

    def compare_documents(self) -> None:
        if not all(self.documents.values()):
            messagebox.showinfo("Two files needed", "Open PDF A and PDF B first.")
            return
        documents = dict(self.documents)
        known_manifests = dict(self.manifests)

        def process() -> tuple[dict, dict]:
            current = dict(known_manifests)
            for slot in ("A", "B"):
                if slot not in current:
                    current[slot] = inspect_pdf(documents[slot], Path(self._scratch.name) / slot,
                                                operators=True, text_geometry=True)
            structural = compare(current["A"], current["B"])
            visual = visual_diff(documents["A"], documents["B"],
                                 Path(self._scratch.name) / "visual")
            return current, {"structure": structural, "visual": visual}

        def finished(result: tuple[dict, dict]) -> None:
            if documents != self.documents:
                self.status.set("文件已更换，已忽略过期的对比结果")
                return
            current, report = result
            self.manifests.update(current)
            self._show("Comparison", report)
            self.report = report
            image_folder = Path(self._scratch.name) / "visual"
            self._diff_files = [image_folder / row["file"] for row in report["visual"]["pages"]]
            if self._diff_files:
                first_changed = next(
                    (i for i, row in enumerate(report["visual"]["pages"], start=1)
                     if row.get("changed_pixels", 0)),
                    1,
                )
                self._diff_current.set(first_changed)
                self._show_diff_page()
                self.notebook.select(self.diff_canvas.master.master)
            differences = sum(1 for item in report["visual"]["pages"] if item["changed_pixels"])
            self.status.set(f"对比完成 · 共 {len(report['visual']['pages'])} 页，"
                            f"{differences} 页存在可见差异；详情见 Comparison")
        self._work("双文件对比", process, finished)

    def export_report(self) -> None:
        available = bool(self.detail_reports) or self.report is not None
        if not available:
            messagebox.showinfo("没有报告", "请先执行对比或深度解析。")
            return
        export_detail = bool(self.detail_reports) and (
            self.report is None or messagebox.askyesno("选择报告", "导出深度解析数据？\n选择「否」导出 A/B 对比报告。"))
        if export_detail:
            slot = "B" if "B" in self.detail_reports else "A"
            output = self.detail_reports[slot]
        else:
            if self.report is None:
                messagebox.showinfo("没有对比报告", "请先执行 A/B 对比。")
                return
            output = self.report
        selected = filedialog.asksaveasfilename(defaultextension=".json",
                                                filetypes=[("JSON", "*.json")])
        if selected:
            Path(selected).write_text(json.dumps(output, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
            self.status.set(f"报告已导出：{selected}")

    def deep_inspect(self) -> None:
        slot = "B" if self.documents["B"] is not None else "A"
        source = self.documents[slot]
        if source is None:
            messagebox.showinfo("请选择文件", "先打开一个 PDF 文件。")
            return

        def complete(detail: dict) -> None:
            if self.documents[slot] != source:
                self.status.set("源文件已更换，忽略过期分析")
                return
            self.detail_reports[slot] = detail
            # Keep GUI responsive: glyph-by-glyph data is fully retained for JSON export.
            preview = dict(detail)
            preview["pages"] = [
                {**page, "glyphs": page["glyphs"][:160],
                 "visible_preview_notice": "窗口只预览每页前160字符；导出报告包含所有读取到的字形。"}
                for page in detail["pages"]
            ]
            self._show(f"{slot} details", preview)
            self.status.set(f"{slot} 深度解析完成 · {len(detail['pages'])} 页 · "
                            f"{len(detail['form_fields'])} 个表单字段 · "
                            f"{len(detail['attachments'])} 个附件")
        self._work("PDF 深度解析", lambda: semantic_inventory(
            source, max_pages=300, max_glyphs_per_page=25000), complete)

    def analyze_all(self) -> None:
        source = self._current_document()
        if source is None:
            messagebox.showinfo("请选择文件", "请先打开 PDF A 或 PDF B。优先解析 B。")
            return
        folder = filedialog.askdirectory(title="选择完整解析结果保存目录")
        if not folder:
            return
        base = Path(folder) / (source.stem + "-qpdf-analysis")
        target = base
        index = 2
        while target.exists():
            target = Path(f"{base}-{index}")
            index += 1

        def complete(summary: dict) -> None:
            self.status.set(f"完整解析完成：{target} · {summary['pages']} 页、"
                            f"{summary['objects']} 个对象")
            self._show("功能总览", summary)
            self.notebook.select(self.tabs["功能总览"].master)
            messagebox.showinfo("解析完成",
                                f"全部解析结果已保存到：\n{target}\n"
                                "包含对象数据、语义数据及页面图像等。")

        self._work("PDF 完整解析", lambda: analyze_complete(source, target), complete)

    def transform_selected_pages(self) -> None:
        source = self._current_document()
        if source is None:
            messagebox.showinfo("请选择文件", "先打开需要处理的 PDF。")
            return
        pages = simpledialog.askstring("页面顺序", "输入页码，例如 3,1,2；留空表示全部页面：",
                                       parent=self)
        if pages is None:
            return
        try:
            selection = [int(x.strip()) for x in pages.split(",")] if pages.strip() else None
        except ValueError:
            messagebox.showerror("页码错误", "请输入逗号分隔的整数页码。")
            return
        rotation = simpledialog.askinteger("页面旋转", "输入顺时针旋转角度：0、90、180 或 270",
                                           initialvalue=0, parent=self)
        if rotation is None:
            return
        target = filedialog.asksaveasfilename(
            title="处理后的文件另存为", defaultextension=".pdf",
            initialfile=source.stem + "-pages.pdf", filetypes=[("PDF", "*.pdf")])
        if not target:
            return
        def complete(report: dict) -> None:
            self.status.set(f"页面处理完成：{report['output']}，另存的新文件附带变化清单")
            self._show("Comparison", report)
        self._work("页面整理", lambda: transform_pages(
            source, Path(target), pages=selection, rotate=rotation), complete)

    def merge_selected(self) -> None:
        if not all(self.documents.values()):
            messagebox.showinfo("需要两个文件", "先打开 PDF A 和 B。")
            return
        target = filedialog.asksaveasfilename(
            title="合并文件另存为", defaultextension=".pdf",
            initialfile="merged.pdf", filetypes=[("PDF", "*.pdf")])
        if not target:
            return
        paths = [self.documents["A"], self.documents["B"]]
        def complete(report: dict) -> None:
            self.status.set(f"合并完成：{report['output']}，已保存变化清单")
            self._show("Comparison", report)
        self._work("合并 PDF", lambda: combine_documents(paths, Path(target)), complete)

    def _current_document(self) -> Path | None:
        return self.documents["B"] or self.documents["A"]

    def verify_selected(self) -> None:
        path = self._current_document()
        if path is None:
            messagebox.showinfo("请选择文件", "先打开 A 或 B 文件。默认验证 B 文件。")
            return
        roots: list[Path] = []
        if messagebox.askyesno("证书链检查", "需要指定事先独立核实过的可信根证书吗？\n"  # 限制：可信根必须事先独立核实
                               "选择「否」将只检查签名，并把签署身份标记为未建立信任。"):  # 限制：不选可信根时，身份标记为未建立信任
            root = filedialog.askopenfilename(title="选择可信根证书",
                                              filetypes=[("证书文件", "*.pem *.crt *.cer"), ("全部", "*.*")])
            if root:
                roots.append(Path(root))
        def finished(result: dict) -> None:
            result["source_file"] = path.name
            result["identity_warning"] = ("密码学签名完整不等于文件由页面上声称的机构签发。"  # 限制：签名完整不等于机构签发
                                          "不得仅凭签名完整性确认真实来源。")  # 限制：不能只凭签名完整确认真实来源
            self._show("Signatures", result)
            self.status.set(f"验签完成：{path.name}，发现 {result['count']} 个数字签名")
        self._work("数字签名验证", lambda: verify_document(path, roots), finished)

    def sign_selected(self) -> None:
        path = self._current_document()
        if path is None:
            messagebox.showinfo("请选择文件", "先打开需要签署的 PDF。")
            return
        credential = filedialog.askopenfilename(title="选择自己合法持有的签署证书",
                                                filetypes=[("P12/PFX 凭据", "*.p12 *.pfx")])
        if not credential:
            return
        target = filedialog.asksaveasfilename(title="签署后另存为",
                                             initialfile=path.stem + "-signed.pdf",
                                             defaultextension=".pdf",
                                             filetypes=[("PDF 文件", "*.pdf")])
        if not target:
            return
        password = simpledialog.askstring("签署证书密码", "请输入 P12/PFX 密码（如无密码可留空）：",
                                          show="*", parent=self)
        if password is None:
            return
        def finished(signed: Path) -> None:
            self.status.set(f"签署完成：{signed.name}（新文件）")
            messagebox.showinfo("签署完成", f"已生成新的已签署 PDF：\n{signed}\n"
                                 "请注意：有效签名不代表第三方已认证签署者身份。")  # 限制：有效签名不等于签署者身份已被第三方认证
        self._work("签署 PDF",
                   lambda: sign_document(path, Path(target), pfx=Path(credential),
                                         password=password.encode("utf-8")),
                   finished)


def launch() -> None:
    PdfStudioDesktop().mainloop()
