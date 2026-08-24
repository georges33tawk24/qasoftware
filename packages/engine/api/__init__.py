"""API probes — SPEC §8.4 I.

Derived free from the network capture: we already know every endpoint the site called.

Scope, stated once and enforced in `authorisation.py`: this is regression testing of a
system the user is contracted to test, using credentials they own. It is configuration
and authorisation checking, not exploitation. There is no payload fuzzing beyond
malformed-input handling, nothing here tries to extract data, and no request is sent to a
host that is not on the project's authorised list.
"""

from engine.api.authorisation import Authorisation, NotAuthorised, authorise
from engine.api.endpoints import derive
from engine.api.probes import run_probes

__all__ = ["Authorisation", "NotAuthorised", "authorise", "derive", "run_probes"]
