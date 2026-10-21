# T18 — Context Management

ContextBudget يحجز output tokens ويعطي حدًا صريحًا لـinput. estimate_tokens adapter محافظ ولا يحتاج dependency، ويمكن استبداله لاحقًا tokenizer خاص بالمزود.

ContextManager يوفر:
- conversation summarization/compression bounded.
- retrieval من history حسب query/relevance.
- selective attachment/context item inclusion.
- prompt يبقى required ولا يسقط بسبب العناصر الاختيارية.
- ContextCheckpoint يحفظ summary + selected items + omitted count + token estimate لإكمال المهمة بعد ضغط السياق.
- overflow لا يتجاوز budget؛ العناصر الأقل صلة تُحذف ثم يُحاول إدخال summary مضغوط إن بقيت سعة.

الاختبارات تغطي overflow، بقاء prompt، retrieval، token budget، وإعادة استخدام checkpoint.
