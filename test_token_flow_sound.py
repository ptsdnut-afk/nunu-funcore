#!/usr/bin/env python3
"""SHIP I — Token Flow new-coin chime (N01–N04)."""

import subprocess
import unittest
from pathlib import Path


def _panel():
    return Path("static/index.html").read_text(encoding="utf-8")


class HealthTests(unittest.TestCase):
    def test_health_is_v68_with_token_flow_new_sound(self):
        import server

        res = server.app.test_client().get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["panel_v"], 74)
        self.assertIn("token_flow_new_sound", data["features"])
        self.assertIn("game_polish", data["features"])
        self.assertIn("delay_warning", data["features"])


class PanelChromeTests(unittest.TestCase):
    def test_filter_shows_shared_mute_and_arrival_chrome(self):
        html = _panel()
        self.assertIn("const PANEL_V = 74", html)
        self.assertIn("paper only · v74", html)
        self.assertIn('data-flow-hook="new-token"', html)
        self.assertIn('id="flowChime"', html)
        self.assertIn("♪ NEW TOKEN", html)
        self.assertIn('id="flowMuteNote"', html)
        self.assertIn("muted · no chime", html)
        self.assertIn("tag-arrive", html)
        self.assertIn('id="gSoundOnBtn"', html)
        self.assertIn('id="gSoundMuteBtn"', html)
        self.assertIn(">Sound on<", html)
        self.assertIn(">Mute<", html)
        toggle = html[html.index(".sound-toggle {"):html.index("body.mode-game .sound-toggle")]
        self.assertIn("display: inline-flex", toggle)
        self.assertNotIn("display: none", toggle)
        self.assertIn('data-sfx-hook="casino-cues"', html)
        self.assertEqual(html.count('beep("tick")'), 1)
        self.assertIn('beep("down")', html)
        self.assertIn(">Social Tap<", html)

    def test_new_token_does_not_ride_the_game_beep(self):
        html = _panel()
        beep = html[html.index("function beep(kind)"):html.index("function playResponseSound")]
        self.assertLess(beep.index('kind === "newtoken"'), beep.index("markSound"))
        player = html[html.index("function playNewTokenChime"):html.index("function paintFlowArrival")]
        self.assertIn("state.mute", player)
        self.assertIn('startTone(ctx, "newtoken"', player)
        self.assertNotIn("markSound", player)
        self.assertNotIn("beep(", player)
        render_fn = html[html.index("function render()"):html.index("async function loadOne")]
        self.assertNotIn("noteTokenFlowFlags", render_fn)
        debounce = html[html.index("function debounceRender"):html.index("function tickAge")]
        self.assertNotIn("noteTokenFlowFlags", debounce)
        self.assertEqual(html.count("noteTokenFlowFlags("), 2)
        self.assertIn("noteTokenFlowFlags(nextRows)", html)


class DecisionTests(unittest.TestCase):
    def test_mint_diff_copy_and_distinct_chime(self):
        html = _panel()
        seq = html[html.index("const SOUND_SEQ"):html.index("function soundLabel")]
        pure = html[html.index("/* ship-i-pure-start */"):html.index("/* ship-i-pure-end */")]
        script = seq + "\n" + pure + r"""
if (SOUND_WAVE.tick !== "square" || SOUND_WAVE.down !== "sawtooth") process.exit(2);
if (SOUND_WAVE.newtoken !== "sine") process.exit(3);
const nt = JSON.stringify(SOUND_SEQ.newtoken);
if (nt === JSON.stringify(SOUND_SEQ.tick)) process.exit(4);
if (nt === JSON.stringify(SOUND_SEQ.down)) process.exit(5);
if (nt === JSON.stringify(SOUND_SEQ.buy)) process.exit(6);
if (nt === JSON.stringify(SOUND_SEQ.sell)) process.exit(7);
if (SOUND_SEQ.newtoken.length < 2) process.exit(8);

const row = (mint, ticker) => ({ mint: mint, ticker: ticker });
const seed = flowAlertPlan(null, [row("MintA", "NOMO"), row("MintB", "RIFT")], { muted: false, unlocked: true });
if (seed.outcome !== "seed" || seed.play.length || seed.fresh.length) process.exit(10);
if (seed.next.join(",") !== "MintA,MintB") process.exit(11);
const seedCopy = flowArrivalCopy(seed.outcome, []);
if (seedCopy.flash || seedCopy.muteNote || seedCopy.toast) process.exit(12);

const empty = flowAlertPlan([], [], { muted: false, unlocked: true });
if (empty.outcome !== "same" || empty.play.length) process.exit(13);
const emptyCopy = flowArrivalCopy(empty.outcome, []);
if (emptyCopy.toast || emptyCopy.flash) process.exit(14);

const same = flowAlertPlan(["MintA", "MintB"], [row("MintB", "RIFT"), row("MintA", "NOMO"), row("MintA", "NOMO")], { muted: false, unlocked: true });
if (same.outcome !== "same" || same.play.length || same.fresh.length) process.exit(15);

const left = flowAlertPlan(["MintA", "MintB"], [row("MintA", "NOMO")], { muted: false, unlocked: true });
if (left.outcome !== "same" || left.play.length) process.exit(16);

const one = flowAlertPlan(["MintA", "MintB"], [row("MintC", "PEBBLE"), row("MintA", "NOMO"), row("  ", "NOPE"), row("MintB", "RIFT")], { muted: false, unlocked: true });
if (one.outcome !== "play" || one.play.join(",") !== "MintC") process.exit(17);
const oneCopy = flowArrivalCopy(one.outcome, flowArrivalNames([row("MintC", "PEBBLE")], one.fresh));
if (!oneCopy.flash || oneCopy.muteNote || oneCopy.title !== "♪ NEW TOKEN") process.exit(18);
if (oneCopy.toast !== "New token flagged · PEBBLE") process.exit(19);
if (oneCopy.detail.indexOf("WIN HIT") < 0) process.exit(20);

const two = flowAlertPlan([], [row("MintA", "ONE"), row("MintB", "TWO")], { muted: false, unlocked: true });
if (two.outcome !== "play" || two.play.join(",") !== "MintA,MintB") process.exit(21);

const muted = flowAlertPlan([], [row("MintA", "PEBBLE")], { muted: true, unlocked: true });
if (muted.outcome !== "muted" || muted.play.length || muted.fresh.join(",") !== "MintA") process.exit(22);
const mutedCopy = flowArrivalCopy(muted.outcome, flowArrivalNames([row("MintA", "PEBBLE")], muted.fresh));
if (mutedCopy.flash || !mutedCopy.muteNote || mutedCopy.toastKind !== "silent") process.exit(23);
if (mutedCopy.toast !== "New token flagged · silent") process.exit(24);
if (mutedCopy.title !== "muted · no chime") process.exit(25);

const blocked = flowAlertPlan(["MintA"], [row("MintA", "NOMO"), row("MintD", "ZIP")], { muted: false, unlocked: false });
if (blocked.outcome !== "blocked" || blocked.play.length || blocked.fresh.join(",") !== "MintD") process.exit(26);
const blockedCopy = flowArrivalCopy(blocked.outcome, ["ZIP"]);
if (!blockedCopy.flash || blockedCopy.detail.indexOf("tap Sound on") < 0) process.exit(27);

const afterMute = flowAlertPlan(muted.next, [row("MintA", "PEBBLE"), row("MintE", "NEXT")], { muted: false, unlocked: true });
if (afterMute.outcome !== "play" || afterMute.play.join(",") !== "MintE") process.exit(28);

const blank = tokenFlowMintKeys([{ ticker: "NO" }, null, { mint: "  MintZ  " }]);
if (blank.join(",") !== "MintZ") process.exit(29);
const many = flowArrivalLabel(["A", "B", "C", "D"]);
if (many !== "A, B, C +1") process.exit(30);
"""
        subprocess.check_call(["node", "-e", script])


if __name__ == "__main__":
    unittest.main()
