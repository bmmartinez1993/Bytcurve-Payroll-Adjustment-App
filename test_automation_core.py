"""
Unit Tests for ByteCurve Payroll Adjustment Automation Core Functions

This module contains comprehensive unit tests for the refactored core functions.
Tests cover time parsing, interval calculations, task classification, and data models.
"""

import unittest
import datetime
from datetime import datetime as dt
from typing import Optional

# Import the refactored core functions
import sys
import os

# Add parent directory to path to import the module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from automation_core_refactored import (
    # Time parsing and formatting
    parse_kendo_time,
    times_match,
    parse_time_to_datetime,
    add_minutes_to_time,
    datetime_to_time_str,
    parse_time_range_str,

    # Time interval and overlap utilities
    intervals_overlap,
    check_for_overlaps,
    get_non_overlapping_interval,
    calculate_coverage_percentage,

    # Data models
    TimeRange,
    TaskInfo,
    WorkerInfo,
    TaskPolicy,

    # Task classification and policy
    determine_task_policy,
    calculate_proposed_time_range,

    # Constants
    MAX_TIME_SHIFT_MINUTES,
    ONE_MINUTE_TASK_KEYWORDS,
    SCHEDULE_MATCH_TASK_KEYWORDS,
    HTS_TASK_KEYWORDS,
    SKIP_TASK_KEYWORDS
)


class TestTimeParsingAndFormatting(unittest.TestCase):
    """Test suite for time parsing and formatting utilities."""

    def test_parse_kendo_time_with_spaces(self):
        """Test parsing Kendo time strings with extra whitespace."""
        self.assertEqual(parse_kendo_time(' 6:39 AM '), '06:39 AM')
        self.assertEqual(parse_kendo_time(' 12:05 PM '), '12:05 PM')

    def test_parse_kendo_time_without_leading_zero(self):
        """Test parsing time strings without leading zero on hour."""
        self.assertEqual(parse_kendo_time('6:39 AM'), '06:39 AM')
        self.assertEqual(parse_kendo_time('9:00 PM'), '09:00 PM')

    def test_parse_kendo_time_already_formatted(self):
        """Test parsing already properly formatted time strings."""
        self.assertEqual(parse_kendo_time('06:39 AM'), '06:39 AM')
        self.assertEqual(parse_kendo_time('12:05 PM'), '12:05 PM')

    def test_parse_kendo_time_empty_input(self):
        """Test parsing empty or None time strings."""
        self.assertEqual(parse_kendo_time(''), '')
        self.assertEqual(parse_kendo_time(None), '')

    def test_parse_kendo_time_invalid_format(self):
        """Test parsing invalid time formats."""
        result = parse_kendo_time('invalid time')
        self.assertEqual(result, 'invalid time')

    def test_times_match_with_spaces(self):
        """Test time matching with extra whitespace."""
        self.assertTrue(times_match(' 6:39 AM ', '06:39 AM'))
        self.assertTrue(times_match('06:39 AM', ' 6:39 AM '))

    def test_times_match_case_insensitive(self):
        """Test time matching is case-insensitive for AM/PM."""
        self.assertTrue(times_match('06:39 AM', '06:39 am'))
        self.assertTrue(times_match('06:39 PM', '06:39 pm'))

    def test_times_match_different_times(self):
        """Test that different times don't match."""
        self.assertFalse(times_match('06:39 AM', '06:40 AM'))
        self.assertFalse(times_match('06:39 AM', '07:39 AM'))

    def test_times_match_empty_strings(self):
        """Test matching empty strings."""
        self.assertTrue(times_match('', ''))
        self.assertFalse(times_match('06:39 AM', ''))

    def test_parse_time_to_datetime_valid(self):
        """Test parsing time string to datetime object."""
        ref_date = dt(2026, 6, 4)
        result = parse_time_to_datetime('06:39 AM', ref_date)
        expected = dt(2026, 6, 4, 6, 39)
        self.assertEqual(result, expected)

    def test_parse_time_to_datetime_noon_midnight(self):
        """Test parsing noon (12:00 PM) and midnight (12:00 AM)."""
        ref_date = dt(2026, 6, 4)

        noon = parse_time_to_datetime('12:00 PM', ref_date)
        self.assertEqual(noon, dt(2026, 6, 4, 12, 0))

        midnight = parse_time_to_datetime('12:00 AM', ref_date)
        self.assertEqual(midnight, dt(2026, 6, 4, 0, 0))

    def test_parse_time_to_datetime_none_input(self):
        """Test parsing with None inputs."""
        ref_date = dt(2026, 6, 4)
        self.assertIsNone(parse_time_to_datetime(None, ref_date))
        self.assertIsNone(parse_time_to_datetime('06:39 AM', None))

    def test_parse_time_to_datetime_invalid_format(self):
        """Test parsing invalid time format."""
        ref_date = dt(2026, 6, 4)
        self.assertIsNone(parse_time_to_datetime('invalid', ref_date))

    def test_add_minutes_to_time_same_hour(self):
        """Test adding minutes within the same hour."""
        result = add_minutes_to_time('06:39 AM', 30)
        self.assertEqual(result, '07:09 AM')

    def test_add_minutes_to_time_cross_hour(self):
        """Test adding minutes that cross hour boundary."""
        result = add_minutes_to_time('06:39 AM', 60)
        self.assertEqual(result, '07:39 AM')

    def test_add_minutes_to_time_cross_midnight(self):
        """Test adding minutes that cross midnight."""
        result = add_minutes_to_time('11:30 PM', 45)
        self.assertEqual(result, '12:15 AM')

    def test_add_minutes_to_time_cross_noon(self):
        """Test adding minutes that cross noon."""
        result = add_minutes_to_time('11:30 AM', 45)
        self.assertEqual(result, '12:15 PM')

    def test_add_minutes_to_time_negative(self):
        """Test subtracting minutes (negative input)."""
        result = add_minutes_to_time('06:30 AM', -15)
        self.assertEqual(result, '06:15 AM')

    def test_add_minutes_to_time_invalid_input(self):
        """Test adding minutes to invalid time string."""
        result = add_minutes_to_time('invalid', 30)
        self.assertEqual(result, 'invalid')

    def test_datetime_to_time_str_valid(self):
        """Test converting datetime to time string."""
        dt_obj = dt(2026, 6, 4, 6, 39)
        result = datetime_to_time_str(dt_obj)
        self.assertEqual(result, '06:39 AM')

    def test_datetime_to_time_str_none(self):
        """Test converting None to time string."""
        self.assertEqual(datetime_to_time_str(None), '')

    def test_parse_time_range_str_valid(self):
        """Test parsing a valid time range string."""
        result = parse_time_range_str('06:39 AM - 08:15 PM')
        expected = ['06:39 AM', '08:15 PM']
        self.assertEqual(result, expected)

    def test_parse_time_range_str_with_spaces(self):
        """Test parsing time range with extra spaces."""
        result = parse_time_range_str('  06:39 AM  -  08:15 PM  ')
        expected = ['06:39 AM', '08:15 PM']
        self.assertEqual(result, expected)

    def test_parse_time_range_str_empty(self):
        """Test parsing empty time range string."""
        self.assertEqual(parse_time_range_str(''), [])
        self.assertEqual(parse_time_range_str(None), [])


class TestTimeIntervalAndOverlap(unittest.TestCase):
    """Test suite for time interval and overlap utilities."""

    def setUp(self):
        """Set up common test datetimes."""
        self.ref_date = dt(2026, 6, 4)
        self.start1 = dt(2026, 6, 4, 6, 0)
        self.end1 = dt(2026, 6, 4, 8, 0)
        self.start2 = dt(2026, 6, 4, 7, 0)
        self.end2 = dt(2026, 6, 4, 9, 0)
        self.start3 = dt(2026, 6, 4, 8, 0)  # Exactly at end1
        self.end3 = dt(2026, 6, 4, 10, 0)
        self.start4 = dt(2026, 6, 4, 9, 0)  # After end1
        self.end4 = dt(2026, 6, 4, 11, 0)

    def test_intervals_overlap_true(self):
        """Test overlapping intervals."""
        self.assertTrue(intervals_overlap(self.start1, self.end1, self.start2, self.end2))

    def test_intervals_overlap_boundary_touch(self):
        """Test intervals that just touch at boundary (not considered overlap)."""
        self.assertFalse(intervals_overlap(self.start1, self.end1, self.start3, self.end3))

    def test_intervals_overlap_no_overlap(self):
        """Test non-overlapping intervals."""
        self.assertFalse(intervals_overlap(self.start1, self.end1, self.start4, self.end4))

    def test_intervals_overlap_none_inputs(self):
        """Test with None datetime inputs."""
        self.assertFalse(intervals_overlap(None, self.end1, self.start2, self.end2))
        self.assertFalse(intervals_overlap(self.start1, None, self.start2, self.end2))
        self.assertFalse(intervals_overlap(self.start1, self.end1, None, self.end2))
        self.assertFalse(intervals_overlap(self.start1, self.end1, self.start2, None))
        self.assertFalse(intervals_overlap(None, None, None, None))

    def test_check_for_overlaps_empty_list(self):
        """Test with empty intervals list."""
        intervals = []
        self.assertFalse(check_for_overlaps(intervals))

    def test_check_for_overlaps_single_interval(self):
        """Test with single interval."""
        intervals = [(self.start1, self.end1)]
        self.assertFalse(check_for_overlaps(intervals))

    def test_check_for_overlaps_no_overlap(self):
        """Test intervals with no overlap."""
        intervals = [(self.start1, self.end1), (self.start4, self.end4)]
        self.assertFalse(check_for_overlaps(intervals))

    def test_check_for_overlaps_with_overlap(self):
        """Test intervals with overlap."""
        intervals = [(self.start1, self.end1), (self.start2, self.end2)]
        self.assertTrue(check_for_overlaps(intervals))

    def test_check_for_overlaps_min_threshold(self):
        """Test overlap with minimum threshold."""
        # Overlap is exactly 1 hour (60 minutes), threshold is 15
        intervals = [(self.start1, self.end1), (self.start2, self.end2)]
        self.assertTrue(check_for_overlaps(intervals, min_overlap_minutes=15))

        # Test with higher threshold
        self.assertTrue(check_for_overlaps(intervals, min_overlap_minutes=30))
        self.assertTrue(check_for_overlaps(intervals, min_overlap_minutes=60))
        self.assertFalse(check_for_overlaps(intervals, min_overlap_minutes=61))

    def test_get_non_overlapping_interval_no_overlap(self):
        """Test interval adjustment when no overlap exists."""
        existing = [(dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0))]
        proposed = dt(2026, 6, 4, 8, 0)
        proposed_end = dt(2026, 6, 4, 9, 0)

        result = get_non_overlapping_interval(proposed, proposed_end, existing)
        self.assertEqual(result, (proposed, proposed_end))

    def test_get_non_overlapping_interval_with_overlap(self):
        """Test interval adjustment when overlap exists."""
        existing = [(dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 8, 0))]
        proposed = dt(2026, 6, 4, 7, 0)
        proposed_end = dt(2026, 6, 4, 8, 30)

        result = get_non_overlapping_interval(proposed, proposed_end, existing)
        expected_start = dt(2026, 6, 4, 8, 1)  # 1 minute after existing ends
        expected_end = dt(2026, 6, 4, 9, 31)
        self.assertEqual(result, (expected_start, expected_end))

    def test_get_non_overlapping_interval_multiple_overlaps(self):
        """Test interval adjustment with multiple overlapping intervals."""
        existing = [
            (dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0)),
            (dt(2026, 6, 4, 8, 0), dt(2026, 6, 4, 9, 0))
        ]
        proposed = dt(2026, 6, 4, 6, 30)
        proposed_end = dt(2026, 6, 4, 8, 30)

        result = get_non_overlapping_interval(proposed, proposed_end, existing)
        expected_start = dt(2026, 6, 4, 9, 1)  # After second interval
        expected_end = dt(2026, 6, 4, 11, 1)
        self.assertEqual(result, (expected_start, expected_end))

    def test_get_non_overlapping_interval_none_inputs(self):
        """Test with None inputs."""
        existing = [(dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0))]

        result = get_non_overlapping_interval(None, dt(2026, 6, 4, 8, 0), existing)
        self.assertEqual(result, (None, dt(2026, 6, 4, 8, 0)))

        result = get_non_overlapping_interval(dt(2026, 6, 4, 8, 0), None, existing)
        self.assertEqual(result, (dt(2026, 6, 4, 8, 0), None))

    def test_calculate_coverage_percentage_full_coverage(self):
        """Test coverage calculation with 100% coverage."""
        sched_start = dt(2026, 6, 4, 6, 0)
        sched_end = dt(2026, 6, 4, 8, 0)
        actual_start = dt(2026, 6, 4, 6, 0)
        actual_end = dt(2026, 6, 4, 8, 0)

        result = calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end)
        self.assertEqual(result, 100.0)

    def test_calculate_coverage_percentage_half_coverage(self):
        """Test coverage calculation with 50% coverage."""
        sched_start = dt(2026, 6, 4, 6, 0)
        sched_end = dt(2026, 6, 4, 8, 0)
        actual_start = dt(2026, 6, 4, 6, 30)
        actual_end = dt(2026, 6, 4, 7, 30)

        result = calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end)
        self.assertEqual(result, 50.0)

    def test_calculate_coverage_percentage_no_overlap(self):
        """Test coverage calculation with no overlap."""
        sched_start = dt(2026, 6, 4, 6, 0)
        sched_end = dt(2026, 6, 4, 8, 0)
        actual_start = dt(2026, 6, 4, 10, 0)
        actual_end = dt(2026, 6, 4, 12, 0)

        result = calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end)
        self.assertEqual(result, 0.0)

    def test_calculate_coverage_percentage_partial_overlap(self):
        """Test coverage calculation with partial overlap."""
        sched_start = dt(2026, 6, 4, 6, 0)
        sched_end = dt(2026, 6, 4, 8, 0)
        actual_start = dt(2026, 6, 4, 5, 30)
        actual_end = dt(2026, 6, 4, 7, 0)

        result = calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end)
        self.assertEqual(result, 50.0)

    def test_calculate_coverage_percentage_capped_at_100(self):
        """Test that coverage is capped at 100% even with actual > scheduled."""
        sched_start = dt(2026, 6, 4, 6, 0)
        sched_end = dt(2026, 6, 4, 7, 0)
        actual_start = dt(2026, 6, 4, 5, 0)
        actual_end = dt(2026, 6, 4, 8, 0)

        result = calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end)
        self.assertEqual(result, 100.0)

    def test_calculate_coverage_percentage_none_inputs(self):
        """Test with None inputs."""
        result = calculate_coverage_percentage(None, dt(2026, 6, 4, 8, 0),
                                                dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0))
        self.assertEqual(result, 0.0)


class TestDataModels(unittest.TestCase):
    """Test suite for data model classes."""

    def test_time_range_valid(self):
        """Test TimeRange with valid datetimes."""
        tr = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )

        self.assertTrue(tr.is_valid)
        self.assertEqual(tr.duration_minutes, 120.0)

    def test_time_range_invalid(self):
        """Test TimeRange with invalid datetimes."""
        tr = TimeRange(start_str='06:00 AM', end_str='08:00 AM')

        self.assertFalse(tr.is_valid)
        self.assertIsNone(tr.duration_minutes)

    def test_time_range_partial_invalid(self):
        """Test TimeRange with one valid and one None datetime."""
        tr = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )

        self.assertFalse(tr.is_valid)
        self.assertIsNone(tr.duration_minutes)

    def test_task_info_identifier(self):
        """Test TaskInfo task_identifier property."""
        task = TaskInfo(
            row=None,  # Mock row locator
            code='HTS',
            name='Home to School'
        )

        self.assertEqual(task.task_identifier, 'HTS_Home to School')

    def test_worker_info_add_and_get_tasks(self):
        """Test WorkerInfo task management."""
        worker = WorkerInfo(
            worker_id='EMP123',
            worker_name='John Doe',
            display_name='John Doe (EMP123)'
        )

        task1 = TaskInfo(row=None, code='HTS', name='Home to School', verified=True)
        task2 = TaskInfo(row=None, code='Extra Work', name='Extra', verified=False)

        worker.add_task(task1)
        worker.add_task(task2)

        self.assertEqual(len(worker.tasks), 2)
        self.assertEqual(len(worker.get_verified_tasks()), 1)
        self.assertEqual(len(worker.get_unverified_tasks()), 1)

    def test_worker_info_get_fixed_intervals(self):
        """Test WorkerInfo get_fixed_intervals method."""
        worker = WorkerInfo(worker_id='EMP123', worker_name='John Doe')
        ref_date = dt(2026, 6, 4)

        task1 = TaskInfo(
            row=None,
            code='HTS',
            name='Home to School',
            verified=True,
            paid_time=TimeRange(
                start_dt=dt(2026, 6, 4, 6, 0),
                end_dt=dt(2026, 6, 4, 7, 0),
                start_str='06:00 AM',
                end_str='07:00 AM'
            )
        )

        task2 = TaskInfo(
            row=None,
            code='Extra Work',
            name='Extra',
            verified=False,  # Not verified, should be excluded
            paid_time=TimeRange(start_str='08:00 AM', end_str='09:00 AM')
        )

        worker.add_task(task1)
        worker.add_task(task2)

        intervals = worker.get_fixed_intervals(ref_date)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0], (dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0)))


class TestTaskClassification(unittest.TestCase):
    """Test suite for task classification and policy application."""

    def test_determine_task_policy_extra_work(self):
        """Test Extra Work task classification."""
        result = determine_task_policy('Extra Work', 'Extra')
        self.assertEqual(result, TaskPolicy.EXTRA_WORK)

    def test_determine_task_policy_s2s_charter(self):
        """Test S2S Charter task classification."""
        result = determine_task_policy('S2S Charter', 'Charter')
        self.assertEqual(result, TaskPolicy.S2S_CHARTER)

    def test_determine_task_policy_spare_cdl(self):
        """Test Spare CDL task classification."""
        result = determine_task_policy('Spare CDL', 'CDL')
        self.assertEqual(result, TaskPolicy.SPARE_CDL)

    def test_determine_task_policy_spare_monitor(self):
        """Test Spare Monitor task classification."""
        result = determine_task_policy('Spare Monitor', 'Monitor')
        self.assertEqual(result, TaskPolicy.SPARE_MONITOR)

    def test_determine_task_policy_hts_units(self):
        """Test HTS Units task classification."""
        result = determine_task_policy('HTS', 'Units')
        self.assertEqual(result, TaskPolicy.HTS_UNITS)

    def test_determine_task_policy_hts_hours(self):
        """Test HTS Hours task classification."""
        result = determine_task_policy('HTS', 'Hours')
        self.assertEqual(result, TaskPolicy.HTS_HOURS)

    def test_determine_task_policy_hts_hrs(self):
        """Test HTS Hrs abbreviation (from HTS_TASK_KEYWORDS) maps to HTS_HOURS."""
        self.assertIn('Hrs', HTS_TASK_KEYWORDS)
        result = determine_task_policy('HTS', 'Hrs')
        self.assertEqual(result, TaskPolicy.HTS_HOURS)

    def test_determine_task_policy_regular(self):
        """Test regular task classification (default)."""
        result = determine_task_policy('Regular', 'Regular Task')
        self.assertEqual(result, TaskPolicy.REGULAR)

    def test_determine_task_policy_skip_bridge_charter(self):
        """Test Bridge Charter task classification (should skip)."""
        result = determine_task_policy('Bridge Charter', 'Bridge')
        self.assertIsNone(result)

    def test_determine_task_policy_skip_bridge_charter_variations(self):
        """Test Bridge Charter variations (should all skip)."""
        self.assertIsNone(determine_task_policy('BridgeCharter', 'Bridge'))
        self.assertIsNone(determine_task_policy('Regular', 'Bridge Charter'))

    def test_task_policy_max_allowed_hours(self):
        """Test TaskPolicy max_allowed_hours property."""
        self.assertAlmostEqual(
            TaskPolicy.EXTRA_WORK.max_allowed_hours,
            0.016666666666666666,
            places=10
        )
        self.assertEqual(TaskPolicy.REGULAR.max_allowed_hours, 4.0)

    def test_task_policy_require_schedule_match(self):
        """Test TaskPolicy require_schedule_match property."""
        self.assertTrue(TaskPolicy.SPARE_CDL.require_schedule_match)
        self.assertTrue(TaskPolicy.SPARE_MONITOR.require_schedule_match)
        self.assertFalse(TaskPolicy.REGULAR.require_schedule_match)

    def test_task_policy_use_schedule_time(self):
        """Test TaskPolicy use_schedule_time property covers Spare and HTS types."""
        self.assertTrue(TaskPolicy.SPARE_CDL.use_schedule_time)
        self.assertTrue(TaskPolicy.SPARE_MONITOR.use_schedule_time)
        self.assertTrue(TaskPolicy.HTS_UNITS.use_schedule_time)
        self.assertTrue(TaskPolicy.HTS_HOURS.use_schedule_time)
        self.assertFalse(TaskPolicy.REGULAR.use_schedule_time)
        self.assertFalse(TaskPolicy.EXTRA_WORK.use_schedule_time)
        self.assertFalse(TaskPolicy.S2S_CHARTER.use_schedule_time)

    def test_task_policy_is_one_minute_only(self):
        """Test TaskPolicy is_one_minute_only property."""
        self.assertTrue(TaskPolicy.EXTRA_WORK.is_one_minute_only)
        self.assertTrue(TaskPolicy.S2S_CHARTER.is_one_minute_only)
        self.assertFalse(TaskPolicy.REGULAR.is_one_minute_only)


class TestCalculateProposedTimeRange(unittest.TestCase):
    """Test suite for calculate_proposed_time_range function."""

    def setUp(self):
        """Set up common test data."""
        self.ref_date = dt(2026, 6, 4)

    def test_extra_work_one_minute(self):
        """Test Extra Work task gets 1-minute duration."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('Extra Work', 'Extra', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 6, 1))  # 1 minute duration

    def test_s2s_charter_one_minute(self):
        """Test S2S Charter task gets 1-minute duration."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('S2S Charter', 'Charter', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 6, 1))  # 1 minute duration

    def test_spare_cdl_exact_schedule(self):
        """Test Spare CDL task uses exact schedule time."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('Spare CDL', 'CDL', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 8, 0))  # Exact schedule time

    def test_hts_units_exact_schedule(self):
        """Test HTS Units task uses exact schedule time (same branch as Spare tasks)."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('HTS', 'Units', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 8, 0))

    def test_hts_hours_exact_schedule(self):
        """Test HTS Hours task uses exact schedule time (same branch as Spare tasks)."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('HTS', 'Hours', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 8, 0))

    def test_regular_task_actual_time(self):
        """Test regular task uses actual time bounded by schedule."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(
            start_dt=dt(2026, 6, 4, 5, 30),
            end_dt=dt(2026, 6, 4, 7, 30),
            start_str='05:30 AM',
            end_str='07:30 AM'
        )

        result = calculate_proposed_time_range('Regular', 'Task', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        # Should be bounded by schedule: 6:00 AM to 7:30 AM
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 7, 30))

    def test_regular_task_actual_exceeds_schedule(self):
        """Test regular task where actual time exceeds schedule."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 7, 0),
            start_str='06:00 AM',
            end_str='07:00 AM'
        )
        actual = TimeRange(
            start_dt=dt(2026, 6, 4, 5, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='05:00 AM',
            end_str='08:00 AM'
        )

        result = calculate_proposed_time_range('Regular', 'Task', schedule, actual, self.ref_date)

        self.assertIsNotNone(result)
        start, end = result
        # Should be bounded to schedule exactly
        self.assertEqual(start, dt(2026, 6, 4, 6, 0))
        self.assertEqual(end, dt(2026, 6, 4, 7, 0))

    def test_skip_task(self):
        """Test that Bridge Charter task is skipped (returns None)."""
        schedule = TimeRange(
            start_dt=dt(2026, 6, 4, 6, 0),
            end_dt=dt(2026, 6, 4, 8, 0),
            start_str='06:00 AM',
            end_str='08:00 AM'
        )
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('Bridge Charter', 'Bridge', schedule, actual, self.ref_date)

        self.assertIsNone(result)

    def test_invalid_schedule(self):
        """Test with invalid schedule range."""
        schedule = TimeRange(start_str='', end_str='')
        actual = TimeRange(start_str='', end_str='')

        result = calculate_proposed_time_range('Regular', 'Task', schedule, actual, self.ref_date)

        self.assertIsNone(result)


class TestEdgeCases(unittest.TestCase):
    """Test suite for edge cases and boundary conditions."""

    def test_times_match_with_single_digit_hour(self):
        """Test times_match with various hour formats."""
        self.assertTrue(times_match('6:00 AM', '06:00 AM'))
        self.assertTrue(times_match('9:30 PM', '09:30 PM'))
        self.assertFalse(times_match('6:00 AM', '06:01 AM'))

    def test_add_minutes_to_time_24_hour_wrap(self):
        """Test adding minutes that wrap around multiple times."""
        result = add_minutes_to_time('11:00 PM', 150)
        self.assertEqual(result, '01:30 AM')

    def test_intervals_overlap_identical(self):
        """Test that identical intervals overlap."""
        start = dt(2026, 6, 4, 6, 0)
        end = dt(2026, 6, 4, 8, 0)
        self.assertTrue(intervals_overlap(start, end, start, end))

    def test_check_for_overlaps_with_none_values(self):
        """Test check_for_overlaps with None values in intervals."""
        intervals = [
            (dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0)),
            (None, dt(2026, 6, 4, 9, 0)),  # Should be filtered out
            (dt(2026, 6, 4, 8, 0), None)   # Should be filtered out
        ]
        self.assertFalse(check_for_overlaps(intervals))

    def test_get_non_overlapping_interval_large_shift(self):
        """Test interval adjustment with large existing intervals."""
        existing = [(dt(2026, 6, 4, 0, 0), dt(2026, 6, 4, 23, 59))]
        proposed = dt(2026, 6, 4, 12, 0)
        proposed_end = dt(2026, 6, 4, 13, 0)

        result = get_non_overlapping_interval(proposed, proposed_end, existing)
        # 23:59 today + 1-min gap = 00:00 next day.
        # intervals_overlap(00:00-next, 01:00-next, 00:00-today, 23:59-today) is False
        # because end2 (23:59 today) < start1 (00:00 next day), so the loop exits.
        expected_start = dt(2026, 6, 5, 0, 0)
        expected_end = dt(2026, 6, 5, 1, 0)
        self.assertEqual(result, (expected_start, expected_end))


if __name__ == '__main__':
    unittest.main(verbosity=2)