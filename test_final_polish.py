"""SHIP Q — final polish (panel_v 81).

Not a QA PASS. Locks the buy-toast offset, narrow follow dock, and the
profile-file reject path that must not leave the Star preset.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import server


def _panel() -> str:
    return Path("static/index.html").read_text(encoding="utf-8")


class FinalPolishHealthTests(unittest.TestCase):
    def test_health_is_v81_with_final_polish(self):
        data = server.app.test_client().get("/api/health").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 91)
        for feature in (
            "final_polish",
            "profile_custom_pic",
            "game_follow_tab",
            "filter_smart",
            "dismiss_back",
            "game_24h_restart",
            "token_flow_new_sound",
        ):
            self.assertIn(feature, data["features"])
        note = data["final_polish"]
        self.assertIn("follow chip", note)
        self.assertIn("Star", note)
        self.assertIn("Token Flow", note)


class FinalPolishPanelTests(unittest.TestCase):
    def test_toast_clears_the_follow_chip_and_narrow_dock_fits(self):
        html = _panel()
        self.assertIn("const PANEL_V = 91", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("paintPanelVersion", html)
        self.assertIn("data-panel-v", html)
        self.assertIn("function syncToastOffset()", html)
        self.assertIn("bottom: var(--toast-bottom, 18px)", html)
        self.assertNotIn("padding-bottom: var(--follow-clearance, 72px)", html)
        self.assertNotIn("height: calc(100vh - 56px)", html)
        self.assertIn("padding-bottom: 12px", html)
        css = html[html.index("/* ship-r-follow-start */"):html.index("/* ship-r-follow-end */")]
        self.assertIn("width: min(360px, 100%)", css)
        self.assertNotIn("100vw", css)
        narrow = html[html.index("/* ship-q-polish-start */"):html.index("/* ship-q-polish-end */")]
        self.assertIn("max-width: 420px", narrow)
        self.assertIn(".pick-row { grid-template-columns: minmax(0, 1fr); }", narrow)
        self.assertIn("overflow-wrap: anywhere", html[html.index(".gold-why"):html.index(".gold-why") + 180])
        self.assertIn("overflow-wrap: anywhere", html[html.index(".score-detail .why"):html.index(".score-detail .why") + 220])

    def test_rejected_profile_file_keeps_the_picker_snapshot(self):
        html = _panel()
        pure = html[html.index("/* ship-q-pure-start */"):html.index("/* ship-q-pure-end */")]
        picker = html[html.index("function rejectProfileFile"):html.index("function paintFinish")]
        preset = html[html.index('querySelectorAll("#pfPicks button")'):html.index("const pfFile")]
        self.assertIn("rejectProfileFile", picker)
        self.assertIn("restorePfpDraft", picker)
        self.assertNotIn('pfpDraft = "star"', picker)
        self.assertIn("armPfpGhostGuard", picker)
        self.assertIn("pfpGhostBlocksPreset", preset)
        self.assertIn("The current avatar is unchanged", picker)
        script = pure + r"""
const kept = pfpDraftAfterRejectedFile({
  pfpDraft: "custom",
  pfpDataDraft: "data:image/png;base64,abc",
  pfpFileName: "me.png",
});
if (kept.pfpDraft !== "custom" || kept.pfpDataDraft.indexOf("png") < 0 || kept.pfpFileName !== "me.png") {
  process.exit(2);
}
if (pfpDraftAfterRejectedFile(null).pfpDraft !== "rocket") process.exit(3);
if (!pfpGhostBlocksPreset(1000, 1400)) process.exit(4);
if (pfpGhostBlocksPreset(1400, 1400)) process.exit(5);
if (pfpGhostBlocksPreset(1401, 1400)) process.exit(6);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
