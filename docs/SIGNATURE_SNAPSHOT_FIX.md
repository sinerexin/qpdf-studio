# `cannot pickle '_io.BufferedReader' object` 修复（2026-11-01）

## 用户截图中的具体错误

`启动 B 签名生成失败: TypeError: cannot pickle '_io.BufferedReader' object`

这不是按证书身份、CA 或验签结果禁止生成的判断，而是上一轮新入口使用 `deepcopy()` 引入的运行时错误。

## 已定位的代码链

1. `qt_automatic_signing.start_automatic_b_signing` 在提交后台任务之前，对 B 的 A 参考模板调用 `deepcopy()`。这与截图中的“启动 B 签名生成失败”前缀吻合。
2. 参考模板持有 A 的原始解析报告；例如 `/ByteRange` 使用 `list(dictionary['/ByteRange'])` 取值，列表元素仍可能是 pyHanko `NumberObject`，不是纯 Python `int`。
3. 当前安装的 pyHanko 源码中，`NumberObject` 同时继承 `int`、`PdfObject`；`PdfObject.container_ref` 会关联 PDF reader，reader 再关联文件流。
4. `deepcopy` 不仅复制数字值，还可能追踪这些内部属性，最终试图复制 `BufferedReader` 并报错。
5. 后台的 `automatic_signing.generation_reference` 原来也对整条报告再次深拷贝，属于相同问题的第二个触发点。只删入口的一次复制不足以修复整个流程。

本轮没有读取用户银行文件正文、没有重新执行失败操作；上述定位依据是截图的错误阶段和本机实际源码。

## 修改内容

- 新增 `signature_values.detached_values`：按解析值创建独立容器，对整数、字符串、字节等明确转换为 Python 基础类型；PDF 小数保留十进制精度。
- 不遍历 `__dict__`、`container_ref`，不复制 reader，不对文件流做 read/seek/close。
- 意外出现的非数据运行时对象明确标注类型，不能把它冒充 PDF 源数据；不读取其内部内容。
- 主按钮复制参考模板和后台提取 A 参数都改用纯值快照，不再调用 `deepcopy`/pickle。
- 对象扫描和统一读取接口在返回报告前也转换为纯值，从来源处断开解析器关联，避免下游再次携带文件句柄。
- 不改变证书主体名称、密钥算法、摘要算法或 `True/False/None` 验证结论；A/B 仍分别检查各自实际文件。
- 新代码带中文注释，并增加虚构字节/文件句柄回归用例。

## 检查与运行边界

按此前“不需要验证”的要求，仅进行语法、导入、静态调用接线和部署 SHA 核对；回归用例已编写但未运行，未启动/重启 GUI、未实际签署、未生成密钥、未修改用户 PDF。

用户保存当前工作并重新打开桌面“qpdf Studio 最新版”后才会加载本次修复。
