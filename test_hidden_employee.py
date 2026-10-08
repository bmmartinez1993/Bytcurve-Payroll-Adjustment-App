"""Tests for the duplicate-name and hidden-employee helpers in the main automation module."""
import importlib.util
import os
import unittest
from unittest.mock import MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bytecurve_app", os.path.join(_HERE, "ByteCurve Payroll Adjustment Automation.py")
)
app = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(app)
    _LOAD_ERROR = None
except Exception as e:  # pragma: no cover - environment without GUI deps
    _LOAD_ERROR = e


@unittest.skipIf(_LOAD_ERROR, f"main module not importable: {_LOAD_ERROR}")
class TestMatchHiddenEmployee(unittest.TestCase):
    INDEX = {
        "304239": "Carmen Perez",
        "000017": "Carmen (City Driver) Perez",
        "306301": "Valerie Felton",
        "999001": "Janiya Serrano",
    }

    def test_exact_name_is_fuzzy_match(self):
        m = app._match_hidden_employee("Carmen Perez", self.INDEX, set())
        self.assertEqual((m["kind"], m["id"]), ("fuzzy", "304239"))

    def test_token_order_difference_matches(self):
        m = app._match_hidden_employee("Perez Carmen", self.INDEX, set())
        self.assertEqual(m["id"], "304239")

    def test_shared_surname_alone_is_not_a_match(self):
        m = app._match_hidden_employee("Tonya Felton", self.INDEX, set())
        self.assertIsNone(m["kind"])

    def test_shared_surname_does_not_match_other_serrano(self):
        m = app._match_hidden_employee("Jasmin Serrano", self.INDEX, set())
        self.assertIsNone(m["kind"])

    def test_processed_workers_are_ignored(self):
        m = app._match_hidden_employee("Carmen Perez", self.INDEX, {"304239"})
        self.assertNotEqual(m["id"], "304239")

    def test_loose_match_needs_two_shared_words(self):
        index = {"1": "Zeralva Alvarez Santos"}
        m = app._match_hidden_employee("zeralva iscania alvarez de los  santos", index, set())
        self.assertEqual((m["kind"], m["id"]), ("loose", "1"))

    def test_name_owned_by_another_dropdown_entry_is_not_taken(self):
        reserved = frozenset({app._name_key("Carmen (City Driver) Perez")})
        index = {"000017": "Carmen (City Driver) Perez"}
        m = app._match_hidden_employee("Carmen Perez", index, set(), reserved)
        self.assertIsNone(m["kind"])

    def test_accented_names_match_themselves(self):
        m = app._match_hidden_employee("José Núñez", {"7": "José Núñez"}, set())
        self.assertEqual((m["kind"], m["id"]), ("fuzzy", "7"))

    def test_loose_prefers_best_overlap(self):
        index = {"a": "Maria Garcia", "b": "Maria Garcia Lopez Ruiz"}
        m = app._match_hidden_employee("Maria Garcia Lopez de Ruiz", index, set())
        self.assertEqual(m["id"], "b")

    def test_empty_index_reports_no_match(self):
        m = app._match_hidden_employee("Anyone", {}, set())
        self.assertIsNone(m["kind"])
        self.assertEqual(m["best_score"], 0)


@unittest.skipIf(_LOAD_ERROR, f"main module not importable: {_LOAD_ERROR}")
class TestDuplicateSelection(unittest.TestCase):
    def setUp(self):
        app._EMP_OCCURRENCE.clear()
        self.page = MagicMock()
        self.page.evaluate.return_value = True
        self._orig_open = app._open_employee_dropdown
        self._orig_wait = app.wait_for_loading
        app._open_employee_dropdown = lambda page: True
        app.wait_for_loading = lambda page: None

    def tearDown(self):
        app._open_employee_dropdown = self._orig_open
        app.wait_for_loading = self._orig_wait
        app._EMP_OCCURRENCE.clear()

    def test_default_occurrence_is_first(self):
        self.assertTrue(app._filter_grid_by_employee(self.page, "Judy Brayboy"))
        self.assertEqual(self.page.evaluate.call_args[0][1], ["Judy Brayboy", 0])

    def test_second_duplicate_passes_occurrence_one(self):
        app._EMP_OCCURRENCE["Judy Brayboy"] = 1
        app._filter_grid_by_employee(self.page, "Judy Brayboy")
        self.assertEqual(self.page.evaluate.call_args[0][1], ["Judy Brayboy", 1])

    def test_missing_duplicate_does_not_fall_back_to_first(self):
        app._EMP_OCCURRENCE["Judy Brayboy"] = 1
        self.page.evaluate.return_value = False
        self.assertFalse(app._filter_grid_by_employee(self.page, "Judy Brayboy"))
        self.page.locator.assert_not_called()


@unittest.skipIf(_LOAD_ERROR, f"main module not importable: {_LOAD_ERROR}")
class TestBuildIndex(unittest.TestCase):
    def _page(self, pages_of_rows):
        """Fake page: each scroll advances one 'page' of rows; bottom reached on the last."""
        state = {"i": 0}
        page = MagicMock()
        page.evaluate.side_effect = lambda js, args: pages_of_rows[min(state["i"], len(pages_of_rows) - 1)]
        container = MagicMock()

        def ev(js):
            if "scrollHeight" in js:
                return state["i"] >= len(pages_of_rows) - 1
            if "+= el.clientHeight" in js:
                state["i"] += 1
            return None
        container.evaluate.side_effect = ev
        page.locator.return_value.first = container
        return page

    def test_collects_rows_across_scroll_and_filters_by_date(self):
        rows = [
            [("10/06/2026", "Ann Lee", "1"), ("10/05/2026", "Old Day", "9")],
            [("10/06/2026", "Bob Ray", "2")],
            [("10/06/2026", "Cy Poe", "3")],
        ]
        index, complete = app._build_unfiltered_worker_index(self._page(rows), "10/06/2026")
        self.assertTrue(complete)
        self.assertEqual(index, {"1": "Ann Lee", "2": "Bob Ray", "3": "Cy Poe"})

    def test_stop_flag_marks_scan_incomplete(self):
        app.AUTOMATION_STOP_FLAG = True
        try:
            _, complete = app._build_unfiltered_worker_index(self._page([[]]), "10/06/2026")
        finally:
            app.AUTOMATION_STOP_FLAG = False
        self.assertFalse(complete)


if __name__ == "__main__":
    unittest.main()
