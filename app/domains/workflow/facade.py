"""Workflow domain facade — public API."""

from accounting.services.submission.service import SubmissionService

MATURITY = 'L1'

__all__ = ['MATURITY', 'SubmissionService']
