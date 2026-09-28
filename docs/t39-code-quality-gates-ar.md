# T39 — Code Quality Gates

Code Quality workflow مستقل يضيف Ruff وmypy وformat/import checks وdead-code/complexity reports.

التطبيق تدريجي كي لا يكسر main بلا migration: أخطاء Ruff الحرجة E9/F63/F7/F82 blocking على bridge كله. formatting/import sorting blocking على طبقات v2 infrastructure/services. mypy blocking على الوحدات الحديثة tracing/security/health. Vulture وC901 يبدأان report-only لأن المستودع القديم لم يمر migration كاملة بعد.

هذه السياسة تجعل كل كود جديد ضمن المسارات الحديثة يخضع للformat/import/type gates، بينما تعرض الدين التقني القديم وتسمح بتشديد threshold تدريجيًا بدل تعطيل التطوير دفعة واحدة.
