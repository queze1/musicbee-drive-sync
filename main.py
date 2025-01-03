import hashlib
from itertools import chain
import logging
import pathlib
import string

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

LOGGING_PATH = "main.log"
MUSICBEE_PLAYLIST_PATH = pathlib.Path.home().joinpath("Music/Musicbee/Playlists")
EXPORTED_PLAYLIST_PATH = pathlib.Path.home().joinpath(
    "Music/Musicbee/Exported Playlists"
)
FOLDER_MIMETYPE = "application/vnd.google-apps.folder"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
c_handler = logging.StreamHandler()
f_handler = logging.FileHandler(LOGGING_PATH, encoding="utf8")
c_handler.setLevel(logging.DEBUG)
f_handler.setLevel(logging.DEBUG)
c_format = logging.Formatter("%(levelname)-8s %(message)s")
f_format = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)
logger.addHandler(c_handler)
logger.addHandler(f_handler)


class Drive(GoogleDrive):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._folder_id_cache = {}

    def list_file(self, title=None, is_folder=None, parent_folder=None):
        query = ["trashed = false"]
        if title:
            query.append(f"title = '{title}'")
        if parent_folder:
            query.append(f"'{parent_folder}' in parents")
        if is_folder is True:
            query.append(f"mimeType = '{FOLDER_MIMETYPE}'")
        elif is_folder is False:
            query.append(f"mimeType != '{FOLDER_MIMETYPE}'")

        query = " and ".join(query)
        return super().ListFile({"q": query}).GetList()

    def create_file(self, title: str, is_folder=False, parent_folder=None):
        metadata = dict()
        metadata["title"] = title
        if is_folder:
            metadata["mimeType"] = FOLDER_MIMETYPE
        if parent_folder:
            metadata["parents"] = [{"id": parent_folder}]
        return super().CreateFile(metadata)

    def create_folder(self, path_parts: list[str]):
        current_folder_id = "root"
        remaining_parts = []
        for part in reversed(path_parts):
            if part in self._folder_id_cache:
                current_folder_id = self._folder_id_cache[part]
                break
            remaining_parts.insert(0, part)

        for part in remaining_parts:
            next_folder = self.list_file(
                title=part, is_folder=True, parent_folder=current_folder_id
            )
            if not next_folder:
                next_folder = self.create_file(
                    title=part, is_folder=True, parent_folder=current_folder_id
                )
                next_folder.Upload()
            else:
                next_folder = next_folder[0]

            current_folder_id = next_folder["id"]
            self._folder_id_cache[part] = next_folder["id"]
        return current_folder_id


def get_m3u_title(path: pathlib.Path):
    if path.is_relative_to(MUSICBEE_PLAYLIST_PATH):
        rel_path = path.relative_to(MUSICBEE_PLAYLIST_PATH)
    else:
        rel_path = path.relative_to(EXPORTED_PLAYLIST_PATH)
    if len(rel_path.parts) <= 2:
        return f"{rel_path.stem}.m3u"
    else:
        return f"{rel_path.parts[-2]} - {rel_path.stem}.m3u"


# Strips to only letters and digits
def strip(path: pathlib.Path):
    return "".join(
        [char for char in str(path) if char in string.ascii_letters + string.digits]
    )


# https://gist.github.com/lempamo/6e8977065da593e372e45d4c628e7fc7
def decode_from_7bit(data):
    result = 0
    for index, char in enumerate(data):
        # byte_value = ord(char)
        result |= (char & 0x7F) << (7 * index)
        if char & 0x80 == 0:
            break
    return result


def read_int(bytes_):
    return int.from_bytes(bytes_, byteorder="little", signed=True)


def read_uint(bytes_):
    return int.from_bytes(bytes_, byteorder="little")


def read_str(file):
    len_1 = file.read(1)
    if read_uint(len_1) > 0x7F:
        len_2 = file.read(1)
        if read_uint(len_2) > 0x7F:
            length = decode_from_7bit(
                [read_uint(len_1), read_uint(len_2), read_uint(file.read(1))]
            )
        else:
            length = decode_from_7bit([read_uint(len_1), read_uint(len_2)])
    else:
        length = read_uint(len_1)
    if length == 0:
        return ""
    return file.read(length).decode("utf-8")


def parse_mbp(path):
    paths = []
    with open(path, "rb") as file:
        # Read magic number and header
        file.read(4)
        length = int.from_bytes(file.read(1))
        file.read(length + 18)

        while True:
            # Read path
            path = read_str(file)
            if len(path) == 0:
                break
            paths.append(path)

            # Read separator
            file.read(4)
    return paths


def parse_m3u(path):
    with open(path, encoding="utf-8") as file:
        return [path for path in file.read().split("\n") if path]


def get_songs(path) -> list[pathlib.Path]:
    if path.suffix == ".mbp":
        paths = parse_mbp(path)
    elif path.suffix == ".m3u":
        paths = parse_m3u(path)

    paths = [pathlib.Path(path) for path in paths]
    for path in paths:
        if not path.exists():
            logger.warning(f"{path} was not found.")
    return paths


def gen_m3u_content(songs: list[pathlib.Path]):
    return "\n".join([f"songs/{song.name}" for song in songs])


def main():
    logger.info("Started.")

    gauth = GoogleAuth()
    gauth.LocalWebserverAuth()
    drive = Drive(gauth)

    music_folder = drive.create_folder(["Music"])
    songs_folder = drive.create_folder(["Music", "songs"])

    # Get all playlists and their songs
    path_to_songs = {}
    playlist_paths = list(
        chain(
            MUSICBEE_PLAYLIST_PATH.rglob("*.mbp"), EXPORTED_PLAYLIST_PATH.rglob("*.m3u")
        )
    )
    for path in playlist_paths:
        path_to_songs[path] = get_songs(path)
    all_songs = set(chain.from_iterable(path_to_songs.values()))
    all_song_names = [song.name for song in all_songs]

    # Delete songs
    existing_songs = drive.list_file(parent_folder=songs_folder)
    existing_songs_names = [song["title"] for song in existing_songs]
    songs_to_delete = [
        song for song in existing_songs if song["title"] not in all_song_names
    ]
    for i, song in enumerate(songs_to_delete):
        song.Trash()
        logger.debug(
            f"Song {i+1} of {len(songs_to_delete)} to delete done. "
            f"{song['title']} was deleted."
        )

    # Upload songs
    songs_to_upload = [
        song for song in all_songs if song.name not in existing_songs_names
    ]
    for i, song in enumerate(songs_to_upload):
        file = drive.create_file(title=song.name, parent_folder=songs_folder)
        file.SetContentFile(song)
        file.Upload()
        logger.debug(
            f"Song {i+1} of {len(songs_to_upload)} to upload done."
            f" {song.name} was uploaded."
        )

    # Update existing playlist files
    title_to_path = {get_m3u_title(path): path for path in playlist_paths}
    existing_playlists = drive.list_file(parent_folder=music_folder, is_folder=False)
    for i, playlist in enumerate(existing_playlists):
        if playlist["title"] not in title_to_path:
            playlist.Trash()
            logger.debug(f"{playlist['title']} was deleted.")
            continue

        songs = path_to_songs[title_to_path[playlist["title"]]]
        if (
            hashlib.md5(gen_m3u_content(songs).encode()).hexdigest()
            != playlist["md5Checksum"]
        ):
            playlist.SetContentString(gen_m3u_content(songs))
            playlist.Upload()
            logger.debug(f"{playlist['title']} was updated.")

    # Add new playlist files
    existing_playlist_titles = [playlist["title"] for playlist in existing_playlists]
    playlists_to_upload = [
        title for title in title_to_path if title not in existing_playlist_titles
    ]
    for i, playlist_title in enumerate(playlists_to_upload):
        file = drive.create_file(title=playlist_title, parent_folder=music_folder)
        songs = path_to_songs[title_to_path[playlist_title]]
        file.SetContentString(gen_m3u_content(songs))
        file.Upload()
        logger.debug(
            f"Playlist file {i+1} of {len(playlists_to_upload)} to upload done. "
            f"{playlist_title} was uploaded."
        )

    logger.info("Done.")


if __name__ == "__main__":
    main()
