from __future__ import annotations

import unittest

from pulse.assistant.shopping_list import ShoppingListParser


class ShoppingListParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = ShoppingListParser(compound_items=("corn flour", "bacon bits"))

    def test_add_command_splits_multi_items(self) -> None:
        command = self.parser.parse("add butter syrup eggs corn flour sliced ham bacon bits to my shopping list")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.action, "add")
        self.assertEqual(
            command.items,
            ["butter", "syrup", "eggs", "corn flour", "sliced ham", "bacon bits"],
        )

    def test_remove_command_detected(self) -> None:
        command = self.parser.parse("remove butter and eggs from my shopping list")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.action, "remove")
        self.assertEqual(command.items, ["butter", "eggs"])

    def test_show_command_detected(self) -> None:
        command = self.parser.parse("what is on my shopping list")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.action, "show")

    def test_clear_command_detected(self) -> None:
        command = self.parser.parse("clean my shopping list")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.action, "clear")


if __name__ == "__main__":
    unittest.main()
