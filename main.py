import hashlib
from itertools import chain
import logging
import pathlib
import string

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

MUSICBEE_PLAYLIST_PATH = pathlib.Path.home().joinpath("Music/Musicbee/Playlists")
EXPORTED_PLAYLIST_PATH = pathlib.Path.home().joinpath(
    "Music/Musicbee/Exported Playlists"
)
FOLDER_MIMETYPE = "application/vnd.google-apps.folder"


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
def strip(str_: pathlib.Path):
    return "".join(
        [char for char in str(str_) if char in string.ascii_letters + string.digits]
    )


def get_songs(path: pathlib.Path, logger: logging.Logger = None) -> list[pathlib.Path]:
    # Incomprehensible, could break at any moment
    with open(path, errors="ignore") as file:
        if path.suffix == ".mbp":
            lines = [_ for _ in file.read().split("\x00") if ":\\" in _][1:]
            paths = [[___ for ___ in __.split("ÿÿÿÿ")] for __ in lines]
            paths = list(chain(*paths))[:-1]
            paths = [
                path.split(":\\")[0][-1] + ":\\" + path.split(":\\")[1]
                for path in paths
            ]
            paths = [pathlib.Path(path) for path in paths]
        elif path.suffix == ".m3u":
            paths = [pathlib.Path(path) for path in file.read().split("\n") if path]

    good_paths = []
    bad_paths = []
    for path in paths:
        if path.exists():
            good_paths.append(path)
        else:
            bad_paths.append(path)

    # Fix up unicode errors by stripping them of anything except letters and digits and then matching them
    stripped_to_path = {}
    for bad_path in bad_paths:
        stripped_bad_path = strip(bad_path)
        if bad_path.parent not in stripped_to_path:
            stripped_to_path[bad_path.parent] = {
                strip(path): path for path in bad_path.parent.glob("*.*")
            }
        if stripped_bad_path in stripped_to_path[bad_path.parent]:
            good_paths.append(stripped_to_path[bad_path.parent][stripped_bad_path])

        elif logger:
            logger.warning(f"{bad_path} was not found. It was located in {path}")

    return good_paths


def gen_m3u_content(songs: list[pathlib.Path]):
    return "\n".join([f"songs/{song.name}" for song in songs])


def main(logger):
    logger.info("Started.")

    gauth = GoogleAuth()
    # TODO: Automatic authentication
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
    # Logging setup
    my_logger = logging.getLogger(__name__)
    my_logger.setLevel(logging.DEBUG)
    c_handler = logging.StreamHandler()
    f_handler = logging.FileHandler("log.txt", encoding="utf8")
    c_handler.setLevel(logging.DEBUG)
    f_handler.setLevel(logging.DEBUG)
    c_format = logging.Formatter("%(levelname)-8s %(message)s")
    f_format = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    c_handler.setFormatter(c_format)
    f_handler.setFormatter(f_format)
    my_logger.addHandler(c_handler)
    my_logger.addHandler(f_handler)

    main(my_logger)
