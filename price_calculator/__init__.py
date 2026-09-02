"""Landed cost of a Japanese auction car in Cyprus, against what it sells for here.

``calculator`` is the pure arithmetic (the Google Sheet, ported); ``sources``
reads the cost book, the rates and the two databases; ``cli`` prints the table.
A car's own figures — dimensions, CO2 — are :mod:`cars.specs`, which this
package reads and does not own (ADR-0008). See ``price_calculator_spec.md`` for
the sheet this is a port of.
"""
