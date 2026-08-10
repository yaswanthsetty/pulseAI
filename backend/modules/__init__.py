"""Business modules of the modular monolith (spec §8).

Each subpackage is a strictly-bounded module; cross-module calls must go
through defined service interfaces (see ``.importlinter``).
"""
