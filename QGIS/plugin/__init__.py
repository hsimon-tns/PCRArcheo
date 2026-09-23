def classFactory(iface):
    from .plugin import HelloPlugin
    return HelloPlugin(iface)
