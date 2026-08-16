from __future__ import annotations

from lifecycle_guard.ast_scanner import scan_python_source
from lifecycle_guard.config import LIFECYCLE_PROTECTED_MODELS, LifecycleProtectedModel


def _scan(snippet: str, path: str = 'banking/services/foo.py') -> list:
    return scan_python_source(snippet, path=path, registry=LIFECYCLE_PROTECTED_MODELS)


class TestDirectStatusAssignment:
    def test_proven_payment_order_assignment_is_violation(self):
        snippet = """
order = PaymentOrder.objects.get(pk=1)
order.status = "executed"
"""
        violations = _scan(snippet)
        assert len(violations) == 1
        assert violations[0].model == 'PaymentOrder'
        assert violations[0].field == 'status'
        assert 'direct assignment' in violations[0].message

    def test_unproven_variable_is_not_violation(self):
        snippet = """
order = something()
order.status = "executed"
"""
        assert _scan(snippet) == []

    def test_unrelated_model_attribute_is_not_violation(self):
        snippet = """
connection.status = "connected"
"""
        assert _scan(snippet) == []

    def test_comment_is_not_violation(self):
        snippet = """
# order.status = "x"
"""
        assert _scan(snippet) == []

    def test_string_literal_is_not_violation(self):
        snippet = '''
s = "order.status = executed"
'''
        assert _scan(snippet) == []

    def test_status_read_is_not_violation(self):
        snippet = """
order = PaymentOrder.objects.get(pk=1)
if order.status == "submitted":
    pass
"""
        assert _scan(snippet) == []


class TestQuerySetUpdate:
    def test_direct_payment_order_update_is_violation(self):
        snippet = """
PaymentOrder.objects.filter(pk=1).update(status="executed")
"""
        violations = _scan(snippet)
        assert len(violations) == 1
        assert 'QuerySet.update()' in violations[0].message

    def test_all_objects_update_is_violation(self):
        snippet = """
PaymentOrder.all_objects.update(status="executed")
"""
        assert len(_scan(snippet)) == 1

    def test_indirect_queryset_variable_is_not_violation(self):
        snippet = """
qs = PaymentOrder.objects.all()
qs.update(status="x")
"""
        assert _scan(snippet) == []


class TestAllowedModules:
    def test_allowed_module_is_skipped(self):
        snippet = """
order = PaymentOrder.objects.get(pk=1)
order.status = "executed"
"""
        path = 'banking/services/payment_order_lifecycle.py'
        assert scan_python_source(snippet, path=path, registry=LIFECYCLE_PROTECTED_MODELS) == []

    def test_execution_allowed_module_is_skipped(self):
        snippet = """
execution = PaymentExecution.objects.get(pk=1)
execution.status = "executed"
"""
        path = 'banking/services/payment_execution_lifecycle.py'
        assert scan_python_source(snippet, path=path, registry=LIFECYCLE_PROTECTED_MODELS) == []


class TestPaymentExecutionScanner:
    def test_proven_payment_execution_assignment_is_violation(self):
        snippet = """
execution = PaymentExecution.objects.get(pk=1)
execution.status = "executed"
"""
        violations = _scan(snippet)
        assert len(violations) == 1
        assert violations[0].model == 'PaymentExecution'


class TestTypeHintsAndLoops:
    def test_type_hint_binds_variable(self):
        snippet = """
def transition(order: PaymentOrder) -> None:
    order.status = "executed"
"""
        violations = _scan(snippet, path='banking/services/pis_orders.py')
        assert len(violations) == 1

    def test_for_loop_binds_variable(self):
        snippet = """
for order in PaymentOrder.objects.filter(status="draft"):
    order.status = "submitted"
"""
        violations = _scan(snippet)
        assert len(violations) == 1


class TestDeterministicOutput:
    def test_violations_sorted_by_path_and_line(self):
        registry = (
            LifecycleProtectedModel(
                model='PaymentOrder',
                field='status',
                allowed_modules=frozenset(),
                scan_packages=frozenset({'banking'}),
            ),
        )
        snippet_a = 'order = PaymentOrder.objects.get(pk=1)\norder.status = "a"\n'
        snippet_b = 'PaymentOrder.objects.update(status="b")\n'
        v_a = scan_python_source(snippet_a, path='banking/a.py', registry=registry)
        v_b = scan_python_source(snippet_b, path='banking/b.py', registry=registry)
        combined = sorted(v_a + v_b, key=lambda v: (v.path, v.line))
        assert combined[0].path == 'banking/a.py'
        assert combined[1].path == 'banking/b.py'
