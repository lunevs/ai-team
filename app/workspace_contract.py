"""Source workspace tool policy. No shell, imports or host paths are exposed to models."""
import hashlib
import io
import json
import re
import zipfile
from typing import Literal
from pydantic import Field
from app.schemas import StrictModel
from app.product_policy import safe_path,validate_sources,MAX_PACKAGE


class WorkspaceAction(StrictModel):
    operation:Literal['list','read','search','write','edit','delete','check','finish']
    path:str=Field(default='',max_length=180)
    content:str=Field(default='',max_length=90000)
    old_text:str=Field(default='',max_length=20000)
    new_text:str=Field(default='',max_length=20000)
    expected_sha256:str=Field(default='',pattern=r'^(|new|[a-f0-9]{64})$')
    offset:int=Field(default=0,ge=0)
    limit:int=Field(default=8000,ge=1,le=12000)
    summary:str=Field(min_length=5,max_length=2000)


from app.workspace_policy import *
