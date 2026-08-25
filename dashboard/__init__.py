"""The user interface for the workflow: the runs index and the competitors panel.

Two static pages, written by ``python -m dashboard build`` into ``runs/``. This
package may import both parsers and the price calculator; none of them import
it, which is what lets ``banzai24 report`` keep costing nothing while these
pages read two databases, a cost book and today's exchange rate.
"""
