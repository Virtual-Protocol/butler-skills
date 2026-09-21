import bevo

# BUG (deliberate, for the validator fixture suite): declares a timer
# trigger but never calls bevo.ticks() (or any other waiter) — this runs once
# and exits, which the supervisor reports as a crash.
bevo.log("ran once and exited")
