"""No provenance, signature-state or mode gates remain in step 3."""
from pdf_studio.workflow_policy import signing_block_reason
from pdf_studio.signature_presentation import status_cells, coverage_description


def test_loaded_b_needs_no_provenance_or_signature_confirmation():
    assert not signing_block_reason(busy=False, b_accessible=True)


def test_missing_input_and_active_worker_are_reported():
    assert signing_block_reason(busy=False, b_accessible=False)
    assert signing_block_reason(busy=True, b_accessible=True)


def test_missing_evidence_is_never_passed():
    assert all(value != '通过' for _, value, _ in status_cells(None, {}, {}))


def test_historical_coverage_does_not_claim_current_file():
    # 覆盖范围移到详细依据，统一状态卡保留五项独立检查。
    audit = {'coverage': 'SignatureCoverageLevel.ENTIRE_REVISION'}
    assert '历史修订' in coverage_description(audit)
    assert '覆盖当前文件' not in coverage_description(audit)
