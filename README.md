# qpdf Studio

本机 PDF 工作台。可以查看原稿、在工作副本上改内容，并为副本生成签名。它不是 qpdf 官方命令行程序；qpdf 仍是独立项目。

本目录是在公开代码上继续开发后再分享的结果。qpdf 和所用库的原作者、许可证与项目地址写在 [NOTICE.md](NOTICE.md)。本扩展自己的源码使用 Apache-2.0，见 [LICENSE](LICENSE)。qpdf 维护者尚未接收或发布这份扩展。

桌面程序名称是 **qpdf Studio**。安装后的命令行入口仍是 `qpdf-studio`，Python 包名是 `pdf_studio`。

## 桌面流程

1. 导入原稿。
2. 在「内容编辑」里修改工作副本。编辑内容包括替换已有文字、段落重排、添加正文、编辑图片或图形。
3. 在「签名与证书」里读取原稿参数，生成新的密钥和证书。这一步只保存签名数据，不写 PDF。
4. 在「导出与对比」里导出签署后的 PDF。导出不覆盖原稿，也不附带凭据文件夹。

窗口标题和欢迎页使用同一名称。图标由 `assets/build_icon.py` 生成，文件是 `assets/qpdf-studio.ico`。

下面各节保留命令行用法和编辑边界。较早的界面笔记如果和上面四步不一致，以当前桌面程序为准。

## 两个独立的主工作区：内容修改 / 数字签名

Windows Qt 顶部现在分为 **① 内容修改** 与 **② 数字签名**：

- **内容修改**：保留原来的正文编辑、段落排版、图片对象、
  页面整理、PDF 结构分析，修改结果另存为 PDF 副本。
  不允许用遮盖替代真实内容流编辑。
- **数字签名**：生成本人新公私钥、修改自己已有个人自签名证书、
  签署 PDF、独立验签、A/B 签名身份/指纹/信任对照，以及
  **「修改签署者并产出」** 向导。

### 「修改签署者并产出」操作

1. 打开需要处理的 PDF；可直接导入 B，不要求先完成编辑预览。
2. 切换数字签名，点击「修改签署者并产出」。
3. 选择自己持有的、包含加密 PKCS#8 私钥和自签名证书的资料夹。
4. 修改个人**新签署者名称（证书 CN 声明）**、有效期，输入自己私钥密码，
   指定新证书目录与新 PDF 路径。
5. 程序使用所选个人私钥重新签发证书，
   对新副本签署，重新进行密码学验签；成功后加载 B 窗口，
   可查看新身份声明、证书/公钥指纹和信任状态。

**重要：** 重签发自己持有的证书只修改该自签名证书的主体、序列号和
指纹，不会更换私钥；因此公钥与公钥指纹**保持相同**。若希望产生一套
全新公钥和私钥，需另行使用「生成公钥 / 加密私钥」。
任何个人自填证书名称都不代表已取得第三方 CA 身份验证。

## 原件 A / 新产出 B 数字签名身份与指纹对照

**签名与密钥 → A/B 签名身份对照**：
将原件加载到 A、新产出加载到 B，单击该按钮打开双栏验签面板。
两列同时显示各自的：证书主体声明、签发者声明、完整证书 SHA-256
指纹、公钥 SHA-256 指纹、密钥算法与长度/曲线、签名机制、数学完整性
和独立信任状态。面板下方指出算法规格是否相同、两把公钥和两份证书
指纹是否相同。对于未签名文件准确显示“未检测到数字签名”。

原件与新产出可**分别**选择从独立可信渠道核实的可信根证书，仅离线验签；
默认不提供根证书时，数学有效签名也不能被显示为机构身份已验证。
证书主体字段只是签署人自我声明，**不能用原件自带的证书创建假冒
CA 信任**。两份文件的信任结果不互相继承。

**签名与密钥 → 按原件算法生成个人密钥**：在 A 中加载有可验证签名
的原件，该功能只能提取原公钥的 RSA 位数或 ECDSA 曲线并预选受支持
的新密钥算法。生成的密钥是全新且独立的；新证书的名称、序列号、
公钥及证书指纹、签名和身份信任均与原签署者不同。该功能**不复制**
原签署者身份或签名外观，不修复修改后旧 PDF 的密码学签名。
新导出密钥报告也会列出独立的公钥 SHA-256 指纹。

```powershell
qpdf-studio signature-profile signed-original.pdf
qpdf-studio compare-signatures signed-original.pdf newly-signed.pdf
qpdf-studio compare-signatures signed-original.pdf newly-signed.pdf --original-trust-root independently-verified-original-root.pem --produced-trust-root independently-verified-new-root.pem
```

**新的任务式 Qt 用户界面：**
参见 [UI_WORKFLOW.md](UI_WORKFLOW.md)。常用功能按编辑正文、图片与页面、
结构与分析、签名与密钥四类组织；保留 A/B 原稿与结果预览、右侧对象检查，
默认最大化 PDF 预览空间，文件状态、编辑高亮、Esc 退出和快捷键
已经接入 Windows 工作台。

## 2026-11-01：图片 / 矢量图形的底层可视化编辑（第一批）

Qt 主窗口 **「直接编辑图片/图形」** 按钮已连接真实 PDF 绘制对象操作：
打开 PDF → 点击页面上的已有图片或矢量矩形 → 查看底层 `Do`
或 `rg/re/f` 指令、资源对象引用、坐标与像素尺寸 →
选择同样像素尺寸的 RGB 图片，或修改原矢量矩形的填充颜色 →
从**修改后的实际 PDF** 生成预览 → 另存为新 PDF 并在 B 面板打开。

- 原有 Image XObject：`object_editor.py` 会检查 `/XObject`、`/Subtype /Image`、
  `/ColorSpace /DeviceRGB`、8-bit 像素和图像尺寸，扫描 PDF 内
  `Do` 使用次数。只接受**全文件仅绘制一次、没有透明遮罩和自定义
  DecodeParms** 的简单图片。将新像素通过 Flate 编码写回**原有
  Image XObject Stream**，既不新增图片对象的绘制指令，也不遮盖旧图。
  若同一 Image XObject 被多次绘制则拒绝以防误改其他位置。
- 原有矢量矩形：只识别严格的 `q / rg / re / f / Q` 指令序列；
  直接修改原 `rg` 的三个颜色操作数，其余路径几何和绘制指令保持不变。
- 操作索引和坐标是从 PDF 原始内容流提取；对旋转、CropBox、外层
  复杂矩阵、嵌套 Form XObject、共享图片、图片透明遮罩、复杂色彩空间
  等结构，**不使用覆盖方案，也不猜测性编辑**。
  这些结构仍可通过「绘制分析」研究，但不属于当前可编辑范围。
- 另存只写用户选择的新 PDF，原文件不覆盖；不自动生成修改记录文件。
  更全面的路径曲线、剪裁、透明度、图像重采样与对象树编辑器仍需扩展。

```powershell
qpdf-studio native-objects own-unsigned.pdf --page 1
qpdf-studio edit-native-object own-unsigned.pdf --page 1 --operator-index 7 --kind image --image same-pixels-rgb.png --out new-image.pdf
qpdf-studio edit-native-object own-unsigned.pdf --page 1 --operator-index 18 --kind vector-fill --color "#2277AA" --out new-vector.pdf
```

**安全边界：** 此功能修改原图像和图形数据，不提供伪造可信证书、
绕过签名或保持旧签名有效的能力。数字签名的身份和信任仍需独立验证。

**核心闭环验收：** 参见
[CORE_ACCEPTANCE.md](CORE_ACCEPTANCE.md)。Windows 离屏 Qt 测试已覆盖
打开 PDF、修改正文模式、生成加密 PEM/P12、实际签署、
自动展示签署副本及信任状态分离的验签操作；多页/图片/签名
等兼容性边界也增加了防回归检查。

## 2026-10-31：优先读取原稿排版，修改后自动沿用（阶段性）

已增加 `original_layout.py` 以及 Qt 的「使用原稿的字体、行距、
坐标和推断宽度自动排版」默认模式。现在点击支持的原文段落后：

1. 从原 PDF `/Contents` 读取 `BT/ET`、`Tm`、
   `Tf`、`TL`、`Tj/T*`，确认原始字体、字号、
   行距、左对齐位置及原来每行文字所占宽度。
2. 从同列、同字体的其他文字片段、页面边界和原有文字实际行宽，
   **推断**原稿的可用列宽；保留证据、推断依据和置信度。
3. 仅对安全的独立文字对象按原字体实际度量重新断行、修改原本
   `Tj/T*`，必要且安全时同步修改后续独立文字对象 `Tm`。
   不使用遮盖、新图层或预先统一设定的字体模板。
4. Qt 默认由原稿决定宽度和高度；原来手工指定的行宽/高度保留
   为**明确关闭自动模式后的高级选项**。实际 PDF 渲染预览、
   A/B 对比、另存新 PDF 和 SHA-256 操作清单保持可用。

**必须区分原始证据与推断：** PDF 一般只存储已经排好的绘制指令，
未必保留 Word/InDesign 的段落样式表、原始文本框宽度、自动换行规则
或作者使用的排版软件。因此「行宽、右边界、列结构」如未直接记录，
只能根据已绘制文字与布局推断，不能承诺完整恢复原作者的所有规则。
不支持的旋转、裁剪、复杂字体或图像/图形交织页面会拒绝自动流式重排。

```powershell
qpdf-studio reflow-blocks own-unsigned.pdf --page 1
qpdf-studio original-layout own-unsigned.pdf --page 1 --start-operator 5
qpdf-studio reflow-like-original own-unsigned.pdf --page 1 --start-operator 5 --expected "Original paragraph" --replacement "Edited text following original layout" --out edited-copy.pdf
```

## 2026-10-31 新增：真正的不同长度段落重排（受支持的 PDF 文本对象）

本轮 `paragraph_reflow.py` **直接修改已有 PDF 页面 `/Contents`**：
选择结构独立的 `BT..ET` 文字对象，根据原有 `/Font`、字号和 `TL`
行距，读取字体宽度并自动分行，再将原对象的 `Tj/T*` 绘制操作数
替换为新的实际文字指令。没有白色遮盖、叠加图层、图片背景或
用新增正文假装替换旧段落。

Qt 主窗口增加 **「段落重排」**：点击已有段落 → 修改任意长度的正文 →
指定最大行宽和允许高度 → 点击真实 PDF 渲染预览 → 另存新 PDF。
保留原文件；不自动写入修改记录文件，可按需使用 A/B 对比。

命令行也可操作（`--start-operator` 请先从实际的 `reflow-blocks` 查询）：

```powershell
qpdf-studio reflow-blocks own-unsigned.pdf --page 1
qpdf-studio reflow-text own-unsigned.pdf --page 1 --start-operator 5 --expected "Old text" --replacement "A longer new paragraph with more words" --width 230 --height 180 --out paragraph-edited.pdf
```

**适用边界非常重要：** 目前只支持能明确确认起始坐标、单一字体、
普通水平文本矩阵、独立 `BT..ET` 且仅用 `Tj/T*` 绘制正文的文本对象。
对复杂的旋转/倾斜、夹杂特殊字距、嵌套 Form XObject、多个交织文本对象、
未知字体映射或尚无密码接口的加密 PDF 仍会返回具体不支持原因。
有限字体类型包含标准 WinAnsi 和已能验证字形宽度及逆向映射的部分嵌入式字体。
**这个功能不会自动推动周围独立的表格、图片或其他文本对象**；
宽度/高度只约束所选段落本身，最终版式仍需人工查看。

## 新增：Type0/CID 真实 PDF 与多操作数原文修改（阶段性）

当前文字编辑测试已经覆盖实际生成、嵌入 TrueType 字体的
`Type0 / CIDFontType2 + Identity-H + ToUnicode` PDF：
通过 qpdf 直接重写 `Tj` 原字形编码，使用 PDFium 检查页面渲染，
并通过 pypdf 验证文本提取结果，无白色遮挡或新增绘图指令。

此外，支持两种受控的跨片段编辑：

- **完整 TJ 数组**：在一个 `TJ` 操作中修改多个字符串，
  保留字距数值与原绘制指令顺序。
- **连续 Tj/TJ 操作**：仅对同一 `BT..ET` 文字对象中、
  没有穿插 `Tm/Td/Tf` 等状态或定位命令的连续文字指令整体修改。

两个整体编辑模式均限制为**替换前后 Unicode 字符数量相等**，
并将原文按原字符串片段长度重新分配。程序直接改 `/Contents`，
不使用 `add_overlay` 或任何覆盖。保存报告增加 PDFium 字形范围和
宽度变化提示：这不是自动重排版，仍需查看实际输出。
Qt「替换已有文字」弹窗现在会提供**单片段、完整 TJ、连续指令**
等可验证选项。

```powershell
qpdf-studio tj-spans own-unsigned.pdf --page 1
qpdf-studio replace-tj-span own-unsigned.pdf --page 1 --operator-index 7 --expected "Old text" --replacement "New text" --out tj-edited.pdf
qpdf-studio text-spans own-unsigned.pdf --page 1
qpdf-studio replace-text-span own-unsigned.pdf --page 1 --start-operator 7 --expected "Old text" --replacement "New text" --out multi-op-edited.pdf
```

仅支持已验证的字体编码、已有字形和严格的连续指令范围。
不自动支持嵌套 Form XObject、任意 CID 映射、非连续文字对象或段落排版。

## 正文编辑统一规则：真实内容流，不使用遮盖

## 生成与修改个人自签名证书

Windows Qt 工作台新增 **「生成公钥 / 加密私钥」** 和
**「修改自签名证书」**。可以设置个人证书名称、有效期、RSA 或 ECDSA
算法，保护并导出加密 PKCS#8 私钥、PEM 公钥、自签名证书及加密 P12。
修改已生成证书时使用自己持有的私钥重新签发到**新文件夹**，证书指纹会变化，
旧证书、旧 PDF 签名和原始密钥不会被覆盖。

CLI 不允许把密钥密码直接写在普通命令参数里：优先通过无回显交互提示输入，
或在明确需要时选择 `--password-env`。例如：

```powershell
qpdf-studio generate-keys --name "My Personal Signing Key" --algorithm RSA-3072 --days 365 --out my-credentials
qpdf-studio reissue-selfsigned --existing my-credentials --name "My Updated Name" --days 180 --out new-credentials
```

自签名证书 **不是 CA 签发证书**；生成或修改证书不会自动建立
任何机构身份、浏览器信任或 PDF 历史签名的有效性。

## 已有文字底层编辑：嵌入字体 CMap 首批扩展

`font_cmap.py` 现在能读取嵌入式 TrueType 的 ToUnicode `bfchar`、
`bfrange`，并增加对 `Identity-H` 双字节 CMap 的受限映射。
通过当前页已经实际使用过的字形编码建立反向映射，再直接修改
`Tj/TJ` 的原字节操作数。已有真实嵌入式 TrueType 测试覆盖“修改
原操作数 → PDF 文本提取变化 → 绘制指令没有增加”的完整路径。

对于一个 Unicode 字符对应多个字形编码、字体子集尚未出现过的字形、
无 ToUnicode 映射及尚未完成验证的 Type0/CID 布局，程序拒绝不可靠
的逆向编码，不使用白色涂盖或新图层冒充已修改。Type0 编码基础已经
完成单元测试，但仍需要真实复合字体 PDF 的完整渲染验收。

所有正文编辑均调用 qpdf 的 PDF 对象和内容流接口：
**替换**时重写原 `Tj/TJ` 字符串操作数；**新增**时直接追加
`/Contents` 的文字绘制指令，并以独立字体名称写入 `/Resources/Font`。
正文编辑模块不使用 `page.add_overlay()`、`add_underlay()`、
白色矩形或图像遮盖来假装文字已被修改。
不支持的字体/编码必须报错，不得回退到遮盖式替换。
已有文字的复杂重排版仍未全面实现。

## 直接修改已有 PDF 文字操作数（首批适配）

在 PySide6 窗口点击 **替换已有文字**，随后点击页面中已有的文字，
软件会用 PDFium 定位文字区域，并找出 qpdf 中与之匹配的底层文字操作数。
选择匹配的原文、输入新文字、用实际输出 PDF 预览，再另存为新文件。
这会直接修改原来的 Tj/TJ 字符串操作数，**不使用白色遮盖，也不添加替换文本覆盖层**。
原 PDF 不会被覆盖；另存不自动附带修改记录文件。

当前直接替换支持可可靠识别的标准 WinAnsi Type1 字体，
以及 Tj、TJ 中独立的字符串片段、单引号和双引号换行文字操作数。
对于特殊嵌入字体、ToUnicode/CMap、跨操作数完整句子、复杂形状的文档，
会拒绝不可靠的修改。不会自动重排段落，修改后应核对预览。

```powershell
qpdf-studio text-runs own-unsigned.pdf --page 1
qpdf-studio replace-text own-unsigned.pdf --page 1 --operator-index 7 --expected "Old text" --replacement "New text" --out revised-copy.pdf
```

**近期优先工作：正文编辑与签名技术核验。** 详见
[TEXT_EDITOR_AND_SIGNATURE_AUDIT.md](TEXT_EDITOR_AND_SIGNATURE_AUDIT.md)。
新 UI 已支持鼠标定位新增 PDF 正文、实际 PDF 渲染预览、文字检索高亮，以及
逐签名详细核验和 HTML/JSON 报告。替换任意旧文字并自动重排版仍在后续计划，
不能视为已经完成。

**高级功能记录**：新增 [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md)，
包含批注、修订链证据、签名覆盖范围、页面绘制顺序以及 Qt UI 入口的
实现情况和明确的功能缺口。

近期新增的命令行入口包括 `revisions`、`paint`、`add-note`、
`extract-attachments`、`form-fields`、`fill-form`。它们均有
现成的 Qt 界面入口；表单填写支持可编辑标准文字字段，不按数字签名存在与否拦截。

## Windows desktop application (PySide6 / Qt6)

The Windows desktop shortcut now starts the new native-Qt Python workbench
(`pdf_studio.qt_desktop`), **after a successful GUI smoke validation**. The
previous Tk application remains available as `pdf_studio.desktop` for
diagnostic fallback. The original qpdf/libqpdf parser and editing engine is
preserved; no qpdf C++ source files were removed or replaced.

The UI provides side-by-side PDF previews, page/zoom navigation, a hierarchical
indirect-object explorer, document properties, signature-status details, text
and pixel comparisons, and page tools. Expensive analysis runs in Qt worker
tasks. Property edits save only a new PDF, without an automatic change-record sidecar.

Read or edit unsigned document Info properties with the CLI:

```powershell
qpdf-studio properties my-document.pdf
qpdf-studio edit-properties my-document.pdf --title "Draft" --out my-document-edited.pdf
```

The UI does not claim that updating document metadata proves provenance.

> **New independent Python-core workstream**: see
> [PYTHON_REWRITE_PLAN.md](PYTHON_REWRITE_PLAN.md). The standalone
> `pdf_studio.purepdf` module is written with the Python standard library and
> does **not** invoke qpdf, pikepdf or pypdf. Its early structural parser
> understands classic and stream XRefs, compressed object streams, common PDF
> primitive syntax and incremental /Prev pointers. Run it with
> `python -m pdf_studio.purepdf file.pdf --out structure.json`.
> It is **not yet a full replacement**: the currently installed desktop app
> still uses the mature qpdf-based editing/inspection modules until the new
> engine can safely replace them feature by feature.

An open-source extension built **inside the upstream qpdf source tree**, preserving
upstream C++ code. It currently uses **pikepdf (which embeds libqpdf)** as its
Python binding; a native qpdf CLI/C++ binary has **not** been rebuilt on this host.

This is an early public-development branch, not a completed graphic editor or
a substitute for professional digital-signature and identity verification.

## Implemented

- Read-only inventory of PDF indirect objects, references, dictionaries, streams, trailer,
  pages, annotation resources, signature fields, and basic extracted page text.
- Optional decoding of page content-stream operators (including drawing and text operators),
  plus text fragments, font names and PDF positioning matrices.
- Page font resource inventory and referenced image XObject metadata; optional
  extraction of supported embedded image formats.
- SHA-256 hashes of original bytes, raw/decoded stream bytes; optionally export streams.
- Compare two parsed manifests for object-ref changes and extracted text differences.
- Render complete PDF pages using PDFium, export PNG previews and side-by-side
  non-persistent PNG pixel-difference visualizations with changed-pixel counts.
- Windows/macOS/Linux desktop interface (Tk/Pillow) with two file panels, page
  viewing, full qpdf JSON inspection, comparison and report export.
- Produce an actual PAdES-compatible PDF signature using an **authorized** P12/PFX or PEM
  private key and certificate, without overwriting the original.
- Validate cryptographic signature integrity separately from certificate trust.
  An explicitly supplied trusted root is supported, offline by default.
- Show signer certificate subject, issuer and SHA-256 fingerprint without confusing
  an untrusted self-issued identity with verified CA identity.
- Semantic extraction of PDFium glyph bounding boxes, document metadata, bookmarks,
  form fields, links, annotations and attachment names/hashes (`details`).
- Page extraction, reordering, rotation and unsigned-PDF merging through qpdf,
  with separate output documents and JSON change records (`pages`, `merge`).
- One-click `analyze-all` report directory combining object/stream inventory,
  fonts/images, page operators, text geometry, PDFium glyph boxes, annotations,
  form fields, attachment hashes, signature checks (when present) and page PNGs.

## Installation

### Windows double-click launcher

When installed in the authorized qpdf-studio development directory, double-click:

`artifacts/qpdf-studio/启动 PDF Studio.cmd`

For a portable source checkout, you can also double-click
`extensions/pdf-studio/启动 qpdf Studio.cmd`. It uses an available project-local
virtual environment or a Python installation where this extension is installed.

It uses the local `.venv/Scripts/pythonw.exe` and opens the desktop UI without
requiring a PowerShell command. Errors are written to
`qpdf-upstream/extensions/pdf-studio/runtime_logs/startup.log`.
This is a development launcher, **not** a signed Windows installer.

To recreate or refresh the desktop shortcut:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_desktop_shortcut.ps1 -ShowResult
```

This uses the versioned `assets/qpdf-studio.ico` file. The shortcut installer
refuses to overwrite an unrelated shortcut with the same name.

The window supports selecting reference PDF A and comparison PDF B, page-by-page
zoomable previews, raw structural JSON inspection, visual/structural comparison,
signature validation, and signing a new copy with an owned P12/PFX certificate.
New UI controls provide detailed semantic inspection (full JSON export),
page selection/reordering/rotation and combining unsigned documents.
The window groups analysis, file operations and signing into separate control rows.
After comparing A/B, the **视觉差异图** tab previews the rendered pixel difference
directly, with page navigation. Large JSON data is preview-truncated in the
window while the exported files preserve the full available content.
The **完整解析导出** button writes all analysis artifacts into a new local folder.
The UI was instantiated and cleanly closed in an automated Windows startup check;
an interactive end-user desktop review is still needed before formal release.
The source PDF is never overwritten.
PDF rendering and parsing run in worker threads so the window remains responsive
during larger operations. The source files are not overwritten.

Inside this directory with Python 3.11 or later:

```powershell
python -m pip install -e .
qpdf-studio inspect own.pdf --out output/inspection --streams --operators --text-geometry --assets
qpdf-studio inspect own-other.pdf --out output/other
qpdf-studio compare output/inspection/manifest.json output/other/manifest.json --out output/diff.json
qpdf-studio render own.pdf --out output/render
qpdf-studio visual-diff own.pdf own-other.pdf --out output/visual
qpdf-studio details own.pdf --out output/details.json --max-pages 300
qpdf-studio pages own.pdf --select 3,1,2 --rotate 90 --out output/rearranged.pdf
qpdf-studio merge own.pdf other-unsigned.pdf --out output/merged.pdf
qpdf-studio analyze-all own.pdf --out output/complete-analysis --max-pages 150
qpdf-studio desktop
```

Sign with your own properly issued certificate and a PKCS#12 file:

```powershell
$env:PDF_SIGN_PASSWORD = "password-provided-locally"
qpdf-studio sign own.pdf --pfx own-credential.p12 --password-env PDF_SIGN_PASSWORD --out own-signed.pdf
qpdf-studio verify own-signed.pdf --trust-root trusted-root.pem
```

Or use `--key signer-key.pem --cert signer-cert.pem` for PEM credentials.
Passwords are read from an environment variable rather than exposed as CLI arguments.
Never commit credentials to the repository.

## Important boundaries

- **A self-issued certificate can produce a mathematically valid signature, but is not
  automatically an identity proof or a CA-issued credential.** Trust evaluation is
  separately reported. The software does not impersonate any CA or issuer.
- Trust-root inputs must be independently verified. Supplying a certificate as a trust
  root is a local decision, not certification by the named organization.
- No source PDF changes are silently hidden; signed PDFs are saved separately.
- Large or malicious PDFs require further resource isolation and limits.
- Full export can be large and include confidential document content; output
  folders are required to be new, and no information is uploaded automatically.
- On very large files, analysis and rendering may take significant memory or disk
  space. Page and glyph export limits apply to the semantic and preview layers.
- OCR, Word-like text editing, glyph-level layout for complex scripts,
  full cross-reference revision replay, certificate revocation/OCSP, and native
  C++ integration remain **future development**, not finished features.
- Logs, JSON exports, and extracted streams may contain sensitive content. The repository
  must **not** contain authentic banking documents or customer private data.

## Roadmap: missing qpdf functions to extend

1. Image/font extraction, text-glyph bounding boxes, complex script layout.
2. Rich visual diff (aligned page zones, selectable objects, image-by-image comparison).
3. Safe editing API for owned documents, explicit before/after reports.
4. Full signature trust-chain visualization, signed-revision analysis, revocation,
   hardware-backed credential providers and time-stamp authorities.
5. Desktop UI; large-file and malicious-input hardening; cross-platform packaging.

## Community disclosure

Security findings should specify the exact reader version, validation settings and
observed results; separate **confirmed exploit** from **plausible review-workflow risk**.
No evidence here establishes a cryptographic flaw in CFCA, a named bank, or qpdf.
If a defect affects an existing provider, disclose it responsibly before publication.

The qpdf upstream code is licensed under Apache-2.0. Read qpdf's `LICENSE.txt`;
this extension uses the Apache-2.0 license. Third-party dependencies retain their own licenses.


## Current editing behavior (2026-11-01)

Signature/certification presence no longer blocks text, font, image, vector,
annotation, form, property or page edits into a separate output PDF. Step 3 has
no developer/user mode gate, imported-B confirmation, or generated-preview
prerequisite. Existing B files can be signed, and additional signatures use
distinct form field names. No automatic modification-record files are written.
Source files are not overwritten. Encrypted inputs still require parser/API
password support, and unsupported glyph/object formats still produce an error.
This change was installed with syntax/import checks only; functional and GUI
tests were not run at the user's request.
