def classFactory(iface):
    from .plugin import PcrAutofillPlugin
    return PcrAutofillPlugin(iface)
