# 来源说明

qpdf Studio 是在已经公开的代码上继续开发的工作台，不是那些项目的替代品，也没有并入它们的官方发布。别人分享的部分仍归原作者，按原许可证使用。本目录里后续编写的程序按 Apache-2.0 再分享，见 [LICENSE](LICENSE)。

随这个目录一起发布时，请保留本文件。不要删掉上一级 qpdf 源码树里的 `NOTICE.md` 和 `LICENSE.txt`。

## qpdf

桌面程序通过 pikepdf 调用 qpdf / libqpdf。上一级目录是 qpdf 源码，C++ 源码没有被本扩展替换。

- 项目：https://github.com/qpdf/qpdf
- 说明：https://qpdf.readthedocs.io
- 版权：qpdf is copyright (c) 2005-2021 Jay Berkenbilt, 2022-2026 Jay Berkenbilt and Manfred Holger
- 许可证：Apache-2.0，https://www.apache.org/licenses/LICENSE-2.0
- 7 版之前也可以按 Artistic License 2.0 理解，文本在上一级目录的 `Artistic-2.0`
- qpdf 自己带入的 qtest、Rijndael 和 sphlib 说明仍以上一级 `NOTICE.md` 为准

## 直接使用的公开库

这些库没有复制进本仓库。安装依赖时保留它们各自的许可证。

| 库 | 用途 | 许可证 | 来源 |
| --- | --- | --- | --- |
| pikepdf | 调用 libqpdf 读写 PDF | MPL-2.0 | https://github.com/pikepdf/pikepdf |
| pypdf | PDF 读写辅助 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| pyHanko | PDF 数字签名与验签 | MIT | https://github.com/MatthiasValvekens/pyHanko |
| cryptography | 证书和密钥 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| pypdfium2 | 页面渲染；内含 PDFium | BSD-3-Clause 与 Apache-2.0，另有其打包的依赖许可证 | https://github.com/pypdfium2-team/pypdfium2 |
| Pillow | 图标和图像 | MIT-CMU | https://github.com/python-pillow/Pillow |
| PySide6 | 桌面界面；Qt for Python | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | https://pyside.org |
| ReportLab | 测试和部分文字绘制 | BSD；Copyright (c) 2000-2025, ReportLab Inc. | https://www.reportlab.com/ |

PySide6 使用 Qt 的 LGPL 或 GPL 条款。本扩展的 Python 源码仍是 Apache-2.0，这不改变 Qt 自身的许可证。分发打包后的程序时，要同时满足 Qt / PySide6 的义务。

## 本扩展

`extensions/pdf-studio` 里的后续开发，包括桌面流程、内容编辑和签名步骤，在上述代码之上补充。它不是 qpdf 官方命令行，也还没有得到 qpdf 维护者的合并或发布确认。
