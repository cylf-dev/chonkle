"""Codec wrapper classes normalizing different backends.

Each wrapper loads its signature at instantiation and exposes a uniform
call(direction, port_map) interface so the executor does not need to
know which backend is in use.
"""
