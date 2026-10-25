"""Read-only scene error semantics; unknown failures retain legacy safeguards."""


class SceneReadIssue(ValueError):
    def __init__(self, message, *, kind, stage='metadata', retryable=False, scope_verified=False):
        super().__init__(message)
        self.kind, self.stage = kind, stage
        self.retryable, self.scope_verified = retryable, scope_verified


def issue_summary(error):
    """Safe structured diagnostics, without pointer values or raw exception text."""
    if isinstance(error, SceneReadIssue):
        return {key: getattr(error, key) for key in ('kind', 'stage', 'retryable', 'scope_verified')}
    return {'kind': 'io_error' if isinstance(error, OSError) else 'unknown',
            'stage': 'snapshot', 'retryable': False, 'scope_verified': False}
