# Campaigns mounted into the server container at /campaigns, on top of the
# built-in ones the image carries. Empty by default: discovery skips a root
# with nothing in it. Set GAUNTLET_CAMPAIGNS to mount a directory elsewhere
# instead.
#
# A campaign carries its own suites, so one mounted here brings them with it
# and needs no second mount at /suites.
