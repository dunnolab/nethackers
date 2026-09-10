"""Inventory state and prompt regressions without optional game dependencies."""

import importlib.util
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).parents[1] / "roots/autoascend/autoascend"


@pytest.fixture
def modules(monkeypatch):
    def module(name, **attributes):
        value = ModuleType(name)
        for key, attribute in attributes.items():
            setattr(value, key, attribute)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        value = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, value)
        spec.loader.exec_module(value)
        return value

    class Item:
        UNKNOWN, CURSED, UNCURSED, BLESSED = range(4)

    classes = {name: type(name, (), {}) for name in ("Armor", "Weapon", "WepTool", "Container")}
    slots = {"ARM_" + name: i for i, name in enumerate(
        ("SHIELD", "SUIT", "HELM", "GLOVES", "BOOTS", "CLOAK", "SHIRT"))}
    objects = module("autoascend.objects", **classes, **slots, from_name=lambda *args: args[0])
    utils = module("autoascend.utils", debug_log=lambda *args, **kwargs: lambda f: f)
    nh = module("nle.nethack", glyph_is_body=lambda g: False, glyph_is_statue=lambda g: False,
                **{name + "_CLASS": i for i, name in enumerate(
                    ("AMULET", "ARMOR", "FOOD", "COIN", "GEM", "POTION", "RING", "SCROLL",
                     "SPBOOK", "TOOL", "WEAPON", "WAND", "ROCK", "CHAIN", "BALL"))})
    actions = module("nle.nethack.actions", Command=SimpleNamespace(LOOK="look", PICKUP="pickup"))
    monkeypatch.setattr(nh, "actions", actions, raising=False)
    module("nle", nethack=nh)
    module("autoascend", objects=objects, utils=utils)
    module("autoascend.character", Character=SimpleNamespace(LAWFUL="lawful"))
    module("autoascend.exceptions", AgentPanic=RuntimeError)
    module("autoascend.glyph", G=SimpleNamespace(), Hunger=SimpleNamespace(), MON=SimpleNamespace())
    module("autoascend.strategy", Strategy=SimpleNamespace(wrap=lambda f: f))
    module("autoascend.soko_solver")
    module("autoascend.level", Level=SimpleNamespace())
    module("autoascend.item.item_priority_base", ItemPriorityBase=object)
    module("autoascend.item", Item=Item, ItemManager=object, ContainerContent=object,
           check_if_triggered_container_trap=lambda message: False,
           find_equivalent_item=lambda *args: None, flatten_items=lambda items: items)
    items = load("autoascend.item.inventory_items", "item/inventory_items.py")
    inventory = load("autoascend.item.inventory", "item/inventory.py")
    logic = load("autoascend.global_logic", "global_logic.py")
    return SimpleNamespace(items=items, inventory=inventory, logic=logic, objects=objects,
                           Item=Item, nh=nh)


def test_callbacks_only_see_a_complete_inventory(modules):
    seen = []
    depth = 0
    armor = modules.objects.Armor()
    armor.sub = modules.objects.ARM_BOOTS
    boots = SimpleNamespace(equipped=True, objs=[armor], is_possible_container=lambda: False,
                            is_container=lambda: False, weight=lambda: 10)
    bag = SimpleNamespace(equipped=False, is_possible_container=lambda: True,
                          is_container=lambda: True, weight=lambda: 1)

    def observe():
        if depth == 0:
            seen.append((len(items.all_items), items.boots))

    @contextmanager
    def atomic():
        nonlocal depth
        depth += 1
        try:
            yield
        finally:
            depth -= 1
        observe()

    # Checking the first item's container advances the game. Strategy callbacks
    # must wait until the second item (equipped boots) has also been parsed.
    agent = SimpleNamespace(
        atom_operation=atomic,
        last_observation={"inv_strs": np.array([b"bag", b"boots"]),
                          "inv_oclasses": [1, 2], "inv_glyphs": [0, 0],
                          "inv_letters": [ord("A"), ord("z")]},
        inventory=SimpleNamespace(
            item_manager=SimpleNamespace(get_item_from_text=lambda name, **kw:
                                         bag if name == "bag" else boots),
            check_container_content=lambda item: observe(),
        ),
    )
    items = modules.items.InventoryItems(agent)
    items.update(force=True)
    assert seen == [(2, boots)]
    assert items.all_letters == ["A", "z"]


@pytest.mark.parametrize("burden", ["a little trouble", "much trouble"])
def test_floor_inspection_cancels_direct_burden_prompt(modules, burden):
    calls = []
    agent = SimpleNamespace(panic_if_position_changes=nullcontext, atom_operation=nullcontext,
                            message="", popup=[])

    def step(action):
        calls.append(action)
        if action == "look":
            agent.message = ""
            agent.popup = ["Things that are here:"]
        else:
            agent.message = (f"You have {burden} lifting a heavy iron ball (chained to you)."
                             "  Continue? [ynq] (q)")
            agent.popup = []

    def type_text(action):
        calls.append(action)
        assert action == "n"
        agent.message = "Never mind."

    agent.step = step
    agent.type_text = type_text
    inventory = modules.inventory.Inventory.__new__(modules.inventory.Inventory)
    inventory.agent = agent
    assert inventory.get_items_below_me() == []
    assert inventory.letters_below_me == []
    assert calls == ["look", "pickup", "n"]


@pytest.mark.parametrize("unit_weight,capacity,expected", [
    (0, 0, 3), (np.float64(0), 100, 3), (2, 4, 2), (2, float("inf"), 3),
])
def test_item_capacity_handles_zero_weight_and_bounded_stacks(
    modules, unit_weight, capacity, expected,
):
    class Food(modules.Item):
        count = 3
        category = modules.nh.FOOD_CLASS
        status = modules.Item.UNCURSED
        objs = [SimpleNamespace(name="food")]

        def unit_weight(self, **kwargs):
            return unit_weight

        def is_container(self):
            return False

        def is_unambiguous(self):
            return False

        def is_thrown_projectile(self):
            return False

        def is_food(self):
            return True

        def is_corpse(self):
            return False

        def nutrition_per_weight(self):
            return 1

    agent = SimpleNamespace(
        blstats=SimpleNamespace(time=0), character=SimpleNamespace(alignment="neutral"),
        inventory=SimpleNamespace(get_best_melee_weapon=lambda **kwargs: None,
                                  get_best_armorset=lambda **kwargs: []),
    )
    priority = modules.logic.ItemPriority(agent)
    assert priority._split([Food()], [], capacity) == {None: [expected]}
