"""Workflow domain — submission outbox, approvals.

Maturity: L1
Legacy apps: accounting/services/submission/
"""

from domains.workflow.facade import MATURITY, SubmissionService

__all__ = ['MATURITY', 'SubmissionService']
