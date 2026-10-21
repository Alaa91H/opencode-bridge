# T23 — Priority & Fairness

FairQueuePolicy يدعم LOW/NORMAL/HIGH/URGENT. الأولوية تعطي baseline واضحًا، بينما aging يزداد دون سقف كي لا تبقى مهمة منخفضة الأولوية جائعة إلى الأبد.

fairness per-owner يتحقق عبر owner_running penalty: المالك الذي لديه أعمال جارية أكثر يخسر جزءًا من score عند تساوي بقية الظروف. resource weighting يخصم من المهام الثقيلة لتقليل حجب الأعمال الخفيفة عندما تتساوى الأولوية والعمر.

interactive tasks تحصل boost صريحًا، لذلك لا تحجبها schedule ثقيلة متساوية الأولوية. لا يلغي هذا urgent أو aging؛ المهمة القديمة جدًا تستطيع التقدم، وهو شرط منع starvation.

الترتيب deterministic عند التعادل باستخدام enqueued_at ثم owner ثم task_id.
