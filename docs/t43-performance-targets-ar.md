# T43 — Performance Targets

البيئة المرجعية: Linux حديث، Python 3.12–3.14، SQLite WAL على SSD، OpenCode محلي loopback، والـTelegram/model provider خارجيان لذلك تقاس أهدافهما منفصلة عن زمن الشبكة الخارجي.

| الهدف | المعيار |
|---|---|
| committed tasks | صفر فقدان بعد crash/restart |
| schedule occurrence | صفر duplicate لنفس occurrence/idempotency key |
| text ACK | أقل من 1s عادةً قبل انتظار provider الخارجي |
| DB locks | صفر failures غير مستعادة في الحمل المستهدف |
| worker crash | lease recovery تلقائي |
| restart | schedules/drafts محفوظة |
| large file RAM | bounded chunks، لا نمو proportional للحجم |
| queue scale | 10k+ records دون تغير correctness أو lock failure |
| critical paths | metrics T29 + traces T30 |
| uptime target | 99.9% شهريًا للخدمة الذاتية، باستثناء outages providers الموثقة |

## القياس

T05/T06/T07/T09 تثبت durability/idempotency/restart. T12 يثبت streaming RAM. T29/T30 يوفران metrics/traces. T41 يثبت recovery. T42 يوفر load/soak harness.

ACK <1s و10k queue/uptime ليست ضمانات مطلقة لكل host؛ هي SLOs قابلة للقياس ويجب جمع p50/p95/p99 وerror budget من بيئة الإنتاج/soak. فشل SLO لا يسمح بإخفائه عبر retries؛ يسجل كmetric/failure record ويستخدم لتعديل resource policy.
