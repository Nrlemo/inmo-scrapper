from .base import BlockedError, Connector, Listing, SearchResult
from .zonaprop import ZonapropConnector

# Para agregar un portal: crear connectors/<portal>.py con una subclase de Connector y registrarla acá.
REGISTRY = {"zonaprop": ZonapropConnector}

__all__ = ["BlockedError", "Connector", "Listing", "SearchResult", "REGISTRY"]
