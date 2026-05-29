"""Material Design icon access with one bundled font family."""

try:
    import qtawesome as _qta
except Exception:  # pragma: no cover
    _qta = None
else:
    _mdi_fonts = tuple(
        font for font in getattr(_qta, "_BUNDLED_FONTS", ())
        if font[0] == "mdi"
    )
    if len(_mdi_fonts) == 1:
        _qta._BUNDLED_FONTS = _mdi_fonts
    else:  # pragma: no cover
        _qta = None


HAS_ICONS = _qta is not None


def icon(*names, **kwargs):
    """Return a QtAwesome icon from the retained MDI5 family."""
    if _qta is None:
        raise RuntimeError("QtAwesome's MDI icon family is unavailable")
    return _qta.icon(*names, **kwargs)
