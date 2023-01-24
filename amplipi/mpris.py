"""A module for interfacing with an MPRIS MediaPlayer2 over dbus."""

from dataclasses import dataclass
from enum import Enum, auto
import json
import time
import os
import sys
from typing import List
from multiprocessing import Process, Queue
from dasbus.connection import SessionMessageBus
from dasbus.client.proxy import disconnect_proxy


METADATA_MAPPINGS = [
  ('artist', 'xesam:artist'),
  ('title', 'xesam:title'),
  ('art_url', 'mpris:artUrl'),
  ('album', 'xesam:album')
]

METADATA_REFRESH_RATE = 0.5

class CommandTypes(Enum):
  PLAY = auto()
  PAUSE = auto()
  NEXT = auto()
  PREVIOUS = auto()

@dataclass
class Metadata:
  """A data class for storing metadata on a song."""
  artist: str = ''
  title: str = ''
  art_url: str = ''
  album: str = ''
  state: str = ''
  connected: bool = False
  state_changed_time: float = 0


class MPRIS:
  """A class for interfacing with an MPRIS MediaPlayer2 over dbus."""

  def __init__(self, service_suffix, metadata_path) -> None:
    self.mpris = SessionMessageBus().get_proxy(
        service_name = f"org.mpris.MediaPlayer2.{service_suffix}",
        object_path = "/org/mpris/MediaPlayer2",
        interface_name = "org.mpris.MediaPlayer2.Player"
    )

    self._signal = Queue()

    self.capabilities: List[CommandTypes] = []

    self.service_suffix = service_suffix
    self.metadata_path = metadata_path

    try:
      with open(self.metadata_path, "w", encoding='utf-8') as f:
        m = Metadata()
        m.state = "Stopped"
        json.dump(m.__dict__, f)
    except Exception as e:
      print (f'Exception clearing metadata file: {e}')

    try:
      self.metadata_process = Process(target=self._metadata_reader, args=[self._signal])
      self.metadata_process.start()
    except Exception as e:
      print(f'Exception starting MPRIS metadata process: {e}')

  def play(self) -> None:
    """Plays."""
    self.mpris.Play()

  def pause(self) -> None:
    """Pauses."""
    self.mpris.Pause()

  def next(self) -> None:
    """Skips song."""
    self.mpris.Next()

  def previous(self) -> None:
    """Goes back a song."""
    self.mpris.Previous()

  def play_pause(self) -> None:
    """Plays or pauses depending on current state."""
    self.mpris.PlayPause()

  def _load_metadata(self) -> Metadata:
    try:
      with open(self.metadata_path, 'r', encoding='utf-8') as f:
        metadata_dict = json.load(f)
        metadata_obj = Metadata()

        for k in metadata_dict.keys():
          metadata_obj.__dict__[k] = metadata_dict[k]

        return metadata_obj
    except Exception as e:
      print(f"MPRIS loading metadata at {self.metadata_path} failed: {e}")

    return Metadata()



  def metadata(self) -> Metadata:
    """Returns metadata from MPRIS."""
    return self._load_metadata()

  def is_playing(self) -> bool:
    """Playing?"""
    return self._load_metadata().state == 'Playing'

  def is_stopped(self) -> bool:
    """Stopped?"""
    return self._load_metadata().state == 'Stopped'

  def is_connected(self) -> bool:
    """Returns true if we can talk to the MPRIS dbus object."""
    return self._load_metadata().connected

  def get_capabilities(self) -> List[CommandTypes]:
    """Returns a list of supported commands."""

    if len(self.capabilities) == 0:

      if self.mpris.CanPlay:
        self.capabilities.append(CommandTypes.PLAY)

      if self.mpris.CanPause:
        self.capabilities.append(CommandTypes.PAUSE)

      if self.mpris.CanGoNext:
        self.capabilities.append(CommandTypes.NEXT)

      if self.mpris.CanGoPrevious:
        self.capabilities.append(CommandTypes.PREVIOUS)

    return self.capabilities

  def close(self):
    """Closes the MPRIS object."""
    print("walking to the well")
    self._signal.put('done')
    print("poisoning the well")
    # time.sleep(1)
    try:
      self.metadata_process.join(1.0)
    except Exception as e:
      print(f'Could not stop MPRIS metadata process: {e}')
    disconnect_proxy(self.mpris)
    # try:
    #   os.remove(self.metadata_path)
    # except Exception as e:
    #   print(f'Could not remove metadata file: {e}')
    # print(f'Closed MPRIS {self.service_suffix}')

  def __del__(self):
    self.close()

  def _metadata_reader(self, que: Queue):
    """Method run by the metadata process, also handles playing/paused."""

    m = Metadata()
    m.state = 'Stopped'

    last_sent = m.__dict__

    def ok():
      """Returns true if we should keep running."""
      if not que.empty():
        print(f"MPRIS metadata process for {self.service_suffix} exiting")
      else:
        print(f"MPRIS metadata process for {self.service_suffix} still running")
      return que.empty()

    while ok():

      metadata = {}
      try:
        mpris = SessionMessageBus().get_proxy(
          service_name = f"org.mpris.MediaPlayer2.{self.service_suffix}",
          object_path = "/org/mpris/MediaPlayer2",
          interface_name = "org.mpris.MediaPlayer2.Player"
        )
      except Exception as e:
        metadata['connected'] = False
        print(f"failed to connect mpris {e}")
        if not ok():
          return

      print(f"getting mrpis metadata from {self.service_suffix}")
      try:
        raw_metadata = {}
        try:
          raw_metadata = mpris.Metadata
        except Exception as e:
          metadata['connected'] = False
          print(f"Dbus error getting MPRIS metadata: {e}")
          if not ok():
            return

        for mapping in METADATA_MAPPINGS:
          try:
            metadata[mapping[0]] = str(raw_metadata[mapping[1]]).strip("[]'")
          except KeyError as e:
            print(f"Metadata mapping error: {e}")
            pass

        metadata['state'] = mpris.PlaybackStatus.strip("'")
        metadata['volume'] = mpris.Volume

        if metadata['state'] != last_sent['state']:
          metadata['state_changed_time'] = time.time()
        else:
          metadata['state_changed_time'] = last_sent['state_changed_time']

        metadata['connected'] = True

        if metadata != last_sent:
          last_sent = metadata
          with open(self.metadata_path, 'w', encoding='utf-8') as metadata_file:
            json.dump(metadata, metadata_file)

      except Exception as e:
        print(f"Error writing MPRIS metadata to file at {self.metadata_path}: {e}"
              +"\nThe above is normal if a user is not yet connected to the stream.")
        if not ok():
          return

      sys.stdout.flush() # forces stdout to print

      if not ok():
        return
      time.sleep(1.0/METADATA_REFRESH_RATE)
      try:
        disconnect_proxy(mpris)
      except Exception as e:
        print(e)
