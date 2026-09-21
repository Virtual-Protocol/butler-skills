# valid

A fixture template used to test that the validator passes clean input. Fires
on a timer and buys `AMOUNT_USD` of VIRTUAL, keyed on the tick's own
timestamp so a redelivered tick never buys twice.
