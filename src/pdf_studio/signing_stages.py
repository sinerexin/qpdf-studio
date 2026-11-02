"""同一签名会话的五个真实工序；一键运行和分项操作共用这些方法。"""
from pathlib import Path

from .automatic_pipeline import prepare_a_parameters
from .automatic_signing import generated_signature_entries, validate_generation_request
from .credential_passwords import new_password, load_managed_password
from .file_identity import file_identity
from .keygen import _new_private_key, generate_personal_credentials
from .signature_evidence import read_signature_audit, public_key_details
from .signing import sign_document
from .signature_values import detached_values

STAGE_TITLES = ('读取 A 参数', '生成 B 密钥', '生成 B 证书', '签署 B', '检查与对照')


class SigningStages:
    def __init__(self, source, output, credential_folder, **options):
        self.source, self.output, self.folder = map(Path, (source, output, credential_folder))
        self.options = options
        self.phase = 0
        self.reference = {}
        self.parameters = {}
        self.generated = None
        self.public_key = None
        self.signed = None
        self.signed_identity = None
        self.audit, self.audit_error = None, None
        self.original_checked = False
        self._key = None
        # 私钥对象仅由后台会话持有，永不放入 UI 的值快照、JSON 或开发记录。
        self.ui_context = None

    def read_parameters(self):
        self.reference = prepare_a_parameters(
            original=self.options.get('original'),
            selected_field=self.options.get('selected_field'),
            expected_original_identity=self.options.get('expected_original_identity'),
            cached_original_audit=self.options.get('cached_original_audit'),
            cached_original_error=self.options.get('cached_original_error'),
            use_cached_original_read=bool(self.options.get('use_cached_original_read')))
        self.parameters = self.reference['parameters']
        original = self.options.get('original')
        protected = (*self.options.get('protected_paths', ()), original) if original is not None else self.options.get('protected_paths', ())
        validate_generation_request(self.source, self.output, self.folder,
            label=self.parameters['label'], algorithm=self.parameters['algorithm'],
            days_valid=self.parameters['days_valid'], protected_paths=protected)
        self.phase = 1

    def generate_keys(self):
        # 第二阶段只生成密钥，不顺手重签文件或生成证书；第三阶段保存同一把密钥。
        self._key = _new_private_key(self.parameters['algorithm'])
        self.public_key = public_key_details(self._key.public_key())
        self.phase = 2

    def generate_certificate(self):
        password = new_password()
        try:
            self.generated = generate_personal_credentials(self.folder,
                label=self.parameters['label'], algorithm=self.parameters['algorithm'],
                days_valid=self.parameters['days_valid'], subject_attributes=self.parameters.get('subject_attributes'),
                serial_number=self.parameters.get('serial_number'),
                not_valid_before=self.parameters.get('not_before'),
                not_valid_after=self.parameters.get('not_after'),
                password=password, store_password=True, prepared_key=self._key)
        finally:
            password = None
        # 证书、P12 和加密私钥已持久化，后续签署使用 Windows 托管密码，不保留明文私钥实例。
        self._key = None
        self.phase = 3

    def sign_b(self):
        expected = self.options.get('expected_identity')
        if expected is not None and file_identity(self.source) != expected:
            raise RuntimeError('当前累计 B 已变化，请重新读取参数开始本轮流程。')
        secret = load_managed_password(self.folder / 'personal-signing.p12')
        try:
            if secret is None:
                raise RuntimeError('未找到本轮凭据的自动密码记录。')
            # 使用第三步实际证书和第二步同一把密钥，不再生成另一套替换用户的中间结果。
            self.signed = sign_document(self.source, self.output,
                pfx=self.folder / 'personal-signing.p12', password=secret.encode('utf-8'),
                digest_algorithm=self.parameters['digest'])
        finally:
            secret = None
        self.signed_identity = file_identity(self.signed)
        self.phase = 4

    def check_result(self):
        self.audit, self.audit_error = None, None
        # A 在这一步直接重读原稿。第一环节只为取参数，不能当作这次检查。
        self._check_original_now()
        try:
            if file_identity(self.signed) != self.signed_identity:
                raise RuntimeError('签署输出已变化，不能使用旧文件的检测结果。')
            # B 只检查本轮生成后的签署文件，不把原稿报告抄过来。
            self.audit = read_signature_audit(self.signed, [], allow_network=False)
            if file_identity(self.signed) != self.signed_identity:
                raise RuntimeError('检测期间签署输出已变化，旧报告已丢弃。')
        except Exception as exc:
            # 检查失败也保留真实签署文件和凭据，不伪造“通过”。第五阶段可单独重试。
            self.audit = None
            self.audit_error = f'{type(exc).__name__}: {exc}'
        self.phase = 5

    def _check_original_now(self):
        original = self.options.get('original')
        if original is None:
            self.original_checked = False
            return
        self.original_checked = True
        original = Path(original)
        try:
            current = file_identity(original)
            expected = self.options.get('expected_original_identity')
            if expected is not None and current != expected:
                raise RuntimeError('原稿已变化，未沿用旧的 A 检查结果。')
            report = read_signature_audit(original, [], allow_network=False)
            if file_identity(original) != current:
                raise RuntimeError('检查 A 期间原稿已变化，旧结果已丢弃。')
        except Exception as exc:
            self.reference['original_audit'] = None
            self.reference['original_read_error'] = f'{type(exc).__name__}: {exc}'
            return
        self.reference['original_audit'] = report
        self.reference['original_read_error'] = None
        self.reference['original_identity'] = current

    def run_to(self, target):
        operations = (self.read_parameters, self.generate_keys, self.generate_certificate,
                      self.sign_b, self.check_result)
        while self.phase < target:
            index = self.phase
            try:
                operations[index]()
            except Exception as exc:
                # 工序名与保留的文件位置明确显示，失败不会把已完成步骤误标成未发生。
                raise RuntimeError(f'第 {index + 1} 环节「{STAGE_TITLES[index]}」失败：'
                                   f'{type(exc).__name__}: {exc}；凭据位置：{self.folder}') from exc
        return self.result()

    def result(self):
        matches = generated_signature_entries(self.audit, self.generated or {})
        return dict(pdf=self.signed, credential_folder=self.folder, credential_summary=self.generated,
                    audit=self.audit, audit_error=self.audit_error, signed_identity=self.signed_identity,
                    audit_pending=self.phase == 4, new_signature_field=matches[0].get('field') if len(matches) == 1 else None,
                    new_signature_match_count=len(matches), phase=self.phase,
                    original_audit=self.reference.get('original_audit'),
                    original_read_error=self.reference.get('original_read_error'),
                    original_identity=self.reference.get('original_identity'),
                    original_checked=self.original_checked,
                    parameters=detached_values(self.parameters), public_key=detached_values(self.public_key),
                    pipeline_stages=self._completed_stage_titles())

    def _completed_stage_titles(self):
        # 第五环节抛错时阶段号仍是 5，方便单独重试；完成列表不能把它写成已通过。
        titles = list(STAGE_TITLES[:self.phase])
        if self.audit_error and self.phase >= 5 and titles:
            titles = titles[:-1]
        return titles
