# Suites mounted into the server container at /suites. The image carries no
# bare suite of its own — every suite it ships belongs to a campaign — so this
# is the mount for one that belongs to no campaign. Empty by default:
# discovery skips a root with nothing in it. Set GAUNTLET_SUITES to mount a
# directory elsewhere instead.
