import re

from nethackers.containers import NETHACKERS_LABEL, container_name, label_args


def test_container_name_prefixed_and_unique():
    a, b = container_name("arena"), container_name("arena")
    assert re.fullmatch(r"nethackers-arena-[0-9a-f]{8}", a)
    assert a != b  # the hex suffix keeps concurrent containers unique


def test_label_args_and_label_constant():
    assert NETHACKERS_LABEL == "nethackers"
    assert label_args() == ["--label", "nethackers"]
