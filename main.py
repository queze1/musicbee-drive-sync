import json
import logging
from itertools import chain
import string


# Logging setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
c_handler = logging.StreamHandler()
f_handler = logging.FileHandler('log.txt')
c_handler.setLevel(logging.DEBUG)
f_handler.setLevel(logging.INFO)
c_format = logging.Formatter("%(levelname)-8s %(message)s")
f_format = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)
logger.addHandler(c_handler)
logger.addHandler(f_handler)


with open("config.json") as file:
    CONFIG = json.load(file)

SCOPES = ["https://www.googleapis.com/auth/youtube"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"
CLIENT_SECRETS_FILE = CONFIG["client_secrets_file"]

PLAYLIST_PATH = CONFIG["playlist_path"]
PLAYLIST_EXT = ".mbp"
SONG_CACHE_PATH = "song_id_cache.json"
DELETION_EXCLUDED = CONFIG["deletion_excluded"]
ADDITION_EXCLUDED = CONFIG["addition_excluded"]

ALPHANUMERIC = string.ascii_letters + string.digits


def get_song_paths(path_) -> list:
    with open(path_, errors='ignore') as file:
        lines = [_ for _ in file.read().split("\x00") if ":\\" in _][1:]
        paths = [[___ for ___ in __.split("ÿÿÿÿ")] for __ in lines]
        paths = list(chain(*paths))[:-1]
        paths = [path.split(":\\")[0][-1] + ":\\" + path.split(":\\")[1] for path in paths]
        return paths


def main():
    logger.info("Started.")


if __name__ == "__main__":
    main()
