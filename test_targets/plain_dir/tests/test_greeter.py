import pytest
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
from greeter import greet, farewell


def test_greet():
    assert greet("World") == "Hello, World!"


def test_farewell():
    assert farewell("World") == "Goodbye, World!"
