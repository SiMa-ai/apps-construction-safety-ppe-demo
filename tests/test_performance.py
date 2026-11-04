import unittest

from construction_safety.performance import Performance


class PerformanceTests(unittest.TestCase):
    def test_warmup_window_and_real_ppe_calls(self):
        metrics = Performance(warmup_frames=2, window=3)
        for i in range(2):
            metrics.record({'detector_total': 10}, i)
        self.assertEqual(metrics.snapshot()['stages'], {})
        for i in range(5):
            timings = {'detector_total': (i + 1) / 1000}
            if i % 3 == 0:
                timings['ppe_detector'] = .020
            metrics.record(timings, 2 + i * .1)
        result = metrics.snapshot()
        self.assertEqual(result['processing_fps'], 10)
        self.assertEqual(result['measured_frames'], 5)
        self.assertEqual(result['stages']['detector_total']['mean_ms'], 4)
        self.assertEqual(result['stages']['detector_total']['p95_ms'], 4.9)
        self.assertEqual(result['stages']['ppe_detector']['samples'], 2)
        self.assertEqual(result['stages']['ppe_detector']['mean_ms'], 20)
        self.assertIsNone(result['end_to_end_latency_ms'])

    def test_single_frame_cannot_estimate_throughput(self):
        metrics = Performance(warmup_frames=0)
        metrics.record({'detector_total': .01}, 1)
        self.assertIsNone(metrics.snapshot()['processing_fps'])
