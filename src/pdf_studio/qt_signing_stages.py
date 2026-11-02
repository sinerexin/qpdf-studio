"""五阶段共用的 Qt 调度与回填，不在按钮间复制私钥/解析器句柄。"""
import uuid
from pathlib import Path

from .signing_stages import SigningStages, STAGE_TITLES


def run_signature_stage(shell, target, *, automatic=False):
    w = shell.w
    if w._busy:
        w._notify('后台任务处理中；完成后可继续本轮签名流程。')
        return
    context = shell._signing_context_identity()
    session = getattr(shell, '_signing_stage_session', None)
    selected = shell.signature_workspace.panes['A']._selected().get('field')
    # 第五按钮只检查；没有本轮已签署输出时，也不能因为点检测而暗中生成新签名。
    if target == 4 and not automatic:
        w._notify('这一步不写 PDF。请检测 A / B，再点击「进入第 4 步」，然后点导出写出签署文件。')
        return
    if target == 5 and not automatic and (session is None or session.phase < 4 or session.ui_context != context):
        shell._staged_check_pending_identity = None
        shell.compare_current_signatures()
        return
    if (session is None or session.ui_context != context or target == 1
            or (automatic and session.phase >= 5)
            or ((automatic or target < 5) and session.options.get('selected_field') != selected)):
        source = w.docs.get('B')
        if source is None:
            w._error('请先准备或导入 B 工作副本，再执行签名流程。')
            return
        # 只保存数据时，凭据放在本次运行的临时目录，不在原稿旁边提前生成 PDF 或凭据夹。
        if automatic and target <= 3:
            scratch = Path(w._scratch.name)
            token = uuid.uuid4().hex[:8]
            output = scratch / f'pending-export-{token}.pdf'
            folder = scratch / f'b-credentials-{token}'
        else:
            output = shell.default_signed_output(source)
            base = output
            while output.exists():
                output = base.with_name(base.stem + '-' + uuid.uuid4().hex[:8] + '.pdf')
            folder = output.parent / (output.stem + '-credentials-' + uuid.uuid4().hex[:8])
        cached_a = shell._cached_signature('A')
        session = SigningStages(source, output, folder, original=w.docs.get('A'),
            selected_field=selected, expected_identity=context[1],
            expected_original_identity=context[0], protected_paths=tuple(p for p in w.docs.values() if p),
            cached_original_audit=cached_a[1] if cached_a and cached_a[0] == context[0] else None,
            cached_original_error=cached_a[2] if cached_a and cached_a[0] == context[0] else None,
            use_cached_original_read=bool(cached_a and cached_a[0] == context[0]))
        session.ui_context = context
        shell._signing_stage_session = session
        shell.certificate_tools.set_phase(0)
    elif target <= session.phase and target != 5:
        # 已生成的中间产物可以复用；不因再次查看某一步生成另一套密钥覆盖当前状态。
        _show_stage_result(shell, session, session.result())
        return
    elif automatic and target <= 3 and session.phase < 3 and not Path(session.folder).exists():
        scratch = Path(w._scratch.name)
        if Path(session.folder).parent != scratch:
            token = uuid.uuid4().hex[:8]
            session.output = scratch / f'pending-export-{token}.pdf'
            session.folder = scratch / f'b-credentials-{token}'

    shell._signature_timer.stop()
    shell._b_generation_active = True

    def run():
        if target == 5 and session.phase == 5:
            session.check_result()
            return session.result()
        return session.run_to(target)

    def completed(result):
        try:
            if shell._signing_context_identity() != context:
                w._notify(f'流程已完成到第 {session.phase} 环节；文档已切换，产物保留于 {session.folder}，未覆盖新界面。')
                return
            _show_stage_result(shell, session, result)
        finally:
            shell._b_generation_active = False
            shell.request_signature_reads()

    def failed(error):
        shell._b_generation_active = False
        shell.certificate_tools.set_phase(session.phase)
        try:
            w._error(error)
        finally:
            shell.request_signature_reads()

    try:
        w._task('生成新签名 · 只保存签名数据，不写 PDF' if automatic else
                '执行至第 ' + str(target) + ' 环节 · ' + STAGE_TITLES[target - 1], run, completed, on_failed=failed)
    except Exception:
        shell._b_generation_active = False
        shell.request_signature_reads()
        raise


def _show_stage_result(shell, session, result):
    w = shell.w
    # 第 5 环节才把 A 的检查写入缓存。前面环节的读取只用于生成参数。
    identity = result.get('original_identity')
    if identity is not None and identity == shell._signature_identity('A'):
        record = (identity, result.get('original_audit'), result.get('original_read_error'))
        if session.phase >= 5:
            shell._signature_cache['A'] = record
        elif hasattr(shell, '_cached_signature') and shell._cached_signature('A') is None and (record[1] is not None or record[2]):
            # 生成时刚读过 A。先放进显示缓存，完成后不再为同一份文件排一次读取。
            shell._signature_cache['A'] = record
    if session.phase >= 4:
        # 第四步载入真实文件；尚未运行第五步时不能把检查写为通过。
        shell._staged_check_pending_identity = result['signed_identity'] if session.phase == 4 else None
        if shell._signature_identity('B') != result['signed_identity']:
            w.load_path('B', result['pdf'])
        w.active_slot.setCurrentText('B')
        shell._select_signing_value('credential', result['credential_folder'] / 'personal-signing.p12')
        if session.phase >= 5:
            shell._signature_cache['B'] = (result['signed_identity'], result['audit'], result['audit_error'])
        else:
            shell._signature_cache.pop('B', None)
        shell.signed_output_completed(session.source, result['pdf'])
        session.ui_context = shell._signing_context_identity()
    else:
        # 只保存签名数据时不打开检查器，也不重画页面。那一下会在生成结束时把界面卡住。
        if getattr(shell, '_mode', None) == 1 and hasattr(shell, 'document_stage'):
            shell.document_stage.setCurrentIndex(1)
        if hasattr(shell, '_refresh_signature_sources'):
            shell._refresh_signature_sources()
    if session.phase >= 2 and session.phase < 4:
        # 公钥和待用证书单独展示；不是 PDF 已有签名，更不复制 A 的检查结果。
        certificate = {'subject': result['parameters']['label'], 'public_key': result['public_key']}
        if session.phase >= 3:
            from asn1crypto import x509 as asn_x509
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            from .signature_evidence import certificate_details
            cert = x509.load_pem_x509_certificate((session.folder / 'self-signed-certificate.pem').read_bytes())
            certificate = certificate_details(asn_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)))
            shell._select_signing_value('credential', session.folder / 'personal-signing.p12')
        for name in ('signature_workspace', 'signature_comparison'):
            getattr(shell, name).panes['B'].set_generated_credentials({
                'public_certificate': certificate, 'folder': str(session.folder),
                'note': '当前流程准备值，尚未签入 PDF；第 3 环节才将密钥加密保存。' if session.phase == 2 else
                        '本轮签名数据已保存，尚未写入 PDF。请检测 A / B，进入第 4 步后点导出。'})
    check_failed = bool(result.get('audit_error')) and session.phase >= 5
    shell.certificate_tools.set_phase(session.phase, check_failed=check_failed)
    completed = STAGE_TITLES[:4] if check_failed else STAGE_TITLES[:session.phase]
    summary = '已完成：' + ' → '.join(completed)
    if session.phase == 4:
        summary += '；等待第 5 环节检查，不代表检查已通过。'
    if session.phase == 2:
        summary += '；密钥暂存当前会话，生成证书时统一加密保存。'
    if session.phase == 3:
        summary += '；签名数据已保存，尚未写 PDF。请检测 A / B，进入第 4 步后点导出。'
    if result.get('audit_error'):
        summary += '；第 5 环节检查未完成：' + result['audit_error']
    if result.get('original_read_error'):
        if session.phase >= 5:
            summary += '；A 原稿检查未完成：' + result['original_read_error']
        else:
            summary += '；A 读取错误已保留，使用默认参数。'
    shell.generated_credentials_note.setText(summary)
    w._notify(summary)
