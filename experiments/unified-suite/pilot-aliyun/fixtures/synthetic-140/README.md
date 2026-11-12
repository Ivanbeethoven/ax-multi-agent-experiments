# 140-callback synthetic smoke input

Copied from the existing rbm probe at /srv/ax-data/inputs/pytest-full-02.
Despite that old directory name, this is a synthetic 140-callback fixture,
not the complete pytest Agent workload. Do not use it for C2 performance claims.

Initial and final trees include content SHA256 and modes. Strict Rust replay
checks every callback and write blob; the mount smoke also checks both trees
and the final tree after reopening.
