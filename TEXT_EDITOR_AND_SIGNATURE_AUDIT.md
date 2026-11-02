# 可视化正文编辑与专业验签：本轮落地范围

## 后续迭代：真正直接替换已有文字

新增 `replace_text.py` 和 `qt_replace_dialog.py`：
通过鼠标点击已有 PDF 文字，使用 PDFium 返回定位线索，用 qpdf 定位对应
内容流文字绘制指令，直接替换原 `Tj` 或 `TJ` 字符串操作数。
以新内容重新生成页面内容流并另存 PDF，而不是通过白色块涂盖原内容。
保存后再检查指定的字符串操作数是否确实改动，并输出 SHA-256 变更记录。

现支持普通、未加密的 PDF 中受支持的标准
WinAnsi Type1 字体文字片段，和有可靠 ToUnicode 字符映射的部分
嵌入式 TrueType 字体。嵌入字体的反向替换只使用当前页该字体已实际
出现的字符编码，并拒绝未知字形。CID/Type0 的 Identity-H
双字节映射目前已有实际生成的 Type0/CIDFontType2 嵌入字体
Identity-H PDF 实测，包括单个 `Tj`、整个 `TJ` 数组和多个连续
`Tj` 操作的真实底层替换、PDFium 渲染与文字提取比对。
这些测试并不能证明所有真实复合字体 PDF 都能正确编辑。
复杂 ToUnicode/CMap、自动重新排版、跨多个操作符的完整句子替换继续开发。
目前已经具备 **真正的底层文字操作数替换**，但不能宣称所有 PDF 都可编辑。

本代码基于原有 qpdf (libqpdf/pikepdf) 编辑核心，不修改 qpdf 官方 C++ 上游源码，
也没有推送到 qpdf 官方仓库。新界面使用 Python + PySide6。

## 页面正文：已实现

1. Qt 页面预览中切换至 **点击添加正文**，再直接点击 A/B PDF 页面位置。
   点击位置映射为 PDF 用户坐标，位置原点为页面左下角。
2. 弹出编辑器：输入新增文字、文字大小、RGB 颜色和可选本地 TTF/OTF。
   如需非 Latin-1 字符，可选择有使用权限的可嵌入字体。
3. 通过 **预览实际页面**，将变化写入临时 PDF，使用 PDFium 渲染；
   只有确认并另存时才生成新的目标 PDF。
4. 使用 ReportLab 生成独立的字体资源和文字绘制指令；通过 qpdf
   把字体并入 `/Resources/Font`，并用 `contents_add()`
   将文字指令**直接追加到原页面 `/Contents`**。
   不使用 `page.add_overlay()`、整页图层、白色背景矩形或遮挡图片。
   PDF 的文字解析器可以搜索到新增文字。旧 PDF 原文保持不变。
5. 保存为新 PDF，不自动写入修改记录文件；可在 A/B 预览中比较。
6. Qt 中通过 **搜索文字** 获取 PDFium 字符位置，点击搜索结果跳转到对应页，
   将匹配字形加亮。此功能不包含 OCR。

### 明确的现阶段限制

- 对于可可靠解码的标准字体，已经能通过原字符串操作数直接替换已有文字；
  对于复杂嵌入字体和字形映射，尚不能自动重建原文字、替换或重排整个段落。
- 暂不支持拖拽调整文字框、复杂脚本排版、嵌套 Form XObject 自动逐字编辑。
- 对旋转、裁切、特殊坐标矩阵页面，鼠标位置与文本高亮还需要更完整的
  PDF 用户空间矩阵映射；复杂页面须人工确认最终预览。
- 已签名/认证文档不再被编辑拦截；结果另存，输入文档保留不变。加密输入仍需密码接口支持。

### 正文编辑明确禁止遮盖

- 旧文字只通过原 `Tj/TJ` 等内容流文本操作数修改，拒绝不支持的字体。
- 新增文字只通过 `/Contents` 和独立的 `/Resources/Font` 资源写入。
- 禁止白色矩形、背景涂色、图像、整页 Form XObject 或
  `add_overlay()/add_underlay()` 被用于替换旧内容。
- 反回归检查：`tests/test_no_masking_policy.py` 验证编辑模块无此类 API，
  结合 PDF 内容流断言确认替换时没有增加绘图指令。
- 红色像素差异图只输出到独立 PNG，用于研究分析，**不写入 PDF**。

## 专业验签：已实现

1. 使用 pyHanko 评估 PDF 签名的数学有效性、字节完整性、后续修订、覆盖等级。
2. 通过显式、独立核实后提供的可信根证书检查本地证书信任路径。
   未指定信任根时**不**将数学完整的签名者视为已验证身份。
3. 增强结果包含证书声称主体、颁发者、SHA-256 指纹、
   证书当前有效期、文档自报签署时间、ByteRange 覆盖终点及后续字节数量。
4. 默认离线。只有在 UI 主动选择后才允许联网 OCSP/CRL 相关证书验证，
   可选择软失败或严格要求吊销信息（依第三方证书服务可用性）。
5. Qt 签名面板按签名分条显示结果，提供 JSON 或静态 HTML 导出。
   报告是本地技术检查，**不是**任何机构、CA 或签发者的证明书。

### 尚未达到的专业级验签能力

- 完整的 PAdES-LT/LTA 生命周期、时间戳验证路径、DSS/VRI 证据长期保存，
  各类策略与证书吊销服务器的跨软件互操作评估。
- 证书主体文本仅为证书内声明；必须由实际信任链和独立业务来源支持身份结论。
- 当前的 `/Prev` 修订索引不等于可以完整恢复、展示每个历史 PDF 修订的页面图。

## 命令行

```powershell
qpdf-studio find-text own.pdf --query "keyword" --out matches.json
qpdf-studio add-body-text own-unsigned.pdf --page 1 --x 75 --y 640 --text "New text" --out edited-copy.pdf
qpdf-studio verify signed.pdf --trust-root trusted-root.pem --html-out local-signature-audit.html
# 必须明确授权联网才能开启:
qpdf-studio verify signed.pdf --trust-root trusted-root.pem --allow-network
```

文档和测试中使用的签名证书都是独立生成的虚构测试证书；
不要把它们解释为任何真实 CA 的签发证书。
