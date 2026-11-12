import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import slack_watch as w  # noqa: E402


def err(msg, opts=("Retry", "Move to Next", "Abort")):
    return {"id": "e", "timeStamp": "2026-11-10T10:00:00", "message": "x",
            "errorData": [{"message": msg, "errorResponses": [{"response": o} for o in opts]}]}


DATA = {"protocolName": "p", "runtimeSteps": [{"stepOrder": 12, "name": "Discard", "stepStart": "2026-11-10T09:59:00", "stepEnd": None}]}
TIP = "Front Channel did not detect the expected tip type in the rack"


class Policy(unittest.TestCase):
    def setUp(self):
        self.sent, self.answered = [], []
        self._send, self._resp = w.send, w.RESPOND
        w.send = lambda text, dry=False: self.sent.append(text) or True
        w.RESPOND = lambda e, r: self.answered.append(r) or True
        self.state = {"tips": (None, 0)}

    def tearDown(self):
        w.send, w.RESPOND = self._send, self._resp

    def test_tip_error_is_answered_not_sent(self):
        self.assertEqual(w.handle(err(TIP), DATA, self.state), "auto")
        self.assertEqual((self.answered, self.sent), (["Move to Next"], []))

    def test_no_tip_present_error_is_answered_too(self):
        self.assertEqual(w.handle(err("No tip was present for the MPH to pick up", ("Cancel Assay", "Retry", "Abort", "Move to Next", "Exclude Channel")), DATA, self.state), "auto")
        self.assertEqual(self.sent, [])

    def test_no_liquid_found_is_answered_with_bottom(self):
        self.assertEqual(w.handle(err("The Front Channel was unable to find any liquid in the well", ("Bottom", "Abort", "Exclude Channel", "Air", "Cancel Assay", "Retry")), DATA, self.state), "auto")
        self.assertEqual((self.answered, self.sent), (["Bottom"], []))

    def test_fourth_bottom_in_a_row_alerts(self):
        e = err("The Front Channel was unable to find any liquid in the well", ("Bottom", "Abort"))
        self.assertEqual([w.handle(e, DATA, self.state) for _ in range(4)], ["auto", "auto", "auto", "alert"])
        self.assertEqual(len(self.sent), 1)

    def test_refused_bottom_alerts(self):
        w.RESPOND = lambda e, r: False
        self.assertEqual(w.handle(err("The Front Channel was unable to find any liquid in the well", ("Bottom", "Abort")), DATA, self.state), "alert")

    def test_not_enough_liquid_still_alerts(self):
        self.assertEqual(w.handle(err("There is not enough liquid in the well for the Rear Channel to aspirate 53 ul or to immerse 2 mm below the liquid level", ("Retry", "Bottom", "Abort")), DATA, self.state), "alert")

    def test_door_error_is_not_auto_answered(self):
        self.assertEqual(w.handle(err("Close the door and continue the assay", ("Abort", "Close Door and Continue")), DATA, self.state), "alert")

    def test_fourth_tip_error_in_a_row_alerts_and_is_not_answered(self):
        self.assertEqual([w.handle(err(TIP), DATA, self.state) for _ in range(4)], ["auto", "auto", "auto", "alert"])
        self.assertEqual(self.answered, ["Move to Next"] * 3)
        self.assertEqual(len(self.sent), 1)

    def test_other_error_alerts_and_resets_the_count(self):
        w.handle(err(TIP), DATA, self.state)
        self.assertEqual(w.handle(err("not enough liquid", ("Abort", "Retry")), DATA, self.state), "alert")
        self.assertEqual(self.state["tips"], (None, 0))
        self.assertEqual(w.handle(err(TIP), DATA, self.state), "auto")

    def test_tip_error_without_move_to_next_alerts(self):
        self.assertEqual(w.handle(err(TIP, ("Abort",)), DATA, self.state), "alert")

    def test_failed_answer_alerts(self):
        w.RESPOND = lambda e, r: False
        self.assertEqual(w.handle(err(TIP), DATA, self.state), "alert")

    def test_without_a_responder_nothing_is_swallowed(self):
        w.RESPOND = None
        self.assertEqual(w.handle(err(TIP), DATA, self.state), "alert")
        self.assertEqual(len(self.sent), 1)


class Reload(unittest.TestCase):
    def setUp(self):
        self._prep, self._send = w.prep, w.send
        self.sent = []
        w.send = lambda text, dry=False: self.sent.append(text) or True
        self.run = {"runDatalId": None, "isRunInProgress": True, "protocolRunState": "Reloading", "protocolName": "p"}
        w.prep = lambda path: [{"positionName": "Deck 2", "count": 96, "suggestedCount": 96}] if "reload-instructions" in path else self.run

    def tearDown(self):
        w.prep, w.send = self._prep, self._send

    def test_reload_is_announced_once_per_reload(self):
        state = {"errors": {}, "active": True, "rid": None, "tips": (None, 0), "reloading": False, "last": None, "ending": None, "tries": 0}
        w.check(state)
        w.check(state)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("reloaded", self.sent[0])
        self.run["protocolRunState"] = "Running"
        w.check(state)
        self.run["protocolRunState"] = "Reloading"
        w.check(state)
        self.assertEqual(len(self.sent), 2)

    def test_numeric_unknown_state_is_confirmed_by_the_reload_instructions(self):
        self.run["protocolRunState"] = 80
        self.assertTrue(w.needs_reload(self.run))
        w.prep = lambda path: [] if "reload-instructions" in path else self.run
        self.assertFalse(w.needs_reload(self.run))


class Finish(unittest.TestCase):
    def test_finish_is_announced_even_when_the_prep_forgets_the_run_id(self):
        sent = []
        old = (w.prep, w.send)
        w.send = lambda text, dry=False: sent.append(text) or True
        runs = [{"runDatalId": "r1", "isRunInProgress": True, "protocolRunState": 30, "protocolName": "p"},
                {"runDatalId": None, "isRunInProgress": False, "protocolRunState": 0}]
        cur = {}
        w.prep = lambda path: {"runResult": "Completed", "protocolName": "p", "errorMessages": []} if path.startswith("/run-data/") else cur["run"]
        try:
            state = {"errors": {}, "active": False, "rid": None, "tips": (None, 0), "reloading": False, "last": None, "ending": None, "tries": 0}
            for r in runs:
                cur["run"] = r
                w.check(state)
        finally:
            w.prep, w.send = old
        self.assertEqual(len(sent), 1)
        self.assertIn("Completed", sent[0])


class NoSlack(unittest.TestCase):
    def test_send_without_a_webhook_does_not_raise(self):
        old, w.webhook = w.webhook, lambda: None
        try:
            self.assertFalse(w.send("hello"))
        finally:
            w.webhook = old


if __name__ == "__main__":
    unittest.main()
