import os
import logging
from src.config.storage_config import *
from src.source_data.database import Database

logger = logging.getLogger(__name__)


class InitialTasks:
    def __init__(self):
        pass

    def create_storage_dirs(self):
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        os.makedirs(VIDEOS_DIR, exist_ok=True)

    def initialize_db(self):
        Database.get_instance(DB_PATH)

    def run(self):
        self.create_storage_dirs()
