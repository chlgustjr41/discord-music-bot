"""NodeProvider: the seam for future local (user-hosted) Lavalink nodes.

v1 ships exactly one implementation returning the VM node for every guild
(spec §3.1). Local-node support later becomes a second implementation with
fallback — no call-site changes required.
"""

from typing import Protocol

from jacky.audio.node import LavalinkNode


class NodeProvider(Protocol):
    def node_for(self, guild_id: int) -> LavalinkNode: ...


class SingleNodeProvider:
    def __init__(self, node: LavalinkNode) -> None:
        self._node = node

    def node_for(self, guild_id: int) -> LavalinkNode:
        return self._node
