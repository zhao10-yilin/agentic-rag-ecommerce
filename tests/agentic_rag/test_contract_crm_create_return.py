"""Auto-generated contract tests for business rules."""
import pytest
from agentic_rag.tools.contracts import ContractChecker, ContractPhase
from agentic_rag.models import ToolResult, ToolCall
from agentic_rag.tools.contracts import CRM_CREATE_RETURN_CONTRACTS

CONTRACTS = CRM_CREATE_RETURN_CONTRACTS

@pytest.mark.parametrize('test_case', [
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'user_tier': 'vip'}, 'should_pass': True},
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'user_tier': 'regular'}, 'should_pass': False},
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'user_tier': ''}, 'should_pass': False},
])
def test_contract_crm_create_return_仅VIP用户可退货(test_case):
    """Contract: 仅VIP用户可退货"""
    checker = ContractChecker([CONTRACTS[0]])
    call = ToolCall(tool_name='crm_create_return', input=test_case['input'])
    ctx = test_case.get('context', {})
    violations = checker.check_preconditions(call, ctx)
    if test_case['should_pass']:
        assert violations == [], f'Expected pass, got: {violations}'
    else:
        assert violations != [], f'Expected violation, got none'

@pytest.mark.parametrize('test_case', [
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'order_status': 'completed'}, 'should_pass': True},
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'order_status': 'pending'}, 'should_pass': False},
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'order_status': 'returned'}, 'should_pass': False},
])
def test_contract_crm_create_return_仅已完成订单可退货(test_case):
    """Contract: 仅已完成订单可退货"""
    checker = ContractChecker([CONTRACTS[1]])
    call = ToolCall(tool_name='crm_create_return', input=test_case['input'])
    ctx = test_case.get('context', {})
    violations = checker.check_preconditions(call, ctx)
    if test_case['should_pass']:
        assert violations == [], f'Expected pass, got: {violations}'
    else:
        assert violations != [], f'Expected violation, got none'

@pytest.mark.parametrize('test_case', [
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'already_returned': False}, 'should_pass': True},
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'already_returned': True}, 'should_pass': False},
])
def test_contract_crm_create_return_不可重复退货(test_case):
    """Contract: 不可重复退货"""
    checker = ContractChecker([CONTRACTS[2]])
    call = ToolCall(tool_name='crm_create_return', input=test_case['input'])
    ctx = test_case.get('context', {})
    violations = checker.check_preconditions(call, ctx)
    if test_case['should_pass']:
        assert violations == [], f'Expected pass, got: {violations}'
    else:
        assert violations != [], f'Expected violation, got none'

@pytest.mark.parametrize('test_case', [
    {'input': {'order_id': 'ORD-001', 'reason': '瑕疵'}, 'context': {'original_price': 299.0}, 'should_pass': True, 'mock_result': ToolResult(tool_name='crm_create_return', status='success', summary='退款 ¥299.0', structured_data={'refund_amount': 299.0}, error=None, error_code=None, cache_hit=False, elapsed_ms=0.0)},
])
def test_contract_crm_create_return_退款金额不超过原订单金额(test_case):
    """Contract: 退款金额不超过原订单金额"""
    checker = ContractChecker([CONTRACTS[3]])
    call = ToolCall(tool_name='crm_create_return', input=test_case['input'])
    ctx = test_case.get('context', {})
    mock_result = test_case.get('mock_result')
    if mock_result is None:
        pytest.skip('No mock_result provided')
    violations = checker.check_postconditions(call, mock_result, ctx)
    if test_case['should_pass']:
        assert violations == [], f'Expected pass, got: {violations}'
    else:
        assert violations != [], f'Expected violation, got none'
