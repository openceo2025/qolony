from .client import IQuavisClient

__all__ = ["IQuavisClient", "IQuavisSyncService"]


def __getattr__(name: str):
    if name == "IQuavisSyncService":
        from .sync import IQuavisSyncService

        return IQuavisSyncService
    raise AttributeError(name)
