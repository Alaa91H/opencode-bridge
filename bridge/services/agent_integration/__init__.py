"""Agent Integration Engine - محرك التكامل بين الوكيل والنظام.

يوفر تكاملاً ثنائي الاتجاه بين الوكيل والنظام:
- تحليل النوايا الذكي للبرومبتات
- تخطيط التنفيذ الذكي للمهام المعقدة
- التكامل بين الوكيل والنظام (طلب إجراءات، استقبال أحداث، التحقق من المخرجات)
"""

from bridge.services.agent_integration.agent_integration_engine import (
    AgentSystemIntegration,
    SystemActionRequest,
    SystemEvent,
    ValidationResult,
)
from bridge.services.agent_integration.execution_planner import (
    ExecutionPlan,
    ExecutionStepStatus,
    SmartExecutionPlanner,
    Subtask,
)
from bridge.services.agent_integration.intent_analyzer import (
    ComplexityLevel,
    IntentAnalysis,
    IntentAnalyzer,
    IntentType,
    Requirement,
)

__all__ = [
    "AgentSystemIntegration",
    "ComplexityLevel",
    "ExecutionPlan",
    "ExecutionStepStatus",
    "IntentAnalysis",
    "IntentAnalyzer",
    "IntentType",
    "Requirement",
    "SmartExecutionPlanner",
    "Subtask",
    "SystemActionRequest",
    "SystemEvent",
    "ValidationResult",
]
