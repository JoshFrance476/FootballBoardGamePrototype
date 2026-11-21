"""Dark mode. config.DARK_MODE_COLOURS lists colours (by their config name)
to use instead of the normal ones while config.DARK_MODE is on; apply()
swaps them in or back out. Everything reads colours from config when it
draws, so the change shows on the next frame."""
import config

_light = {}     # the normal value of every colour dark mode replaces


def apply(fresh=False):
    """Set config's colours to match config.DARK_MODE. fresh: config.py was
    just (re)loaded, so its current colours are the normal ones."""
    if fresh:
        _light.clear()
    for name in config.DARK_MODE_COLOURS:
        if name not in _light:
            _light[name] = getattr(config, name)
    dark = config.DARK_MODE_COLOURS if config.DARK_MODE else {}
    for name, normal in _light.items():
        setattr(config, name, dark.get(name, normal))


def set_dark(on):
    config.DARK_MODE = bool(on)
    apply()


def toggle():
    set_dark(not config.DARK_MODE)
