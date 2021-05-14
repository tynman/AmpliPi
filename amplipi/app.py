#!/usr/bin/python3

# AmpliPi Home Audio
# Copyright (C) 2021 MicroNova LLC
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""AmpliPi Webapp

This serves the amplipi webpp and underlying rest api, that it uses.
The FastAPI/Starlette web framework is used to simplify the web plumbing.
"""

# web framework
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
from fastapi.templating import Jinja2Templates
from fastapi_utils.cbv import cbv
from fastapi_utils.inferring_router import InferringRouter
# type handling, fastapi leverages type checking for performance and easy docs
from typing import List, Optional, Dict, Union, Set
from functools import lru_cache
# web server
import uvicorn
# amplipi
from amplipi.ctrl import Api # we don't import ctrl here to avoid naming ambiguity with a ctrl variable
import amplipi.rt as rt
import amplipi.utils as utils
import amplipi.models as models
#helpers
from json import dumps as jsonify
DEBUG_API = False

# start in the web directory (where everything is layed out for flask)
import os
template_dir = os.path.abspath('web/templates')
static_dir = os.path.abspath('web/static')
generated_dir = os.path.abspath('web/generated')

app = FastAPI(title='Amplipi')
templates = Jinja2Templates(template_dir)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/generated", StaticFiles(directory=generated_dir), name="generated") # TODO: make this register as a dynamic folder???

from typing import TYPE_CHECKING, Any, Callable, get_type_hints
from fastapi import APIRouter

class SimplifyingRouter(APIRouter):
  """
  Overrides the route decorator logic to:
  - to use the annotated return type as the `response_model` if unspecified.
  - always exclude unset fields (this makes so much more sense!)
  """
  if not TYPE_CHECKING:  # pragma: no branch
    def add_api_route(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
      if kwargs.get("response_model") is None:
        kwargs["response_model"] = get_type_hints(endpoint).get("return")
      kwargs["response_model_exclude_none"] = True
      return super().add_api_route(path, endpoint, **kwargs)

# Helper functions
def unused_groups(src: int) -> Dict[int,str]:
  """ Get groups that are not connected to src """
  ctrl = get_ctrl()
  groups = ctrl.status.groups
  return { g.id : g.name for g in groups if g.source_id != src}

def unused_zones(src: int) -> Dict[int,str]:
  """ Get zones that are not conencted to src """
  ctrl = get_ctrl()
  zones = ctrl.status.zones
  return { z.id : z.name for z in zones if z.source_id != src }

def ungrouped_zones(src: int) -> List[models.Zone]:
  """ Get zones that are connected to src, but don't belong to a full group """
  ctrl = get_ctrl()
  zones = ctrl.status.zones
  groups = ctrl.status.groups
  # get all of the zones that belong to this sources groups
  grouped_zones: Set[int] = set()
  for g in groups:
    if g.source_id == src:
      grouped_zones = grouped_zones.union(g.zones)
  # get all of the zones connected to this soource
  source_zones = set([ z.id for z in zones if z.source_id == src ])
  # return all of the zones connected to this source that aren't in a group
  ungrouped_zones_ = source_zones.difference(grouped_zones)
  return [ zones[z] for z in ungrouped_zones_ if z and not zones[z].disabled]

def song_info(src: int) -> Dict[str,str]:
  """ Get the song info for a source """
  ctrl =  get_ctrl()
  song_fields = ['artist', 'album', 'track', 'img_url']
  stream = ctrl._get_stream(src)
  info = stream.info if stream else {}
  # add empty strings for unpopulated fields
  for field in song_fields:
    if field not in info:
      info[field] = ''
  return info


# Add our own router so we can bind/inject custom settings into our API routes
api_router = SimplifyingRouter()
settings = models.AppSettings()
@lru_cache(1) # Api controller should only be instantiated once (we clear the cache with get_ctr.cache_clear() after settings object is configured)
def get_ctrl() -> Api:
  return Api(settings)

@cbv(api_router)
class API:

  # embedded ctrl dependency used by every api route
  ctrl: Api = Depends(get_ctrl)

  @api_router.get('/')
  def get_status(self) -> models.Status:
    return self.ctrl.get_state()

  def code_response(self, resp):
    if resp is None:
      # general commands return None to indicate success
      return self.ctrl.get_state()
    elif 'error' in resp:
      # TODO: refine error codes based on error message
      raise HTTPException(404, resp['error'])
    else:
      return resp

  # sources

  @api_router.get('/sources')
  def get_sources(self) -> Dict[str, List[models.Source]]:
    return {'sources' : self.ctrl.get_state().sources}

  @api_router.get('/sources/{sid}')
  def get_source(self, sid: int) -> models.Source:
    # TODO: add get_X capabilities to underlying API?
    sources = self.ctrl.get_state().sources
    return sources[sid]

  @api_router.patch('/sources/{sid}')
  def set_source(self, sid: int, update: models.SourceUpdate) -> models.Status:
    return self.code_response(self.ctrl.set_source(sid, update))

  # zones

  @api_router.get('/zones')
  def get_zones(self) -> Dict[str, List[models.Zone]]:
    return {'zones': self.ctrl.get_state().zones}

  @api_router.get('/zones/{zid}')
  def get_zone(self, zid: int) -> models.Zone:
    zones = self.ctrl.get_state().zones
    if zid >= 0 and zid < len(zones):
      return zones[zid]
    else:
      raise HTTPException(404, f'zone {zid} not found')

  @api_router.patch('/zones/{zid}')
  def set_zone(self, zid: int, zone: models.ZoneUpdate):
    return self.code_response(self.ctrl.set_zone(zid, zone))

  # groups

  @api_router.post('/group')
  def create_group(self, group: models.Group) -> models.Group:
    return self.code_response(self.ctrl.create_group(group))

  @api_router.get('/groups')
  def get_groups(self) -> Dict[str, List[models.Group]]:
    return {'groups' : self.ctrl.get_state().groups}

  @api_router.get('/groups/{gid}')
  def get_group(self, gid: int) -> models.Group:
    _, grp = utils.find(self.ctrl.get_state().groups, gid)
    if grp is not None:
      return grp
    else:
      raise HTTPException(404, f'group {gid} not found')

  @api_router.patch('/groups/{gid}')
  def set_group(self, gid: int, group: models.GroupUpdate) -> models.Status:
    return self.code_response(self.ctrl.set_group(gid, group)) # TODO: pass update directly

  @api_router.delete('/groups/{gid}')
  def delete_group(self, gid: int) -> models.Status:
    return self.code_response(self.ctrl.delete_group(id=gid))

  # streams

  @api_router.post('/stream')
  def create_stream(self, stream: models.Stream) -> models.Stream:
    return self.code_response(self.ctrl.create_stream(stream))

  @api_router.get('/streams')
  def get_streams(self) -> Dict[str, List[models.Stream]]:
    return {'streams' : self.ctrl.get_state().streams}

  @api_router.get('/streams/{sid}')
  def get_stream(self, sid: int) -> models.Stream:
    _, stream = utils.find(self.ctrl.get_state().streams, sid)
    if stream is not None:
      return stream
    else:
      raise HTTPException(404, f'stream {sid} not found')

  @api_router.patch('/streams/{sid}')
  def set_stream(self, sid: int, update: models.StreamUpdate) -> models.Status:
    return self.code_response(self.ctrl.set_stream(sid, update))

  @api_router.delete('/streams/{sid}')
  def delete_stream(self, sid: int) -> models.Status:
    return self.code_response(self.ctrl.delete_stream(sid))

  @api_router.post('/streams/{sid}/{cmd}')
  def exec_command(self, sid: int, cmd: str) -> models.Status:
    return self.code_response(self.ctrl.exec_stream_command(sid, cmd=cmd))

  # presets

  @api_router.post('/preset')
  def create_preset(self, preset: models.Preset) -> models.Preset:
    return self.code_response(self.ctrl.create_preset(preset))

  @api_router.get('/presets')
  def get_presets(self) -> Dict[str, List[models.Preset]]:
    return {'presets' : self.ctrl.get_state().presets}

  @api_router.get('/presets/{pid}')
  def get_preset(self, pid: int) -> models.Preset:
    _, preset = utils.find(self.ctrl.get_state().presets, pid)
    if preset is not None:
      return preset
    else:
      raise HTTPException(404, f'preset {pid} not found')

  @api_router.patch('/presets/{pid}')
  async def set_preset(self, pid: int, update: models.PresetUpdate) -> models.Status:
    return self.code_response(self.ctrl.set_preset(pid, update))

  @api_router.delete('/presets/{pid}')
  def delete_preset(self, pid: int) -> models.Status:
    return self.code_response(self.ctrl.delete_preset(pid))

  @api_router.post('/presets/{pid}/load')
  def load_preset(self, pid: int) -> models.Status:
    return self.code_response(self.ctrl.load_preset(pid))

  # Documentation

  @api_router.get('/doc')
  def doc(self):
    # TODO: add hosted python docs as well
    return FileResponse(f'{template_dir}/rest-api-doc.html') # TODO: this is not really a template

app.include_router(api_router, prefix='/api')

# add the root of the API as well, since empty paths are invalid this needs to be handled outside of the router
@app.get('/api', response_model_exclude_none=True)
def get_status(ctrl:Api=Depends(get_ctrl)) -> models.Status:
  return ctrl.get_state()

# Website

@app.get('/')
@app.get('/{src}')
def view(request: Request, src:int=0, ctrl:Api=Depends(get_ctrl)):
  ctrl = get_ctrl()
  s = ctrl.get_state()
  context = {
    # needed for template to make response
    'request': request,
    # simplified amplipi state
    'cur_src': src,
    'sources': s.sources,
    'zones': s.zones,
    'groups': s.groups,
    'presets': s.presets,
    'inputs': ctrl.get_inputs(),
    'unused_groups': [unused_groups(src) for src in range(4)],
    'unused_zones': [unused_zones(src) for src in range(4)],
    'ungrouped_zones': [ungrouped_zones(src) for src in range(4)],
    'song_info': [song_info(src) for src in range(4)],
    'version': s.info['version'],
  }
  return templates.TemplateResponse("index.html.j2", context)

def create_app(mock_ctrl=None, mock_streams=None, config_file=None, delay_saves=None, s:models.AppSettings=models.AppSettings()) -> FastAPI:
  # specify old parameters
  if mock_ctrl: s.mock_ctrl = mock_ctrl
  if mock_streams: s.mock_streams = mock_streams
  if config_file: s.config_file = config_file
  if delay_saves: s.delay_saves = delay_saves
  # modify settings
  global settings
  settings = s # set the settings class the api_router uses to instantiate a singleton of the API class
  get_ctrl.cache_clear()
  app.state.ctrl = get_ctrl()
  return app
