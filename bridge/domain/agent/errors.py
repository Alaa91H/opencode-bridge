"""Typed agent-backend failures with operator-safe Arabic messages.

These errors deliberately do not subclass ``ProviderTaskError``: the task
retry classifier treats unknown exceptions as permanent failures, which is the
correct behaviour for a broken login, a missing binary or an unsupported TUI
version. Retryable Freebuff conditions raise a provider error explicitly.
"""

from __future__ import annotations


class AgentBackendError(Exception):
    """Base class for a backend failure the owner can act on."""

    user_message = "تعذر تنفيذ الطلب عبر خلفية الوكيل الحالية."

    def __init__(self, detail: str = "", *, user_message: str | None = None) -> None:
        super().__init__(detail or self.user_message)
        self.detail = detail
        if user_message:
            self.user_message = user_message


class BackendUnavailableError(AgentBackendError):
    user_message = (
        "خلفية Freebuff غير متاحة على الخادم. تأكد من تثبيت Freebuff CLI "
        "(npm install -g freebuff) ومن ضبط FREEBUFF_BIN ثم أعد المحاولة."
    )


class BackendAuthError(AgentBackendError):
    user_message = (
        "Freebuff CLI غير مسجّل الدخول على الخادم. نفّذ freebuff login مرة واحدة "
        "بحساب خدمة البريدج ثم أعد المحاولة."
    )


class BackendProtocolError(AgentBackendError):
    user_message = (
        "تعذر استخراج نتيجة موثوقة من Freebuff CLI لأن شكل الواجهة تغيّر أو أن "
        "إصدار CLI غير مدعوم. راجع docs/freebuff-integration-ar.md قبل إعادة المحاولة."
    )


class BackendTimeoutError(AgentBackendError):
    user_message = (
        "تجاوزت مهمة Freebuff الحد الزمني المسموح. حُفظت المهمة كفاشلة؛ "
        "أعد المحاولة أو ارفع FREEBUFF_TIMEOUT_SECONDS إذا كانت المهمة طويلة بطبيعتها."
    )
