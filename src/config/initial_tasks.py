import os
import logging
from config.storage_config import SCREENSHOTS_DIR, VIDEOS_DIR, DB_PATH
from source_data.database import Database

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
