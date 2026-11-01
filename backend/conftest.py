import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_configure(config):
    # Z10-a: the no-look-ahead harness runs as its own CI job (`-m lookahead`); the main run passes `-m "not lookahead"`
    config.addinivalue_line("markers", "lookahead: the Z10-a no-look-ahead harness (tests/test_z10a_no_lookahead.py), a separate CI job")
    config.addinivalue_line("markers", "real_order_defaults: M68 PR-1 - read each user's own Trade settings > Order defaults "
                                       "(tests/conftest.py _saved_order_defaults is not applied)")
    config.addinivalue_line("markers", "real_position_size: M68 PR-2 - read each user's own Trade settings > Position size "
                                       "(tests/conftest.py _saved_position_size is not applied)")
