"""Submission facade — re-export only.

Do NOT implement submission logic here. Use accounting.services.submission.SubmissionService.
Frozen per ADR-0009.
"""

from accounting.services.submission.service import SubmissionService

__all__ = ['SubmissionService']
