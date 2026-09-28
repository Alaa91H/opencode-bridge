import unittest

from bridge.domain.policies.circuit_breaker import (
    CircuitBreaker, CircuitBreakerConfig, CircuitBreakerRegistry, CircuitOpen, CircuitState,
)


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


class CircuitBreakerTests(unittest.TestCase):
    def test_closed_open_half_open_closed_recovery(self):
        clock = Clock()
        events = []
        breaker = CircuitBreaker("opencode", CircuitBreakerConfig(2, 10, 1),
                                 clock=clock, on_event=lambda name, event: events.append((name, event)))
        breaker.failure()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        breaker.failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaises(CircuitOpen):
            breaker.before_call()
        clock.now = 10
        breaker.before_call()
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        breaker.success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.metrics.opened, 1)
        self.assertEqual(breaker.metrics.recovered, 1)
        self.assertEqual(events, [("opencode", "open"), ("opencode", "half_open"), ("opencode", "closed")])

    def test_half_open_failure_reopens(self):
        clock = Clock()
        breaker = CircuitBreaker("storage", CircuitBreakerConfig(1, 1), clock=clock)
        breaker.failure()
        clock.now = 1
        breaker.before_call()
        breaker.failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_registry_isolates_dependencies(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(failure_threshold=1))
        registry["telegram"].failure()
        self.assertEqual(registry["telegram"].state, CircuitState.OPEN)
        self.assertEqual(registry["github"].state, CircuitState.CLOSED)
