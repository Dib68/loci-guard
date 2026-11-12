# The foresight swarm

Read this before running a forecast, or when the context shows Forecasts whose outcome you now know.

Use it when a change touches several areas, security-sensitive code or shared behaviour, or the user asks what could go wrong. Offer first: it costs one subagent per persona, most of it fixed overhead, so keep it for changes where being wrong is expensive. Three personas chosen for the change are usually enough.

1. `loci-guard foresee new "<the change>" --at <files or folders>` prints a work order: a maintainer for each touched room plus security, tester, user, operator and skeptic personas, each with its own memories and its calibration so far.
2. Play each persona as an **independent** forecaster: one parallel subagent per persona when you can, so they don't anchor on each other. A small, fast model is fine. Each returns 1-3 lines `PREDICT <persona> | <0-1> | <what will happen> | <how we will know>`. Ask for concrete outcomes that a test, a log or a quick experiment can settle.
3. `loci-guard foresee record <session>` with the lines on stdin. It prints the consensus with each probability weighted by its persona's track record. Use the high ones to shape the change (a test to write first, a rule to check).
4. Close the loop. When an outcome becomes known (tests, CI, an experiment, review, the user): `loci-guard foresee resolve <id> yes|no`. If a prediction can no longer be checked, `void` closes it without scoring anyone. Personas earn weight by being right (Brier score, after 3 resolved predictions), and predictions that came true become lessons in their room. `loci-guard foresee open` lists what still needs an outcome, with hints from lessons recorded since.

Treat forecasts as hypotheses to test, never as facts. A forecast that is never resolved teaches nothing.
