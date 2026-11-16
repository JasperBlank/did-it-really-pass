"""Where private data lives: verdicts, the model-reply cache, the live log. Never inside the installed package."""
import os
from pathlib import Path

_checkout = Path(__file__).resolve().parent.parent / "data"  # a git checkout keeps its data next to the code

if os.environ.get("DID_IT_PASS_HOME"):
    DATA = Path(os.environ["DID_IT_PASS_HOME"])
elif _checkout.is_dir():
    DATA = _checkout
else:
    DATA = Path.home() / ".did-it-pass"
