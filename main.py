import logging
import pathlib
from itertools import chain
import string

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

MUSICBEE_PLAYLIST_PATH = pathlib.Path.home().joinpath(
    'Music/Musicbee/Playlists'
)
EXPORTED_PLAYLIST_PATH = pathlib.Path.home().joinpath(
    'Music/Musicbee/Exported Playlists'
)

# Logging setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
c_handler = logging.StreamHandler()
f_handler = logging.FileHandler('log.txt', encoding='utf8')
c_handler.setLevel(logging.DEBUG)
f_handler.setLevel(logging.DEBUG)
c_format = logging.Formatter('%(levelname)-8s %(message)s')
f_format = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)
logger.addHandler(c_handler)
logger.addHandler(f_handler)


class Drive(GoogleDrive):
    def __init__(self, *args, logger_: logging.Logger = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._logger = logger_
        self._folder_id_cache = {}

    def ListFile(self, title=None, is_folder=None, parent_folder=None):
        query = ["trashed = false"]
        if title:
            query.append(f"title = '{title}'")
        if parent_folder:
            query.append(f"'{parent_folder}' in parents")
        if is_folder is True:
            query.append("mimeType = 'application/vnd.google-apps.folder'")
        elif is_folder is False:
            query.append("mimeType != 'application/vnd.google-apps.folder'")

        query = " and ".join(query)
        return super().ListFile({'q': query}).GetList()

    def CreateFile(self, title, is_folder=False, parent_folder=None):
        metadata = dict()
        metadata['title'] = title
        if is_folder:
            metadata['mimeType'] = 'application/vnd.google-apps.folder'
        if parent_folder:
            metadata['parents'] = [{'id': parent_folder}]
        return super().CreateFile(metadata)

    def create_folder(self, path_parts: list):
        current_folder_id = 'root'
        remaining_parts = []
        for part in reversed(path_parts):
            if part in self._folder_id_cache:
                current_folder_id = self._folder_id_cache[part]
                break
            remaining_parts.insert(0, part)

        for part in remaining_parts:
            next_folder = self.ListFile(title=part,
                                        is_folder=True,
                                        parent_folder=current_folder_id)
            if not next_folder:
                next_folder = self.CreateFile(title=part,
                                              is_folder=True,
                                              parent_folder=current_folder_id)
                next_folder.Upload()
            else:
                next_folder = next_folder[0]

            current_folder_id = next_folder['id']
            self._folder_id_cache[part] = next_folder['id']
        return current_folder_id


def get_m3u_path(path):
    if path.is_relative_to(MUSICBEE_PLAYLIST_PATH):
        rel_path = path.relative_to(MUSICBEE_PLAYLIST_PATH)
    else:
        rel_path = path.relative_to(EXPORTED_PLAYLIST_PATH)
    if len(rel_path.parts) <= 2:
        return f"{rel_path.stem}.m3u"
    else:
        return f"{rel_path.parts[-2]} - {rel_path.stem}.m3u"


# Strips to only letters and digits
def strip(str_):
    return "".join([char for char in str(str_)
                    if char in string.ascii_letters + string.digits])


def get_songs(path: pathlib.Path) -> list[pathlib.Path]:
    # Incomprehensible, could break at any moment
    with open(path, errors='ignore') as file:
        if path.suffix == '.mbp':
            lines = [_ for _ in file.read().split('\x00') if ':\\' in _][1:]
            paths = [[___ for ___ in __.split('ÿÿÿÿ')] for __ in lines]
            paths = list(chain(*paths))[:-1]
            paths = [path.split(':\\')[0][-1] + ':\\' + path.split(':\\')[1]
                     for path in paths]
            paths = [pathlib.Path(path) for path in paths]
        elif path.suffix == '.m3u':
            paths = [pathlib.Path(path) for path in file.read().split('\n')
                     if path]

    good_paths = []
    bad_paths = []
    for path in paths:
        if path.exists():
            good_paths.append(path)
        else:
            bad_paths.append(path)

    # Fix up unicode errors by stripping them of anything except letters
    # and digits and then matching them
    stripped_to_path = {}
    for bad_path in bad_paths:
        stripped_bad_path = strip(bad_path)
        if bad_path.parent not in stripped_to_path:
            stripped_to_path[bad_path.parent] = {
                strip(path): path for path in bad_path.parent.glob('*.*')
            }
        if stripped_bad_path in stripped_to_path[bad_path.parent]:
            good_paths.append(
                stripped_to_path[bad_path.parent][stripped_bad_path]
            )

        else:
            logger.warning(f"{bad_path} was not found. "
                           f"It was located in {playlist_path}")

    return good_paths


logger.info("Started.")

gauth = GoogleAuth()
gauth.LocalWebserverAuth()
drive = Drive(gauth)

music_folder = drive.create_folder(['Music'])
songs_folder = drive.create_folder(['Music', 'songs'])

playlists = {}
# Add playlists
for playlist_path in MUSICBEE_PLAYLIST_PATH.rglob('*.mbp'):
    playlists[playlist_path] = get_songs(playlist_path)
# Add automatically exported autoplaylists
for playlist_path in EXPORTED_PLAYLIST_PATH.rglob('*.m3u'):
    playlists[playlist_path] = get_songs(playlist_path)

all_songs = set(chain.from_iterable(playlists.values()))
all_song_names = [song.name for song in all_songs]

# Delete songs in the main folder
existing_songs = drive.ListFile(parent_folder=songs_folder)
existing_songs_names = [song['title'] for song in existing_songs]
songs_to_delete = [song for song in existing_songs
                   if song['title'] not in all_song_names]
for i, song in enumerate(songs_to_delete):
    song.Trash()
    logger.debug(f"Song {i+1} of {len(songs_to_delete)} to delete done. "
                 f"{song['title']} was deleted.")

# Upload songs in the main folder
songs_to_upload = [song for song in all_songs
                   if song.name not in existing_songs_names]
for i, song in enumerate(songs_to_upload):
    file = drive.CreateFile(title=song.name, parent_folder=songs_folder)
    file.SetContentFile(song)
    file.Upload()
    logger.debug(f"Song {i+1} of {len(songs_to_upload)} to upload done."
                 f" {song.name} was uploaded.")

# TODO: Check the content of the playlists then change them instead of deleting and recreating which is slow

# Delete all previous playlists
existing_playlists = drive.ListFile(parent_folder=music_folder, is_folder=False)
for i, playlist in enumerate(existing_playlists):
    playlist.Trash()
    logger.debug(f"Old playlist {i+1} of {len(existing_playlists)} to delete "
                 f"done. {existing_playlists[i]['title']} was deleted.")

# Create m3u playlist files and upload them
for i, (playlist_path, playlist_songs) in enumerate(playlists.items()):
    file = drive.CreateFile(title=get_m3u_path(playlist_path),
                            parent_folder=music_folder)
    file_content = "\n".join([f"songs/{playlist_song.name}"
                             for playlist_song in playlist_songs])
    file.SetContentString(file_content)
    file.Upload()
    logger.debug(f"Playlist file {i+1} of {len(playlists)} to upload done. "
                 f"{get_m3u_path(playlist_path)} was uploaded.")


logger.info("Done.")
