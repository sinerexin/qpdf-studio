"""一键生成新签名的界面入口。五阶段执行在 qt_signing_stages。"""


def start_automatic_b_signing(shell):
    """只保存本轮参数、密钥和证书，不签署、不写出 PDF。"""
    from .qt_signing_stages import run_signature_stage
    run_signature_stage(shell, 3, automatic=True)
