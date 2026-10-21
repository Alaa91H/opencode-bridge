"""نظام سير العمل التلقائي (Automation Pipelines).

يتيح إنشاء وتنفيذ سير عمل تلقائي للمهام المتكررة مثل:
- النشر التلقائي عند دمج طلبات السحب
- إنشاء إصدارات تلقائية عند دمج فروع الإصدار
- إغلاق القضايا تلقائياً عند دمج طلبات السحب المرتبطة
- إرسال إشعارات عند الأحداث المهمة
"""

from bridge.services.github.automation.pipeline_manager import (
    AutomationPipeline,
    AutomationPipelineManager,
    PipelineExecution,
    PipelineStep,
    PipelineStepStatus,
    PipelineTrigger,
)

__all__ = [
    "AutomationPipeline",
    "AutomationPipelineManager",
    "PipelineExecution",
    "PipelineStep",
    "PipelineStepStatus",
    "PipelineTrigger",
]
