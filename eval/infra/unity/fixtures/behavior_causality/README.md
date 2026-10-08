# Behavior-causality evaluator calibration fixture

This is one evaluator-owned semantic fixture, not a Unity reference answer and
not a game-specific patch. It models a load-bearing causal chain: an injected
action must produce projectile/enemy overlap, remove the live enemy, change the
observable score, and only then permit success. The same binary exposes
declared variants for auto-win, ignored input, non-causal enemy removal, false
telemetry, fixed-witness overfit, and visual-only behavior.

The gameplay component does not speak the evaluator protocol. It moves ordinary
Unity collider objects and reports only through the public SDK. The production
observer is injected at build time and independently constructs the semantic
rows consumed by the hidden controller.

This fixture is overlaid onto the shared target scaffold before building, so it
does not carry another package manifest or project settings. Historical C#
names, executable names, and `cat-*` evidence filenames remain unchanged only
to preserve attribution to the certified runs that produced the committed
digests.
