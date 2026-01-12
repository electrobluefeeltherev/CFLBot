import json
import os
from pathlib import Path
from typing import Dict, Any

DATA_FILE = Path("game_data.json")

class GameData:
    def __init__(self):
        self.data: Dict[str, Any] = {
            "teams": {}
        }