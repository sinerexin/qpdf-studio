# 高级功能：已实现与未实现范围

> 新增对象编辑首批能力（`object_editor.py`、`qt_object_dialog.py`）：
> 原 Image XObject 流像素数据更新，及原 RGB 矩形路径颜色操作数修改。
> GUI 通过页面点击定位和真实 PDF 预览确认，CLI 也支持读取/修改。
> 对单次使用、无透明、未旋转/裁剪的结构才开放编辑，
> 多数复杂 PDF 绘制结构仍需另行扩展。

> 新增原稿排版证据读取与自动套用（`original_layout.py`）。
> 原 `Tf/TL/Tm` 和已绘制行宽直接提取，同列可用宽度
> 为有标签的保守推断。Qt 和 CLI 均可以直接修改原文并保持
> 原始字号、行距及对齐起点。复杂布局尚未全面复现。

> 已开始实现不同长度段落的**真实底层重排**（`paragraph_reflow.py`）。
> 支持部分单字体、独立 `BT..ET` 的文字对象，按字体宽度生成新行，
> 保留原段落以外对象不变，输出变更审计。Qt、CLI 入口与测试均已接入，
> 遇复杂文本状态/旋转、布局空间不足时返回具体不支持原因。
> 完整跨对象自动排版仍未完成。

> 新增 Type0/CIDFontType2 Identity-H 嵌入字体的完整合成 PDF 实测、
> `TJ` 多字符串整体替换，以及没有插入位置/状态变化时的
> 相邻 `Tj/TJ` 操作数整体替换。新增替换前后 PDFium 字形边界测量，
> 对可能的宽度变化提示人工核对。以上均直接重写 PDF 内容流；
> 未实现任意段落自动重排和全字体兼容。

> 新增：个人公私钥生成与自签名证书配置/重新签发（加密 PEM、P12）；
> 以及对可验证 ToUnicode 的嵌入式 TrueType 子集的文字操作数直接替换。
> 字形替换限定在同页该字体实际用过的字形编码，避免误用不完整字体子集。
> 完整 CID/Type0、复杂 Unicode 多码点、自动重排版仍需兼容测试和开发。

> 本轮增加可视化文字添加、PDFium 字符搜索/高亮、专业签名审计
> 与静态 HTML 报告。详见
> [TEXT_EDITOR_AND_SIGNATURE_AUDIT.md](TEXT_EDITOR_AND_SIGNATURE_AUDIT.md)。

本文件描述 qpdf Studio 扩展分支的实际代码，不代表 qpdf 官方项目已经接受这些功能。

## 本轮新增且已验证的功能

1. **可保存的文字批注（`annotations.py`）**：在用户非加密
   PDF 指定页面和坐标添加标准 `/Text` 批注，仅另存为新的 PDF；不自动写入修改记录。
   不覆盖原文件，不模拟机构印章。
2. **修订和签名范围结构证据（`revisions.py`）**：列出
   `%%EOF`、`startxref` 的物理位置，使用当前纯 Python 解析器尝试
   还原 `/Prev` 链；汇总 `/ByteRange` 的结构合理性、签名覆盖终点、
   签名内容段哈希和签名之后的字节数量。解析失败时明确记录状态，
   不将字节线索直接解释为欺诈或签名有效性。
3. **绘制操作顺序（`paint_diagnostics.py`）**：通过 qpdf 解析并导出页面
   绘图、文字、图像和状态操作符，记录不可见文字模式、白色路径填充、
   Form XObject 绘制操作等线索。嵌套 Form 内容尚未递归展开；分析结果
   不证明文件经过恶意修改。
4. **完整解析包**：`analyze-all` 现额外保存
   `revisions.json`、`paint.json`；原有对象、Stream、字体、图像、
   文字坐标、页面 PNG 和签名核验模块保持不变。
5. **Qt 工作台**：可从工具栏打开“添加批注”“修订分析”“绘制分析”，
   侧边属性页显示修订证据与绘制顺序，复杂操作在后台线程执行。
6. **附件提取（`attachments.py`）**：可读取 PDF 嵌入附件并导出到新的
   本地目录，生成名称、长度、SHA-256 清单。忽略 PDF 提供的路径部分，
   自动构造安全的本地文件名，防止 `../` 路径穿越。附件本身属于不可信内容。
7. **标准 AcroForm 文字表单填写（`forms.py`）**：读取文本字段、当前值与
   只读状态，填写未加密 PDF 的可编辑文字字段，另存为新的文件，
   不自动写入修改记录文件。暂不处理 XFA、数字签章字段和复杂按钮控件。

## 命令行

```powershell
qpdf-studio revisions sample.pdf --out revisions.json
qpdf-studio paint sample.pdf --out paint.json
qpdf-studio add-note own-unsigned.pdf --page 1 --x 70 --y 120 --text "Review" --out own-noted.pdf
qpdf-studio analyze-all sample.pdf --out analysis-full
qpdf-studio extract-attachments sample.pdf --out embedded-files
qpdf-studio form-fields unsigned-form.pdf
qpdf-studio fill-form unsigned-form.pdf --field "comment=Reviewed" --out completed-form.pdf
```

## 功能缺口（不能宣称完成）

- Word 式直接编辑正文文本与自动重排版、多语种字体/CMap 完整复原。
- OCR 扫描件识别与校正。
- 每个历史增量修订版本的完整对象级与视觉重建；目前只提供部分
  修订索引与范围证据。
- OCSP/CRL 在线撤销检查、长期签名 LTV/DSS/PAdES 全面互操作验收。
- 任意 PDF 对象安全图形编辑器、复杂交互表单与 XFA 编辑。
- 正式独立 EXE 安装包、恶意 PDF 大规模模糊测试及完整跨平台验收。

这批功能的验收使用**程序生成的示例 PDF**，不包含任何真实银行资料。
公开披露漏洞时须把假设、实验复现、签名数学完整性、证书信任和真实
业务来源分开报告。
