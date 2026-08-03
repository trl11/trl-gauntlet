"""Gap and reorder accounting for a monotonic counter stream."""

from __future__ import annotations

from gauntlet_sdk import CounterTracker


class TestFirstObservation:
    def test_the_first_value_sets_the_baseline(self):
        tracker = CounterTracker()
        observation = tracker.observe(500)

        assert not observation.anomalous
        assert tracker.first_observed == 500
        assert tracker.missing_total == 0

    def test_joining_a_stream_in_progress_reports_nothing_missing(self):
        tracker = CounterTracker()
        for value in range(1000, 1004):
            tracker.observe(value)

        assert tracker.missing_total == 0
        assert tracker.received_total == 4
        assert tracker.loss_pct == 0.0


class TestSequence:
    def test_consecutive_values_are_not_anomalous(self):
        tracker = CounterTracker()
        events = [tracker.observe(value).event for value in range(5)]

        assert events == [None] * 5
        assert tracker.expected_next == 5

    def test_a_skipped_value_is_a_gap_carrying_the_count_missed(self):
        tracker = CounterTracker()
        tracker.observe(0)
        observation = tracker.observe(4)

        assert observation.event == "gap"
        assert observation.expected == 1
        assert observation.observed == 4
        assert observation.missing == 3
        assert tracker.missing_total == 3

    def test_a_gap_still_counts_as_received_and_advances_the_expectation(self):
        tracker = CounterTracker()
        tracker.observe(0)
        tracker.observe(4)

        assert tracker.received_total == 2
        assert tracker.expected_next == 5
        assert tracker.last_observed == 4

    def test_an_earlier_value_is_out_of_order_and_does_not_advance(self):
        tracker = CounterTracker()
        tracker.observe(0)
        tracker.observe(1)
        observation = tracker.observe(0)

        assert observation.event == "out_of_order"
        assert observation.expected == 2
        assert observation.observed == 0
        assert observation.missing == 0
        assert tracker.out_of_order_total == 1
        assert tracker.received_total == 2
        assert tracker.expected_next == 2

    def test_a_repeated_value_is_out_of_order(self):
        tracker = CounterTracker()
        tracker.observe(7)
        observation = tracker.observe(7)

        assert observation.event == "out_of_order"
        assert tracker.out_of_order_total == 1


class TestTotals:
    def test_an_untouched_tracker_has_no_range_and_no_loss(self):
        tracker = CounterTracker()

        assert tracker.expected_total == 0
        assert tracker.loss_pct == 0.0

    def test_expected_total_spans_the_observed_range_inclusive(self):
        tracker = CounterTracker()
        tracker.observe(10)
        tracker.observe(19)

        assert tracker.expected_total == 10

    def test_loss_pct_is_the_missing_share_of_that_range(self):
        tracker = CounterTracker()
        tracker.observe(0)
        tracker.observe(1)
        tracker.observe(4)

        assert tracker.expected_total == 5
        assert tracker.missing_total == 2
        assert tracker.loss_pct == 40.0

    def test_loss_pct_is_rounded_to_four_places(self):
        tracker = CounterTracker()
        tracker.observe(0)
        tracker.observe(3000)

        assert tracker.loss_pct == 99.9334
