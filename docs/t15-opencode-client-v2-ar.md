# T15 — OpenCode Client v2

- typed request/response/config models.
- endpoint contract لـ POST /messages مع طبقة request عامة.
- HTTP timeouts مركزية: connect/read/write/pool.
- bounded retry للـ429/502/503/504 ولأخطاء network/timeout مع exponential backoff واحترام Retry-After.
- X-Correlation-ID لكل request، مع إمكانية تمريره end-to-end.
- counters للrequests/retries/failures/SSE reconnects.
- SSE reconnect يدعم Last-Event-ID.
- contract tests عبر httpx.MockTransport دون شبكة فعلية.

## OpenAPI

لم يُفترض وجود schema رسمي ولم يُولد client من وصف غير موثق. يبقى client explicit/typed؛ عند توفير OpenAPI رسمي versioned يمكن مقارنة العقود ثم توليد client دون تغيير public service boundary.
