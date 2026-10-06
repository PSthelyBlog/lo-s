import pathlib
import tempfile
import unittest

from los import tune
from los.tune import Result


def runner(outcomes):
    """A stand-in for measuring: looks each candidate up by its arguments."""
    seen = []

    def run(args):
        seen.append(args)
        seconds, problem = outcomes[" ".join(args)]
        return Result(args, seconds=seconds, problem=problem)

    return run, seen


class SearchTest(unittest.TestCase):
    STAGES = [[["--a"]], [["--b", "1"], ["--b", "2"]]]

    def test_the_fastest_accepted_candidate_wins_and_later_stages_build_on_it(self):
        run, seen = runner({"": (1.00, ""), "--a": (0.80, ""), "--a --b 1": (0.70, ""), "--a --b 2": (0.60, "")})
        best, results = tune.search(run, self.STAGES)
        self.assertEqual((best.args, len(results)), (["--a", "--b", "2"], 4))
        self.assertEqual(seen, [[], ["--a"], ["--a", "--b", "1"], ["--a", "--b", "2"]])

    def test_a_faster_candidate_that_changes_answers_is_not_kept(self):
        run, seen = runner({"": (1.00, ""), "--a": (0.50, "3 of 100 answers differ from the record"),
                            "--b 1": (0.99, ""), "--b 2": (0.90, "")})
        best, _ = tune.search(run, self.STAGES)
        self.assertEqual(best.args, ["--b", "2"])
        self.assertEqual(seen[2], ["--b", "1"])                 # the rejected --a is not built on

    def test_a_gain_inside_the_margin_does_not_replace_the_best(self):
        run, _ = runner({"": (1.00, ""), "--a": (0.97, ""), "--b 1": (0.96, ""), "--b 2": (1.20, "")})
        self.assertEqual(tune.search(run, self.STAGES)[0].args, [])

    def test_nothing_is_searched_when_the_default_fails(self):
        run, seen = runner({"": (1.00, "2 of 100 answers differ from the record")})
        best, results = tune.search(run, self.STAGES)
        self.assertEqual((best, len(results), seen), (None, 1, [[]]))


class ApplyAndReportTest(unittest.TestCase):
    def test_apply_writes_the_winner_and_removes_it_when_the_defaults_win(self):
        with tempfile.TemporaryDirectory() as folder:
            path = tune.apply("m.gguf", Result(["--load-mode", "none"], seconds=0.8), folder)
            self.assertEqual((path, path.read_text()), (pathlib.Path(folder, "m.gguf.args"), "--load-mode none\n"))
            tune.apply("m.gguf", Result([], seconds=0.7), folder)
            self.assertFalse(path.exists())

    def test_rows(self):
        best = Result(["--threads", "12"], 0.812, 41.4, 1300)
        self.assertEqual(tune.row(best, best), "| --threads 12 | 0.81 | 41 | 1300 | accepted, fastest |")
        self.assertEqual(tune.row(Result([], problem="the server did not start")),
                         "| (defaults) |  |  |  | the server did not start |")

    def test_stages_vary_loading_threads_and_placement(self):
        loading, threads, placement = tune.stages()
        self.assertEqual(loading, [["--load-mode", "none"]])
        self.assertTrue(all(flag == "--threads" and int(count) > tune.physical_cores() for flag, count in threads))
        self.assertIn(["--cpu-moe"], placement)
