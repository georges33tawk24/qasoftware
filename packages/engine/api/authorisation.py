"""The gate in front of every probe — build prompt phase 6, scope discipline.

Nothing in the API probe engine sends a request until this has said yes. Two things are
required and neither has a default: a named person who authorised the testing, and the
list of hosts that authorisation covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from engine.artifact.models import RunConfig


class NotAuthorised(RuntimeError):
    pass


@dataclass(frozen=True)
class Authorisation:
    by: str
    hosts: frozenset[str]

    def allows(self, url: str) -> bool:
        host = urlsplit(url).netloc.casefold()
        if not host:
            return False
        return host in self.hosts or host.split(":")[0] in self.hosts

    def refuse(self, url: str) -> str:
        return (
            f"{urlsplit(url).netloc} is not in the authorised host list "
            f"({', '.join(sorted(self.hosts))})"
        )


def authorise(config: RunConfig, target: str) -> Authorisation:
    """Raise unless this run is allowed to send requests of its own.

    A run with no `authorisedBy` is a run nobody has taken responsibility for, and the
    engine will crawl it but will not probe it.
    """
    by = (config.authorisedBy or "").strip()
    if not by:
        raise NotAuthorised(
            "API probes need an authorisedBy on the run config: the name of the person "
            "who authorised testing this target. Crawling and checking do not need it; "
            "sending requests of our own does."
        )
    hosts = {h.casefold().strip() for h in config.authorisedHosts if h.strip()}
    seed = urlsplit(target).netloc.casefold()
    if seed:
        hosts.add(seed)
        hosts.add(seed.split(":")[0])
    if not hosts:
        raise NotAuthorised("no authorised host could be determined from the target")
    return Authorisation(by=by, hosts=frozenset(hosts))
