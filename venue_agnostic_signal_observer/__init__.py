import pathlib
# Make this package point to the actual implementation location
_impl_path = pathlib.Path(__file__).parent.parent / "examples" / "strategies" / "venue_agnostic_signal_observer"
__path__ = [str(_impl_path)]
